import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from fastapi import Request
from starlette.datastructures import FormData, UploadFile


_REQUEST_LOG_DIR = Path(__file__).resolve().parent.parent / "logs" / "requests"


def _sanitize_path(path: str) -> str:
    slug = "".join(ch if ch.isalnum() else "_" for ch in path.strip("/"))
    return slug or "root"


def _append_multi(target: Dict[str, Any], key: str, value: Any) -> None:
    if key not in target:
        target[key] = value
        return
    existing = target[key]
    if isinstance(existing, list):
        existing.append(value)
    else:
        target[key] = [existing, value]


def _serialize_items(items: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key, value in items:
        _append_multi(payload, key, value)
    return payload


def _serialize_form(form: FormData) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key, value in form.multi_items():
        if isinstance(value, UploadFile):
            file_info = {
                "filename": value.filename or "",
                "content_type": value.content_type or "",
            }
            _append_multi(payload, key, file_info)
        else:
            _append_multi(payload, key, value)
    return payload


async def _parse_form_from_body(request: Request, body: bytes) -> Dict[str, Any]:
    async def receive() -> Dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    temp_request = Request(request.scope, receive)
    form = await temp_request.form()
    return _serialize_form(form)


async def build_request_log(request: Request, body: Optional[bytes] = None) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "timestamp_utc": datetime.utcnow().isoformat() + "Z",
        "method": request.method,
        "path": request.url.path,
        "query": _serialize_items(request.query_params.multi_items()),
        "client": {
            "host": request.client.host if request.client else "",
            "port": request.client.port if request.client else None,
            "forwarded_for": request.headers.get("x-forwarded-for", ""),
        },
        "headers": {
            "content_type": request.headers.get("content-type", ""),
            "user_agent": request.headers.get("user-agent", ""),
        },
    }

    content_type = entry["headers"]["content_type"]
    body_payload: Optional[Dict[str, Any]] = None
    if request.method in {"POST", "PUT", "PATCH"}:
        try:
            if "application/json" in content_type:
                raw = body or b""
                if raw:
                    try:
                        body_payload = {"json": json.loads(raw)}
                    except json.JSONDecodeError:
                        body_payload = {"raw": raw.decode("utf-8", errors="replace")}
            elif "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
                raw = body or b""
                if raw:
                    body_payload = {"form": await _parse_form_from_body(request, raw)}
        except Exception as exc:
            body_payload = {"parse_error": str(exc)}

    if body_payload:
        entry["body"] = body_payload

    return entry


def write_request_log(entry: Dict[str, Any], status_code: int, duration_ms: float, error: str = "") -> None:
    try:
        _REQUEST_LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        path_token = _sanitize_path(entry.get("path", "request"))
        method = entry.get("method", "UNKNOWN")
        log_entry = dict(entry)
        log_entry["response"] = {
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
        }
        if error:
            log_entry["response"]["error"] = error
        path = _REQUEST_LOG_DIR / f"{timestamp}_{method}_{path_token}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(log_entry, f, ensure_ascii=False, indent=2)
    except Exception:
        return
