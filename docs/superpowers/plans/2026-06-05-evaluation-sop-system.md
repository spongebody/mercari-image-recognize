# Evaluation SOP System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an MVP evaluation SOP workspace to the existing `/config` Shell page so users can upload test data, run model evaluations, review AI outputs with customer checks, analyze optimization points, and archive each run.

**Architecture:** Keep the existing file-based evaluation outputs under `logs/image_model_tests/<beijing-stamp>/`. Add `app/evaluation/runs.py` as a small run manager around the current `image_model_evaluation` helpers and `MercariAnalyzer`. Add FastAPI endpoints under `/api/v1/evaluations`, then add a third `/config#evaluations` panel that creates runs, polls status, edits review fields, saves analysis notes, and archives runs.

**Tech Stack:** Python 3.11, FastAPI, `ThreadPoolExecutor`, CSV/JSON/Markdown files, vanilla JS in `web/config.html`, existing Shell chrome in `web/assets/shell.js`. Tests run with `.venv/bin/pytest`.

---

## File Structure

- **Create** `app/evaluation/runs.py` — run directory management, status files, execution, review saving, analysis saving, archive locking.
- **Modify** `app/evaluation/image_model_evaluation.py` — add `customerNotes`, reviewed summary metrics, CSV row read helper if needed.
- **Modify** `main.py` — add evaluation executor and `/api/v1/evaluations` endpoints.
- **Modify** `web/config.html` — add `tab-evaluations`, sidebar entry, API calls, polling, result table, review/analysis/archive UI.
- **Create** `tests/test_evaluation_runs.py` — unit tests for run manager and review/summary behavior.
- **Create** `tests/test_evaluations_api.py` — API tests with fake execution.

---

## Task 1: Extend result fields and reviewed summary metrics

**Files:**
- Modify: `app/evaluation/image_model_evaluation.py`
- Modify: `tests/test_image_model_evaluation.py`

- [ ] **Step 1: Write failing tests for `customerNotes` and reviewed summary**

Add this test to `tests/test_image_model_evaluation.py`:

```python
def test_result_fields_include_customer_notes():
    assert "customerNotes" in RESULT_FIELDS
    assert RESULT_FIELDS[-1] == "customerNotes"


def test_summarize_rows_includes_customer_reviewed_accuracy():
    rows = [
        {
            "genreId": "100040",
            "aiCategory": "100040",
            "brand": "ASUS",
            "aiBrand": "ASUS",
            "visionModel": "vision-a",
            "categoryModel": "category-a",
            "productDataModel": "product-a",
            "reasoningEffort": "none",
            "customerCategoryCheck": "",
            "customerBrandCheck": "",
        },
        {
            "genreId": "100181",
            "aiCategory": "565105",
            "brand": "recolte",
            "aiBrand": "",
            "visionModel": "vision-a",
            "categoryModel": "category-a",
            "productDataModel": "product-a",
            "reasoningEffort": "none",
            "customerCategoryCheck": "ACCEPTABLE",
            "customerBrandCheck": "NG",
        },
    ]

    summary = summarize_rows(rows)

    assert summary["overall"]["categoryReviewedCorrect"] == 2
    assert summary["overall"]["brandReviewedCorrect"] == 1
    assert summary["overall"]["categoryReviewedAccuracy"] == 1.0
    assert summary["overall"]["brandReviewedAccuracy"] == 0.5
    assert summary["overall"]["categoryPendingReview"] == 0
    assert summary["overall"]["brandPendingReview"] == 0
```

- [ ] **Step 2: Run the tests and verify failure**

Run:

```bash
.venv/bin/pytest tests/test_image_model_evaluation.py -q
```

Expected: fail because `customerNotes` and reviewed metrics do not exist.

- [ ] **Step 3: Implement the minimal helper changes**

In `RESULT_FIELDS`, append:

```python
"customerNotes",
```

In `build_result_row`, add:

```python
"customerNotes": "",
```

Add helper:

```python
def _review_check_is_positive(value: Any) -> bool:
    return _clean(value).upper() in {"OK", "ACCEPTABLE"}
```

Update `_summary_bucket` so it returns strict and reviewed metrics:

```python
category_reviewed_correct = sum(
    1
    for row in rows
    if _is_category_correct(row) or _review_check_is_positive(row.get("customerCategoryCheck"))
)
brand_reviewed_correct = sum(
    1
    for row in rows
    if _is_brand_correct(row) or _review_check_is_positive(row.get("customerBrandCheck"))
)
category_pending_review = sum(
    1
    for row in rows
    if not _is_category_correct(row) and not _clean(row.get("customerCategoryCheck"))
)
brand_pending_review = sum(
    1
    for row in rows
    if not _is_brand_correct(row) and not _clean(row.get("customerBrandCheck"))
)
```

