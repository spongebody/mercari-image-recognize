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
