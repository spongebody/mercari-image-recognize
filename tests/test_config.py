import importlib
import unittest
from unittest.mock import patch

import app.config as config_module


class SettingsConfigTest(unittest.TestCase):
    def test_image_compression_threshold_mb_defaults_to_one_mb(self):
        with patch.dict("os.environ", {}, clear=True):
            module = importlib.reload(config_module)
            settings = module.load_settings()

        self.assertEqual(settings.image_compression_threshold_mb, 1)
        self.assertEqual(settings.image_compression_threshold_bytes, 1024 * 1024)

    def test_image_compression_threshold_mb_converts_to_bytes(self):
        with patch.dict("os.environ", {"IMAGE_COMPRESSION_THRESHOLD_MB": "3"}, clear=True):
            module = importlib.reload(config_module)
            settings = module.load_settings()

        self.assertEqual(settings.image_compression_threshold_mb, 3)
        self.assertEqual(settings.image_compression_threshold_bytes, 3 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