Include these keys:

```python
"categoryReviewedCorrect": category_reviewed_correct,
"brandReviewedCorrect": brand_reviewed_correct,
"categoryReviewedAccuracy": round(category_reviewed_correct / total, 6) if total else 0.0,
"brandReviewedAccuracy": round(brand_reviewed_correct / total, 6) if total else 0.0,
"categoryPendingReview": category_pending_review,
"brandPendingReview": brand_pending_review,
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/pytest tests/test_image_model_evaluation.py -q
```

Expected: pass.

---

## Task 2: Build the file-based run manager

**Files:**
- Create: `app/evaluation/runs.py`
- Create: `tests/test_evaluation_runs.py`

- [ ] **Step 1: Write failing tests for run creation and status**

Create `tests/test_evaluation_runs.py`:

```python
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from app.evaluation.runs import EvaluationRunConfig, EvaluationRunStore


def _write_input(path: Path) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["itemName", "genreId", "image", "brand"],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerow({
            "itemName": "ASUS laptop",
            "genreId": "100040",
            "image": "https://example.test/01.jpg|https://example.test/02.jpg",
            "brand": "ASUS",
        })


def test_create_run_writes_input_config_and_status(tmp_path):
    source = tmp_path / "source.csv"
    _write_input(source)
    store = EvaluationRunStore(tmp_path / "runs")

    run = store.create_run(
        input_path=source,
        config=EvaluationRunConfig(
            visionModel="vision-a",
            categoryModel="category-a",
            productDataModel="product-a",
            reasoningEffort="none",
            language="ja",
            limit=0,
        ),
    )

    assert (run.path / "input.csv").exists()
    assert json.loads((run.path / "run_config.json").read_text())["visionModel"] == "vision-a"
    assert json.loads((run.path / "status.json").read_text())["status"] == "pending"


def test_create_run_rejects_missing_required_input_columns(tmp_path):
    source = tmp_path / "bad.csv"
    source.write_text("itemName\tgenreId\nx\t1\n", encoding="utf-8")
    store = EvaluationRunStore(tmp_path / "runs")

    with pytest.raises(ValueError, match="missing required columns"):
        store.create_run(
            input_path=source,
            config=EvaluationRunConfig(
                visionModel="vision-a",
                categoryModel="category-a",
                productDataModel="product-a",
                reasoningEffort="none",
                language="ja",
                limit=0,
            ),
        )
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/pytest tests/test_evaluation_runs.py -q
```

Expected: fail because `app.evaluation.runs` does not exist.

- [ ] **Step 3: Implement `runs.py` dataclasses and create_run**

Create `app/evaluation/runs.py` with:

```python
from __future__ import annotations

import csv
import json
import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from app.evaluation.image_model_evaluation import load_cases

BEIJING = ZoneInfo("Asia/Shanghai")
REQUIRED_COLUMNS = ("itemName", "genreId", "image", "brand")


@dataclass(frozen=True)
class EvaluationRunConfig:
    visionModel: str
    categoryModel: str
    productDataModel: str
    reasoningEffort: str = "none"
    language: str = "ja"
    limit: int = 0


@dataclass(frozen=True)
class EvaluationRun:
    runId: str
    path: Path


def beijing_now() -> datetime:
    return datetime.now(timezone.utc).astimezone(BEIJING)


def stamp_for(value: datetime) -> str:
    return value.astimezone(BEIJING).strftime("%Y-%m-%d-%H-%M")


class EvaluationRunStore:
    def __init__(self, root: Path):
        self.root = root

    def _unique_run_path(self) -> EvaluationRun:
        self.root.mkdir(parents=True, exist_ok=True)
        base = stamp_for(beijing_now())
        for index in range(1, 100):
            run_id = base if index == 1 else f"{base}-{index}"
            path = self.root / run_id
            if not path.exists():
                path.mkdir(parents=True)
                return EvaluationRun(runId=run_id, path=path)
        raise RuntimeError("Unable to allocate a unique evaluation run directory.")

    def create_run(self, *, input_path: Path, config: EvaluationRunConfig) -> EvaluationRun:
        load_cases(input_path, limit=1)
        run = self._unique_run_path()
        shutil.copyfile(input_path, run.path / "input.csv")
        created_at = beijing_now().isoformat()
        payload = {"runId": run.runId, **asdict(config), "createdAt": created_at, "archived": False}
        self._write_json(run.path / "run_config.json", payload)
        self._write_json(run.path / "status.json", {
            "runId": run.runId,
            "status": "pending",
            "total": 0,
            "completed": 0,
            "success": 0,
            "failed": 0,
            "createdAt": created_at,
            "updatedAt": created_at,
            "elapsedSeconds": 0,
            "etaSeconds": 0,
            "message": "pending",
        })
        return run

    @staticmethod
    def _write_json(path: Path, value: Dict[str, Any]) -> None:
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/pytest tests/test_evaluation_runs.py -q
```

