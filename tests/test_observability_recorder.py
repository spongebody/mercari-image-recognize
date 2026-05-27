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
