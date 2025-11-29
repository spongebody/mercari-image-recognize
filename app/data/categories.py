import csv
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from ..utils import compress_whitespace, normalize_category_label


class CategoryStore:
    def __init__(self, path: str):
        self.path = path
        self.by_group: Dict[str, List[Dict[str, str]]] = defaultdict(list)
        self._lookup: Dict[Tuple[str, str], Dict[str, str]] = {}
        self._load()

    def _load(self) -> None:
        with open(self.path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                category_id = compress_whitespace(row.get("category_id", ""))
                name = row.get("category_name") or row.get("path") or ""
                name = compress_whitespace(name)
                group = compress_whitespace(row.get("group_name", ""))
                if not category_id or not name or not group:
                    continue
                entry = {"id": category_id, "name": name, "group_name": group}
                self.by_group[group].append(entry)
                key = (normalize_category_label(group), normalize_category_label(name))
                self._lookup[key] = entry

    def get_categories_by_group(self, group_name: str) -> List[Dict[str, str]]:
        return self.by_group.get(group_name, [])

    def find_category(self, group_name: str, category_name: str) -> Optional[Dict[str, str]]:
        key = (normalize_category_label(group_name), normalize_category_label(category_name))
        return self._lookup.get(key)
