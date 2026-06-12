from __future__ import annotations

from fastapi.testclient import TestClient

import main


def _bypass_auth(monkeypatch):
    monkeypatch.setattr(main, "is_console_authed", lambda request, password: True)


def test_image_proxy_requires_auth():
    client = TestClient(main.app)

    response = client.get(
        "/api/v1/image-proxy", params={"url": "https://example.com/a.jpg"}
    )

    assert response.status_code == 403


def test_image_proxy_rejects_bad_scheme(monkeypatch):
    _bypass_auth(monkeypatch)
    client = TestClient(main.app)

    response = client.get(
        "/api/v1/image-proxy", params={"url": "ftp://example.com/a.jpg"}
    )

    assert response.status_code == 400


def test_image_proxy_rejects_private_host(monkeypatch):
    _bypass_auth(monkeypatch)
    client = TestClient(main.app)

    response = client.get(
        "/api/v1/image-proxy", params={"url": "http://127.0.0.1/a.jpg"}
    )

    assert response.status_code == 400


def test_image_proxy_streams_remote_image(monkeypatch):
    _bypass_auth(monkeypatch)
    monkeypatch.setattr(main, "_resolves_to_public_addresses", lambda hostname: True)
    monkeypatch.setattr(
        main, "fetch_image_from_url", lambda url, **kwargs: (b"fake-bytes", "image/png")
    )
    client = TestClient(main.app)

    response = client.get(
        "/api/v1/image-proxy", params={"url": "https://cdn.example.com/a.png"}
    )

    assert response.status_code == 200
    assert response.content == b"fake-bytes"
    assert response.headers["content-type"].startswith("image/png")
    assert "max-age" in response.headers.get("cache-control", "")
