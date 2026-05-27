from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.observability.recorder import Recorder
from app.observability.retention import clear_all, prune
from app.observability.store import Store


def _make_recorder(tmp_path: Path) -> Recorder:
    store = Store(tmp_path / "obs.db")
    store.init_schema()
    return Recorder(store=store, store_root=tmp_path / "store")


def _seed_request(recorder: Recorder, request_id: str, days_ago: int):
    recorder.start_request(
        request_id=request_id, method="POST", endpoint="/x",
        client_ip="", user_agent="", language="", headers={},
        body_bytes=b"hello", content_type="application/json",
        uploaded_images=[],
    )
    # backdate the row to simulate age
    backdated = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    with recorder.store.connect() as conn:
        conn.execute("UPDATE requests SET timestamp_utc=? WHERE request_id=?", (backdated, request_id))


def test_prune_by_age(tmp_path: Path):
    recorder = _make_recorder(tmp_path)
    _seed_request(recorder, "old", days_ago=10)
    _seed_request(recorder, "fresh", days_ago=1)
    stats = prune(recorder.store, recorder.store_root, retention_days=7, max_total_bytes=10**12)
    assert stats.rows_deleted == 1
    with recorder.store.connect() as conn:
        ids = {r["request_id"] for r in conn.execute("SELECT request_id FROM requests")}
    assert ids == {"fresh"}


def test_prune_by_total_bytes(tmp_path: Path):
    recorder = _make_recorder(tmp_path)
    for i in range(5):
        rid = f"r{i}"
        _seed_request(recorder, rid, days_ago=i)  # oldest = r4
        # pad disk
        d = next(recorder.store_root.rglob(rid))
        (d / "padding.bin").write_bytes(b"\x00" * (1024 * 1024))  # 1 MB each
    stats = prune(recorder.store, recorder.store_root, retention_days=999, max_total_bytes=2 * 1024 * 1024)
    assert stats.rows_deleted >= 3  # only ~2 MB fits


def test_prune_purges_fts_rows(tmp_path: Path):
    recorder = _make_recorder(tmp_path)
    _seed_request(recorder, "old", days_ago=30)
    prune(recorder.store, recorder.store_root, retention_days=7, max_total_bytes=10**12)
    with recorder.store.connect() as conn:
        rows = list(conn.execute("SELECT request_id FROM requests_fts WHERE request_id='old'"))
    assert rows == []


def test_clear_all_drops_rows_and_artifacts(tmp_path: Path):
    recorder = _make_recorder(tmp_path)
    _seed_request(recorder, "a", days_ago=0)
    _seed_request(recorder, "b", days_ago=5)
    # seed an LLM call + FTS row to confirm cascade
    recorder.record_llm_stage(
        request_id="a", stage="category",
        attempts=[{"model": "m", "attempt": 1, "error_kind": "ok",
                   "message": "", "latency_ms": 1.0, "status_code": 200}],
        messages=[{"role": "user", "content": "x"}],
        raw_response={"usage": {"total_tokens": 1}},
        parsed={},
    )
    stats = clear_all(recorder.store, recorder.store_root)
    assert stats.rows_deleted == 2
    with recorder.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM requests").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM llm_calls").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM requests_fts").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM llm_fts").fetchone()["n"] == 0
    # date dirs are gone
    date_dirs = [p for p in recorder.store_root.iterdir() if p.is_dir() and not p.name.startswith("_")]
    assert date_dirs == []


def test_clear_all_preserves_dead_letter(tmp_path: Path):
    recorder = _make_recorder(tmp_path)
    recorder._dead_letter("test", {"k": "v"})
    _seed_request(recorder, "x", days_ago=0)
    clear_all(recorder.store, recorder.store_root)
    dead = recorder.store_root / "_dead_letter"
    assert dead.exists()
    assert any(dead.iterdir()), "_dead_letter contents should be preserved"