Expected: pass.

---

## Task 3: Add review save, summary recompute, analysis, archive

**Files:**
- Modify: `app/evaluation/runs.py`
- Modify: `tests/test_evaluation_runs.py`

- [ ] **Step 1: Add failing tests for review and archive**

Append:

```python
from app.evaluation.image_model_evaluation import RESULT_FIELDS, write_result_rows


def test_save_review_updates_results_and_summary(tmp_path):
    store = EvaluationRunStore(tmp_path / "runs")
    source = tmp_path / "source.csv"
    _write_input(source)
    run = store.create_run(
        input_path=source,
        config=EvaluationRunConfig("vision-a", "category-a", "product-a", "none", "ja", 0),
    )
    write_result_rows(run.path / "results.csv", [{
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
    }])

    store.save_review(run.runId, [{
        "rowIndex": 0,
        "customerCategoryCheck": "ACCEPTABLE",
        "customerBrandCheck": "OK",
        "customerNotes": "near category",
    }])

    rows = store.read_results(run.runId)
    assert rows[0]["customerCategoryCheck"] == "ACCEPTABLE"
    assert rows[0]["customerNotes"] == "near category"
    summary = json.loads((run.path / "summary.json").read_text(encoding="utf-8"))
    assert summary["overall"]["categoryReviewedCorrect"] == 1


def test_archive_locks_review(tmp_path):
    store = EvaluationRunStore(tmp_path / "runs")
    source = tmp_path / "source.csv"
    _write_input(source)
    run = store.create_run(
        input_path=source,
        config=EvaluationRunConfig("vision-a", "category-a", "product-a", "none", "ja", 0),
    )
    store.archive(run.runId)

    with pytest.raises(ValueError, match="archived"):
        store.save_review(run.runId, [])
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
.venv/bin/pytest tests/test_evaluation_runs.py -q
```

Expected: fail because methods are missing.

- [ ] **Step 3: Implement methods**

Add methods to `EvaluationRunStore`:

```python
def run_path(self, run_id: str) -> Path:
    path = self.root / run_id
    if not path.exists():
        raise FileNotFoundError(run_id)
    return path

def _read_json(self, path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def _is_archived(self, run_id: str) -> bool:
    path = self.run_path(run_id)
    status = self._read_json(path / "status.json")
    config = self._read_json(path / "run_config.json")
    return status.get("status") == "archived" or bool(config.get("archived"))

def read_results(self, run_id: str) -> List[Dict[str, str]]:
    path = self.run_path(run_id) / "results.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))

def write_results(self, run_id: str, rows: List[Dict[str, str]]) -> None:
    from app.evaluation.image_model_evaluation import RESULT_FIELDS, write_result_rows
    write_result_rows(self.run_path(run_id) / "results.csv", rows)

def save_review(self, run_id: str, updates: List[Dict[str, Any]]) -> Dict[str, Any]:
    if self._is_archived(run_id):
        raise ValueError("Run is archived and cannot be edited.")
    rows = self.read_results(run_id)
    allowed = {"", "OK", "ACCEPTABLE", "NG"}
    for update in updates:
        idx = int(update["rowIndex"])
        if idx < 0 or idx >= len(rows):
            raise ValueError("rowIndex out of range.")
        for key in ("customerCategoryCheck", "customerBrandCheck"):
            value = str(update.get(key, rows[idx].get(key, ""))).strip().upper()
            if value not in allowed:
                raise ValueError(f"{key} must be one of OK, ACCEPTABLE, NG, or empty.")
            rows[idx][key] = value
        rows[idx]["customerNotes"] = str(update.get("customerNotes", rows[idx].get("customerNotes", ""))).strip()
    self.write_results(run_id, rows)
    review_path = self.run_path(run_id) / "customer_review.csv"
    self.write_results_to_path(review_path, rows)
    summary = summarize_rows(rows)
    self._write_json(self.run_path(run_id) / "summary.json", summary)
    return summary
```

Also add:

