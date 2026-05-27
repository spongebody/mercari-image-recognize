import json
from pathlib import Path

import pytest

from app.observability.recorder import Recorder
from app.observability.store import Store


@pytest.fixture
def recorder(tmp_path: Path) -> Recorder:
    store = Store(tmp_path / "obs.db")
    store.init_schema()
    return Recorder(store=store, store_root=tmp_path / "store")


def test_start_request_creates_row_and_files(recorder: Recorder):
    recorder.start_request(
        request_id="rid1",
        method="POST",
        endpoint="/api/v1/mercari/image/analyze",
        client_ip="127.0.0.1",
        user_agent="curl/8",
        language="ja",
        headers={"content-type": "application/json"},
        body_bytes=b'{"foo":"bar"}',
        content_type="application/json",
        uploaded_images=[],
    )
    with recorder.store.connect() as conn:
        row = conn.execute("SELECT * FROM requests WHERE request_id='rid1'").fetchone()
    assert row["endpoint"].endswith("/analyze")
    assert row["body_summary"]
    request_file = next(recorder.store_root.rglob("rid1/request.json"))
    assert json.loads(request_file.read_text())["body"]["json"] == {"foo": "bar"}


def test_start_request_records_image_manifest(recorder: Recorder):
    """Uploaded images are saved AND listed in request.json so the UI can render them."""
    recorder.start_request(
        request_id="rid_img",
        method="POST",
        endpoint="/api/v1/mercari/image/analyze",
        client_ip="", user_agent="", language="ja",
        headers={"content-type": "multipart/form-data; boundary=---"},
        body_bytes=b"",
        content_type="multipart/form-data",
        uploaded_images=[
            {"filename": "shirt.jpg", "content_type": "image/jpeg",
             "suffix": ".jpg", "bytes": b"\xff\xd8\xff\xe0fake"},
            {"filename": "tag.png", "content_type": "image/png",
             "suffix": ".png", "bytes": b"\x89PNG\r\n\x1a\nfake"},
        ],
    )
    request_file = next(recorder.store_root.rglob("rid_img/request.json"))
    payload = json.loads(request_file.read_text())
    assert "images" in payload
    assert len(payload["images"]) == 2
    assert payload["images"][0]["filename"] == "shirt.jpg"
    assert payload["images"][0]["content_type"] == "image/jpeg"
    assert payload["images"][0]["saved_as"] == "image_0.jpg"
    assert payload["images"][0]["size_bytes"] > 0
    assert payload["images"][1]["saved_as"] == "image_1.png"
    # actual bytes are on disk
    d = request_file.parent
    assert (d / "image_0.jpg").exists()
    assert (d / "image_1.png").exists()


def test_finalize_request_writes_response_and_status(recorder: Recorder):
    recorder.start_request(
        request_id="rid2", method="POST", endpoint="/x",
        client_ip="", user_agent="", language="", headers={},
        body_bytes=b"", content_type="", uploaded_images=[],
    )
    recorder.finalize_request(
        request_id="rid2", status_code=502, duration_ms=12.3,
        error="boom", response_body=b'{"detail":"all attempts failed"}',
        job_id="",
    )
    with recorder.store.connect() as conn:
        row = conn.execute("SELECT status_code, error_kind, error, duration_ms FROM requests WHERE request_id='rid2'").fetchone()
    assert row["status_code"] == 502
    assert row["error_kind"] == "exception"
    assert row["error"] == "boom"
    assert row["duration_ms"] == 12.3
    response_file = next(recorder.store_root.rglob("rid2/response.json"))
    assert "all attempts failed" in response_file.read_text()


