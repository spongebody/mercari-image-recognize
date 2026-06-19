import base64
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

import main


def test_analyze_creates_request_with_llm_calls(monkeypatch):
    monkeypatch.setenv("LOGS_PASSWORD", "h")
    import importlib, app.config
    importlib.reload(app.config)
    importlib.reload(main)

    # mock the analyzer to avoid real OpenRouter
    classification = {"title": "x", "categories": [], "category_paths": [], "prices": []}
    main.analyzer.classify_first_image_categories = MagicMock(return_value=classification)
    main.analyzer.generate_product_data = MagicMock(return_value={"product_data": {}})

    creds = base64.b64encode(b"a:h").decode()
    headers = {"Authorization": f"Basic {creds}"}

    with TestClient(main.app) as client:
        # one tiny fake PNG
        png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        files = [("image_list", ("a.png", png, "image/png"))]
        r = client.post("/api/v1/mercari/image/analyze",
                        headers=headers,
                        data={"language": "ja", "debug": "false"},
                        files=files)
        assert r.status_code == 200
        rid = r.headers["x-request-id"]

        # list contains it
        listing = client.get("/api/v1/logs/requests", headers=headers).json()
        assert any(item["request_id"] == rid for item in listing["items"])

        # detail returns the row
        detail = client.get(f"/api/v1/logs/requests/{rid}", headers=headers).json()
        assert detail["request"]["request_id"] == rid

        # request.json file exists
        r2 = client.get(f"/api/v1/logs/requests/{rid}/files/request.json", headers=headers)
        assert r2.status_code == 200
        assert "endpoint" in r2.text
