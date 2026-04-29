import base64
import json
import tempfile
import unittest
from pathlib import Path

from app.showcase.archive import ArchiveWriter
from app.showcase.openrouter_image_client import (
    ImagePayload,
    OpenRouterImageClientError,
    OpenRouterImageResult,
)
from app.showcase.service import ShowcaseService
from app.showcase.storage import StorageManager


class _FakeSuccessClient:
    def __init__(self):
        self.last_call = None

    def generate_image(self, *, prompt, image_bytes, content_type, request_id, model=None):
        self.last_call = {
            "prompt": prompt,
            "image_bytes": image_bytes,
            "content_type": content_type,
            "request_id": request_id,
            "model": model,
        }
        return OpenRouterImageResult(
            image=ImagePayload(
                mime_type="image/png",
                base64_data=base64.b64encode(b"generated-image").decode("utf-8"),
            ),
            upstream_status_code=200,
            response_body={"choices": []},
            attempts=1,
        )


class _FakeFailureClient:
    def generate_image(self, *, prompt, image_bytes, content_type, request_id, model=None):
        raise OpenRouterImageClientError(
            "OpenRouter returned status 500", status_code=500
        )


def _build_service(
    tmp_path: Path,
    client,
    *,
    retain_input: bool = False,
    retain_output: bool = False,
) -> ShowcaseService:
    storage = StorageManager(
        tmp_path / "storage",
        retain_input_files=retain_input,
        retain_output_files=retain_output,
    )
    archive = ArchiveWriter(tmp_path / "logs" / "showcase")
    return ShowcaseService(
        model="google/gemini-3.1-flash-image-preview",
        storage_manager=storage,
        archive_writer=archive,
        client=client,
    )


class ShowcaseServiceTest(unittest.TestCase):
    def test_success_archives_record_without_input_output_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            service = _build_service(tmp_path, _FakeSuccessClient())

            response = service.generate_showcase(
                upload_filename="bag.jpg",
                content_type="image/jpeg",
                image_bytes=b"input-image",
                prompt_hint="luxury studio",
            )

            self.assertEqual(response["status"], "succeeded")
            self.assertEqual(response["model"], "google/gemini-3.1-flash-image-preview")
            self.assertEqual(
                response["image_base64"],
                base64.b64encode(b"generated-image").decode("utf-8"),
            )
            self.assertIsNone(response["input_path"])
            self.assertIsNone(response["output_path"])
            self.assertNotIn("storage/inputs", str(tmp_path))
            self.assertFalse((tmp_path / "storage" / "inputs").exists())
            self.assertFalse((tmp_path / "storage" / "outputs").exists())

            archive_files = list((tmp_path / "logs" / "showcase").rglob("*.json"))
            self.assertEqual(len(archive_files), 1)
            record = json.loads(archive_files[0].read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "succeeded")
            self.assertEqual(record["response_parse_status"], "ok")
            self.assertIsNone(record["input_path"])
            self.assertIsNone(record["output_path"])
            self.assertEqual(record["upstream_status_code"], 200)

    def test_success_writes_files_when_retention_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            service = _build_service(
                tmp_path, _FakeSuccessClient(), retain_input=True, retain_output=True
            )

            response = service.generate_showcase(
                upload_filename="bag.jpg",
                content_type="image/jpeg",
                image_bytes=b"input-image",
                prompt_hint=None,
            )

            self.assertIsNotNone(response["input_path"])
            self.assertIsNotNone(response["output_path"])
            self.assertTrue(Path(response["input_path"]).exists())
            self.assertTrue(Path(response["output_path"]).exists())

    def test_failure_archives_failed_record_and_returns_failed_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            service = _build_service(tmp_path, _FakeFailureClient())

            response = service.generate_showcase(
                upload_filename="sofa.jpg",
                content_type="image/jpeg",
                image_bytes=b"input-image",
                prompt_hint=None,
            )

            self.assertEqual(response["status"], "failed")
            self.assertEqual(response["error_code"], "upstream_generation_failed")

            archive_files = list((tmp_path / "logs" / "showcase").rglob("*.json"))
            self.assertEqual(len(archive_files), 1)
            record = json.loads(archive_files[0].read_text(encoding="utf-8"))
            self.assertEqual(record["status"], "failed")
            self.assertEqual(record["error_code"], "upstream_generation_failed")
            self.assertEqual(record["upstream_status_code"], 500)

    def test_model_override_is_forwarded_to_client_and_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            client = _FakeSuccessClient()
            service = _build_service(tmp_path, client)

            response = service.generate_showcase(
                upload_filename="bag.jpg",
                content_type="image/jpeg",
                image_bytes=b"input-image",
                prompt_hint=None,
                model_override="custom/override-model",
            )

            self.assertEqual(client.last_call["model"], "custom/override-model")
            self.assertEqual(response["model"], "custom/override-model")
            self.assertEqual(response["model_override"], "custom/override-model")

            archive_files = list((tmp_path / "logs" / "showcase").rglob("*.json"))
            self.assertEqual(len(archive_files), 1)
            record = json.loads(archive_files[0].read_text(encoding="utf-8"))
            self.assertEqual(record["model"], "custom/override-model")
            self.assertEqual(record["model_override"], "custom/override-model")

    def test_blank_or_whitespace_override_falls_back_to_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            client = _FakeSuccessClient()
            service = _build_service(tmp_path, client)

            response = service.generate_showcase(
                upload_filename="bag.jpg",
                content_type="image/jpeg",
                image_bytes=b"input-image",
                prompt_hint=None,
                model_override="   ",
            )

            self.assertIsNone(client.last_call["model"])
            self.assertEqual(response["model"], "google/gemini-3.1-flash-image-preview")
            self.assertIsNone(response["model_override"])


if __name__ == "__main__":
    unittest.main()