```python
def write_results_to_path(self, path: Path, rows: List[Dict[str, str]]) -> None:
    from app.evaluation.image_model_evaluation import RESULT_FIELDS
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_FIELDS, delimiter="\t", lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

def save_analysis(self, run_id: str, payload: Dict[str, Any]) -> None:
    if self._is_archived(run_id):
        raise ValueError("Run is archived and cannot be edited.")
    text = (
        "# Evaluation Analysis\n\n"
        "## 可优化点\n\n" + str(payload.get("analysisNotes", "")).strip() + "\n\n"
        "## 优化动作\n\n" + str(payload.get("optimizationActions", "")).strip() + "\n\n"
        "## 下一轮测试建议\n\n" + str(payload.get("nextRunSuggestion", "")).strip() + "\n"
    )
    (self.run_path(run_id) / "analysis.md").write_text(text, encoding="utf-8")

def archive(self, run_id: str) -> Dict[str, Any]:
    path = self.run_path(run_id)
    status = self._read_json(path / "status.json")
    config = self._read_json(path / "run_config.json")
    now = beijing_now().isoformat()
    status.update({"status": "archived", "updatedAt": now, "message": "archived"})
    config["archived"] = True
    config["archivedAt"] = now
    self._write_json(path / "status.json", status)
    self._write_json(path / "run_config.json", config)
    return status
```

Remember to import:

```python
from app.evaluation.image_model_evaluation import summarize_rows
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/pytest tests/test_evaluation_runs.py -q
```

Expected: pass.

---

## Task 4: Execute runs in the backend with progress status

**Files:**
- Modify: `app/evaluation/runs.py`
- Modify: `tests/test_evaluation_runs.py`

- [ ] **Step 1: Add a fake runner test**

Add:

```python
def test_execute_run_updates_status_and_writes_results(tmp_path):
    source = tmp_path / "source.csv"
    _write_input(source)
    store = EvaluationRunStore(tmp_path / "runs")
    run = store.create_run(
        input_path=source,
        config=EvaluationRunConfig("vision-a", "category-a", "product-a", "none", "ja", 0),
    )

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

    status = json.loads((run.path / "status.json").read_text())
    assert status["status"] == "completed"
    assert status["completed"] == 1
    assert (run.path / "results.csv").exists()
    assert json.loads((run.path / "summary.json").read_text())["overall"]["categoryAccuracy"] == 1.0
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
.venv/bin/pytest tests/test_evaluation_runs.py::test_execute_run_updates_status_and_writes_results -q
```

Expected: fail because `execute_run` is missing.

- [ ] **Step 3: Implement `execute_run` with injectable `case_runner`**

Add to `EvaluationRunStore`:

```python
def update_status(self, run_id: str, **updates: Any) -> Dict[str, Any]:
    path = self.run_path(run_id) / "status.json"
    status = self._read_json(path)
    status.update(updates)
    status["updatedAt"] = beijing_now().isoformat()
    self._write_json(path, status)
    return status

def load_config(self, run_id: str) -> EvaluationRunConfig:
    data = self._read_json(self.run_path(run_id) / "run_config.json")
    return EvaluationRunConfig(
        visionModel=data["visionModel"],
        categoryModel=data["categoryModel"],
        productDataModel=data["productDataModel"],
        reasoningEffort=data.get("reasoningEffort", "none"),
        language=data.get("language", "ja"),
        limit=int(data.get("limit", 0) or 0),
    )

def execute_run(self, run_id: str, *, case_runner) -> None:
    config = self.load_config(run_id)
    cases = load_cases(self.run_path(run_id) / "input.csv", limit=config.limit)
    total = len(cases)
    started = time.perf_counter()
    self.update_status(run_id, status="running", total=total, completed=0, success=0, failed=0, message="running")
    rows = []
    errors = []
    for index, case in enumerate(cases, start=1):
        try:
            rows.append(case_runner(case, config))
            success = sum(1 for _ in rows)
        except Exception as exc:
            row = {"itemName": case.get("itemName", ""), "genreId": case.get("genreId", ""), "image": case.get("image", ""), "brand": case.get("brand", "")}
            rows.append(row)
            errors.append({"caseIndex": index, "error": str(exc)})
            success = index - len(errors)
        elapsed = time.perf_counter() - started
        eta = (elapsed / index) * (total - index) if index else 0
        self.update_status(
            run_id,
            completed=index,
            success=success,
            failed=len(errors),
            elapsedSeconds=round(elapsed, 2),
            etaSeconds=round(eta, 2),
            message=f"{index}/{total} completed",
        )
    write_result_rows(self.run_path(run_id) / "results.csv", rows)
    self._write_json(self.run_path(run_id) / "summary.json", summarize_rows(rows))
    if errors:
        with (self.run_path(run_id) / "errors.jsonl").open("w", encoding="utf-8") as f:
            for item in errors:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")
    final_status = "completed" if not errors else "completed"
    self.update_status(run_id, status=final_status, completed=total, message="completed")
```

