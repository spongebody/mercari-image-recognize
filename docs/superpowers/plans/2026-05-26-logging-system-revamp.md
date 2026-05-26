# Logging System Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current dual-mechanism logging (file-per-LLM-call + per-request JSON) with a unified, queryable system: SQLite metadata + filesystem artifacts, linked by `request_id`, served through a password-protected web viewer.

**Architecture:** New `app/observability/` package owns the store, context propagation, recorder, retention, auth, and query API. HTTP middleware mints `request_id`, propagates via contextvar (carried into the `ThreadPoolExecutor`), and writes to SQLite + `logs/store/<date>/<request_id>/`. Old `app/request_logging.py` and `service._log_raw` are deleted in a single cutover.

**Tech Stack:** Python 3.11, FastAPI, SQLite (stdlib `sqlite3`, WAL mode, FTS5), pytest, vanilla JS for the web viewer.

**Spec:** `docs/superpowers/specs/2026-05-26-logging-system-revamp-design.md`

---

## File Structure

**Create:**
- `app/observability/__init__.py` — package marker, re-export public API
- `app/observability/store.py` — SQLite schema, connection, low-level insert/query
- `app/observability/context.py` — `request_id` contextvar + set/get/reset helpers
- `app/observability/paths.py` — request_id → file path resolution, traversal-safe
- `app/observability/recorder.py` — high-level `start_request / finalize_request / record_llm_stage`
- `app/observability/retention.py` — days + max-bytes prune
- `app/observability/auth.py` — Basic Auth FastAPI dependency
- `app/observability/api.py` — `/api/v1/logs/*` routes
- `web/logs.html` — single-page viewer
- `scripts/wipe_old_logs.py` — one-shot cleanup of pre-cutover files
- `tests/test_observability_store.py`
- `tests/test_observability_context.py`
- `tests/test_observability_paths.py`
- `tests/test_observability_recorder.py`
- `tests/test_observability_retention.py`
- `tests/test_observability_auth.py`
- `tests/test_observability_api.py`
- `tests/test_observability_middleware.py`
- `tests/test_observability_e2e.py`

**Modify:**
- `app/config.py` — add 5 new settings, remove `log_llm_raw`
- `main.py` — replace middleware, propagate request_id to executor, wire new API router, gate `/config` behind auth
- `app/service.py` — replace `_log_raw` calls with `recorder.record_llm_stage`, accept `request_id` in worker entrypoints
- `app/showcase/service.py` — reuse contextvar request_id, add `record_llm_stage`
- `.env.example` — document new env vars
- `README.md` — link to `/logs`, document `LOGS_PASSWORD`

**Delete:**
- `app/request_logging.py`
- All log files under `logs/` (via `scripts/wipe_old_logs.py`)

---

## Task 1: SQLite store — schema + open/close

**Files:**
- Create: `app/observability/__init__.py` (empty for now)
- Create: `app/observability/store.py`
- Create: `tests/test_observability_store.py`

- [ ] **Step 1: Write the failing test for schema initialization**

Create `tests/test_observability_store.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /Users/youbo/gala/labs/mercari-image-recognize
pytest tests/test_observability_store.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'app.observability'`.

- [ ] **Step 3: Implement Store with schema**

Create `app/observability/__init__.py` (empty file).

Create `app/observability/store.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_observability_store.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/observability/__init__.py app/observability/store.py tests/test_observability_store.py
git commit -m "feat(observability): add SQLite store with schema and WAL pragmas"
```

---

## Task 2: SQLite store — insert/update/query helpers

**Files:**
- Modify: `app/observability/store.py`
- Modify: `tests/test_observability_store.py`

- [ ] **Step 1: Add failing tests for inserts and queries**

Append to `tests/test_observability_store.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_observability_store.py -v
```

Expected: 3 new tests FAIL with `AttributeError: 'Store' object has no attribute 'insert_request_start'`.

- [ ] **Step 3: Implement insert/update/query methods**

Append to `app/observability/store.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_observability_store.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/observability/store.py tests/test_observability_store.py
git commit -m "feat(observability): add store insert/update/query helpers"
```

---

## Task 3: Context module — request_id contextvar

**Files:**
- Create: `app/observability/context.py`
- Create: `tests/test_observability_context.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_observability_context.py`:

```python
from concurrent.futures import ThreadPoolExecutor

from app.observability import context as ctx


def test_set_get_reset_request_id():
    assert ctx.get_request_id() is None
    token = ctx.set_request_id("rid-1")
    try:
        assert ctx.get_request_id() == "rid-1"
    finally:
        ctx.reset_request_id(token)
    assert ctx.get_request_id() is None


def test_propagate_into_worker_thread():
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        def worker(request_id: str):
            token = ctx.set_request_id(request_id)
            try:
                return ctx.get_request_id()
            finally:
                ctx.reset_request_id(token)

        fut = pool.submit(worker, "rid-bg")
        assert fut.result() == "rid-bg"
        # parent thread untouched
        assert ctx.get_request_id() is None
    finally:
        pool.shutdown()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_observability_context.py -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement context module**

Create `app/observability/context.py`:

```python
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

_request_id_var: ContextVar[Optional[str]] = ContextVar("observability_request_id", default=None)


def get_request_id() -> Optional[str]:
    return _request_id_var.get()


def set_request_id(request_id: str) -> Token:
    return _request_id_var.set(request_id)


def reset_request_id(token: Token) -> None:
    _request_id_var.reset(token)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_observability_context.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add app/observability/context.py tests/test_observability_context.py
git commit -m "feat(observability): add request_id contextvar helpers"
```

---

## Task 4: Paths module — date-bucketed dir + traversal-safe lookup

**Files:**
- Create: `app/observability/paths.py`
- Create: `tests/test_observability_paths.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_observability_paths.py`:

```python
from pathlib import Path

import pytest

from app.observability.paths import (
    artifact_dir,
    resolve_artifact,
    request_dir_for_date,
)


def test_artifact_dir_uses_iso_date_then_request_id(tmp_path: Path):
    d = artifact_dir(tmp_path, "2026-05-26", "abc123")
    assert d == tmp_path / "2026-05-26" / "abc123"


def test_resolve_artifact_inside_dir_ok(tmp_path: Path):
    base = tmp_path / "2026-05-26" / "abc"
    base.mkdir(parents=True)
    (base / "request.json").write_text("{}")
    p = resolve_artifact(tmp_path, "2026-05-26", "abc", "request.json")
    assert p == base / "request.json"


def test_resolve_artifact_rejects_traversal(tmp_path: Path):
    base = tmp_path / "2026-05-26" / "abc"
    base.mkdir(parents=True)
    with pytest.raises(ValueError):
        resolve_artifact(tmp_path, "2026-05-26", "abc", "../../etc/passwd")
    with pytest.raises(ValueError):
        resolve_artifact(tmp_path, "2026-05-26", "abc", "/abs/path")


def test_request_dir_for_date_lists_subdirs(tmp_path: Path):
    (tmp_path / "2026-05-26" / "r1").mkdir(parents=True)
    (tmp_path / "2026-05-26" / "r2").mkdir(parents=True)
    dirs = sorted(p.name for p in request_dir_for_date(tmp_path, "2026-05-26"))
    assert dirs == ["r1", "r2"]
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_observability_paths.py -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement paths module**

Create `app/observability/paths.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Iterable


def artifact_dir(store_root: Path, date_str: str, request_id: str) -> Path:
    return Path(store_root) / date_str / request_id


def resolve_artifact(store_root: Path, date_str: str, request_id: str, filename: str) -> Path:
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise ValueError(f"Disallowed filename: {filename!r}")
    base = artifact_dir(store_root, date_str, request_id).resolve()
    candidate = (base / filename).resolve()
    if not str(candidate).startswith(str(base) + ("\\" if "\\" in str(base) else "/")) and candidate != base:
        raise ValueError(f"Path escapes artifact dir: {candidate}")
    return candidate


def request_dir_for_date(store_root: Path, date_str: str) -> Iterable[Path]:
    day_dir = Path(store_root) / date_str
    if not day_dir.exists():
        return []
    return [p for p in day_dir.iterdir() if p.is_dir()]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_observability_paths.py -v
```

Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add app/observability/paths.py tests/test_observability_paths.py
git commit -m "feat(observability): add traversal-safe artifact path helpers"
```

---

## Task 5: Recorder — start_request + finalize_request

**Files:**
- Create: `app/observability/recorder.py`
- Create: `tests/test_observability_recorder.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_observability_recorder.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_observability_recorder.py -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement Recorder.start_request + finalize_request**

Create `app/observability/recorder.py`:

