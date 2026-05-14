import csv
import tempfile
import unittest
from pathlib import Path

from app.data.categories import CategoryStore


class CategoryStoreTest(unittest.TestCase):
    def test_loads_platform_paths_from_category_csv(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            csv_path = Path(tmpdir) / "category_rakuten.csv"
            with csv_path.open("w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "category_id",
                        "path",
                        "group_name",
                        "meru_id",
                        "rakuma_id",
                        "zenplus_id",
                        "meru_path",
                        "rakuma_path",
                        "zenplus_path",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "category_id": "123",
                        "path": "メンズファッション > トップス",
                        "group_name": "メンズファッション",
                        "meru_id": "m-123",
                        "rakuma_id": "r-123",
                        "zenplus_id": "z-123",
                        "meru_path": "Mercari > Tops",
                        "rakuma_path": "Rakuma>Tops",
                        "zenplus_path": "ZenPlus>Tops",
                    }
                )

            store = CategoryStore(str(csv_path))

        category = store.find_category("メンズファッション", "メンズファッション > トップス")
        self.assertEqual(category["meru_path"], "Mercari > Tops")
        self.assertEqual(category["rakuma_path"], "Rakuma>Tops")
        self.assertEqual(category["zenplus_path"], "ZenPlus>Tops")


if __name__ == "__main__":
    unittest.main()