Add imports:

```python
import time
from app.evaluation.image_model_evaluation import write_result_rows
```

- [ ] **Step 4: Run tests**

Run:

```bash
.venv/bin/pytest tests/test_evaluation_runs.py -q
```

Expected: pass.

---

## Task 5: Add API endpoints

**Files:**
- Modify: `main.py`
- Create: `tests/test_evaluations_api.py`

- [ ] **Step 1: Add API tests with monkeypatched store execution**

Create `tests/test_evaluations_api.py`:

```python
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
```

- [ ] **Step 2: Run and verify failure**

Run:

```bash
.venv/bin/pytest tests/test_evaluations_api.py -q
```

Expected: fail because endpoints do not exist.

- [ ] **Step 3: Add store and executor globals in `main.py`**

Near existing global executors:

```python
from app.evaluation.runs import EvaluationRunConfig, EvaluationRunStore

evaluation_store = EvaluationRunStore(BASE_DIR / "logs" / "image_model_tests")
evaluation_executor = ThreadPoolExecutor(max_workers=1)
```

- [ ] **Step 4: Add a case runner builder in `main.py`**

Add:

```python
def _evaluation_case_runner(case: Dict[str, str], config: EvaluationRunConfig) -> Dict[str, str]:
    from app.evaluation.image_model_evaluation import ModelCombination, build_result_row
    combo = ModelCombination(
        vision_model=config.visionModel,
        category_model=config.categoryModel,
        product_data_model=config.productDataModel,
        reasoning_effort=config.reasoningEffort,
    )
    settings.vision_model = config.visionModel
    settings.category_model = config.categoryModel
    settings.product_data_model = config.productDataModel
    image_payloads = []
    for url in case["image"].split("|"):
        if not url.strip():
            continue
        data, mime = fetch_image_from_url(url.strip(), settings.request_timeout, settings.max_image_bytes, settings.allowed_mime_types)
        processed = compress_image_if_needed(data, mime, settings.image_compression_threshold_bytes)
        image_payloads.append((processed.data, processed.mime_type))
    started = time.monotonic()
    classification = analyzer.classify_first_image_categories(
        image_payloads,
        config.language,
        debug=False,
        vision_model_override=config.visionModel,
        category_model_override=config.categoryModel,
    )
    product_data = analyzer.generate_product_data(
        image_payloads,
        config.language,
        debug=False,
        model_override=config.productDataModel,
    )
    return build_result_row(case, combo, classification, product_data, total_duration_s=time.monotonic() - started)
```

This keeps MVP simple by reusing the global analyzer. A later refactor can instantiate per-run analyzers to isolate config more strictly.

- [ ] **Step 5: Add endpoints**

Add after prompt endpoints:

```python
@app.post("/api/v1/evaluations", dependencies=[Depends(require_logs_auth(settings.logs_password))])
async def create_evaluation(
    request: Request,
    file: UploadFile = File(...),
    visionModel: str = Form(...),
    categoryModel: str = Form(...),
    productDataModel: str = Form(...),
    reasoningEffort: str = Form("none"),
    language: str = Form(DEFAULT_LANGUAGE),
    limit: int = Form(0),
):
    _reject_cross_origin(request)
    tmp_path = BASE_DIR / "logs" / "tmp" / f"evaluation-{uuid.uuid4().hex}.csv"
    tmp_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path.write_bytes(await file.read())
    try:
        run = evaluation_store.create_run(
            input_path=tmp_path,
            config=EvaluationRunConfig(visionModel, categoryModel, productDataModel, reasoningEffort, language, limit),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        with suppress(Exception):
            tmp_path.unlink()
    evaluation_executor.submit(evaluation_store.execute_run, run.runId, case_runner=_evaluation_case_runner)
    return {"runId": run.runId, "status": "pending"}
```

Add read endpoints:

