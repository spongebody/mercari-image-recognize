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
