from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def artifact_dir(store_root: Path, date_str: str, request_id: str) -> Path:
    return Path(store_root) / date_str / request_id


def resolve_artifact(store_root: Path, date_str: str, request_id: str, filename: str) -> Path:
    if not _DATE_RE.match(date_str):
        raise ValueError(f"Invalid date_str: {date_str!r}")
    if not _REQUEST_ID_RE.match(request_id):
        raise ValueError(f"Invalid request_id: {request_id!r}")
    if "/" in filename or "\\" in filename or filename.startswith("."):
        raise ValueError(f"Disallowed filename: {filename!r}")
    if any(ord(c) < 0x20 for c in filename):
        raise ValueError(f"Disallowed control char in filename: {filename!r}")
    base = artifact_dir(store_root, date_str, request_id).resolve()
    candidate = (base / filename).resolve()
    if not candidate.is_relative_to(base):
        raise ValueError(f"Path escapes artifact dir: {candidate}")
    return candidate


def request_dir_for_date(store_root: Path, date_str: str) -> List[Path]:
    day_dir = Path(store_root) / date_str
    if not day_dir.exists():
        return []
    return [p for p in day_dir.iterdir() if p.is_dir()]