```python
@app.get("/api/v1/evaluations")
def list_evaluations() -> Dict[str, Any]:
    return {"runs": evaluation_store.list_runs()}

@app.get("/api/v1/evaluations/{run_id}")
def read_evaluation(run_id: str) -> Dict[str, Any]:
    return evaluation_store.read_run(run_id)

@app.get("/api/v1/evaluations/{run_id}/results")
def read_evaluation_results(run_id: str) -> Dict[str, Any]:
    return {"rows": evaluation_store.read_results(run_id)}

@app.get("/api/v1/evaluations/{run_id}/results.csv")
def download_evaluation_results(run_id: str):
    return FileResponse(evaluation_store.run_path(run_id) / "results.csv")

@app.put("/api/v1/evaluations/{run_id}/review", dependencies=[Depends(require_logs_auth(settings.logs_password))])
def save_evaluation_review(run_id: str, payload: Dict[str, Any], request: Request):
    _reject_cross_origin(request)
    try:
        return {"summary": evaluation_store.save_review(run_id, payload.get("rows", []))}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.put("/api/v1/evaluations/{run_id}/analysis", dependencies=[Depends(require_logs_auth(settings.logs_password))])
def save_evaluation_analysis(run_id: str, payload: Dict[str, Any], request: Request):
    _reject_cross_origin(request)
    try:
        evaluation_store.save_analysis(run_id, payload)
        return {"ok": True}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@app.post("/api/v1/evaluations/{run_id}/archive", dependencies=[Depends(require_logs_auth(settings.logs_password))])
def archive_evaluation(run_id: str, request: Request):
    _reject_cross_origin(request)
    try:
        return evaluation_store.archive(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

- [ ] **Step 6: Implement missing `list_runs` and `read_run` in store**

Add:

```python
def list_runs(self) -> List[Dict[str, Any]]:
    if not self.root.exists():
        return []
    runs = []
    for path in sorted(self.root.iterdir(), reverse=True):
        if not path.is_dir() or not (path / "run_config.json").exists():
            continue
        config = self._read_json(path / "run_config.json")
        status = self._read_json(path / "status.json") if (path / "status.json").exists() else {}
        runs.append({**config, **status})
    return runs

def read_run(self, run_id: str) -> Dict[str, Any]:
    path = self.run_path(run_id)
    config = self._read_json(path / "run_config.json")
    status = self._read_json(path / "status.json")
    summary = self._read_json(path / "summary.json") if (path / "summary.json").exists() else {}
    return {"run": config, "status": status, "summary": summary}
```

- [ ] **Step 7: Run API tests**

Run:

```bash
.venv/bin/pytest tests/test_evaluations_api.py -q
```

Expected: pass.

---

## Task 6: Add the `/config#evaluations` panel

**Files:**
- Modify: `web/config.html`

- [ ] **Step 1: Add the panel markup**

After `tab-prompts`, add:

```html
<div class="tab-panel" id="tab-evaluations" hidden>
  <div class="card grid">
    <h2>创建测试</h2>
    <div class="controls-row">
      <div>
        <label for="eval-file">测试数据</label>
        <input id="eval-file" type="file" accept=".csv,.tsv,text/csv,text/tab-separated-values" />
      </div>
      <div>
        <label for="eval-limit">测试条数</label>
        <input id="eval-limit" type="number" min="0" step="1" value="0" />
        <div class="hint">0 表示全部数据。</div>
      </div>
    </div>
    <div class="controls-row">
      <div><label for="eval-vision-model">visionModel</label><input id="eval-vision-model" type="text" /></div>
      <div><label for="eval-category-model">categoryModel</label><input id="eval-category-model" type="text" /></div>
      <div><label for="eval-product-model">productDataModel</label><input id="eval-product-model" type="text" /></div>
    </div>
    <div class="controls-row">
      <div>
        <label for="eval-reasoning">reasoningEffort</label>
        <select id="eval-reasoning">
          <option value="none">none</option>
          <option value="minimal">minimal</option>
          <option value="low">low</option>
          <option value="medium">medium</option>
          <option value="high">high</option>
        </select>
      </div>
      <div><label for="eval-language">language</label><input id="eval-language" type="text" value="ja" /></div>
    </div>
    <div class="message" id="eval-message"></div>
    <button id="eval-start-btn" type="button">开始测试</button>
  </div>

  <div class="card grid" style="margin-top:16px">
    <h2>历史与进度</h2>
    <div id="eval-runs">正在读取...</div>
    <div id="eval-current"></div>
  </div>

  <div class="card grid" style="margin-top:16px">
    <h2>客服校验</h2>
    <div class="actions">
      <button id="eval-save-review-btn" type="button">保存校验</button>
      <button class="secondary" id="eval-download-btn" type="button">下载结果 CSV</button>
    </div>
    <div id="eval-results"></div>
  </div>

  <div class="card grid" style="margin-top:16px">
    <h2>分析与归档</h2>
    <textarea id="eval-analysis-notes" rows="5" placeholder="可优化点"></textarea>
    <textarea id="eval-actions" rows="5" placeholder="优化动作"></textarea>
    <textarea id="eval-next" rows="4" placeholder="下一轮测试建议"></textarea>
    <div class="actions">
      <button id="eval-save-analysis-btn" type="button">保存分析</button>
      <button class="secondary" id="eval-archive-btn" type="button">归档</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add the route**

Change:

```javascript
const tabPanels = {
  api: document.getElementById("tab-api"),
  prompts: document.getElementById("tab-prompts"),
};
```

to:

```javascript
const tabPanels = {
  api: document.getElementById("tab-api"),
  prompts: document.getElementById("tab-prompts"),
  evaluations: document.getElementById("tab-evaluations"),
};
```

Update `showPanel` title:

```javascript
const titles = { api: "API 配置", prompts: "提示词配置", evaluations: "测试评估" };
Shell.setHeader({ title: titles[key] || "API 配置", crumb: statusText ? statusText.textContent : "" });
```

Update sidebar:

```javascript
sidebar: () => [
  { id: "api", label: "API 配置" },
  { id: "prompts", label: "提示词配置" },
  { id: "evaluations", label: "测试评估" },
],
```

- [ ] **Step 3: Add frontend state and API calls**

Add JS after prompts block:

```javascript
let currentEvaluationRunId = "";
let evaluationPollTimer = null;

