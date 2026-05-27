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
        status_code: Optional[int],
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
            # Best-effort cap. The count→write window is racy under concurrent
            # writers, so the directory may briefly exceed _MAX_DEAD_LETTERS; the
            # goal is bounded disk usage during a logging outage, not exact limit.
            existing = list(d.iterdir())
            if len(existing) >= _MAX_DEAD_LETTERS:
                return
            ts = int(time.time() * 1000)
            (d / f"{ts}_{kind}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
        except Exception:
            pass

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
