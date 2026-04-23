import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


def _load_script_module():
    script_path = Path(__file__).resolve().parent.parent / "scripts" / "build_mercari_brand_csv.py"
    spec = importlib.util.spec_from_file_location("build_mercari_brand_csv", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class BuildMercariBrandCsvTest(unittest.TestCase):
    def test_normalize_mercari_brand_csv_backfills_platform_ids(self):
        module = _load_script_module()

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            mercari_path = tmp_path / "mercari_brand.csv"
            rdx_path = tmp_path / "rdx_brand.csv"
            output_path = tmp_path / "normalized_mercari_brand.csv"

            mercari_path.write_text(
                "\n".join(
                    [
                        "ブランドID,ブランド名,ブランド名（カナ）,ブランド名（英語）",
                        "mer-1,Acme,アクメ,Acme",
                        "mer-2,Beta,ベータ,Beta",
                    ]
                ),
                encoding="cp932",
            )

            with rdx_path.open("w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(
                    fh,
                    fieldnames=[
                        "id",
                        "name_jp",
                        "name_en",
                        "name_cn",
                        "rakuten_id",
                        "yshop_id",
                        "yauc_id",
                        "meru_id",
                        "ebay_id",
                        "rakuma_id",
                        "amazon_id",
                        "qoo10_id",
                    ],
                )
                writer.writeheader()
                writer.writerows(
                    [
                        {
                            "id": "rdx-1",
                            "name_jp": "アクメ",
                            "name_en": "Acme",
                            "name_cn": "Acme",
                            "rakuten_id": "rak-1",
                            "yshop_id": "0",
                            "yauc_id": "",
                            "meru_id": "mer-1",
                            "ebay_id": "eb-1",
                            "rakuma_id": "rk-1",
                            "amazon_id": "",
                            "qoo10_id": "q-1",
                        }
                    ]
                )

            module.normalize_mercari_brand_csv(
                mercari_path=mercari_path,
                rdx_path=rdx_path,
                output_path=output_path,
            )

            with output_path.open("r", newline="", encoding="utf-8-sig") as fh:
                rows = list(csv.DictReader(fh))

        self.assertEqual(
            rows[0],
            {
                "id": "mer-1",
                "name": "Acme",
                "name_jp": "アクメ",
                "name_en": "Acme",
                "rakuten_id": "rak-1",
                "yshop_id": "",
                "yauc_id": "",
                "meru_id": "mer-1",
                "ebay_id": "eb-1",
                "rakuma_id": "rk-1",
                "amazon_id": "",
                "qoo10_id": "q-1",
            },
        )
        self.assertEqual(rows[1]["id"], "mer-2")
        self.assertEqual(rows[1]["meru_id"], "mer-2")
        self.assertEqual(rows[1]["rakuten_id"], "")
        self.assertEqual(rows[1]["qoo10_id"], "")


if __name__ == "__main__":
    unittest.main()
