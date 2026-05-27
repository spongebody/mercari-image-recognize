import sqlite3
from pathlib import Path

from app.observability.store import Store


def test_store_creates_tables_and_indexes(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    store = Store(db_path)
    store.init_schema()

    with sqlite3.connect(db_path) as conn:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"requests", "llm_calls", "requests_fts", "llm_fts"}.issubset(names)


def test_store_enables_wal_and_foreign_keys(tmp_path: Path):
    db_path = tmp_path / "obs.db"
    store = Store(db_path)
    store.init_schema()
    with store.connect() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def _make_store(tmp_path: Path) -> Store:
    store = Store(tmp_path / "obs.db")
    store.init_schema()
    return store


def test_insert_request_and_finalize(tmp_path: Path):
    store = _make_store(tmp_path)
    store.insert_request_start(
        request_id="rid1",
        timestamp_utc="2026-05-26T00:00:00",
        method="POST",
        endpoint="/api/v1/mercari/image/analyze",
        client_ip="127.0.0.1",
        user_agent="curl/8",
        language="ja",
        body_summary="3 files; debug=false",
        has_image=True,
    )
    store.finalize_request(
        request_id="rid1",
        status_code=200,
        duration_ms=1234.5,
        error="",
        error_kind="ok",
        job_id="job-1",
    )
    with store.connect() as conn:
        row = conn.execute("SELECT * FROM requests WHERE request_id='rid1'").fetchone()
    assert row["status_code"] == 200
    assert row["duration_ms"] == 1234.5
    assert row["job_id"] == "job-1"
    assert row["error_kind"] == "ok"


def test_insert_llm_call_and_aggregate_tokens(tmp_path: Path):
    store = _make_store(tmp_path)
    store.insert_request_start("rid2", "2026-05-26T00:00:00", "POST", "/x", "", "", "", "", False)
    store.insert_llm_call(
        request_id="rid2",
        timestamp_utc="2026-05-26T00:00:01",
        stage="category",
        attempt=1,
        model="openai/gpt-4o-mini",
        status="ok",
        error_kind=None,
        error_message=None,
        latency_ms=812.0,
        http_status_code=200,
        prompt_tokens=400,
        completion_tokens=120,
        total_tokens=520,
        cost_usd=0.0042,
        prompt_file="2026-05-26/rid2/llm_category_1_prompt.json",
        response_file="2026-05-26/rid2/llm_category_1_response.json",
        parsed_file="2026-05-26/rid2/llm_category_1_parsed.json",
    )
    store.aggregate_request_totals("rid2")
    with store.connect() as conn:
        row = conn.execute("SELECT total_tokens, total_cost_usd, llm_call_count FROM requests WHERE request_id='rid2'").fetchone()
    assert row["total_tokens"] == 520
    assert abs(row["total_cost_usd"] - 0.0042) < 1e-9
    assert row["llm_call_count"] == 1


def test_fts_insert_and_match(tmp_path: Path):
    store = _make_store(tmp_path)
    store.insert_request_start("rid3", "2026-05-26T00:00:00", "POST", "/p", "", "", "", "looking for nike shirt", False)
    store.insert_request_fts("rid3", "/p", "looking for nike shirt", "")
    with store.connect() as conn:
        rows = list(conn.execute("SELECT request_id FROM requests_fts WHERE requests_fts MATCH 'nike'"))
    assert rows[0]["request_id"] == "rid3"
