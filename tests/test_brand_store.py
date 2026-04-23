import csv
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.data.brands import BrandStore


class BrandStoreTest(unittest.TestCase):
    def test_brand_store_reads_normalized_mercari_csv(self):
        fieldnames = [
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
        rows = [
            {
                "id": "mercari-1",
                "name": "Acme",
                "name_jp": "アクメ",
                "name_en": "Acme",
                "rakuten_id": "rak-1",
                "yshop_id": "0",
                "yauc_id": "",
                "meru_id": "mercari-1",
                "ebay_id": "eb-1",
                "rakuma_id": "0",
                "amazon_id": "",
                "qoo10_id": "q-1",
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "mercari_brand.csv"
            with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(fh, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

            store = BrandStore(str(csv_path))
            record = store.match("アクメ")

        self.assertIsNotNone(record)
        self.assertEqual(record["brand_name"], "アクメ")
        self.assertEqual(
            record["brand_id_obj"],
            {
                "rakuten_brand_id": "rak-1",
                "yshop_brand_id": "",
                "yauc_brand_id": "",
                "meru_brand_id": "mercari-1",
                "ebay_brand_id": "eb-1",
                "rakuma_brand_id": "",
                "amazon_brand_id": "",
                "qoo10_brand_id": "q-1",
            },
        )

    def test_settings_default_brand_csv_path_is_mercari_brand(self):
        self.assertEqual(Settings().brand_csv_path, "data/mercari_brand.csv")


if __name__ == "__main__":
    unittest.main()
