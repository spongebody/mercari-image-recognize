from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  request_id        TEXT PRIMARY KEY,
  timestamp_utc     TEXT NOT NULL,
  method            TEXT NOT NULL,
  endpoint          TEXT NOT NULL,
  status_code       INTEGER,
  duration_ms       REAL,
  error             TEXT,
  error_kind        TEXT,
  client_ip         TEXT,
  user_agent        TEXT,
  job_id            TEXT,
  language          TEXT,
  body_summary      TEXT,
  total_tokens      INTEGER,
  total_cost_usd    REAL,
  llm_call_count    INTEGER DEFAULT 0,
  has_image         INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_requests_ts          ON requests(timestamp_utc DESC);
CREATE INDEX IF NOT EXISTS idx_requests_status      ON requests(status_code);
CREATE INDEX IF NOT EXISTS idx_requests_endpoint    ON requests(endpoint, timestamp_utc DESC);
CREATE INDEX IF NOT EXISTS idx_requests_job_id      ON requests(job_id) WHERE job_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_requests_error_kind  ON requests(error_kind) WHERE error_kind != 'ok';

CREATE TABLE IF NOT EXISTS llm_calls (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id        TEXT NOT NULL REFERENCES requests(request_id) ON DELETE CASCADE,
  timestamp_utc     TEXT NOT NULL,
  stage             TEXT NOT NULL,
  attempt           INTEGER NOT NULL,
  model             TEXT NOT NULL,
  status            TEXT NOT NULL,
  error_kind        TEXT,
  error_message     TEXT,
  latency_ms        REAL,
  http_status_code  INTEGER,
  prompt_tokens     INTEGER,
  completion_tokens INTEGER,
  total_tokens      INTEGER,
  cost_usd          REAL,
  prompt_file       TEXT,
  response_file     TEXT,
  parsed_file       TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_request ON llm_calls(request_id);
CREATE INDEX IF NOT EXISTS idx_llm_ts      ON llm_calls(timestamp_utc DESC);
CREATE INDEX IF NOT EXISTS idx_llm_stage   ON llm_calls(stage, status);
CREATE INDEX IF NOT EXISTS idx_llm_failed  ON llm_calls(status) WHERE status != 'ok';

CREATE VIRTUAL TABLE IF NOT EXISTS requests_fts USING fts5(
  request_id UNINDEXED,
  endpoint,
  body_summary,
  error,
  content=''
);

CREATE VIRTUAL TABLE IF NOT EXISTS llm_fts USING fts5(
  request_id UNINDEXED,
  llm_call_id UNINDEXED,
  stage,
  model,
  error_message,
  prompt_text,
  response_text,
  content=''
);
"""


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)

    def init_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()
