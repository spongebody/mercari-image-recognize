"""
Normalize data/mercari_brand.csv into the UTF-8 schema expected by BrandStore.

Accepted input formats:
- raw Mercari export in CP932 with Japanese headers
- already-normalized UTF-8 CSV with English headers

Platform ids are backfilled from data/rdx_brand.csv by matching:
    rdx_brand.meru_id == mercari_brand.id
"""

import csv
from pathlib import Path
from typing import Dict, Iterable, List

BASE_DIR = Path(__file__).parent.parent
DEFAULT_MERCARI_CSV = BASE_DIR / "data" / "mercari_brand.csv"
DEFAULT_RDX_CSV = BASE_DIR / "data" / "rdx_brand.csv"
OUTPUT_FIELDNAMES = [
    "id",
    "name",
    "name_jp",
    "name_en",
    "rakuten_id",
    "yshop_id",
    "yauc_id",
    "meru_id",
    "ebay_id",
    "rakuma_id",
    "amazon_id",
    "qoo10_id",
]
RAW_MERCARI_HEADER_MAP = {
    "ブランドID": "id",
    "ブランド名": "name",
    "ブランド名（カナ）": "name_jp",
    "ブランド名（英語）": "name_en",
}
PLATFORM_FIELDS = [
    "rakuten_id",
    "yshop_id",
    "yauc_id",
    "ebay_id",
    "rakuma_id",
    "amazon_id",
    "qoo10_id",
]


def _clean_value(value: str) -> str:
    cleaned = (value or "").strip()
    return "" if cleaned == "0" else cleaned


def _read_csv_rows(path: Path, encoding: str) -> List[Dict[str, str]]:
    with path.open("r", newline="", encoding=encoding) as fh:
        return list(csv.DictReader(fh))


def _load_mercari_rows(path: Path) -> List[Dict[str, str]]:
    last_error = None
    for encoding in ("utf-8-sig", "cp932"):
        try:
            rows = _read_csv_rows(path, encoding)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        if not rows:
            return []
        fieldnames = set(rows[0].keys())
        if {"id", "name", "name_jp", "name_en"}.issubset(fieldnames):
            return [
                {
                    "id": (row.get("id") or "").strip(),
                    "name": (row.get("name") or "").strip(),
                    "name_jp": (row.get("name_jp") or "").strip(),
                    "name_en": (row.get("name_en") or "").strip(),
                }
                for row in rows
            ]
        if set(RAW_MERCARI_HEADER_MAP).issubset(fieldnames):
            return [
                {
                    target: (row.get(source) or "").strip()
                    for source, target in RAW_MERCARI_HEADER_MAP.items()
                }
                for row in rows
            ]
    if last_error is not None:
        raise last_error
    raise ValueError(f"Unsupported mercari brand CSV format: {path}")


def _load_rdx_mapping(path: Path) -> Dict[str, Dict[str, str]]:
    mapping: Dict[str, Dict[str, str]] = {}
    with path.open("r", newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            meru_id = _clean_value(row.get("meru_id", ""))
            if not meru_id:
                continue
            mapping[meru_id] = {field: _clean_value(row.get(field, "")) for field in PLATFORM_FIELDS}
    return mapping


def _normalize_rows(
    mercari_rows: Iterable[Dict[str, str]],
    rdx_mapping: Dict[str, Dict[str, str]],
) -> List[Dict[str, str]]:
    normalized_rows: List[Dict[str, str]] = []
    for row in mercari_rows:
        brand_id = (row.get("id") or "").strip()
        if not brand_id:
            continue
        rdx_match = rdx_mapping.get(brand_id, {})
        normalized = {
            "id": brand_id,
            "name": (row.get("name") or "").strip(),
            "name_jp": (row.get("name_jp") or "").strip(),
            "name_en": (row.get("name_en") or "").strip(),
            "meru_id": brand_id,
        }
        for field in PLATFORM_FIELDS:
            normalized[field] = rdx_match.get(field, "")
        normalized_rows.append(normalized)
    return normalized_rows


def normalize_mercari_brand_csv(
    mercari_path: Path = DEFAULT_MERCARI_CSV,
    rdx_path: Path = DEFAULT_RDX_CSV,
    output_path: Path = DEFAULT_MERCARI_CSV,
) -> int:
    mercari_rows = _load_mercari_rows(mercari_path)
    rdx_mapping = _load_rdx_mapping(rdx_path)
    normalized_rows = _normalize_rows(mercari_rows, rdx_mapping)

    with output_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUTPUT_FIELDNAMES)
        writer.writeheader()
        writer.writerows(normalized_rows)

    return len(normalized_rows)


def main() -> None:
    count = normalize_mercari_brand_csv()
    print(f"[OK] Wrote {count} rows to {DEFAULT_MERCARI_CSV}")


if __name__ == "__main__":
    main()
