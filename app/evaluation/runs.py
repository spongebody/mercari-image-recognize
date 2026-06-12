from __future__ import annotations

import csv
import json
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List
from zoneinfo import ZoneInfo

from app.evaluation.image_model_evaluation import (
    RESULT_FIELDS,
    load_cases,
    summarize_rows,
    write_result_rows,
)

BEIJING = ZoneInfo("Asia/Shanghai")
REVIEW_VALUES = {"", "OK", "ACCEPTABLE", "NG"}


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
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
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
        self._write_json(
            run.path / "run_config.json",
            {
                "runId": run.runId,
                **asdict(config),
                "createdAt": created_at,
                "archived": False,
            },
        )
        self._write_json(
            run.path / "status.json",
            {
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
            },
        )
        return run

    def run_path(self, run_id: str) -> Path:
        path = self.root / run_id
        # Reject traversal ("..") and empty ids before anything reads or, worse,
        # deletes the resolved path. Treat them as not found.
        resolved = path.resolve()
        root = self.root.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise FileNotFoundError(run_id)
        if not path.exists():
            raise FileNotFoundError(run_id)
        return path

    def list_runs(self) -> List[Dict[str, Any]]:
        if not self.root.exists():
            return []
        runs: List[Dict[str, Any]] = []
        for path in sorted(self.root.iterdir(), reverse=True):
            if not path.is_dir() or not (path / "run_config.json").exists():
                continue
            config = self._read_json(path / "run_config.json")
            status = (
                self._read_json(path / "status.json")
                if (path / "status.json").exists()
                else {}
            )
            runs.append({**config, **status})
        return runs

    def read_run(self, run_id: str) -> Dict[str, Any]:
        path = self.run_path(run_id)
        summary = (
            self._read_json(path / "summary.json")
            if (path / "summary.json").exists()
            else {}
        )
        return {
            "run": self._read_json(path / "run_config.json"),
            "status": self._read_json(path / "status.json"),
            "summary": summary,
        }

    def load_config(self, run_id: str) -> EvaluationRunConfig:
        data = self._read_json(self.run_path(run_id) / "run_config.json")
        return EvaluationRunConfig(
            visionModel=str(data["visionModel"]),
            categoryModel=str(data["categoryModel"]),
            productDataModel=str(data["productDataModel"]),
            reasoningEffort=str(data.get("reasoningEffort") or "none"),
            language=str(data.get("language") or "ja"),
            limit=int(data.get("limit") or 0),
        )

    def update_status(self, run_id: str, **updates: Any) -> Dict[str, Any]:
        path = self.run_path(run_id) / "status.json"
        status = self._read_json(path)
        status.update(updates)
        status["updatedAt"] = beijing_now().isoformat()
        self._write_json(path, status)
        return status

    def execute_run(
        self,
        run_id: str,
        *,
        case_runner: Callable[[Dict[str, str], EvaluationRunConfig], Dict[str, str]],
    ) -> None:
        config = self.load_config(run_id)
        cases = load_cases(self.run_path(run_id) / "input.csv", limit=config.limit)
        total = len(cases)
        started = time.perf_counter()
        rows: List[Dict[str, str]] = []
        errors: List[Dict[str, Any]] = []
        self.update_status(
            run_id,
            status="running",
            total=total,
            completed=0,
            success=0,
            failed=0,
            elapsedSeconds=0,
            etaSeconds=0,
            message="running",
        )
        for index, case in enumerate(cases, start=1):
            try:
                rows.append(case_runner(case, config))
            except Exception as exc:  # noqa: BLE001 - keep the run alive
                rows.append(self._empty_error_row(case, config))
                errors.append({"caseIndex": index, "itemName": case.get("itemName", ""), "error": str(exc)})
            elapsed = time.perf_counter() - started
            eta = (elapsed / index) * (total - index) if index else 0
            self.update_status(
                run_id,
                completed=index,
                success=index - len(errors),
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
        self.update_status(
            run_id,
            status="completed",
            completed=total,
            success=total - len(errors),
            failed=len(errors),
            elapsedSeconds=round(time.perf_counter() - started, 2),
            etaSeconds=0,
            message="completed",
        )

    def import_results(self, results_path: Path) -> EvaluationRun:
        """Register an externally produced results.csv as a completed run."""
        with results_path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            fieldnames = reader.fieldnames or []
            missing = [
                field
                for field in ("itemName", "genreId", "image", "brand", "aiCategory")
                if field not in fieldnames
            ]
            if missing:
                raise ValueError(
                    f"results file missing required columns: {', '.join(missing)}"
                )
            rows = [row for row in reader if str(row.get("itemName") or "").strip()]
        if not rows:
            raise ValueError("results file contains no data rows.")

        run = self._unique_run_path()
        write_result_rows(run.path / "results.csv", rows)
        self._write_json(run.path / "summary.json", summarize_rows(rows))
        first = rows[0]
        created_at = beijing_now().isoformat()
        self._write_json(
            run.path / "run_config.json",
            {
                "runId": run.runId,
                "visionModel": str(first.get("visionModel") or ""),
                "categoryModel": str(first.get("categoryModel") or ""),
                "productDataModel": str(first.get("productDataModel") or ""),
                "reasoningEffort": str(first.get("reasoningEffort") or "none"),
                "language": "ja",
                "limit": 0,
                "createdAt": created_at,
                "archived": False,
                "imported": True,
            },
        )
        self._write_json(
            run.path / "status.json",
            {
                "runId": run.runId,
                "status": "completed",
                "total": len(rows),
                "completed": len(rows),
                "success": len(rows),
                "failed": 0,
                "createdAt": created_at,
                "updatedAt": created_at,
                "elapsedSeconds": 0,
                "etaSeconds": 0,
                "message": "imported",
            },
        )
        return run

    def delete_run(self, run_id: str) -> None:
        shutil.rmtree(self.run_path(run_id))

    def read_results(self, run_id: str) -> List[Dict[str, str]]:
        path = self.run_path(run_id) / "results.csv"
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f, delimiter="\t"))

    def read_errors(self, run_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        path = self.run_path(run_id) / "errors.jsonl"
        if not path.exists():
            return []
        errors: List[Dict[str, Any]] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    errors.append(json.loads(line))
                except json.JSONDecodeError:
                    continue  # keep serving partial diagnostics over failing hard
                if len(errors) >= limit:
                    break
        return errors

    def write_results_to_path(self, path: Path, rows: List[Dict[str, str]]) -> None:
        with path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=RESULT_FIELDS,
                delimiter="\t",
                lineterminator="\n",
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)

    def save_review(self, run_id: str, updates: List[Dict[str, Any]]) -> Dict[str, Any]:
        if self._is_archived(run_id):
            raise ValueError("Run is archived and cannot be edited.")
        rows = self.read_results(run_id)
        for update in updates:
            idx = int(update["rowIndex"])
            if idx < 0 or idx >= len(rows):
                raise ValueError("rowIndex out of range.")
            for key in ("customerCategoryCheck", "customerBrandCheck"):
                value = str(update.get(key, rows[idx].get(key, ""))).strip().upper()
                if value not in REVIEW_VALUES:
                    raise ValueError(f"{key} must be one of OK, ACCEPTABLE, NG, or empty.")
                rows[idx][key] = value
            rows[idx]["customerNotes"] = str(
                update.get("customerNotes", rows[idx].get("customerNotes", ""))
            ).strip()
        write_result_rows(self.run_path(run_id) / "results.csv", rows)
        self.write_results_to_path(self.run_path(run_id) / "customer_review.csv", rows)
        summary = summarize_rows(rows)
        self._write_json(self.run_path(run_id) / "summary.json", summary)
        return summary

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

    def _is_archived(self, run_id: str) -> bool:
        path = self.run_path(run_id)
        status = self._read_json(path / "status.json")
        config = self._read_json(path / "run_config.json")
        return status.get("status") == "archived" or bool(config.get("archived"))

    @staticmethod
    def _empty_error_row(case: Dict[str, str], config: EvaluationRunConfig) -> Dict[str, str]:
        return {
            "itemName": case.get("itemName", ""),
            "genreId": case.get("genreId", ""),
            "image": case.get("image", ""),
            "brand": case.get("brand", ""),
            "visionModel": config.visionModel,
            "categoryModel": config.categoryModel,
            "productDataModel": config.productDataModel,
            "reasoningEffort": config.reasoningEffort,
            "aiCategory": "",
            "aiCategoryPath": "",
            "aiCategoryConfidence": "",
            "aiBrand": "",
            "aiTitle": "",
            "categoryDurationS": "",
            "productDataDurationS": "",
            "totalDurationS": "",
            "customerCategoryCheck": "",
            "customerBrandCheck": "",
            "customerNotes": "",
        }

    @staticmethod
    def _write_json(path: Path, value: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))
