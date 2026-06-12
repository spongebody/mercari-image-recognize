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


def test_read_evaluation_errors_endpoint(monkeypatch, tmp_path):
    from app.evaluation.runs import EvaluationRunStore

    store = EvaluationRunStore(tmp_path)
    run_dir = tmp_path / "2026-06-11-09-00"
    run_dir.mkdir(parents=True)
    (run_dir / "errors.jsonl").write_text(
        '{"caseIndex": 1, "itemName": "x", "error": "boom"}\n', encoding="utf-8"
    )
    monkeypatch.setattr(main, "evaluation_store", store)
    client = TestClient(main.app)

    response = client.get("/api/v1/evaluations/2026-06-11-09-00/errors")

    assert response.status_code == 200
    assert response.json() == {
        "errors": [{"caseIndex": 1, "itemName": "x", "error": "boom"}]
    }


def test_read_evaluation_errors_404_for_unknown_run(monkeypatch, tmp_path):
    from app.evaluation.runs import EvaluationRunStore

    monkeypatch.setattr(main, "evaluation_store", EvaluationRunStore(tmp_path))
    client = TestClient(main.app)

    assert client.get("/api/v1/evaluations/nope/errors").status_code == 404


def test_analysis_endpoint_removed():
    client = TestClient(main.app)

    response = client.put("/api/v1/evaluations/some-run/analysis", json={})

    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_import_evaluation_endpoint(monkeypatch, tmp_path):
    from app.evaluation.runs import EvaluationRunStore

    monkeypatch.setattr(main, "evaluation_store", EvaluationRunStore(tmp_path))
    client = TestClient(main.app)
    body = (
        "itemName\tgenreId\timage\tbrand\taiCategory\n"
        "item\t100\thttp://example.com/a.jpg\tnike\t100\n"
    ).encode("utf-8")

    response = client.post(
        "/api/v1/evaluations/import",
        files={"file": ("results.csv", body, "text/tab-separated-values")},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "completed"
    assert client.get(f"/api/v1/evaluations/{data['runId']}").status_code == 200


def test_import_evaluation_rejects_bad_file(monkeypatch, tmp_path):
    from app.evaluation.runs import EvaluationRunStore

    monkeypatch.setattr(main, "evaluation_store", EvaluationRunStore(tmp_path))
    client = TestClient(main.app)

    response = client.post(
        "/api/v1/evaluations/import",
        files={"file": ("bad.csv", b"itemName\tgenreId\nx\t1\n", "text/csv")},
    )

    assert response.status_code == 400


def test_delete_evaluation_endpoint(monkeypatch, tmp_path):
    from app.evaluation.runs import EvaluationRunStore

    monkeypatch.setattr(main, "evaluation_store", EvaluationRunStore(tmp_path))
    run_dir = tmp_path / "2026-06-12-10-00"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text("{}", encoding="utf-8")
    client = TestClient(main.app)

    assert client.delete("/api/v1/evaluations/2026-06-12-10-00").status_code == 200
    assert not run_dir.exists()
    assert client.delete("/api/v1/evaluations/2026-06-12-10-00").status_code == 404
