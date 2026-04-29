"""Batch driver for POST /api/v1/showcase/generate using FastAPI's TestClient.

Reads supported images from the input directory and writes the generated image
plus the full response JSON to the output directory.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
DEFAULT_INPUT_DIR = REPO_ROOT / "data" / "showcase_test_images"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "showcase_output"


@dataclass
class WrittenResult:
    image_path: Path
    metadata_path: Path


def collect_image_files(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        return []
    return sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    )


def detect_content_type(image_path: Path) -> str:
    suffix = image_path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    return "application/octet-stream"


def write_result_files(output_dir: Path, image_path: Path, payload: dict) -> WrittenResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    image_output_path = output_dir / f"{image_path.stem}_output.png"
    metadata_output_path = output_dir / f"{image_path.stem}_response.json"

    image_base64 = payload.get("image_base64")
    if image_base64:
        image_output_path.write_bytes(base64.b64decode(image_base64))
    else:
        image_output_path.touch()

    metadata_output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return WrittenResult(
        image_path=image_output_path,
        metadata_path=metadata_output_path,
    )


def run_batch(*, input_dir: Path, output_dir: Path, prompt_hint: str | None) -> int:
    image_files = collect_image_files(input_dir)
    if not image_files:
        print(f"No supported images found in {input_dir}")
        return 1

    client = TestClient(app)
    success_count = 0

    for image_path in image_files:
        with image_path.open("rb") as file_obj:
            response = client.post(
                "/api/v1/showcase/generate",
                files={"file": (image_path.name, file_obj, detect_content_type(image_path))},
                data={"prompt_hint": prompt_hint} if prompt_hint else {},
            )

        payload = response.json()
        written = write_result_files(output_dir, image_path, payload)
        print(
            json.dumps(
                {
                    "source": image_path.as_posix(),
                    "status_code": response.status_code,
                    "status": payload.get("status"),
                    "request_id": payload.get("request_id"),
                    "image_output": written.image_path.as_posix(),
                    "metadata_output": written.metadata_path.as_posix(),
                },
                ensure_ascii=False,
            )
        )
        if response.status_code == 200 and payload.get("status") == "succeeded":
            success_count += 1

    print(
        f"Completed {len(image_files)} images, succeeded {success_count}, "
        f"failed {len(image_files) - success_count}"
    )
    return 0 if success_count == len(image_files) else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch test showcase image generation.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing input product images.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for generated images and response metadata.",
    )
    parser.add_argument(
        "--prompt-hint",
        default=None,
        help="Optional prompt hint applied to every image request.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return run_batch(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        prompt_hint=args.prompt_hint,
    )


if __name__ == "__main__":
    raise SystemExit(main())
