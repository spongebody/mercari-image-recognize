from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .store import Store


_logger = logging.getLogger(__name__)


@dataclass
class PruneStats:
    rows_deleted: int
    bytes_freed: int


def _dir_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for sub in path.rglob("*"):
        try:
            if sub.is_file():
                total += sub.stat().st_size
        except OSError:
            continue
    return total


def _total_store_bytes(store_root: Path) -> int:
    if not store_root.exists():
        return 0
    return _dir_size(store_root)


def _delete_one(store: Store, store_root: Path, request_id: str) -> int:
    freed = 0
    with store.connect() as conn:
        row = conn.execute("SELECT timestamp_utc FROM requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            return 0
        date_str = row["timestamp_utc"][:10]
        conn.execute("DELETE FROM requests_fts WHERE request_id=?", (request_id,))
        conn.execute("DELETE FROM llm_fts WHERE request_id=?", (request_id,))
        conn.execute("DELETE FROM requests WHERE request_id=?", (request_id,))  # cascades llm_calls
    d = store_root / date_str / request_id
    if d.exists():
        freed = _dir_size(d)
        shutil.rmtree(d, ignore_errors=True)
        parent = d.parent
        try:
            if parent.exists() and not any(parent.iterdir()):
                parent.rmdir()
        except OSError:
            pass
    return freed


def prune(store: Store, store_root: Path, retention_days: int, max_total_bytes: int) -> PruneStats:
    rows_deleted = 0
    bytes_freed = 0

    # 1) age-based
    if retention_days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with store.connect() as conn:
            old = [r["request_id"] for r in conn.execute(
                "SELECT request_id FROM requests WHERE timestamp_utc < ? ORDER BY timestamp_utc", (cutoff,))]
        for rid in old:
            bytes_freed += _delete_one(store, store_root, rid)
            rows_deleted += 1

    # 2) capacity-based: snapshot total once then subtract per-delete to avoid
    # walking the filesystem on every iteration (O(N*K) → O(N+K)).
    if max_total_bytes > 0:
        current_bytes = _total_store_bytes(store_root)
        while current_bytes > max_total_bytes:
            with store.connect() as conn:
                row = conn.execute("SELECT request_id FROM requests ORDER BY timestamp_utc ASC LIMIT 1").fetchone()
            if row is None:
                break
            freed = _delete_one(store, store_root, row["request_id"])
            bytes_freed += freed
            current_bytes -= freed
            rows_deleted += 1

    _logger.info("observability.prune deleted=%d freed_bytes=%d", rows_deleted, bytes_freed)
    return PruneStats(rows_deleted=rows_deleted, bytes_freed=bytes_freed)


def clear_all(store: Store, store_root: Path) -> PruneStats:
    """Wipe every recorded request and its artifacts.

    Drops all rows from requests / llm_calls / requests_fts / llm_fts, then
    removes every per-date directory under store_root. Leaves _dead_letter/
    intact so diagnostic info from logging failures survives a manual wipe.
    """
    with store.connect() as conn:
        rows_deleted = conn.execute("SELECT COUNT(*) AS n FROM requests").fetchone()["n"]
        conn.execute("DELETE FROM requests_fts")
        conn.execute("DELETE FROM llm_fts")
        conn.execute("DELETE FROM requests")  # cascades llm_calls

    bytes_freed = 0
    if store_root.exists():
        for entry in store_root.iterdir():
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            bytes_freed += _dir_size(entry)
            shutil.rmtree(entry, ignore_errors=True)

    _logger.info("observability.clear_all deleted=%d freed_bytes=%d", rows_deleted, bytes_freed)
    return PruneStats(rows_deleted=rows_deleted, bytes_freed=bytes_freed)
