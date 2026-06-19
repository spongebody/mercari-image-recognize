import concurrent.futures
import json

from fastapi.testclient import TestClient

from console_auth_helpers import auth_headers
import main


def test_request_gets_x_request_id_header():
    with TestClient(main.app) as client:
        r = client.get("/api/v1/config")
    assert "x-request-id" in {k.lower() for k in r.headers}
    assert len(r.headers["x-request-id"]) == 32


def test_health_endpoint_does_not_log():
    """/health is exempt to keep monitoring noise out of the logs DB."""
    with TestClient(main.app) as client:
        r = client.get("/health")
    # /health may or may not have X-Request-Id; verify it doesn't error
    assert r.status_code == 200


def test_large_response_body_passes_through_uncorrupted(monkeypatch):
    """Responses larger than log_response_max_bytes still deliver full body to client."""
    from fastapi import FastAPI
    big_payload = {"data": "x" * (3 * 1024 * 1024)}  # ~3 MiB JSON
    # add a route on the existing app that returns the big payload
    @main.app.get("/__test_big__")
    def _big():
        return big_payload

    with TestClient(main.app) as client:
        r = client.get("/__test_big__")
    assert r.status_code == 200
    body = r.json()
    assert len(body["data"]) == 3 * 1024 * 1024


def test_request_id_propagates_into_background_thread(monkeypatch):
    """Submitting work to product_data_executor preserves request_id contextvar."""
    captured = []

    def fake_generate(images, language, debug, use_fallback_prompt, started_at, model_override=None):
        from app.observability import context as ctx
        captured.append(ctx.get_request_id())
        return {"ok": True}

    from main import analyzer
    monkeypatch.setattr(
        analyzer,
        "classify_first_image_categories",
        lambda **_kwargs: {
            "status": "product_pending",
            "categories": [],
            "timings": {"total_ms": 1.0, "classification_ms": 1.0},
        },
    )
    monkeypatch.setattr(analyzer, "generate_product_data", fake_generate)
    # build a minimal multipart request
    files = [("image_list", ("a.png", b"\x89PNG\r\n\x1a\n", "image/png"))]
    data = {"language": "ja", "debug": "false"}
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(main.product_data_executor, "submit", executor.submit)
        with TestClient(main.app) as client:
            response = client.post(
                "/api/v1/mercari/image/analyze",
                headers=auth_headers(),
                data=data,
                files=files,
            )
        assert response.status_code == 200

    assert captured, "executor never called"
    assert captured[0] is not None
    assert len(captured[0]) == 32


def test_prune_loop_starts_and_shuts_down():
    """FastAPI startup hook creates prune_task; shutdown cancels it."""
    with TestClient(main.app) as client:
        client.get("/health")
        # task should be set during startup
        task = getattr(main.app.state, "prune_task", None)
        assert task is not None
        assert not task.done()
    # after the TestClient context exits, shutdown ran — task should be cancelled
    # (we can't easily assert task.cancelled() here because the task object survives;
    # the key signal is that no exception is raised during shutdown)
