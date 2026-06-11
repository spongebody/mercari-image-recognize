from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from app.evaluation.image_model_evaluation import write_result_rows
from app.evaluation.runs import EvaluationRunConfig, EvaluationRunStore


def _write_input(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["itemName", "genreId", "image", "brand"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow(
            {
                "itemName": "ASUS laptop",
                "genreId": "100040",
                "image": "https://example.test/01.jpg|https://example.test/02.jpg",
                "brand": "ASUS",
            }
        )


def _config() -> EvaluationRunConfig:
    return EvaluationRunConfig(
        visionModel="vision-a",
        categoryModel="category-a",
        productDataModel="product-a",
        reasoningEffort="none",
        language="ja",
        limit=0,
    )


def test_create_run_writes_input_config_and_status(tmp_path):
    source = tmp_path / "source.csv"
    _write_input(source)
    store = EvaluationRunStore(tmp_path / "runs")

    run = store.create_run(input_path=source, config=_config())

    assert (run.path / "input.csv").exists()
    config = json.loads((run.path / "run_config.json").read_text(encoding="utf-8"))
    assert config["visionModel"] == "vision-a"
    assert config["runId"] == run.runId
    status = json.loads((run.path / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "pending"


def test_create_run_rejects_missing_required_input_columns(tmp_path):
    source = tmp_path / "bad.csv"
    source.write_text("itemName\tgenreId\nx\t1\n", encoding="utf-8")
    store = EvaluationRunStore(tmp_path / "runs")

    with pytest.raises(ValueError, match="missing required columns"):
        store.create_run(input_path=source, config=_config())


def test_save_review_updates_results_and_summary(tmp_path):
    source = tmp_path / "source.csv"
    _write_input(source)
    store = EvaluationRunStore(tmp_path / "runs")
    run = store.create_run(input_path=source, config=_config())
    write_result_rows(
        run.path / "results.csv",
        [
            {
                "itemName": "ASUS laptop",
                "genreId": "100040",
                "image": "https://example.test/01.jpg",
                "brand": "ASUS",
                "visionModel": "vision-a",
                "categoryModel": "category-a",
                "productDataModel": "product-a",
                "reasoningEffort": "none",
                "aiCategory": "565105",
                "aiCategoryPath": "wrong",
                "aiCategoryConfidence": "0.7",
                "aiBrand": "ASUS",
                "aiTitle": "title",
                "categoryDurationS": "1",
                "productDataDurationS": "1",
                "totalDurationS": "2",
                "customerCategoryCheck": "",
                "customerBrandCheck": "",
                "customerNotes": "",
            }
        ],
    )

    store.save_review(
        run.runId,
        [
            {
                "rowIndex": 0,
                "customerCategoryCheck": "ACCEPTABLE",
                "customerBrandCheck": "OK",
                "customerNotes": "near category",
            }
        ],
    )

    rows = store.read_results(run.runId)
    assert rows[0]["customerCategoryCheck"] == "ACCEPTABLE"
    assert rows[0]["customerNotes"] == "near category"
    summary = json.loads((run.path / "summary.json").read_text(encoding="utf-8"))
    assert summary["overall"]["categoryReviewedCorrect"] == 1


def test_archive_locks_review(tmp_path):
    source = tmp_path / "source.csv"
    _write_input(source)
    store = EvaluationRunStore(tmp_path / "runs")
    run = store.create_run(input_path=source, config=_config())

    store.archive(run.runId)

    with pytest.raises(ValueError, match="archived"):
        store.save_review(run.runId, [])


def test_execute_run_updates_status_and_writes_results(tmp_path):
    source = tmp_path / "source.csv"
    _write_input(source)
    store = EvaluationRunStore(tmp_path / "runs")
    run = store.create_run(input_path=source, config=_config())

    def fake_case_runner(case, config):
        return {
            "itemName": case["itemName"],
            "genreId": case["genreId"],
            "image": case["image"],
            "brand": case["brand"],
            "visionModel": config.visionModel,
            "categoryModel": config.categoryModel,
            "productDataModel": config.productDataModel,
            "reasoningEffort": config.reasoningEffort,
            "aiCategory": case["genreId"],
            "aiCategoryPath": "path",
            "aiCategoryConfidence": "1",
            "aiBrand": case["brand"],
            "aiTitle": "title",
            "categoryDurationS": "0",
            "productDataDurationS": "0",
            "totalDurationS": "0",
            "customerCategoryCheck": "",
            "customerBrandCheck": "",
            "customerNotes": "",
        }

    store.execute_run(run.runId, case_runner=fake_case_runner)

    status = json.loads((run.path / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "completed"
    assert status["completed"] == 1
    assert (run.path / "results.csv").exists()
    summary = json.loads((run.path / "summary.json").read_text(encoding="utf-8"))
    assert summary["overall"]["categoryAccuracy"] == 1.0


def test_read_errors_parses_jsonl_and_skips_bad_lines(tmp_path):
    store = EvaluationRunStore(tmp_path)
    run_dir = tmp_path / "2026-06-11-10-00"
    run_dir.mkdir(parents=True)
    (run_dir / "errors.jsonl").write_text(
        '{"caseIndex": 1, "itemName": "a", "error": "boom"}\n'
        "not-json\n"
        '{"caseIndex": 2, "itemName": "b", "error": "bang"}\n',
        encoding="utf-8",
    )

    errors = store.read_errors("2026-06-11-10-00")

    assert errors == [
        {"caseIndex": 1, "itemName": "a", "error": "boom"},
        {"caseIndex": 2, "itemName": "b", "error": "bang"},
    ]


def test_read_errors_returns_empty_when_file_missing(tmp_path):
    store = EvaluationRunStore(tmp_path)
    run_dir = tmp_path / "2026-06-11-10-01"
    run_dir.mkdir(parents=True)

    assert store.read_errors("2026-06-11-10-01") == []


def test_read_errors_respects_limit(tmp_path):
    store = EvaluationRunStore(tmp_path)
    run_dir = tmp_path / "2026-06-11-10-02"
    run_dir.mkdir(parents=True)
    lines = "".join(f'{{"caseIndex": {i}, "error": "e{i}"}}\n' for i in range(30))
    (run_dir / "errors.jsonl").write_text(lines, encoding="utf-8")

    assert len(store.read_errors("2026-06-11-10-02")) == 20


def test_read_errors_raises_for_missing_run_directory(tmp_path):
    store = EvaluationRunStore(tmp_path)
    with pytest.raises(FileNotFoundError):
        store.read_errors("does-not-exist")


def test_read_run_omits_analysis_field(tmp_path):
    store = EvaluationRunStore(tmp_path)
    input_path = tmp_path / "input.tsv"
    input_path.write_text(
        "itemName\tgenreId\timage\tbrand\nitem\t100\thttp://example.com/a.jpg\tnike\n",
        encoding="utf-8",
    )
    run = store.create_run(
        input_path=input_path,
        config=EvaluationRunConfig(
            visionModel="v", categoryModel="c", productDataModel="p"
        ),
    )

    data = store.read_run(run.runId)

    assert set(data.keys()) == {"run", "status", "summary"}
    assert not hasattr(store, "save_analysis")