function showEvalMessage(text, type) {
  const el = document.getElementById("eval-message");
  el.textContent = text;
  el.className = `message show ${type}`;
}

function setEvaluationDefaults(config) {
  document.getElementById("eval-vision-model").value = config.VISION_MODEL || "";
  document.getElementById("eval-category-model").value = config.CATEGORY_MODEL || "";
  document.getElementById("eval-product-model").value = config.PRODUCT_DATA_MODEL || "";
}
```

Call `setEvaluationDefaults(data)` in `loadConfig()` after `setFormValues(data)`.

Add create:

```javascript
async function startEvaluation() {
  const file = document.getElementById("eval-file").files[0];
  if (!file) return showEvalMessage("请选择测试数据文件。", "error");
  const form = new FormData();
  form.append("file", file);
  form.append("visionModel", document.getElementById("eval-vision-model").value.trim());
  form.append("categoryModel", document.getElementById("eval-category-model").value.trim());
  form.append("productDataModel", document.getElementById("eval-product-model").value.trim());
  form.append("reasoningEffort", document.getElementById("eval-reasoning").value);
  form.append("language", document.getElementById("eval-language").value.trim() || "ja");
  form.append("limit", document.getElementById("eval-limit").value || "0");
  const resp = await fetch("/api/v1/evaluations", { method: "POST", body: form });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || "创建测试失败");
  currentEvaluationRunId = data.runId;
  showEvalMessage(`测试已创建：${data.runId}`, "success");
  await loadEvaluations();
  startEvaluationPolling(data.runId);
}
```

Bind:

```javascript
document.getElementById("eval-start-btn").addEventListener("click", () => {
  startEvaluation().catch((err) => showEvalMessage(String(err.message || err), "error"));
});
```

- [ ] **Step 4: Add run list, polling, results rendering, review save, analysis save, archive**

Use compact DOM rendering:

```javascript
async function loadEvaluations() {
  const resp = await fetch("/api/v1/evaluations");
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || "读取测试列表失败");
  const host = document.getElementById("eval-runs");
  host.innerHTML = "";
  data.runs.forEach((run) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "secondary";
    btn.textContent = `${run.runId} · ${run.status || "unknown"} · ${run.visionModel || ""}`;
    btn.addEventListener("click", () => selectEvaluationRun(run.runId));
    host.appendChild(btn);
  });
}

async function selectEvaluationRun(runId) {
  currentEvaluationRunId = runId;
  const resp = await fetch(`/api/v1/evaluations/${encodeURIComponent(runId)}`);
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || "读取测试失败");
  renderEvaluationStatus(data);
  if (data.status.status === "running" || data.status.status === "pending") {
    startEvaluationPolling(runId);
  } else {
    stopEvaluationPolling();
    await loadEvaluationResults(runId);
  }
}