def test_error_kind_classification(recorder: Recorder):
    cases = [
        (200, "", "ok"),
        (404, "", "http_4xx"),
        (500, "", "http_5xx"),
        (500, "ZeroDivisionError(...)", "exception"),
    ]
    for i, (status, err, expected_kind) in enumerate(cases):
        rid = f"rid_kind_{i}"
        recorder.start_request(request_id=rid, method="GET", endpoint="/x",
                               client_ip="", user_agent="", language="",
                               headers={}, body_bytes=b"", content_type="",
                               uploaded_images=[])
        recorder.finalize_request(request_id=rid, status_code=status, duration_ms=1.0,
                                  error=err, response_body=b"", job_id="")
        with recorder.store.connect() as conn:
            kind = conn.execute("SELECT error_kind FROM requests WHERE request_id=?", (rid,)).fetchone()["error_kind"]
        assert kind == expected_kind, f"case {i}: got {kind}"


def test_error_kind_llm_failed_when_http_5xx_and_llm_attempts_failed(recorder: Recorder):
    """A 5xx HTTP response with an llm_calls row marked failed promotes error_kind to 'llm_failed'."""
    recorder.start_request(
        request_id="rid_llm_fail", method="POST", endpoint="/x",
        client_ip="", user_agent="", language="", headers={},
        body_bytes=b"", content_type="", uploaded_images=[],
    )
    # seed a failed llm_call directly via the store
    recorder.store.insert_llm_call(
        request_id="rid_llm_fail",
        timestamp_utc="2026-05-26T00:00:00",
        stage="category",
        attempt=1,
        model="m",
        status="failed",
        error_kind="request_failed",
        error_message="upstream 500",
        latency_ms=10.0,
        http_status_code=500,
        prompt_tokens=None,
        completion_tokens=None,
        total_tokens=None,
        cost_usd=None,
        prompt_file=None,
        response_file=None,
        parsed_file=None,
    )
    recorder.finalize_request(
        request_id="rid_llm_fail",
        status_code=502, duration_ms=1.0,
        error="",  # no Python exception — HTTP-level llm failure
        response_body=b"", job_id="",
    )
    with recorder.store.connect() as conn:
        kind = conn.execute("SELECT error_kind FROM requests WHERE request_id='rid_llm_fail'").fetchone()["error_kind"]
    assert kind == "llm_failed"


