#!/usr/bin/env python3
"""
Build a legacy-compatible Rakuten category CSV from data/others/rdx_category.csv.

Output columns:
  - category_id: Rakuten category id
  - path: normalized Japanese path using " > " separators
  - group_name: top-level category derived from path
  - meru_id
  - rakuma_id
  - zenplus_id
  - meru_path
  - rakuma_path
  - zenplus_path
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = BASE_DIR / "data" / "others" / "rdx_category.csv"
DEFAULT_OUTPUT = BASE_DIR / "data" / "category_rakuten.csv"
DEFAULT_SOURCE_PATH = BASE_DIR / "data" / "source_path"
OUTPUT_FIELDS = [
    "category_id",
    "path",
    "group_name",
    "meru_id",
    "rakuma_id",
    "zenplus_id",
    "meru_path",
    "rakuma_path",
    "zenplus_path",
]
PLATFORM_PATH_SOURCES = {
    "meru": "merari_categories.csv",
    "rakuma": "rakuma_categories.csv",
    "zenplus": "zenplus_category.csv",
}


def normalize_path(raw_path: str) -> str:
    parts = [part.strip() for part in str(raw_path or "").split(">") if part.strip()]
    return " > ".join(parts)


def load_category_paths(csv_path: Path) -> dict[str, str]:
    if not csv_path.exists():
        return {}
    paths: dict[str, str] = {}
    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            category_id = str(row.get("category_id") or "").strip()
            path = str(row.get("path") or "").strip()
            if category_id and path:
                paths[category_id] = path
    return paths


def load_platform_path_indexes(source_path: Path) -> dict[str, dict[str, str]]:
    return {
        platform: load_category_paths(source_path / filename)
        for platform, filename in PLATFORM_PATH_SOURCES.items()
    }


def find_platform_path(paths: dict[str, str], platform_id: str, rakuten_id: str) -> str:
    return paths.get(platform_id) or paths.get(rakuten_id, "")


def build_rows(
    input_path: Path,
    source_path: Path = DEFAULT_SOURCE_PATH,
) -> list[dict[str, str]]:
    platform_paths = load_platform_path_indexes(source_path)
    rows: list[dict[str, str]] = []
    with input_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            category_id = str(row.get("id") or "").strip()
            path = normalize_path(str(row.get("path_name_jp") or ""))
            group_name = path.split(" > ", 1)[0].strip() if path else ""
            meru_id = str(row.get("meru_id") or "").strip()
            rakuma_id = str(row.get("rakuma_id") or "").strip()
            zenplus_id = str(row.get("zenplus_id") or "").strip()
            if not category_id or not path or not group_name:
                continue
            rows.append(
                {
                    "category_id": category_id,
                    "path": path,
                    "group_name": group_name,
                    "meru_id": meru_id,
                    "rakuma_id": rakuma_id,
                    "zenplus_id": zenplus_id,
                    "meru_path": find_platform_path(platform_paths["meru"], meru_id, category_id),
                    "rakuma_path": find_platform_path(platform_paths["rakuma"], rakuma_id, category_id),
                    "zenplus_path": find_platform_path(platform_paths["zenplus"], zenplus_id, category_id),
                }
            )
    return rows


def write_rows(output_path: Path, rows: list[dict[str, str]]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=OUTPUT_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build legacy-compatible Rakuten category CSV.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Source rdx_category.csv path.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output CSV path. Defaults to data/category_rakuten.csv.",
    )
    parser.add_argument(
        "--source-path",
        default=str(DEFAULT_SOURCE_PATH),
        help="Directory containing platform category path CSV files.",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise SystemExit(f"[error] input file not found: {input_path}")

    rows = build_rows(input_path, source_path=Path(args.source_path))
    write_rows(output_path, rows)
    print(f"[ok] wrote {len(rows)} rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
