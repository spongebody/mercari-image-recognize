from __future__ import annotations

import base64
import mimetypes
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from .paths import resolve_artifact
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

        # SQLite's compiled parameter limit (default 999, max 32766) caps how
        # many ids we can put in the IN-clause. For broad FTS matches we keep
        # only the most recent N ids before expansion.
        matched_ids: Optional[list] = None
        if q:
            try:
                ids: list = []
                seen: set = set()
                _FTS_MAX_IDS = 500
                with store.connect() as conn:
                    fts_sql = (
                        "SELECT r.request_id, r.timestamp_utc FROM requests r "
                        "JOIN requests_fts f ON r.request_id = f.request_id "
                        "WHERE requests_fts MATCH ? "
                        "ORDER BY r.timestamp_utc DESC LIMIT ?"
                    )
                    for row in conn.execute(fts_sql, (q, _FTS_MAX_IDS)):
                        if row["request_id"] not in seen:
                            seen.add(row["request_id"])
                            ids.append(row["request_id"])
                    if include_llm_text and len(ids) < _FTS_MAX_IDS:
                        remaining = _FTS_MAX_IDS - len(ids)
                        llm_sql = (
                            "SELECT r.request_id FROM requests r "
                            "JOIN llm_fts l ON r.request_id = l.request_id "
                            "WHERE llm_fts MATCH ? "
                            "ORDER BY r.timestamp_utc DESC LIMIT ?"
                        )
                        for row in conn.execute(llm_sql, (q, remaining)):
                            if row["request_id"] not in seen:
                                seen.add(row["request_id"])
                                ids.append(row["request_id"])
            except sqlite3.OperationalError as exc:
                raise HTTPException(status_code=400, detail=f"invalid search query: {exc}")
            matched_ids = ids
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
