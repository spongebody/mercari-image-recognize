import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


def _load_script_module():
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "build_rakuten_category_csv.py"
    spec = importlib.util.spec_from_file_location("build_rakuten_category_csv", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BuildRakutenCategoryCsvTest(unittest.TestCase):
    def test_build_rows_backfills_platform_paths_from_source_path_files(self):
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            source_path = tmp_path / "source_path"
            source_path.mkdir()
            input_path = tmp_path / "rdx_category.csv"

            with input_path.open("w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "id",
                        "path_name_jp",
                        "meru_id",
                        "rakuma_id",
                        "zenplus_id",
                    ],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "id": "rak-1",
                        "path_name_jp": "メンズファッション > トップス",
                        "meru_id": "mer-1",
                        "rakuma_id": "rakuma-1",
                        "zenplus_id": "zen-1",
                    }
                )

            for filename, category_id, path in [
                ("merari_categories.csv", "mer-1", "Mercari > Tops"),
                ("rakuma_categories.csv", "rak-1", "Rakuma>Tops"),
                ("zenplus_category.csv", "zen-1", "ZenPlus>Tops"),
            ]:
                with (source_path / filename).open("w", newline="", encoding="utf-8-sig") as fh:
                    writer = csv.DictWriter(fh, fieldnames=["category_id", "path"])
                    writer.writeheader()
                    writer.writerow({"category_id": category_id, "path": path})

            rows = module.build_rows(input_path, source_path=source_path)

        self.assertEqual(rows[0]["meru_path"], "Mercari > Tops")
        self.assertEqual(rows[0]["rakuma_path"], "Rakuma>Tops")
        self.assertEqual(rows[0]["zenplus_path"], "ZenPlus>Tops")


if __name__ == "__main__":
    unittest.main()