def test_record_llm_stage_writes_rows_and_files(recorder: Recorder):
    # need a parent request row first
    recorder.start_request(
        request_id="rid_llm", method="POST", endpoint="/api/v1/x",
        client_ip="", user_agent="", language="", headers={},
        body_bytes=b"", content_type="", uploaded_images=[],
    )
    attempts = [
        {"model": "openai/gpt-4o-mini", "attempt": 1, "error_kind": "ok",
         "message": "", "latency_ms": 800.0, "status_code": 200},
    ]
    raw_response = {
        "choices": [{"message": {"content": "{\"category\":\"shoes\"}"}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
        "cost": 0.0021,
    }
    parsed = {"category": "shoes"}
    messages = [{"role": "user", "content": "hello"}]
    recorder.record_llm_stage(
        request_id="rid_llm",
        stage="category",
        attempts=attempts,
        messages=messages,
        raw_response=raw_response,
        parsed=parsed,
    )
    with recorder.store.connect() as conn:
        row = conn.execute("SELECT * FROM llm_calls WHERE request_id='rid_llm'").fetchone()
    assert row["stage"] == "category"
    assert row["status"] == "ok"
    assert row["total_tokens"] == 130
    assert abs(row["cost_usd"] - 0.0021) < 1e-9
    assert row["prompt_file"].endswith("llm_category_1_prompt.json")
    assert row["response_file"].endswith("llm_category_1_response.json")
    assert row["parsed_file"].endswith("llm_category_1_parsed.json")
    d = next(recorder.store_root.rglob("rid_llm"))
    assert (d / "llm_category_1_prompt.json").exists()
    assert (d / "llm_category_1_response.json").exists()
    assert (d / "llm_category_1_parsed.json").exists()


def test_record_llm_stage_failure_only(recorder: Recorder):
    recorder.start_request(
        request_id="rid_fail", method="POST", endpoint="/x",
        client_ip="", user_agent="", language="", headers={},
        body_bytes=b"", content_type="", uploaded_images=[],
    )
    attempts = [
        {"model": "openai/gpt-4o-mini", "attempt": 1, "error_kind": "request_failed",
         "message": "OpenRouter returned 500", "latency_ms": 50.0, "status_code": 500},
        {"model": "openai/gpt-4o-mini", "attempt": 2, "error_kind": "request_failed",
         "message": "OpenRouter returned 500", "latency_ms": 70.0, "status_code": 500},
    ]
    recorder.record_llm_stage(
        request_id="rid_fail",
        stage="category",
        attempts=attempts,
        messages=[{"role": "user", "content": "x"}],
        raw_response=None,
        parsed=None,
    )
    with recorder.store.connect() as conn:
        rows = list(conn.execute("SELECT status, parsed_file FROM llm_calls WHERE request_id='rid_fail' ORDER BY attempt"))
    assert [r["status"] for r in rows] == ["failed", "failed"]
    assert all(r["parsed_file"] is None for r in rows)


def test_record_llm_stage_mixed_attempts(recorder: Recorder):
    """Failed attempt followed by a successful retry — only the ok row gets parsed/cost/tokens."""
    recorder.start_request(
        request_id="rid_mix", method="POST", endpoint="/x",
        client_ip="", user_agent="", language="", headers={},
        body_bytes=b"", content_type="", uploaded_images=[],
    )
    attempts = [
        {"model": "m1", "attempt": 1, "error_kind": "request_failed",
         "message": "timeout", "latency_ms": 5000.0, "status_code": 504},
        {"model": "m2", "attempt": 2, "error_kind": "ok",
         "message": "", "latency_ms": 800.0, "status_code": 200},
    ]
    raw = {"usage": {"total_tokens": 250, "prompt_tokens": 200, "completion_tokens": 50}, "cost": 0.001}
    recorder.record_llm_stage(
        request_id="rid_mix", stage="category",
        attempts=attempts, messages=[{"role": "user", "content": "x"}],
        raw_response=raw, parsed={"category": "x"},
    )
    with recorder.store.connect() as conn:
        rows = list(conn.execute(
            "SELECT attempt, status, total_tokens, cost_usd, parsed_file FROM llm_calls "
            "WHERE request_id='rid_mix' ORDER BY attempt"))
    assert [r["status"] for r in rows] == ["failed", "ok"]
    # failed attempt: no tokens, no cost, no parsed_file
    assert rows[0]["total_tokens"] is None
    assert rows[0]["cost_usd"] is None
    assert rows[0]["parsed_file"] is None
    # ok attempt: gets the tokens/cost/parsed
    assert rows[1]["total_tokens"] == 250
    assert abs(rows[1]["cost_usd"] - 0.001) < 1e-9
    assert rows[1]["parsed_file"].endswith("llm_category_2_parsed.json")


def test_record_llm_stage_cost_in_usage_field(recorder: Recorder):
    """OpenRouter sometimes reports cost under usage.cost — confirm we pick it up."""
    recorder.start_request(
        request_id="rid_cu", method="POST", endpoint="/x",
        client_ip="", user_agent="", language="", headers={},
        body_bytes=b"", content_type="", uploaded_images=[],
    )
    raw = {"usage": {"total_tokens": 100, "cost": 0.0099}}
    recorder.record_llm_stage(
        request_id="rid_cu", stage="category",
        attempts=[{"model": "m", "attempt": 1, "error_kind": "ok",
                   "message": "", "latency_ms": 10.0, "status_code": 200}],
        messages=[{"role": "user", "content": "y"}],
        raw_response=raw, parsed={"k": "v"},
    )
    with recorder.store.connect() as conn:
        row = conn.execute("SELECT cost_usd FROM llm_calls WHERE request_id='rid_cu'").fetchone()
    assert abs(row["cost_usd"] - 0.0099) < 1e-9