```python
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .paths import artifact_dir
from .store import Store


_logger = logging.getLogger(__name__)
_MAX_DEAD_LETTERS = 1000


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _date_str_from_iso(iso_ts: str) -> str:
    return iso_ts[:10]


def _classify_error_kind(status_code: Optional[int], error: str) -> str:
    if error:
        return "exception"
    if status_code is None:
        return "exception"
    if 200 <= status_code < 400:
        return "ok"
    if 400 <= status_code < 500:
        return "http_4xx"
    return "http_5xx"


def _summarize_body(content_type: str, body_bytes: bytes, uploaded_images: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    if uploaded_images:
        parts.append(f"{len(uploaded_images)} images: " + ", ".join(img.get("filename", "") for img in uploaded_images))
    if "application/json" in content_type and body_bytes:
        try:
            obj = json.loads(body_bytes)
            if isinstance(obj, dict):
                parts.append("keys: " + ",".join(sorted(obj.keys())[:10]))
        except Exception:
            pass
    if not parts:
        parts.append(f"{len(body_bytes)} bytes")
    return "; ".join(parts)


def _try_parse_json(body_bytes: bytes) -> Any:
    if not body_bytes:
        return None
    try:
        return json.loads(body_bytes)
    except Exception:
        return None


class Recorder:
    def __init__(self, *, store: Store, store_root: Path) -> None:
        self.store = store
        self.store_root = Path(store_root)
        self.store_root.mkdir(parents=True, exist_ok=True)

    # ---- HTTP request lifecycle -------------------------------------------

    def start_request(
        self,
        *,
        request_id: str,
        method: str,
        endpoint: str,
        client_ip: str,
        user_agent: str,
        language: str,
        headers: Dict[str, str],
        body_bytes: bytes,
        content_type: str,
        uploaded_images: List[Dict[str, Any]],
    ) -> None:
        try:
            ts = _utcnow_iso()
            date_str = _date_str_from_iso(ts)
            body_summary = _summarize_body(content_type, body_bytes, uploaded_images)
            self.store.insert_request_start(
                request_id=request_id,
                timestamp_utc=ts,
                method=method,
                endpoint=endpoint,
                client_ip=client_ip,
                user_agent=user_agent,
                language=language,
                body_summary=body_summary,
                has_image=bool(uploaded_images),
            )
            self.store.insert_request_fts(request_id, endpoint, body_summary, "")
            d = artifact_dir(self.store_root, date_str, request_id)
            d.mkdir(parents=True, exist_ok=True)
            request_payload: Dict[str, Any] = {
                "timestamp_utc": ts,
                "method": method,
                "endpoint": endpoint,
                "headers": dict(headers),
                "client_ip": client_ip,
                "language": language,
            }
            parsed = _try_parse_json(body_bytes) if "application/json" in content_type else None
            if parsed is not None:
                request_payload["body"] = {"json": parsed}
            elif body_bytes:
                request_payload["body"] = {"size_bytes": len(body_bytes)}
            for idx, img in enumerate(uploaded_images):
                data = img.get("bytes")
                if data:
                    suffix = img.get("suffix", ".bin")
                    (d / f"image_{idx}{suffix}").write_bytes(data)
            (d / "request.json").write_text(json.dumps(request_payload, ensure_ascii=False, indent=2))
        except Exception as exc:
            _logger.exception("observability.start_request failed: %s", exc)
            self._dead_letter("start_request", {"request_id": request_id, "error": repr(exc)})

    def finalize_request(
        self,
        *,
        request_id: str,
        status_code: int,
        duration_ms: float,
        error: str,
        response_body: bytes,
        job_id: str,
    ) -> None:
        try:
            error_kind = _classify_error_kind(status_code, error)
            if error_kind == "http_5xx":
                with self.store.connect() as conn:
                    row = conn.execute(
                        "SELECT COUNT(*) AS n FROM llm_calls WHERE request_id=? AND status!='ok'",
                        (request_id,),
                    ).fetchone()
                if row and row["n"] > 0:
                    error_kind = "llm_failed"
            self.store.finalize_request(
                request_id=request_id,
                status_code=status_code,
                duration_ms=duration_ms,
                error=error,
                error_kind=error_kind,
                job_id=job_id or "",
            )
            self.store.aggregate_request_totals(request_id)
            # locate the day-dir for this request
            d = self._find_request_dir(request_id)
            if d is not None:
                response_payload: Dict[str, Any] = {
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                    "error": error,
                }
                parsed = _try_parse_json(response_body)
                if parsed is not None:
                    response_payload["body"] = {"json": parsed}
                elif response_body:
                    response_payload["body"] = {"size_bytes": len(response_body)}
                (d / "response.json").write_text(json.dumps(response_payload, ensure_ascii=False, indent=2))
        except Exception as exc:
            _logger.exception("observability.finalize_request failed: %s", exc)
            self._dead_letter("finalize_request", {"request_id": request_id, "error": repr(exc)})

    def _find_request_dir(self, request_id: str) -> Optional[Path]:
        for date_dir in sorted(self.store_root.iterdir(), reverse=True):
            if not date_dir.is_dir() or date_dir.name.startswith("_"):
                continue
            candidate = date_dir / request_id
            if candidate.is_dir():
                return candidate
        return None

    def _dead_letter(self, kind: str, payload: Dict[str, Any]) -> None:
        try:
            d = self.store_root / "_dead_letter"
            d.mkdir(parents=True, exist_ok=True)
            existing = list(d.iterdir())
            if len(existing) >= _MAX_DEAD_LETTERS:
                return
            ts = int(time.time() * 1000)
            (d / f"{ts}_{kind}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception:
            pass
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_observability_recorder.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/observability/recorder.py tests/test_observability_recorder.py
git commit -m "feat(observability): add Recorder.start_request and finalize_request"
```

---

## Task 6: Recorder — record_llm_stage

**Files:**
- Modify: `app/observability/recorder.py`
- Modify: `tests/test_observability_recorder.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_observability_recorder.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_observability_recorder.py -v
```

Expected: 2 new tests FAIL with `AttributeError`.

- [ ] **Step 3: Implement record_llm_stage**

Append to `app/observability/recorder.py` (inside `Recorder` class):

```python
    def record_llm_stage(
        self,
        *,
        request_id: str,
        stage: str,
        attempts: Iterable[Dict[str, Any]],
        messages: List[Dict[str, Any]],
        raw_response: Optional[Dict[str, Any]],
        parsed: Optional[Dict[str, Any]],
    ) -> None:
        try:
            attempts = list(attempts)
            if not attempts:
                return
            d = self._find_request_dir(request_id)
            if d is None:
                # no parent: create a tombstone day-dir under today
                from datetime import datetime as _dt
                date_str = _dt.now(timezone.utc).strftime("%Y-%m-%d")
                d = artifact_dir(self.store_root, date_str, request_id)
                d.mkdir(parents=True, exist_ok=True)

            usage = (raw_response or {}).get("usage") or {}
            cost = (raw_response or {}).get("cost")
            if isinstance(cost, dict):
                cost = cost.get("total")

            for idx, attempt in enumerate(attempts):
                attempt_idx = int(attempt.get("attempt") or (idx + 1))
                error_kind = attempt.get("error_kind") or "ok"
                status = "ok" if error_kind == "ok" else "failed"

                # prompt file: write per attempt (cheap, simplifies UI)
                prompt_rel = f"llm_{stage}_{attempt_idx}_prompt.json"
                (d / prompt_rel).write_text(json.dumps({"messages": messages}, ensure_ascii=False, indent=2))

                response_rel = None
                parsed_rel = None
                attempt_prompt_tokens = None
                attempt_completion_tokens = None
                attempt_total_tokens = None
                attempt_cost = None
                if status == "ok":
                    if raw_response is not None:
                        response_rel = f"llm_{stage}_{attempt_idx}_response.json"
                        (d / response_rel).write_text(json.dumps(raw_response, ensure_ascii=False, indent=2))
                    if parsed is not None:
                        parsed_rel = f"llm_{stage}_{attempt_idx}_parsed.json"
                        (d / parsed_rel).write_text(json.dumps(parsed, ensure_ascii=False, indent=2))
                    attempt_prompt_tokens = usage.get("prompt_tokens")
                    attempt_completion_tokens = usage.get("completion_tokens")
                    attempt_total_tokens = usage.get("total_tokens")
                    attempt_cost = float(cost) if cost is not None else None

                # store path relative to store_root
                date_str = d.parent.name
                def _rel(name):
                    return f"{date_str}/{request_id}/{name}" if name else None

                llm_call_id = self.store.insert_llm_call(
                    request_id=request_id,
                    timestamp_utc=_utcnow_iso(),
                    stage=stage,
                    attempt=attempt_idx,
                    model=attempt.get("model") or "",
                    status=status,
                    error_kind=None if status == "ok" else error_kind,
                    error_message=None if status == "ok" else (attempt.get("message") or ""),
                    latency_ms=float(attempt.get("latency_ms") or 0.0),
                    http_status_code=attempt.get("status_code"),
                    prompt_tokens=attempt_prompt_tokens,
                    completion_tokens=attempt_completion_tokens,
                    total_tokens=attempt_total_tokens,
                    cost_usd=attempt_cost,
                    prompt_file=_rel(prompt_rel),
                    response_file=_rel(response_rel),
                    parsed_file=_rel(parsed_rel),
                )

                prompt_text = json.dumps(messages, ensure_ascii=False)
                response_text = json.dumps(raw_response, ensure_ascii=False) if (raw_response and status == "ok") else ""
                self.store.insert_llm_fts(
                    request_id=request_id,
                    llm_call_id=llm_call_id,
                    stage=stage,
                    model=attempt.get("model") or "",
                    error_message=attempt.get("message") or "",
                    prompt_text=prompt_text,
                    response_text=response_text,
                )
        except Exception as exc:
            _logger.exception("observability.record_llm_stage failed: %s", exc)
            self._dead_letter("record_llm_stage", {"request_id": request_id, "stage": stage, "error": repr(exc)})
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_observability_recorder.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/observability/recorder.py tests/test_observability_recorder.py
git commit -m "feat(observability): add record_llm_stage with per-attempt rows"
```