function renderEvaluationStatus(data) {
  document.getElementById("eval-current").innerHTML =
    `<div class="hint">状态：${data.status.status} · ${data.status.completed || 0}/${data.status.total || 0} · elapsed ${data.status.elapsedSeconds || 0}s · eta ${data.status.etaSeconds || 0}s</div>`;
}
```

Render results:

```javascript
async function loadEvaluationResults(runId) {
  const resp = await fetch(`/api/v1/evaluations/${encodeURIComponent(runId)}/results`);
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || "读取结果失败");
  const host = document.getElementById("eval-results");
  const rows = data.rows || [];
  host.innerHTML = `<table class="result-table"><thead><tr>
    <th>#</th><th>原分类</th><th>AI分类</th><th>AI路径</th><th>原品牌</th><th>AI品牌</th><th>分类校验</th><th>品牌校验</th><th>备注</th>
  </tr></thead><tbody></tbody></table>`;
  const tbody = host.querySelector("tbody");
  rows.forEach((row, index) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>${index + 1}</td>
      <td>${escapeHtml(row.genreId)}</td>
      <td>${escapeHtml(row.aiCategory)}</td>
      <td>${escapeHtml(row.aiCategoryPath)}</td>
      <td>${escapeHtml(row.brand)}</td>
      <td>${escapeHtml(row.aiBrand)}</td>
      <td><select data-row="${index}" data-field="customerCategoryCheck"><option></option><option>OK</option><option>ACCEPTABLE</option><option>NG</option></select></td>
      <td><select data-row="${index}" data-field="customerBrandCheck"><option></option><option>OK</option><option>ACCEPTABLE</option><option>NG</option></select></td>
      <td><input type="text" data-row="${index}" data-field="customerNotes" /></td>`;
    tbody.appendChild(tr);
    tr.querySelector('[data-field="customerCategoryCheck"]').value = row.customerCategoryCheck || "";
    tr.querySelector('[data-field="customerBrandCheck"]').value = row.customerBrandCheck || "";
    tr.querySelector('[data-field="customerNotes"]').value = row.customerNotes || "";
  });
}
```

Save review:

```javascript
async function saveEvaluationReview() {
  if (!currentEvaluationRunId) return;
  const updates = {};
  document.querySelectorAll("#eval-results [data-row]").forEach((el) => {
    const row = Number(el.getAttribute("data-row"));
    const field = el.getAttribute("data-field");
    updates[row] = updates[row] || { rowIndex: row };
    updates[row][field] = el.value;
  });
  const resp = await fetch(`/api/v1/evaluations/${encodeURIComponent(currentEvaluationRunId)}/review`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rows: Object.values(updates) }),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.detail || "保存校验失败");
  showEvalMessage("客服校验已保存。", "success");
  await selectEvaluationRun(currentEvaluationRunId);
}
```

Bind buttons similarly for download, save analysis, and archive.

- [ ] **Step 5: Add basic result table CSS**

Inside `<style>`:

```css
.result-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.result-table th, .result-table td { border-bottom: 1px solid var(--border); padding: 8px; vertical-align: top; }
.result-table th { color: var(--muted); text-align: left; font-weight: 700; background: #f8fafc; }
.result-table select, .result-table input { min-width: 120px; padding: 8px; border-width: 1px; border-radius: 8px; }
```

- [ ] **Step 6: Manual smoke**

Run server:

```bash
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Open `/config#evaluations`, verify the tab renders.

---

## Task 7: End-to-end verification

**Files:**
- No new files unless fixes are needed.

- [ ] **Step 1: Run focused tests**

Run:

```bash
.venv/bin/pytest tests/test_image_model_evaluation.py tests/test_evaluation_runs.py tests/test_evaluations_api.py -q
```

Expected: all pass.

- [ ] **Step 2: Run existing config/prompt tests**

Run:

```bash
.venv/bin/pytest tests/test_config_api.py tests/test_prompts_api.py tests/test_prompt_store.py -q
```

Expected: all pass.

- [ ] **Step 3: Run a real one-row evaluation from the UI or API**

Use the existing test set and `limit=1`. Expected:

- status reaches `completed`
- `results.csv` exists
- `summary.json` exists
- `/api/v1/evaluations/{run_id}/results` returns one row

- [ ] **Step 4: Save review and archive**

Expected:

- review changes persist in `results.csv`
- summary reviewed accuracy changes
- archive locks the run
- later review save returns HTTP 400

---

## Self-Review Notes

- The plan keeps MVP file-based and avoids DB/queue complexity.
- The UI integrates with existing Shell route structure instead of creating a new page.
- The first backend tests use an injectable fake `case_runner`, so no OpenRouter calls are needed.
- Real model execution is still covered by one-row manual verification.
- No placeholder implementation steps remain; each task has concrete files, commands, and expected behavior.
