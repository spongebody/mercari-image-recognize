from __future__ import annotations

import base64
import importlib

import app.config
import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def reloaded_main(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGS_USER", "admin")
    monkeypatch.setenv("LOGS_PASSWORD", "testpass")
    monkeypatch.setenv("CONSOLE_USERS_PATH", str(tmp_path / "console_users.json"))
    importlib.reload(app.config)
    return importlib.reload(main)


def _auth() -> dict[str, str]:
    creds = base64.b64encode(b"admin:testpass").decode()
    return {"Authorization": f"Basic {creds}"}


def test_image_proxy_requires_auth(reloaded_main):
    client = TestClient(reloaded_main.app)

    response = client.get(
        "/api/v1/image-proxy", params={"url": "https://example.com/a.jpg"}
    )

    assert response.status_code == 401


def test_image_proxy_rejects_bad_scheme(reloaded_main):
    client = TestClient(reloaded_main.app)

    response = client.get(
        "/api/v1/image-proxy",
        headers=_auth(),
        params={"url": "ftp://example.com/a.jpg"},
    )

    assert response.status_code == 400


def test_image_proxy_rejects_private_host(reloaded_main):
    client = TestClient(reloaded_main.app)

    response = client.get(
        "/api/v1/image-proxy",
        headers=_auth(),
        params={"url": "http://127.0.0.1/a.jpg"},
    )

    assert response.status_code == 400


def test_image_proxy_rejects_subaccount_without_evaluations_menu(reloaded_main):
    reloaded_main.console_account_store.create_user("config-user", "secret123", ["config"])

    with TestClient(reloaded_main.app) as client:
        login = client.post(
            "/api/v1/console/login",
            json={"username": "config-user", "password": "secret123", "remember": True},
        )
        assert login.status_code == 200

        response = client.get(
            "/api/v1/image-proxy", params={"url": "https://example.com/a.jpg"}
        )

    assert response.status_code == 403


def test_image_proxy_streams_remote_image_for_evaluations_subaccount(
    reloaded_main, monkeypatch
):
    reloaded_main.console_account_store.create_user("model-tester", "secret123", ["evaluations"])
    monkeypatch.setattr(reloaded_main, "_resolves_to_public_addresses", lambda hostname: True)
    monkeypatch.setattr(
        reloaded_main, "fetch_image_from_url", lambda url, **kwargs: (b"fake-bytes", "image/png")
    )

    with TestClient(reloaded_main.app) as client:
        login = client.post(
            "/api/v1/console/login",
            json={"username": "model-tester", "password": "secret123", "remember": True},
        )
        assert login.status_code == 200

        response = client.get(
            "/api/v1/image-proxy", params={"url": "https://cdn.example.com/a.png"}
        )

    assert response.status_code == 200
    assert response.content == b"fake-bytes"
    assert response.headers["content-type"].startswith("image/png")
    assert "max-age" in response.headers.get("cache-control", "")
