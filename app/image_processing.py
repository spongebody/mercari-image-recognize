import io
from dataclasses import dataclass

from PIL import Image, UnidentifiedImageError


@dataclass
class ProcessedImage:
    data: bytes
    mime_type: str
    original_bytes: int
    processed_bytes: int
    compressed: bool


def _resize_dimensions(width: int, height: int, max_dimension: int) -> tuple[int, int]:
    if max_dimension <= 0 or max(width, height) <= max_dimension:
        return width, height
    scale = max_dimension / max(width, height)
    return max(1, round(width * scale)), max(1, round(height * scale))


def compress_image_if_needed(
    image_bytes: bytes,
    mime_type: str,
    threshold_bytes: int,
    max_dimension: int = 1600,
    quality: int = 82,
) -> ProcessedImage:
    original_size = len(image_bytes)
    if threshold_bytes <= 0 or original_size <= threshold_bytes:
        return ProcessedImage(
            data=image_bytes,
            mime_type=mime_type,
            original_bytes=original_size,
            processed_bytes=original_size,
            compressed=False,
        )

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.load()
            width, height = _resize_dimensions(image.width, image.height, max_dimension)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            if (width, height) != image.size:
                image = image.resize((width, height), Image.LANCZOS)

            output = io.BytesIO()
            image.save(output, format="JPEG", quality=quality, optimize=True)
            compressed = output.getvalue()
    except (UnidentifiedImageError, OSError, ValueError):
        return ProcessedImage(
            data=image_bytes,
            mime_type=mime_type,
            original_bytes=original_size,
            processed_bytes=original_size,
            compressed=False,
        )

    if not compressed or len(compressed) >= original_size:
        return ProcessedImage(
            data=image_bytes,
            mime_type=mime_type,
            original_bytes=original_size,
            processed_bytes=original_size,
            compressed=False,
        )

    return ProcessedImage(
        data=compressed,
        mime_type="image/jpeg",
        original_bytes=original_size,
        processed_bytes=len(compressed),
        compressed=True,
    )
