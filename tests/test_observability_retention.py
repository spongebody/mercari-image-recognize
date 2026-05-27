from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.observability.recorder import Recorder
from app.observability.retention import prune
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
