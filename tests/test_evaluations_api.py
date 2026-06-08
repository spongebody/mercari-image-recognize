from __future__ import annotations

from fastapi.testclient import TestClient

import main


def test_create_evaluation_rejects_bad_file():
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/evaluations",
        files={"file": ("bad.csv", b"itemName\tgenreId\nx\t1\n", "text/csv")},
        data={
            "visionModel": "vision-a",
            "categoryModel": "category-a",
            "productDataModel": "product-a",
            "reasoningEffort": "none",
            "language": "ja",
            "limit": "0",
        },
    )

    assert response.status_code == 400


def test_list_evaluations_returns_runs():
    client = TestClient(main.app)

    response = client.get("/api/v1/evaluations")

    assert response.status_code == 200
    assert "runs" in response.json()


def test_create_evaluation_returns_run_id_and_submits_background(monkeypatch, tmp_path):
    calls = []

    class _FakeRun:
        runId = "2026-06-05-14-30"

    class _FakeStore:
        def create_run(self, *, input_path, config):
            calls.append((input_path, config))
            return _FakeRun()

        def execute_run(self, run_id, *, case_runner):
            calls.append((run_id, case_runner))

    class _FakeExecutor:
        def submit(self, fn, *args, **kwargs):
            calls.append((fn, args, kwargs))

    monkeypatch.setattr(main, "evaluation_store", _FakeStore())
    monkeypatch.setattr(main, "evaluation_executor", _FakeExecutor())
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/evaluations",
        files={
            "file": (
                "input.csv",
                (
                    "itemName\tgenreId\timage\tbrand\n"
                    "ASUS\t100040\thttps://example.test/01.jpg\tASUS\n"
                ).encode("utf-8"),
                "text/csv",
            )
        },
        data={
            "visionModel": "vision-a",
            "categoryModel": "category-a",
            "productDataModel": "product-a",
            "reasoningEffort": "none",
            "language": "ja",
            "limit": "0",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"runId": "2026-06-05-14-30", "status": "pending"}
    assert len(calls) >= 2
