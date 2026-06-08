import base64
import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import main


def _auth():
    creds = base64.b64encode(b"admin:testpass").decode()
    return {"Authorization": f"Basic {creds}"}


class PromptsApiTest(unittest.TestCase):
    def setUp(self):
        self._env_patcher = patch.dict(os.environ, {"LOGS_PASSWORD": "testpass"})
        self._env_patcher.start()
        import app.config
        importlib.reload(app.config)
        importlib.reload(main)
        self._tmp = tempfile.TemporaryDirectory()
        self._path = Path(self._tmp.name) / "prompt_overrides.json"
        self._path_patch = patch.object(main.prompt_store, "OVERRIDES_PATH", self._path)
        self._path_patch.start()
        main.prompt_store._overrides = {}

    def tearDown(self):
        main.prompt_store._overrides = {}
        self._path_patch.stop()
        self._tmp.cleanup()
        self._env_patcher.stop()

    def test_get_prompts_returns_registry(self):
        client = TestClient(main.app)
        resp = client.get("/api/v1/prompts")
        self.assertEqual(resp.status_code, 200)
        prompts = resp.json()["prompts"]
        self.assertEqual(len(prompts), 17)
        self.assertIn("SHOWCASE_PROMPT", {p["key"] for p in prompts})

    def test_put_prompt_updates_and_persists(self):
        client = TestClient(main.app)
        resp = client.put(
            "/api/v1/prompts",
            headers=_auth(),
            json={"PRODUCT_DATA_USER_PROMPT": "Lang {language_label}. Go."},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            main.prompt_store.get("PRODUCT_DATA_USER_PROMPT"), "Lang {language_label}. Go."
        )
        self.assertTrue(self._path.exists())

    def test_put_invalid_prompt_returns_400(self):
        client = TestClient(main.app)
        resp = client.put(
            "/api/v1/prompts",
            headers=_auth(),
            json={"PRODUCT_DATA_USER_PROMPT": "missing placeholder"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_put_requires_auth(self):
        client = TestClient(main.app)
        resp = client.put("/api/v1/prompts", json={"PRODUCT_DATA_USER_PROMPT": "x"})
        self.assertEqual(resp.status_code, 401)

    def test_put_rejects_cross_origin(self):
        client = TestClient(main.app)
        resp = client.put(
            "/api/v1/prompts",
            headers={**_auth(), "Origin": "https://evil.example", "Host": "api.example"},
            json={"PRODUCT_DATA_USER_PROMPT": "Lang {language_label}."},
        )
        self.assertEqual(resp.status_code, 403)

    def test_reset_reverts_override(self):
        client = TestClient(main.app)
        client.put(
            "/api/v1/prompts",
            headers=_auth(),
            json={"PRODUCT_DATA_USER_PROMPT": "Lang {language_label}. Go."},
        )
        resp = client.post(
            "/api/v1/prompts/reset",
            headers=_auth(),
            json={"keys": ["PRODUCT_DATA_USER_PROMPT"]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(main.prompt_store.is_overridden("PRODUCT_DATA_USER_PROMPT"))


if __name__ == "__main__":
    unittest.main()
