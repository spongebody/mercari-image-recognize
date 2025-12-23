import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

from fastapi import Request
from starlette.datastructures import FormData, UploadFile


_REQUEST_LOG_DIR = Path(__file__).resolve().parent.parent / "logs" / "requests"
_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


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


def _file_size_bytes(upload: UploadFile) -> Optional[int]:
    file_obj = getattr(upload, "file", None)
    if not file_obj:
        return None
    try:
        current = file_obj.tell()
        file_obj.seek(0, 2)
        size = file_obj.tell()
        file_obj.seek(current)
        return int(size)
    except Exception:
        return None


def _serialize_form(form: FormData) -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    for key, value in form.multi_items():
        if isinstance(value, UploadFile):
            file_info = {
                "filename": value.filename or "",
                "content_type": value.content_type or "",
                "size_bytes": _file_size_bytes(value),
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
        "timestamp_utc": datetime.utcnow().strftime(_TIMESTAMP_FORMAT),
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
            "content_length": request.headers.get("content-length", ""),
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

    if body is not None:
        entry["body_size_bytes"] = len(body)

    if body_payload:
        entry["body"] = body_payload

    return entry


def _unique_log_path(directory: Path, basename: str) -> Path:
    candidate = directory / f"{basename}.json"
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = directory / f"{basename}_{counter}.json"
        if not candidate.exists():
            return candidate
        counter += 1


def _prune_request_logs(directory: Path, retention_days: int, max_files: int) -> None:
    if retention_days <= 0 and max_files <= 0:
        return

    files = [item for item in directory.iterdir() if item.is_file() and item.suffix == ".json"]
    if not files:
        return

    now = datetime.utcnow()
    if retention_days > 0:
        cutoff = now - timedelta(days=retention_days)
        for item in files:
            try:
                if datetime.utcfromtimestamp(item.stat().st_mtime) < cutoff:
                    item.unlink()
            except Exception:
                continue

    if max_files > 0:
        remaining = [item for item in directory.iterdir() if item.is_file() and item.suffix == ".json"]
        if len(remaining) <= max_files:
            return
        remaining.sort(key=lambda p: p.stat().st_mtime)
        to_remove = remaining[: max(0, len(remaining) - max_files)]
        for item in to_remove:
            try:
                item.unlink()
            except Exception:
                continue


def write_request_log(
    entry: Dict[str, Any],
    status_code: int,
    duration_ms: float,
    error: str = "",
    retention_days: int = 7,
    max_files: int = 1000,
) -> None:
    try:
        _REQUEST_LOG_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.utcnow().strftime(_TIMESTAMP_FORMAT)
        path_token = _sanitize_path(entry.get("path", "request"))
        method = entry.get("method", "UNKNOWN")
        log_entry = dict(entry)
        log_entry["response"] = {
            "status_code": status_code,
            "duration_ms": round(duration_ms, 2),
        }
        if error:
            log_entry["response"]["error"] = error
        basename = f"{timestamp}_{method}_{path_token}"
        path = _unique_log_path(_REQUEST_LOG_DIR, basename)
        with path.open("w", encoding="utf-8") as f:
            json.dump(log_entry, f, ensure_ascii=False, indent=2)
        _prune_request_logs(_REQUEST_LOG_DIR, retention_days, max_files)
    except Exception:
        return
