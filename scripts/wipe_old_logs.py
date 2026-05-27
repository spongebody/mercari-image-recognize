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
