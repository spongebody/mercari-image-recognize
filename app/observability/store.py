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
  error
);

CREATE VIRTUAL TABLE IF NOT EXISTS llm_fts USING fts5(
  request_id UNINDEXED,
  llm_call_id UNINDEXED,
  stage,
  model,
  error_message,
  prompt_text,
  response_text
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

    def insert_request_start(
        self,
        request_id: str,
        timestamp_utc: str,
        method: str,
        endpoint: str,
        client_ip: str,
        user_agent: str,
        language: str,
        body_summary: str,
        has_image: bool,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO requests (request_id, timestamp_utc, method, endpoint,
                    client_ip, user_agent, language, body_summary, has_image, error_kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (request_id, timestamp_utc, method, endpoint, client_ip, user_agent,
                 language, body_summary, 1 if has_image else 0),
            )

    def finalize_request(
        self,
        request_id: str,
        status_code: int,
        duration_ms: float,
        error: str,
        error_kind: str,
        job_id: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE requests
                   SET status_code = ?, duration_ms = ?, error = ?, error_kind = ?,
                       job_id = COALESCE(NULLIF(?, ''), job_id)
                 WHERE request_id = ?
                """,
                (status_code, duration_ms, error, error_kind, job_id, request_id),
            )

    def insert_llm_call(
        self,
        request_id: str,
        timestamp_utc: str,
        stage: str,
        attempt: int,
        model: str,
        status: str,
        error_kind,
        error_message,
        latency_ms,
        http_status_code,
        prompt_tokens,
        completion_tokens,
        total_tokens,
        cost_usd,
        prompt_file,
        response_file,
        parsed_file,
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO llm_calls (
                    request_id, timestamp_utc, stage, attempt, model, status,
                    error_kind, error_message, latency_ms, http_status_code,
                    prompt_tokens, completion_tokens, total_tokens, cost_usd,
                    prompt_file, response_file, parsed_file
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (request_id, timestamp_utc, stage, attempt, model, status,
                 error_kind, error_message, latency_ms, http_status_code,
                 prompt_tokens, completion_tokens, total_tokens, cost_usd,
                 prompt_file, response_file, parsed_file),
            )
            return int(cur.lastrowid)

    def aggregate_request_totals(self, request_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE requests
                   SET total_tokens = (SELECT COALESCE(SUM(total_tokens), 0) FROM llm_calls WHERE request_id = ?),
                       total_cost_usd = (SELECT COALESCE(SUM(cost_usd), 0.0) FROM llm_calls WHERE request_id = ?),
                       llm_call_count = (SELECT COUNT(*) FROM llm_calls WHERE request_id = ?)
                 WHERE request_id = ?
                """,
                (request_id, request_id, request_id, request_id),
            )

    def insert_request_fts(self, request_id: str, endpoint: str, body_summary: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO requests_fts (request_id, endpoint, body_summary, error) VALUES (?, ?, ?, ?)",
                (request_id, endpoint, body_summary, error),
            )

    def insert_llm_fts(
        self,
        request_id: str,
        llm_call_id: int,
        stage: str,
        model: str,
        error_message: str,
        prompt_text: str,
        response_text: str,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_fts (request_id, llm_call_id, stage, model,
                    error_message, prompt_text, response_text)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (request_id, llm_call_id, stage, model, error_message, prompt_text, response_text),
            )
