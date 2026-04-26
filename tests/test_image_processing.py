import io
import random
import unittest

from PIL import Image

from app.image_processing import compress_image_if_needed


def make_noisy_png(width=900, height=700):
    rng = random.Random(12345)
    data = bytes(rng.randrange(256) for _ in range(width * height * 3))
    image = Image.frombytes("RGB", (width, height), data)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class ImageProcessingTest(unittest.TestCase):
    def test_does_not_compress_images_at_or_below_threshold(self):
        image_bytes = b"small-image"

        result = compress_image_if_needed(
            image_bytes=image_bytes,
            mime_type="image/png",
            threshold_bytes=len(image_bytes),
        )

        self.assertEqual(result.data, image_bytes)
        self.assertEqual(result.mime_type, "image/png")
        self.assertFalse(result.compressed)
        self.assertEqual(result.original_bytes, len(image_bytes))
        self.assertEqual(result.processed_bytes, len(image_bytes))

    def test_compresses_images_above_threshold(self):
        image_bytes = make_noisy_png()

        result = compress_image_if_needed(
            image_bytes=image_bytes,
            mime_type="image/png",
            threshold_bytes=1024,
            max_dimension=640,
            quality=75,
        )

        self.assertTrue(result.compressed)
        self.assertEqual(result.mime_type, "image/jpeg")
        self.assertLess(result.processed_bytes, result.original_bytes)
        self.assertLessEqual(result.processed_bytes, len(image_bytes))


if __name__ == "__main__":
    unittest.main()
