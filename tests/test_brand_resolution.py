import csv
import tempfile
import unittest
from pathlib import Path

from app.data.brands import BrandStore, empty_brand_id_obj
from app.service import _resolve_brand


BRAND_ROWS = [
    {"id": "tp-1", "name": "tp-link", "name_jp": "ティーピーリンク", "name_en": "tp-link", "meru_id": "tp-1"},
    {"id": "sony-1", "name": "Sony", "name_jp": "ソニー", "name_en": "Sony", "meru_id": "sony-1"},
    {"id": "acme-1", "name": "Acme", "name_jp": "アクメ", "name_en": "Acme", "meru_id": "acme-1"},
]

FIELDNAMES = ["id", "name", "name_jp", "name_en", "name_cn", "meru_id"]


def _build_store(tmpdir: str) -> BrandStore:
    csv_path = Path(tmpdir) / "mercari_brand.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDNAMES)
        writer.writeheader()
        for row in BRAND_ROWS:
            writer.writerow({key: row.get(key, "") for key in FIELDNAMES})
    return BrandStore(str(csv_path))


def _description(brand: str = "") -> dict:
    return {
        "product_details": {
            "brand": brand,
            "product_name": "",
            "model_number": "",
            "color": "",
        }
    }


class CountingBrandStore(BrandStore):
    """BrandStore that records every raw value passed to match()."""

    def __init__(self, path: str):
        super().__init__(path)
        self.match_calls = []

    def match(self, raw_name):
        self.match_calls.append(raw_name)
        return super().match(raw_name)


class ResolveBrandTest(unittest.TestCase):
    def test_first_candidate_matches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _build_store(tmpdir)
            ai_raw = {"brand_name": "Sony", "brand_candidates": ["Sony"]}
            name, id_obj, raw = _resolve_brand(store, ai_raw, _description())
        self.assertEqual(name, "Sony")
        self.assertEqual(id_obj["meru_brand_id"], "sony-1")
        self.assertEqual(raw, "Sony")

    def test_subbrand_falls_back_to_parent_via_candidates(self):
        # Tapo is not in the table; TP-Link is. The ordered candidate list lets
        # the resolver skip the unmatched sub-brand and land on the parent.
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _build_store(tmpdir)
            ai_raw = {
                "brand_name": "Tapo",
                "brand_candidates": ["Tapo", "TP-Link"],
            }
            name, id_obj, raw = _resolve_brand(store, ai_raw, _description("Tapo"))
        self.assertEqual(name, "tp-link")
        self.assertEqual(id_obj["meru_brand_id"], "tp-1")
        # brand_raw stays the printed name so title behavior is unchanged.
        self.assertEqual(raw, "Tapo")

    def test_falls_back_to_product_details_brand(self):
        # brand_name / brand_candidates miss, but product_details.brand matches.
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _build_store(tmpdir)
            ai_raw = {"brand_name": "Tapo", "brand_candidates": ["Tapo"]}
            name, id_obj, raw = _resolve_brand(store, ai_raw, _description("Sony"))
        self.assertEqual(name, "Sony")
        self.assertEqual(id_obj["meru_brand_id"], "sony-1")
        self.assertEqual(raw, "Tapo")

    def test_falls_back_to_title_first_token(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _build_store(tmpdir)
            ai_raw = {
                "brand_name": "",
                "brand_candidates": [],
                "title": "Acme wireless keyboard model X",
            }
            name, id_obj, raw = _resolve_brand(store, ai_raw, _description())
        self.assertEqual(name, "Acme")
        self.assertEqual(id_obj["meru_brand_id"], "acme-1")

    def test_no_match_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _build_store(tmpdir)
            ai_raw = {
                "brand_name": "Tapo",
                "brand_candidates": ["Tapo", "Nothing"],
                "title": "Tapo smart plug",
            }
            name, id_obj, raw = _resolve_brand(store, ai_raw, _description("Tapo"))
        self.assertEqual(name, "")
        self.assertEqual(id_obj, empty_brand_id_obj())
        self.assertEqual(raw, "Tapo")

    def test_missing_brand_candidates_degrades_gracefully(self):
        # Old / fallback models may not return brand_candidates at all.
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _build_store(tmpdir)
            ai_raw = {"brand_name": "Sony"}
            name, _id_obj, raw = _resolve_brand(store, ai_raw, _description())
        self.assertEqual(name, "Sony")
        self.assertEqual(raw, "Sony")

    def test_non_list_brand_candidates_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = _build_store(tmpdir)
            ai_raw = {"brand_name": "Sony", "brand_candidates": "TP-Link"}
            name, _id_obj, _raw = _resolve_brand(store, ai_raw, _description())
        self.assertEqual(name, "Sony")

    def test_duplicate_candidates_are_not_queried_twice(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = CountingBrandStore(_build_store(tmpdir).path)
            ai_raw = {
                "brand_name": "Tapo",
                "brand_candidates": ["tapo", "TAPO", "Tapo "],
            }
            _resolve_brand(store, ai_raw, _description("tapo"))
        # All variants normalize to "tapo"; only one lookup should happen for it.
        self.assertEqual(store.match_calls, ["Tapo"])


if __name__ == "__main__":
    unittest.main()
