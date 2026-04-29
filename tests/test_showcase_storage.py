import base64
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.showcase.archive import ArchiveWriter
from app.showcase.storage import StorageManager


class StorageManagerTest(unittest.TestCase):
    def test_saves_input_and_output_when_retention_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            storage = StorageManager(
                tmp_path, retain_input_files=True, retain_output_files=True
            )
            now = datetime(2026, 4, 28, 20, 30, 0)

            input_path = storage.save_input_image(
                request_id="req-123",
                image_bytes=b"input-bytes",
                original_filename="bag.jpg",
                now=now,
            )
            output_path = storage.save_output_image(
                request_id="req-123",
                mime_type="image/png",
                base64_data=base64.b64encode(b"output-bytes").decode("utf-8"),
                now=now,
            )

            self.assertIsNotNone(input_path)
            self.assertEqual(input_path.read_bytes(), b"input-bytes")
            self.assertIn("20260428", input_path.as_posix())
            self.assertIsNotNone(output_path)
            self.assertEqual(output_path.read_bytes(), b"output-bytes")

    def test_skips_input_and_output_when_retention_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            storage = StorageManager(
                tmp_path, retain_input_files=False, retain_output_files=False
            )
            now = datetime(2026, 4, 28, 20, 30, 0)

            input_path = storage.save_input_image(
                request_id="req-123",
                image_bytes=b"input-bytes",
                original_filename="bag.jpg",
                now=now,
            )
            output_path = storage.save_output_image(
                request_id="req-123",
                mime_type="image/png",
                base64_data=base64.b64encode(b"output-bytes").decode("utf-8"),
                now=now,
            )

            self.assertIsNone(input_path)
            self.assertIsNone(output_path)
            self.assertFalse((tmp_path / "inputs").exists())
            self.assertFalse((tmp_path / "outputs").exists())


class ArchiveWriterTest(unittest.TestCase):
    def test_writes_record_under_date_partition(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            writer = ArchiveWriter(tmp_path)
            now = datetime(2026, 4, 28, 20, 30, 0)

            archive_path = writer.write_record(
                request_id="req-456",
                record={"status": "succeeded", "model": "demo-model"},
                now=now,
            )

            self.assertTrue(archive_path.exists())
            self.assertEqual(archive_path.parent.name, "20260428")
            self.assertEqual(
                json.loads(archive_path.read_text(encoding="utf-8")),
                {"status": "succeeded", "model": "demo-model"},
            )


if __name__ == "__main__":
    unittest.main()
