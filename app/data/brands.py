import csv
import difflib
from typing import Any, Dict, List, Optional

from ..utils import normalize_text

BRAND_ID_FIELD_MAP = {
    "rakuten_brand_id": "rakuten_id",
    "yshop_brand_id": "yshop_id",
    "yauc_brand_id": "yauc_id",
    "meru_brand_id": "meru_id",
    "ebay_brand_id": "ebay_id",
    "rakuma_brand_id": "rakuma_id",
    "amazon_brand_id": "amazon_id",
    "qoo10_brand_id": "qoo10_id",
}


def empty_brand_id_obj() -> Dict[str, str]:
    return {key: "" for key in BRAND_ID_FIELD_MAP}


def _clean_brand_id(value: str) -> str:
    cleaned = value.strip()
    return "" if cleaned == "0" else cleaned


def _pick_brand_name(record: Dict[str, str]) -> str:
    for field in ("name_jp", "name_en", "name_cn", "name"):
        value = record.get(field, "").strip()
        if value:
            return value
    return ""


class BrandStore:
    def __init__(self, path: str):
        self.path = path
        self.records: List[Dict[str, Any]] = []
        self._index: Dict[str, Dict[str, Any]] = {}
        self._keys: List[str] = []
        self._load()

    def _load(self) -> None:
        with open(self.path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                record = {
                    "id": row.get("id", "").strip(),
                    "name": row.get("name", "").strip(),
                    "name_jp": row.get("name_jp", "").strip(),
                    "name_en": row.get("name_en", "").strip(),
                    "name_cn": row.get("name_cn", "").strip(),
                }
                record["brand_name"] = _pick_brand_name(record)
                record["brand_id_obj"] = {
                    output_field: _clean_brand_id(row.get(source_field, ""))
                    for output_field, source_field in BRAND_ID_FIELD_MAP.items()
                }
                self.records.append(record)
                for field in ("name", "name_jp", "name_en", "name_cn"):
                    value = record.get(field, "")
                    normalized = normalize_text(value) if value else ""
                    if not normalized:
                        continue
                    if normalized not in self._index:
                        self._index[normalized] = record
                        self._keys.append(normalized)

    def match(self, raw_name: str) -> Optional[Dict[str, Any]]:
        if not raw_name:
            return None
        normalized = normalize_text(raw_name)
        if not normalized:
            return None
        if normalized in self._index:
            return self._index[normalized]

        close = difflib.get_close_matches(normalized, self._keys, n=1, cutoff=0.9)
        if close:
            return self._index.get(close[0])
        return None