---

## Task 7: Retention — days + max bytes prune

**Files:**
- Create: `app/observability/retention.py`
- Create: `tests/test_observability_retention.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_observability_retention.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_observability_retention.py -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement retention**

Create `app/observability/retention.py`:

```python
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Tuple

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

    # 2) capacity-based
    if max_total_bytes > 0:
        while _total_store_bytes(store_root) > max_total_bytes:
            with store.connect() as conn:
                row = conn.execute("SELECT request_id FROM requests ORDER BY timestamp_utc ASC LIMIT 1").fetchone()
            if row is None:
                break
            bytes_freed += _delete_one(store, store_root, row["request_id"])
            rows_deleted += 1

    _logger.info("observability.prune deleted=%d freed_bytes=%d", rows_deleted, bytes_freed)
    return PruneStats(rows_deleted=rows_deleted, bytes_freed=bytes_freed)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_observability_retention.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add app/observability/retention.py tests/test_observability_retention.py
git commit -m "feat(observability): add days + max-bytes retention prune"
```

---

## Task 8: Config additions

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Create/modify: `tests/test_config.py` (extend)

- [ ] **Step 1: Find current test file and verify pattern**

```bash
grep -n "log_requests_retention_days\|log_requests_max_files\|Settings" tests/test_config.py | head
```

- [ ] **Step 2: Add failing test for new settings**

Append to `tests/test_config.py`:

```python
def test_new_observability_settings_defaults(monkeypatch):
    monkeypatch.delenv("LOGS_PASSWORD", raising=False)
    monkeypatch.delenv("LOG_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("LOG_MAX_TOTAL_BYTES", raising=False)
    monkeypatch.delenv("LOG_PRUNE_INTERVAL_MINUTES", raising=False)
    monkeypatch.delenv("LOG_RESPONSE_MAX_BYTES", raising=False)
    from app.config import Settings
    s = Settings()
    assert s.logs_password == ""
    assert s.log_retention_days == 7
    assert s.log_max_total_bytes == 5 * 1024 ** 3
    assert s.log_prune_interval_minutes == 60
    assert s.log_response_max_bytes == 2 * 1024 * 1024


def test_new_observability_settings_env_override(monkeypatch):
    monkeypatch.setenv("LOGS_PASSWORD", "hunter2")
    monkeypatch.setenv("LOG_RETENTION_DAYS", "30")
    monkeypatch.setenv("LOG_MAX_TOTAL_BYTES", "1073741824")
    monkeypatch.setenv("LOG_PRUNE_INTERVAL_MINUTES", "10")
    monkeypatch.setenv("LOG_RESPONSE_MAX_BYTES", "65536")
    from app.config import Settings
    s = Settings()
    assert s.logs_password == "hunter2"
    assert s.log_retention_days == 30
    assert s.log_max_total_bytes == 1073741824
    assert s.log_prune_interval_minutes == 10
    assert s.log_response_max_bytes == 65536
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
pytest tests/test_config.py::test_new_observability_settings_defaults tests/test_config.py::test_new_observability_settings_env_override -v
```

Expected: FAIL with `AttributeError: 'Settings' object has no attribute 'logs_password'`.

- [ ] **Step 4: Add new fields, remove `log_llm_raw`**

In `app/config.py`, locate the line `log_llm_raw: bool = _env_bool("LOG_LLM_RAW", False)` (line 123). Replace that line with:

```python
    log_requests: bool = _env_bool("LOG_REQUESTS", True)
    log_retention_days: int = _env_int_min("LOG_RETENTION_DAYS", 7, 1)
    log_max_total_bytes: int = _env_int_min("LOG_MAX_TOTAL_BYTES", 5 * 1024 ** 3, 1024 ** 2)
    log_prune_interval_minutes: int = _env_int_min("LOG_PRUNE_INTERVAL_MINUTES", 60, 1)
    log_response_max_bytes: int = _env_int_min("LOG_RESPONSE_MAX_BYTES", 2 * 1024 * 1024, 0)
    logs_password: str = os.getenv("LOGS_PASSWORD", "")
```

Also delete the existing line `log_requests: bool = _env_bool("LOG_REQUESTS", True)` (line 124) — it's been moved up. Also delete:

```python
    log_requests_retention_days: int = _env_int("LOG_REQUESTS_RETENTION_DAYS", 7)
    log_requests_max_files: int = _env_int("LOG_REQUESTS_MAX_FILES", 1000)
```

(They become obsolete; the new retention covers both.)

- [ ] **Step 5: Run tests to verify they pass**

```bash
pytest tests/test_config.py -v
```

Expected: all tests pass (including 2 new ones). Note: existing tests referencing `log_llm_raw` / `log_requests_retention_days` / `log_requests_max_files` will fail — update those test references to use the new names, or delete tests that only checked the old names.

If you see failures in `tests/test_config.py` for `log_llm_raw` / `log_requests_retention_days`, edit those tests to use the new fields or remove them (those features are being retired).

- [ ] **Step 6: Update `.env.example`**

Add to `.env.example`:

```
# Logs viewer auth (empty disables /logs and /config)
LOGS_PASSWORD=

# Observability retention (days OR bytes — whichever hits first)
LOG_RETENTION_DAYS=7
LOG_MAX_TOTAL_BYTES=5368709120
LOG_PRUNE_INTERVAL_MINUTES=60
LOG_RESPONSE_MAX_BYTES=2097152
```

Remove the line `LOG_LLM_RAW=false` if present, and `LOG_REQUESTS_RETENTION_DAYS` / `LOG_REQUESTS_MAX_FILES`.

- [ ] **Step 7: Commit**

```bash
git add app/config.py tests/test_config.py .env.example
git commit -m "feat(observability): config additions; retire log_llm_raw and per-file retention"
```

---

## Task 9: Auth dependency

**Files:**
- Create: `app/observability/auth.py`
- Create: `tests/test_observability_auth.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_observability_auth.py`:

```python
import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability.auth import require_logs_auth


def _app(password: str) -> FastAPI:
    app = FastAPI()
    from fastapi import Depends

    @app.get("/secret", dependencies=[Depends(require_logs_auth(password))])
    def secret():
        return {"ok": True}

    return app


def test_no_password_returns_503():
    client = TestClient(_app(""))
    r = client.get("/secret")
    assert r.status_code == 503


def test_missing_credentials_returns_401():
    client = TestClient(_app("hunter2"))
    r = client.get("/secret")
    assert r.status_code == 401
    assert r.headers["WWW-Authenticate"].startswith("Basic")


def test_wrong_password_returns_401():
    client = TestClient(_app("hunter2"))
    creds = base64.b64encode(b"admin:wrong").decode()
    r = client.get("/secret", headers={"Authorization": f"Basic {creds}"})
    assert r.status_code == 401


def test_correct_password_returns_200():
    client = TestClient(_app("hunter2"))
    creds = base64.b64encode(b"admin:hunter2").decode()
    r = client.get("/secret", headers={"Authorization": f"Basic {creds}"})
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_bearer_token_also_accepted():
    client = TestClient(_app("hunter2"))
    r = client.get("/secret", headers={"Authorization": "Bearer hunter2"})
    assert r.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_observability_auth.py -v
```

Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement auth dependency**

Create `app/observability/auth.py`:

```python
from __future__ import annotations

import base64
import hmac
from typing import Callable, Optional

from fastapi import Header, HTTPException


def require_logs_auth(expected_password: str) -> Callable:
    def _dep(authorization: Optional[str] = Header(default=None)) -> None:
        if not expected_password:
            raise HTTPException(status_code=503, detail="Logs viewer not configured (set LOGS_PASSWORD).")
        if not authorization:
            raise HTTPException(
                status_code=401,
                detail="Unauthorized",
                headers={"WWW-Authenticate": 'Basic realm="logs"'},
            )
        scheme, _, value = authorization.partition(" ")
        provided: Optional[str] = None
        if scheme.lower() == "basic":
            try:
                decoded = base64.b64decode(value).decode("utf-8", errors="replace")
                _, _, pw = decoded.partition(":")
                provided = pw
            except Exception:
                provided = None
        elif scheme.lower() == "bearer":
            provided = value
        if not provided or not hmac.compare_digest(provided, expected_password):
            raise HTTPException(
                status_code=401,
                detail="Unauthorized",
                headers={"WWW-Authenticate": 'Basic realm="logs"'},
            )

    return _dep
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_observability_auth.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/observability/auth.py tests/test_observability_auth.py
git commit -m "feat(observability): basic-auth dependency for logs and config pages"
```

---

## Task 10: Replace HTTP middleware in main.py

**Files:**
- Modify: `main.py:444-480` (replace `log_requests` middleware)
- Modify: `main.py` imports
- Create: `tests/test_observability_middleware.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_observability_middleware.py`:

```python
import json

from fastapi.testclient import TestClient

from main import app


def test_request_gets_x_request_id_header():
    with TestClient(app) as client:
        r = client.get("/health")
    assert "x-request-id" in {k.lower() for k in r.headers}
    assert len(r.headers["x-request-id"]) == 32


def test_request_logged_to_sqlite(tmp_path, monkeypatch):
    # point store at tmp path
    from app import observability
    # this test depends on the singleton wired in main.py; we just verify the
    # row exists for whatever path the running app uses
    with TestClient(app) as client:
        client.get("/health")
    # the actual store path is dictated by main.py's wiring; assert via API
    # introduced later in Task 13. For now, only assert header presence above.
```

(We intentionally keep the assertions light here — full e2e is Task 17.)

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_observability_middleware.py -v
```

Expected: FAIL with `KeyError: 'x-request-id'`.

- [ ] **Step 3: Wire up the recorder singleton and replace middleware**

In `main.py`, replace the import line `from app.request_logging import build_request_log, write_request_log` with:

```python
from app.observability import context as obs_ctx
from app.observability.auth import require_logs_auth
from app.observability.recorder import Recorder
from app.observability.retention import prune as obs_prune
from app.observability.store import Store as ObsStore
```

After `settings = load_settings()` (around line 32) add:

```python
_obs_store = ObsStore(BASE_DIR / "logs" / "observability.db")
_obs_store.init_schema()
recorder = Recorder(store=_obs_store, store_root=BASE_DIR / "logs" / "store")
```

Replace the existing `@app.middleware("http")` block (lines 444-480) with:

```python
import uuid as _uuid

_JOB_ID_PATH_PREFIX = "/api/v1/mercari/image/analyze/"


def _job_id_from_path(path: str) -> str:
    if path.startswith(_JOB_ID_PATH_PREFIX):
        return path[len(_JOB_ID_PATH_PREFIX):].split("/", 1)[0]
    return ""


def _job_id_from_response(body: bytes) -> str:
    try:
        obj = json.loads(body or b"{}")
    except Exception:
        return ""
    if isinstance(obj, dict):
        for key in ("job_id", "id"):
            v = obj.get(key)
            if isinstance(v, str):
                return v
    return ""


@app.middleware("http")
async def observe_request(request: Request, call_next):
    if not settings.log_requests or request.url.path == "/health":
        return await call_next(request)

    request_id = _uuid.uuid4().hex
    token = obs_ctx.set_request_id(request_id)
    start = time.monotonic()
    body = b""
    status_code = 500
    error_message = ""
    response_body_bytes = b""

    try:
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()

            async def receive() -> dict:
                return {"type": "http.request", "body": body, "more_body": False}

            request = Request(request.scope, receive)

        content_type = request.headers.get("content-type", "")
        uploaded_images: List[Dict[str, Any]] = []
        if "multipart/form-data" in content_type and body:
            # parse to capture image bytes for archive
            from starlette.datastructures import UploadFile as _Upload
            from starlette.requests import Request as _SReq

            async def _recv():
                return {"type": "http.request", "body": body, "more_body": False}

            tmp_req = _SReq(request.scope, _recv)
            try:
                form = await tmp_req.form()
                for key, value in form.multi_items():
                    if isinstance(value, _Upload):
                        data = await value.read()
                        suffix = "." + (value.content_type.rsplit("/", 1)[-1] if value.content_type else "bin")
                        uploaded_images.append({
                            "filename": value.filename or "",
                            "content_type": value.content_type or "",
                            "suffix": suffix,
                            "bytes": data,
                        })
            except Exception:
                pass

        try:
            recorder.start_request(
                request_id=request_id,
                method=request.method,
                endpoint=request.url.path,
                client_ip=(request.client.host if request.client else ""),
                user_agent=request.headers.get("user-agent", ""),
                language=request.query_params.get("language", "") or "",
                headers={
                    "content-type": content_type,
                    "user-agent": request.headers.get("user-agent", ""),
                    "content-length": request.headers.get("content-length", ""),
                },
                body_bytes=body,
                content_type=content_type,
                uploaded_images=uploaded_images,
            )
        except Exception:
            pass

        response = await call_next(request)
        status_code = response.status_code

        # buffer response body (cap at LOG_RESPONSE_MAX_BYTES)
        cap = settings.log_response_max_bytes
        chunks: List[bytes] = []
        total = 0
        async for chunk in response.body_iterator:
            chunks.append(chunk)
            total += len(chunk)
            if total > cap:
                break
        response_body_bytes = b"".join(chunks)

        from starlette.responses import Response as _Resp
        new_response = _Resp(
            content=response_body_bytes,
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() != "content-length"},
            media_type=response.media_type,
        )
        new_response.headers["X-Request-Id"] = request_id
        return new_response

    except Exception as exc:
        error_message = repr(exc)
        raise
    finally:
        duration_ms = (time.monotonic() - start) * 1000.0
        try:
            job_id = _job_id_from_path(request.url.path) or _job_id_from_response(response_body_bytes)
            recorder.finalize_request(
                request_id=request_id,
                status_code=status_code,
                duration_ms=duration_ms,
                error=error_message,
                response_body=response_body_bytes,
                job_id=job_id,
            )
        except Exception:
            pass
        obs_ctx.reset_request_id(token)
```

- [ ] **Step 4: Run middleware tests to verify they pass**

```bash
pytest tests/test_observability_middleware.py -v
```

Expected: PASS.

- [ ] **Step 5: Run the full test suite to surface regressions**

```bash
pytest -x -q
```

If any pre-existing test broke because it relied on the old `log_requests_retention_days` / `log_requests_max_files` or old request logging behavior, update those tests to assert new behavior (or delete them if they tested the now-removed code path).

- [ ] **Step 6: Commit**

```bash
git add main.py tests/test_observability_middleware.py
git commit -m "feat(observability): replace log_requests middleware with recorder + X-Request-Id"
```

---

## Task 11: Propagate request_id into product_data_executor

**Files:**
- Modify: `main.py` (around lines 507, 519)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_observability_middleware.py`:

```python
def test_request_id_propagates_into_background_thread(monkeypatch):
    """Submitting work to product_data_executor preserves request_id contextvar."""
    captured = []

    def fake_generate(images, language, debug, use_fallback_prompt, started_at, model_override=None):
        from app.observability import context as ctx
        captured.append(ctx.get_request_id())
        return {"ok": True}

    from main import analyzer
    monkeypatch.setattr(analyzer, "generate_product_data", fake_generate)
    # build a minimal multipart request
    files = [("image_list", ("a.png", b"\x89PNG\r\n\x1a\n", "image/png"))]
    data = {"language": "ja", "debug": "false"}
    with TestClient(app) as client:
        client.post("/api/v1/mercari/image/analyze", data=data, files=files)
    assert captured, "executor never called"
    assert captured[0] is not None
    assert len(captured[0]) == 32
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_observability_middleware.py::test_request_id_propagates_into_background_thread -v
```

Expected: FAIL (None captured — executor strips contextvar).

- [ ] **Step 3: Wrap executor submissions**

In `main.py`, add a helper near the executor declaration (after line 59):

```python
def _submit_with_request_id(fn, /, *args, **kwargs):
    rid = obs_ctx.get_request_id()
    def _runner():
        token = obs_ctx.set_request_id(rid) if rid else None
        try:
            return fn(*args, **kwargs)
        finally:
            if token is not None:
                obs_ctx.reset_request_id(token)
    return product_data_executor.submit(_runner)
```

Replace lines 507-514 with:

```python
        product_future = _submit_with_request_id(
            analyzer.generate_product_data,
            images=image_payloads,
            language=language,
            debug=debug_enabled,
            use_fallback_prompt=False,
            started_at=primary_submitted_at,
        )
```

Replace lines 519-527 with:

```python
            fallback_future = _submit_with_request_id(
                analyzer.generate_product_data,
                images=image_payloads,
                language=language,
                debug=debug_enabled,
                model_override=fallback_model,
                use_fallback_prompt=True,
                started_at=fallback_submitted_at,
            )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_observability_middleware.py::test_request_id_propagates_into_background_thread -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_observability_middleware.py
git commit -m "feat(observability): propagate request_id into product_data_executor"
```

---

## Task 12: Replace `_log_raw` in service.py with `record_llm_stage`

**Files:**
- Modify: `app/service.py` (lines 788, 845, 914, 973, 1032, 1094 — all `_log_raw` sites — and delete 1039-1053 method itself)
- Modify: `tests/test_service_parallel_flow.py` (existing tests that assert `_log_raw` was called must be updated; otherwise leave them alone if they don't reference it)

- [ ] **Step 1: Add a service-level test asserting record_llm_stage is invoked**

Create `tests/test_observability_service_integration.py`:

```python
from unittest.mock import MagicMock

from app.observability import context as obs_ctx


def test_choose_categories_records_llm_stage(monkeypatch, tmp_path):
    """Service layer routes LLM logging through the recorder."""
    from app import service as svc

    recorder = MagicMock()
    monkeypatch.setattr(svc, "recorder", recorder, raising=False)

    # Drive _choose_categories by mocking dependencies. Easier path: patch
    # category_caller.call_and_parse and call the helper directly.
    analyzer = MagicMock()
    parsed = {"category_paths": [{"name": "x"}]}
    raw = {"usage": {"total_tokens": 50}, "cost": 0.0001}
    attempts = [type("A", (), {"__dict__": {"model": "m", "attempt": 1, "error_kind": "ok",
                                            "message": "", "latency_ms": 10.0, "status_code": 200}})()]

    analyzer.category_caller = MagicMock()
    analyzer.category_caller.call_and_parse.return_value = (parsed, raw, attempts)
    analyzer.settings = MagicMock(category_model="m", category_fallback_models=[])
    analyzer.category_store = MagicMock()
    analyzer.category_store.get_categories_by_group.return_value = [{"name": "x"}]

    token = obs_ctx.set_request_id("rid-service")
    try:
        svc.MercariAnalyzer._choose_categories(
            analyzer, title="t", description="d", brand_for_prompt="b", group_name="g"
        )
    finally:
        obs_ctx.reset_request_id(token)

    recorder.record_llm_stage.assert_called_once()
    kwargs = recorder.record_llm_stage.call_args.kwargs
    assert kwargs["stage"] == "category"
    assert kwargs["request_id"] == "rid-service"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_observability_service_integration.py -v
```

Expected: FAIL — `service` has no attribute `recorder`.

- [ ] **Step 3: Import recorder + context in service.py and replace `_log_raw` calls**

At the top of `app/service.py`, add:

```python
from .observability import context as obs_ctx
from .observability.recorder import Recorder

recorder: "Recorder | None" = None  # set by main.py at startup


def set_recorder(r: "Recorder") -> None:
    global recorder
    recorder = r
```

In `main.py`, after creating the `recorder` singleton, add:

```python
from app import service as _svc_module
_svc_module.set_recorder(recorder)
```

Now replace each `_log_raw` call site in `app/service.py`:

**At line 788** (inside `_title_image_fallback`-like path), replace the trio with:

```python
        if recorder is not None:
            request_id = obs_ctx.get_request_id() or ""
            recorder.record_llm_stage(
                request_id=request_id,
                stage="title_image_fallback",
                attempts=[a.__dict__ for a in attempts],
                messages=messages,
                raw_response=raw_response,
                parsed=parsed,
            )
```

Then delete the original 3 lines (788, 793-795).

**At line 845-849** (fast_vision), replace with:

```python
        if recorder is not None:
            request_id = obs_ctx.get_request_id() or ""
            recorder.record_llm_stage(
                request_id=request_id,
                stage="fast_vision",
                attempts=[a.__dict__ for a in attempts],
                messages=messages,
                raw_response=raw_response,
                parsed=parsed,
            )
```

Delete the original failure-path `self._log_raw("fast_vision_attempts", ...)` inside the `except` block — replaced by:

```python
        except LLMAllAttemptsFailedError as exc:
            if recorder is not None:
                request_id = obs_ctx.get_request_id() or ""
                recorder.record_llm_stage(
                    request_id=request_id,
                    stage="fast_vision",
                    attempts=[a.__dict__ for a in exc.attempts],
                    messages=messages,
                    raw_response=None,
                    parsed=None,
                )
            raise
```

**At line 914-918** (product_data, parametrized by `stage`), same pattern with `stage=stage`.

**At line 973-983** (product_data_regeneration), same pattern with `stage="product_data_regeneration"`.

**At line 1032-1036** (title_category), same pattern with `stage="title_category"`.

**At line 1094-1098** (category), same pattern with `stage="category"`.

Delete lines 467 (`self._logs_dir = ...`) and 1039-1053 (`def _log_raw(...)`).

- [ ] **Step 4: Run service integration test + full suite**

```bash
pytest tests/test_observability_service_integration.py -v
pytest -x -q
```

Expected: new integration test passes; any prior test that asserted file output of `_log_raw` may fail — update those tests to mock `recorder` instead.

- [ ] **Step 5: Commit**

```bash
git add app/service.py main.py tests/test_observability_service_integration.py
git commit -m "feat(observability): route service LLM calls through recorder"
```

---

## Task 13: Update showcase to reuse contextvar request_id

**Files:**
- Modify: `app/showcase/service.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_observability_service_integration.py`:

```python
def test_showcase_reuses_contextvar_request_id(monkeypatch, tmp_path):
    from app.observability import context as obs_ctx
    from app.showcase import service as sc_module

    client = MagicMock()
    client.generate_image.return_value = type("R", (), {
        "image": type("Img", (), {"mime_type": "image/png", "base64_data": ""})(),
        "upstream_status_code": 200,
        "response_body": {},
        "attempts": 1,
        "attempt_records": [],
        "model": "m",
    })()
    storage = MagicMock()
    storage.save_input_image.return_value = tmp_path / "in.png"
    storage.save_output_image.return_value = tmp_path / "out.png"
    archive = MagicMock()
    service = sc_module.ShowcaseService(
        model="m", storage_manager=storage, archive_writer=archive, client=client
    )

    token = obs_ctx.set_request_id("rid-showcase")
    try:
        resp = service.generate_showcase(
            upload_filename="a.png", content_type="image/png",
            image_bytes=b"x", prompt_hint=None,
        )
    finally:
        obs_ctx.reset_request_id(token)

    assert resp["request_id"] == "rid-showcase"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_observability_service_integration.py::test_showcase_reuses_contextvar_request_id -v
```

Expected: FAIL (showcase generates its own request_id).

- [ ] **Step 3: Modify showcase to read contextvar first**

In `app/showcase/service.py`, replace line 60 (`request_id = f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"`) with:

```python
        from app.observability import context as obs_ctx
        existing = obs_ctx.get_request_id()
        request_id = existing or f"{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
```

After the existing `result = self.client.generate_image(...)` call, add (under both success and failure paths) a call to the recorder. At top of `service.py`, add:

```python
from app.service import recorder as _service_recorder  # set by main.set_recorder
```

In the success path, before the return at the end of `generate_showcase`, insert:

```python
        if _service_recorder is not None:
            try:
                _service_recorder.record_llm_stage(
                    request_id=request_id,
                    stage="showcase_generate",
                    attempts=[a.__dict__ for a in result.attempt_records],
                    messages=[{"role": "user", "content": final_prompt}],
                    raw_response=result.response_body,
                    parsed=None,
                )
            except Exception:
                pass
```

In the failure path (inside the `except Exception as exc:` block), insert before `return response`:

```python
        if _service_recorder is not None:
            try:
                _service_recorder.record_llm_stage(
                    request_id=request_id,
                    stage="showcase_generate",
                    attempts=[],
                    messages=[{"role": "user", "content": final_prompt}],
                    raw_response=None,
                    parsed=None,
                )
            except Exception:
                pass
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_observability_service_integration.py::test_showcase_reuses_contextvar_request_id -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/showcase/service.py tests/test_observability_service_integration.py
git commit -m "feat(observability): showcase reuses contextvar request_id and records LLM stage"
```

---

## Task 14: Query API — list, detail, file download

**Files:**
- Create: `app/observability/api.py`
- Modify: `main.py` (register router, gate with auth)
- Create: `tests/test_observability_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_observability_api.py`:

```python
import base64
import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def set_password(monkeypatch):
    monkeypatch.setenv("LOGS_PASSWORD", "hunter2")
    # force re-import so settings reload
    import importlib, app.config, main
    importlib.reload(app.config)
    importlib.reload(main)
    return main


def _auth():
    creds = base64.b64encode(b"admin:hunter2").decode()
    return {"Authorization": f"Basic {creds}"}


def test_list_requests_requires_auth(set_password):
    with TestClient(set_password.app) as client:
        r = client.get("/api/v1/logs/requests")
    assert r.status_code == 401


def test_list_requests_returns_recent(set_password):
    with TestClient(set_password.app) as client:
        client.get("/health")  # produces nothing logged (health bypass)
        client.get("/api/v1/config", headers=_auth())  # produces a log row
        r = client.get("/api/v1/logs/requests?limit=5", headers=_auth())
    assert r.status_code == 200
    body = r.json()
    assert "items" in body
    assert any(item["endpoint"] == "/api/v1/config" for item in body["items"])


def test_request_detail_returns_llm_calls(set_password):
    with TestClient(set_password.app) as client:
        client.get("/api/v1/config", headers=_auth())
        items = client.get("/api/v1/logs/requests", headers=_auth()).json()["items"]
        rid = items[0]["request_id"]
        r = client.get(f"/api/v1/logs/requests/{rid}", headers=_auth())
    assert r.status_code == 200
    assert r.json()["request"]["request_id"] == rid
    assert isinstance(r.json()["llm_calls"], list)


def test_file_download_path_traversal_blocked(set_password):
    with TestClient(set_password.app) as client:
        client.get("/api/v1/config", headers=_auth())
        items = client.get("/api/v1/logs/requests", headers=_auth()).json()["items"]
        rid = items[0]["request_id"]
        r = client.get(f"/api/v1/logs/requests/{rid}/files/..%2F..%2Fetc%2Fpasswd",
                       headers=_auth())
    assert r.status_code in (400, 403, 404)


def test_stats_endpoint(set_password):
    with TestClient(set_password.app) as client:
        client.get("/api/v1/config", headers=_auth())
        r = client.get("/api/v1/logs/stats", headers=_auth())
    assert r.status_code == 200
    assert "total" in r.json()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_observability_api.py -v
```

Expected: FAIL — `/api/v1/logs/*` routes don't exist.

- [ ] **Step 3: Implement query API**

Create `app/observability/api.py`:

```python
from __future__ import annotations

import base64
import json
import mimetypes
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

from .paths import resolve_artifact
from .recorder import Recorder
from .store import Store


def build_router(*, store: Store, store_root: Path, auth_dep) -> APIRouter:
    router = APIRouter(prefix="/api/v1/logs", dependencies=[Depends(auth_dep)])

    @router.get("/requests")
    def list_requests(
        from_: Optional[str] = Query(default=None, alias="from"),
        to: Optional[str] = Query(default=None),
        endpoint: Optional[str] = None,
        status: Optional[int] = None,
        error_kind: Optional[str] = None,
        min_duration_ms: Optional[int] = None,
        job_id: Optional[str] = None,
        q: Optional[str] = None,
        include_llm_text: bool = False,
        cursor: Optional[str] = None,
        limit: int = 50,
    ):
        limit = max(1, min(int(limit), 200))
        where = []
        params: list = []
        if from_:
            where.append("timestamp_utc >= ?")
            params.append(from_)
        if to:
            where.append("timestamp_utc <= ?")
            params.append(to)
        if endpoint:
            where.append("endpoint = ?")
            params.append(endpoint)
        if status is not None:
            where.append("status_code = ?")
            params.append(status)
        if error_kind:
            where.append("error_kind = ?")
            params.append(error_kind)
        if min_duration_ms is not None:
            where.append("duration_ms >= ?")
            params.append(min_duration_ms)
        if job_id:
            where.append("job_id = ?")
            params.append(job_id)

        matched_ids: Optional[set] = None
        if q:
            matched_ids = set()
            with store.connect() as conn:
                for row in conn.execute(
                    "SELECT request_id FROM requests_fts WHERE requests_fts MATCH ?", (q,)
                ):
                    matched_ids.add(row["request_id"])
                if include_llm_text:
                    for row in conn.execute(
                        "SELECT request_id FROM llm_fts WHERE llm_fts MATCH ?", (q,)
                    ):
                        matched_ids.add(row["request_id"])
            if not matched_ids:
                return {"items": [], "next_cursor": None}
            placeholders = ",".join("?" * len(matched_ids))
            where.append(f"request_id IN ({placeholders})")
            params.extend(matched_ids)

        if cursor:
            try:
                ts, rid = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
            except Exception:
                raise HTTPException(400, "bad cursor")
            where.append("(timestamp_utc, request_id) < (?, ?)")
            params.extend([ts, rid])

        sql = "SELECT * FROM requests"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY timestamp_utc DESC, request_id DESC LIMIT ?"
        params.append(limit + 1)

        with store.connect() as conn:
            rows = list(conn.execute(sql, params))

        next_cursor = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = base64.urlsafe_b64encode(
                f"{last['timestamp_utc']}|{last['request_id']}".encode()
            ).decode()
            rows = rows[:limit]

        return {"items": [dict(r) for r in rows], "next_cursor": next_cursor}

    @router.get("/requests/{request_id}")
    def request_detail(request_id: str):
        with store.connect() as conn:
            req = conn.execute("SELECT * FROM requests WHERE request_id=?", (request_id,)).fetchone()
            if req is None:
                raise HTTPException(404, "request not found")
            calls = list(conn.execute(
                "SELECT * FROM llm_calls WHERE request_id=? ORDER BY timestamp_utc, id", (request_id,)
            ))
            job_id = req["job_id"]
            siblings = []
            if job_id:
                siblings = list(conn.execute(
                    "SELECT request_id, timestamp_utc, endpoint, method, status_code, duration_ms "
                    "FROM requests WHERE job_id=? AND request_id != ? "
                    "ORDER BY timestamp_utc", (job_id, request_id)
                ))
        return {
            "request": dict(req),
            "llm_calls": [dict(c) for c in calls],
            "job_siblings": [dict(s) for s in siblings],
        }

    @router.get("/requests/{request_id}/files/{filename}")
    def download_file(request_id: str, filename: str):
        with store.connect() as conn:
            row = conn.execute(
                "SELECT timestamp_utc FROM requests WHERE request_id=?", (request_id,)
            ).fetchone()
        if row is None:
            raise HTTPException(404, "request not found")
        date_str = row["timestamp_utc"][:10]
        try:
            path = resolve_artifact(store_root, date_str, request_id, filename)
        except ValueError:
            raise HTTPException(400, "invalid filename")
        if not path.exists() or not path.is_file():
            raise HTTPException(404, "file not found")
        ct, _ = mimetypes.guess_type(path.name)
        return FileResponse(path, media_type=ct or "application/octet-stream")

    @router.get("/stats")
    def stats(
        from_: Optional[str] = Query(default=None, alias="from"),
        to: Optional[str] = Query(default=None),
    ):
        where = []
        params: list = []
        if from_:
            where.append("timestamp_utc >= ?")
            params.append(from_)
        if to:
            where.append("timestamp_utc <= ?")
            params.append(to)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        with store.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) AS n FROM requests{clause}", params).fetchone()["n"]
            by_status = {r["status_code"]: r["n"] for r in conn.execute(
                f"SELECT status_code, COUNT(*) AS n FROM requests{clause} GROUP BY status_code", params)}
            by_endpoint = {r["endpoint"]: r["n"] for r in conn.execute(
                f"SELECT endpoint, COUNT(*) AS n FROM requests{clause} GROUP BY endpoint", params)}
            by_error_kind = {r["error_kind"]: r["n"] for r in conn.execute(
                f"SELECT error_kind, COUNT(*) AS n FROM requests{clause} GROUP BY error_kind", params)}
            sums = conn.execute(
                f"SELECT COALESCE(SUM(total_tokens),0) AS t, COALESCE(SUM(total_cost_usd),0) AS c FROM requests{clause}",
                params).fetchone()
        return {
            "total": total,
            "by_status": by_status,
            "by_endpoint": by_endpoint,
            "by_error_kind": by_error_kind,
            "sum_tokens": sums["t"],
            "sum_cost_usd": sums["c"],
        }

    @router.post("/prune")
    def manual_prune():
        from .retention import prune as _prune
        from app.config import load_settings
        s = load_settings()
        stats = _prune(store, store_root, s.log_retention_days, s.log_max_total_bytes)
        return {"rows_deleted": stats.rows_deleted, "bytes_freed": stats.bytes_freed}

    return router
```

In `main.py`, after the recorder singleton is created, add:

```python
from app.observability.api import build_router as build_obs_router

app.include_router(build_obs_router(
    store=_obs_store,
    store_root=BASE_DIR / "logs" / "store",
    auth_dep=require_logs_auth(settings.logs_password),
))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_observability_api.py -v
```

Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/observability/api.py main.py tests/test_observability_api.py
git commit -m "feat(observability): query API with auth, list/detail/file/stats/prune"
```

---

## Task 15: Web log viewer page

**Files:**
- Create: `web/logs.html`
- Modify: `main.py` (add `/logs` route)

- [ ] **Step 1: Add the route + a smoke test**

In `main.py`, near other static page handlers (the existing `/config` GET, around line 412), add:

```python
from fastapi.responses import FileResponse


@app.get("/logs", response_class=HTMLResponse,
        dependencies=[Depends(require_logs_auth(settings.logs_password))])
def logs_page():
    return FileResponse(BASE_DIR / "web" / "logs.html")
```

Append to `tests/test_observability_api.py`:

```python
def test_logs_page_requires_auth(set_password):
    with TestClient(set_password.app) as client:
        r = client.get("/logs")
    assert r.status_code == 401

    r = client.get("/logs", headers=_auth())
    assert r.status_code == 200
    assert "<html" in r.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_observability_api.py::test_logs_page_requires_auth -v
```

Expected: FAIL — `web/logs.html` doesn't exist.

- [ ] **Step 3: Implement the viewer page**

Create `web/logs.html`:

```html
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>日志查看 - mercari-image-recognize</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 0; padding: 16px; background: #f6f7f9; color: #1f2328; }
  h1 { font-size: 18px; margin: 0 0 12px; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; padding: 10px 12px; background: #fff; border: 1px solid #d0d7de; border-radius: 6px; margin-bottom: 12px; }
  .toolbar label { font-size: 13px; }
  .toolbar input, .toolbar select { padding: 4px 6px; border: 1px solid #d0d7de; border-radius: 4px; font-size: 13px; }
  table { width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d0d7de; border-radius: 6px; overflow: hidden; }
  th, td { padding: 8px 10px; text-align: left; font-size: 13px; border-bottom: 1px solid #eaeef2; }
  tr.row { cursor: pointer; }
  tr.row:hover { background: #f6f8fa; }
  .status-ok { color: #1a7f37; }
  .status-bad { color: #cf222e; }
  .details { background: #fafbfc; padding: 12px 24px; }
  .details h3 { font-size: 14px; margin: 8px 0 4px; }
  .llm-table th, .llm-table td { font-size: 12px; padding: 4px 8px; }
  pre.json { background: #f6f8fa; padding: 8px; max-height: 360px; overflow: auto; border-radius: 4px; font-size: 12px; }
  button { font-size: 12px; padding: 2px 8px; cursor: pointer; }
  .stats { color: #57606a; font-size: 12px; margin-left: auto; }
</style>
</head>
<body>
<h1>日志查看</h1>
<div class="toolbar">
  <label>时间: <select id="time-range">
    <option value="today">今天</option>
    <option value="24h">24小时</option>
    <option value="7d" selected>7天</option>
    <option value="">全部</option>
  </select></label>
  <label>端点:
    <select id="endpoint">
      <option value="">全部</option>
      <option>/api/v1/mercari/image/analyze</option>
      <option>/api/v1/mercari/title/analyze</option>
      <option>/api/v1/mercari/product-data/regenerate</option>
      <option>/api/v1/showcase/generate</option>
    </select>
  </label>
  <label>状态:
    <select id="error-kind">
      <option value="">全部</option>
      <option value="ok">成功</option>
      <option value="http_4xx">4xx</option>
      <option value="http_5xx">5xx</option>
      <option value="llm_failed">LLM失败</option>
      <option value="exception">异常</option>
    </select>
  </label>
  <label>慢请求(ms): <input id="min-duration" type="number" placeholder="0" style="width:80px"></label>
  <label>搜索: <input id="q" type="text" placeholder="error / brand / request_id" style="width:240px"></label>
  <label><input id="include-llm" type="checkbox"> 含 prompt/response</label>
  <button id="refresh">刷新</button>
  <span class="stats" id="stats"></span>
</div>
<table>
  <thead>
    <tr><th>时间(UTC)</th><th>端点</th><th>状态</th><th>耗时</th><th>token</th><th>$</th><th>错误</th><th>request_id</th></tr>
  </thead>
  <tbody id="rows"></tbody>
</table>
<div style="text-align:center; padding:12px;"><button id="load-more">加载更多</button></div>

<script>
let nextCursor = null;
function timeFilter() {
  const sel = document.getElementById('time-range').value;
  if (!sel) return {};
  const now = new Date();
  const start = new Date(now);
  if (sel === 'today') start.setUTCHours(0,0,0,0);
  else if (sel === '24h') start.setTime(now.getTime() - 24*3600*1000);
  else if (sel === '7d') start.setTime(now.getTime() - 7*24*3600*1000);
  return {from: start.toISOString()};
}
function qs(params) {
  return Object.entries(params).filter(([,v])=>v!=='' && v!=null).map(([k,v])=>k+'='+encodeURIComponent(v)).join('&');
}
async function fetchList(reset) {
  if (reset) { nextCursor = null; document.getElementById('rows').innerHTML = ''; }
  const params = Object.assign(
    {limit: 50},
    timeFilter(),
    {endpoint: document.getElementById('endpoint').value,
     error_kind: document.getElementById('error-kind').value,
     min_duration_ms: document.getElementById('min-duration').value,
     q: document.getElementById('q').value,
     include_llm_text: document.getElementById('include-llm').checked,
     cursor: nextCursor || ''}
  );
  const r = await fetch('/api/v1/logs/requests?' + qs(params), {credentials:'include'});
  if (!r.ok) { alert('请求失败: ' + r.status); return; }
  const body = await r.json();
  body.items.forEach(item => appendRow(item));
  nextCursor = body.next_cursor;
  document.getElementById('load-more').style.display = nextCursor ? 'inline' : 'none';
  refreshStats();
}
async function refreshStats() {
  const params = Object.assign({}, timeFilter());
  const r = await fetch('/api/v1/logs/stats?' + qs(params), {credentials:'include'});
  if (!r.ok) return;
  const s = await r.json();
  document.getElementById('stats').textContent =
    `总数 ${s.total} · 失败 ${(s.by_error_kind.http_4xx||0)+(s.by_error_kind.http_5xx||0)+(s.by_error_kind.llm_failed||0)+(s.by_error_kind.exception||0)} · tokens ${s.sum_tokens} · $${(s.sum_cost_usd||0).toFixed(4)}`;
}
function appendRow(item) {
  const tr = document.createElement('tr');
  tr.className = 'row';
  const statusClass = item.error_kind === 'ok' ? 'status-ok' : 'status-bad';
  tr.innerHTML = `
    <td>${item.timestamp_utc.replace('T',' ').slice(0,19)}</td>
    <td>${item.endpoint}</td>
    <td class="${statusClass}">${item.status_code||''}</td>
    <td>${(item.duration_ms||0).toFixed(0)}ms</td>
    <td>${item.total_tokens||''}</td>
    <td>${(item.total_cost_usd||0).toFixed(4)}</td>
    <td class="${statusClass}">${item.error_kind!=='ok'?item.error_kind:''}</td>
    <td><code style="font-size:11px">${item.request_id.slice(0,8)}…</code></td>`;
  tr.addEventListener('click', () => toggleDetails(tr, item.request_id));
  document.getElementById('rows').appendChild(tr);
}
async function toggleDetails(row, request_id) {
  const next = row.nextElementSibling;
  if (next && next.classList.contains('details-row')) {
    next.remove();
    return;
  }
  const r = await fetch('/api/v1/logs/requests/' + request_id, {credentials:'include'});
  if (!r.ok) return;
  const data = await r.json();
  const dr = document.createElement('tr');
  dr.className = 'details-row';
  const td = document.createElement('td');
  td.colSpan = 8;
  td.className = 'details';
  td.innerHTML = renderDetails(data);
  dr.appendChild(td);
  row.parentNode.insertBefore(dr, row.nextSibling);
}
function renderDetails(data) {
  const rid = data.request.request_id;
  const links = `
    <button onclick="openFile('${rid}','request.json')">request.json</button>
    <button onclick="openFile('${rid}','response.json')">response.json</button>
    <button onclick="copyText('${rid}')">复制 request_id</button>
  `;
  let html = `<h3>${data.request.endpoint} · ${rid}</h3>${links}`;
  if (data.job_siblings && data.job_siblings.length) {
    html += `<h3>同 job 兄弟请求 (${data.job_siblings.length})</h3>`;
    html += '<ul>' + data.job_siblings.map(s =>
      `<li>${s.timestamp_utc.slice(11,19)} · ${s.method} ${s.endpoint} · ${s.status_code}</li>`).join('') + '</ul>';
  }
  if (data.llm_calls && data.llm_calls.length) {
    html += '<h3>LLM 调用</h3><table class="llm-table"><tr><th>时间</th><th>stage</th><th>attempt</th><th>model</th><th>状态</th><th>耗时</th><th>token</th><th>文件</th></tr>';
    for (const c of data.llm_calls) {
      const files = ['prompt_file','response_file','parsed_file']
        .filter(k => c[k]).map(k => {
          const name = c[k].split('/').pop();
          return `<button onclick="openFile('${rid}','${name}')">${k.replace('_file','')}</button>`;
        }).join(' ');
      html += `<tr><td>${c.timestamp_utc.slice(11,19)}</td><td>${c.stage}</td><td>${c.attempt}</td><td>${c.model}</td><td>${c.status}</td><td>${(c.latency_ms||0).toFixed(0)}ms</td><td>${c.total_tokens||''}</td><td>${files}</td></tr>`;
    }
    html += '</table>';
  }
  return html;
}
async function openFile(rid, name) {
  const r = await fetch(`/api/v1/logs/requests/${rid}/files/${name}`, {credentials:'include'});
  if (!r.ok) { alert('文件读取失败: '+r.status); return; }
  const text = await r.text();
  const w = window.open('', '_blank');
  w.document.write('<pre style="font:12px ui-monospace,monospace; white-space:pre-wrap">'+ text.replace(/</g,'&lt;') +'</pre>');
}
function copyText(t) { navigator.clipboard.writeText(t); }
document.getElementById('refresh').addEventListener('click', () => fetchList(true));
document.getElementById('load-more').addEventListener('click', () => fetchList(false));
document.querySelectorAll('#time-range,#endpoint,#error-kind').forEach(el =>
  el.addEventListener('change', () => fetchList(true)));
document.getElementById('q').addEventListener('keydown', e => { if (e.key === 'Enter') fetchList(true); });
fetchList(true);
</script>
</body>
</html>
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_observability_api.py::test_logs_page_requires_auth -v
```

Expected: PASS.

- [ ] **Step 5: Manual browser check**

```bash
LOGS_PASSWORD=hunter2 ./run.sh &
SERVER_PID=$!
sleep 2
# fire a request to populate logs
curl -s -X POST http://localhost:8000/api/v1/mercari/title/analyze -F 'title=Nike shirt' -F 'language=en' > /dev/null
# open in browser, log in with admin/hunter2
open http://localhost:8000/logs
# manually verify: 1 row visible, click row expands, request.json button works
# When done:
kill $SERVER_PID
```

Confirm in the browser: row visible, expand shows LLM stages, file buttons render JSON content.

- [ ] **Step 6: Commit**

```bash
git add web/logs.html main.py tests/test_observability_api.py
git commit -m "feat(observability): web log viewer page with timeline, filters, full-text search"
```

---

## Task 16: Apply auth to `/config` page and `PUT /api/v1/config`

**Files:**
- Modify: `main.py` (find the `/config` GET handler ~ line 412 and the `PUT /api/v1/config` handler ~ line 425)

- [ ] **Step 1: Add a failing test**

Append to `tests/test_observability_api.py`:

```python
def test_config_page_requires_auth(set_password):
    with TestClient(set_password.app) as client:
        r = client.get("/config")
    assert r.status_code == 401
    r = client.get("/config", headers=_auth())
    assert r.status_code == 200


def test_put_config_requires_auth(set_password):
    with TestClient(set_password.app) as client:
        r = client.put("/api/v1/config", json={})
    assert r.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_observability_api.py::test_config_page_requires_auth tests/test_observability_api.py::test_put_config_requires_auth -v
```

Expected: FAIL.

- [ ] **Step 3: Add auth dep to both routes**

Locate the existing `@app.get("/config", ...)` and `@app.put("/api/v1/config", ...)` decorators in `main.py`. Add `dependencies=[Depends(require_logs_auth(settings.logs_password))]` to each, matching the syntax used in Task 15. Also add `from fastapi import Depends` if not already imported.

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_observability_api.py::test_config_page_requires_auth tests/test_observability_api.py::test_put_config_requires_auth -v
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add main.py tests/test_observability_api.py
git commit -m "feat(observability): gate /config and PUT /api/v1/config behind logs auth"
```

---

## Task 17: Periodic prune on startup

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Add periodic prune via asyncio task**

In `main.py`, near the bottom (just before `if __name__ == "__main__":` or equivalent — if absent, near the end of the module):

```python
import asyncio
from contextlib import suppress


@app.on_event("startup")
async def _start_prune_loop():
    async def loop():
        while True:
            try:
                obs_prune(_obs_store, BASE_DIR / "logs" / "store",
                          settings.log_retention_days, settings.log_max_total_bytes)
            except Exception:
                pass
            await asyncio.sleep(settings.log_prune_interval_minutes * 60)

    app.state.prune_task = asyncio.create_task(loop())


@app.on_event("shutdown")
async def _stop_prune_loop():
    task = getattr(app.state, "prune_task", None)
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
```

- [ ] **Step 2: Smoke-check startup doesn't crash**

```bash
LOGS_PASSWORD=hunter2 timeout 5 ./run.sh
echo "exit: $?"
```

Expected: server starts, prune task scheduled, no exceptions.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat(observability): start periodic prune loop on app startup"
```

---

## Task 18: Delete old logging code

**Files:**
- Delete: `app/request_logging.py`
- Modify: `main.py` (remove unused import)
- Possibly delete: any old `tests/test_request_logging*.py`

- [ ] **Step 1: Verify nothing still uses the old module**

```bash
grep -rn "request_logging\|_log_raw\|LOG_LLM_RAW" app/ main.py tests/ 2>/dev/null
```

Expected: no matches. If matches found, fix them first (rerun Task 12 if `_log_raw` is still present).

- [ ] **Step 2: Delete the file and update tests**

```bash
rm app/request_logging.py
# if any test file exists that tests only the old module:
ls tests/ | grep -i "request_logging" | xargs -I{} rm tests/{}
```

If `tests/test_image_analyze_jobs.py` or others reference the old `logs/requests/` path, update them to use the new viewer API or remove the assertion.

- [ ] **Step 3: Run the full suite**

```bash
pytest -x -q
```

Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "chore(observability): delete legacy request_logging module"
```

---

## Task 19: Wipe old log files script

**Files:**
- Create: `scripts/wipe_old_logs.py`

- [ ] **Step 1: Write the script**

Create `scripts/wipe_old_logs.py`:

```python
#!/usr/bin/env python3
"""Single-cutover cleanup: delete legacy logs/*.log and logs/requests/.

Run once after deploying the observability revamp. Safe to run multiple
times — it only removes files matching the legacy naming patterns and
the legacy directory.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


_LEGACY_PREFIXES = (
    "category_attempts_",
    "category_parsed_",
    "category_raw_response_",
    "vision_attempts_",
    "vision_parsed_",
    "vision_raw_",
    "fast_vision_",
    "requests_",
    "title_category_",
    "title_image_fallback_",
    "product_data_",
    "product_data_fallback_",
    "product_data_regeneration_",
    "done.json_",
    "done2.json_",
    "initial2.json_",
    "showcase_",
)


def main(logs_dir: Path) -> int:
    if not logs_dir.exists():
        print(f"No logs dir at {logs_dir}; nothing to do.")
        return 0
    removed = 0
    for entry in logs_dir.iterdir():
        if entry.is_file() and entry.name.endswith(".log") and entry.name.startswith(_LEGACY_PREFIXES):
            entry.unlink()
            removed += 1
    legacy_requests = logs_dir / "requests"
    if legacy_requests.exists() and legacy_requests.is_dir():
        shutil.rmtree(legacy_requests)
        print(f"Removed legacy directory: {legacy_requests}")
    print(f"Removed {removed} legacy .log files.")
    return 0


if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent / "logs"
    sys.exit(main(base))
```

- [ ] **Step 2: Verify it dry-runs cleanly**

```bash
python scripts/wipe_old_logs.py
```

Expected: prints removal count; the new `logs/observability.db` and `logs/store/` are untouched.

- [ ] **Step 3: Commit**

```bash
git add scripts/wipe_old_logs.py
git commit -m "chore(observability): add legacy-log cleanup script for cutover"
```

---

## Task 20: End-to-end verification

**Files:**
- Create: `tests/test_observability_e2e.py`

- [ ] **Step 1: Write the end-to-end test**

Create `tests/test_observability_e2e.py`:

```python
import base64
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import main


def test_analyze_creates_request_with_llm_calls(monkeypatch):
    monkeypatch.setenv("LOGS_PASSWORD", "h")
    import importlib, app.config
    importlib.reload(app.config)
    importlib.reload(main)

    # mock the analyzer to avoid real OpenRouter
    classification = {"title": "x", "categories": [], "category_paths": [], "prices": []}
    main.analyzer.classify_first_image_categories = MagicMock(return_value=classification)
    main.analyzer.generate_product_data = MagicMock(return_value={"product_data": {}})

    creds = base64.b64encode(b"a:h").decode()
    headers = {"Authorization": f"Basic {creds}"}

    with TestClient(main.app) as client:
        # one tiny fake PNG
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        files = [("image_list", ("a.png", png, "image/png"))]
        r = client.post("/api/v1/mercari/image/analyze",
                        data={"language": "ja", "debug": "false"},
                        files=files)
        assert r.status_code == 200
        rid = r.headers["x-request-id"]

        # list contains it
        listing = client.get("/api/v1/logs/requests", headers=headers).json()
        assert any(item["request_id"] == rid for item in listing["items"])

        # detail returns the row
        detail = client.get(f"/api/v1/logs/requests/{rid}", headers=headers).json()
        assert detail["request"]["request_id"] == rid

        # request.json file exists
        r2 = client.get(f"/api/v1/logs/requests/{rid}/files/request.json", headers=headers)
        assert r2.status_code == 200
        assert "endpoint" in r2.text
```

- [ ] **Step 2: Run E2E test**

```bash
pytest tests/test_observability_e2e.py -v
```

Expected: PASS.

- [ ] **Step 3: Run the entire suite**

```bash
pytest -q
```

Expected: all green.

- [ ] **Step 4: Manual UI smoke**

```bash
LOGS_PASSWORD=hunter2 ./run.sh &
SERVER_PID=$!
sleep 2
# real analyze (will fail without OPENROUTER_API_KEY but still log)
curl -s -X POST http://localhost:8000/api/v1/mercari/title/analyze \
  -F 'title=Nike shirt' -F 'language=en' > /dev/null
open http://localhost:8000/logs
echo "log in as admin / hunter2"
# Verify:
#   - timeline shows the title/analyze row
#   - click expands; LLM stages visible (or empty if no API key)
#   - X-Request-Id header was returned (curl -i to check)
#   - /config still works behind the same auth
kill $SERVER_PID
```

- [ ] **Step 5: Commit + update README**

Edit `README.md` to add:

```markdown
## Observability

The service writes structured logs to SQLite (`logs/observability.db`) and per-request files in `logs/store/<date>/<request_id>/`. View them at `http://<host>:8000/logs`.

Set `LOGS_PASSWORD` to enable the viewer (the `/config` page uses the same credential):

```sh
export LOGS_PASSWORD=hunter2
./run.sh
```

Retention is age + total-size double bottom: `LOG_RETENTION_DAYS` (default 7), `LOG_MAX_TOTAL_BYTES` (default 5 GiB). The prune task runs every `LOG_PRUNE_INTERVAL_MINUTES` (default 60).

Every HTTP response carries `X-Request-Id` — paste it in the viewer search box for instant lookup.
```

```bash
git add tests/test_observability_e2e.py README.md
git commit -m "test(observability): end-to-end coverage; update README"
```

---

## Self-Review Notes

**Spec coverage:**
- §1–3 (intro/goals/decisions) → no code, captured in plan header.
- §4 (schema) → Task 1 (DDL) + Task 2 (DML).
- §5 (file layout) → Tasks 4, 5, 6.
- §6 (write paths) → Tasks 10, 11, 12, 13.
- §7 (API + UI + auth) → Tasks 9, 14, 15, 16.
- §8 (error handling + dead letter) → Tasks 5, 6.
- §9 (perf) → covered by design choices (synchronous WAL writes, body cap in middleware).
- §10 (retention) → Task 7, scheduled by Task 17.
- §11 (config) → Task 8.
- §12 (testing) → tests in every task; aggregated E2E in Task 20.
- §13 (migration) → Tasks 18, 19.

**Placeholder scan:** no TBD/TODO/"implement later" in any task. All code shown verbatim.

**Type consistency:**
- `Recorder.start_request` uses keyword-only args throughout (Tasks 5, 10).
- `AttemptRecord.__dict__` shape (`model`, `attempt`, `error_kind`, `message`, `latency_ms`, `status_code`) matches what `service.py` passes (Task 12) and what the recorder reads (Task 6).
- `prompt_file`, `response_file`, `parsed_file` all stored as paths relative to `store_root` (Task 6); the API reconstructs them via `resolve_artifact(store_root, date_str, request_id, filename)` (Task 14) — date_str comes from `requests.timestamp_utc[:10]`, which matches `_date_str_from_iso` in the recorder.
- `error_kind` enum values (`ok`, `http_4xx`, `http_5xx`, `llm_failed`, `exception`) consistent between recorder (Task 5), test (Task 5), API filter (Task 14), and UI dropdown (Task 15).
