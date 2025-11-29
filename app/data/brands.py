import csv
import difflib
from typing import Dict, List, Optional

from ..utils import normalize_text


class BrandStore:
    def __init__(self, path: str):
        self.path = path
        self.records: List[Dict[str, str]] = []
        self._index: Dict[str, Dict[str, str]] = {}
        self._keys: List[str] = []
        self._load()

    def _load(self) -> None:
        with open(self.path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                record = {
                    "id": row.get("id", "").strip(),
                    "name": row.get("name", "").strip(),
                    "name_jp": row.get("name_jp", "").strip(),
                    "name_en": row.get("name_en", "").strip(),
                }
                self.records.append(record)
                for field in ("name", "name_jp", "name_en"):
                    value = record.get(field, "")
                    normalized = normalize_text(value) if value else ""
                    if not normalized:
                        continue
                    if normalized not in self._index:
                        self._index[normalized] = record
                        self._keys.append(normalized)

    def match(self, raw_name: str) -> Optional[Dict[str, str]]:
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
