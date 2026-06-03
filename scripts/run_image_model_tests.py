#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple
from zoneinfo import ZoneInfo

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.config import load_settings
from app.data.brands import BrandStore
from app.data.categories import CategoryStore
from app.evaluation.image_model_evaluation import (
    ModelCombination,
    build_model_combinations,
    build_result_row,
    image_urls_from_case,
    load_cases,
    load_model_combinations_from_json,
    split_csv_arg,
    summarize_rows,
    write_result_rows,
    write_summary,
)
from app.errors import LLMAllAttemptsFailedError
from app.image_processing import compress_image_if_needed
from app.llm.client import OpenRouterClient
from app.service import MercariAnalyzer
from app.utils import fetch_image_from_url


DEFAULT_INPUT = ROOT_DIR / "data" / "test" / "image_recognition_testset_2026-05-30.csv"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "logs" / "image_model_tests"
OUTPUT_TIMEZONE = ZoneInfo("Asia/Shanghai")
VALID_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh"}


def format_output_stamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%d-%H-%M")


def output_stamp_from_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return format_output_stamp(value.astimezone(OUTPUT_TIMEZONE))


def _stamp() -> str:
    return output_stamp_from_utc(datetime.now(timezone.utc))


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    remainder = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m{remainder:02d}s"
    hours = minutes // 60
    minutes = minutes % 60
    return f"{hours}h{minutes:02d}m{remainder:02d}s"


def format_progress_message(
    *,
    task_index: int,
    total_tasks: int,
    status: str,
    model: str,
    reasoning_effort: str,
    elapsed_s: float,
) -> str:
    if task_index > 0 and total_tasks > 0:
        avg_s = elapsed_s / task_index
        eta_s = max(0.0, avg_s * (total_tasks - task_index))
    else:
        eta_s = 0.0
    return (
        f"[{task_index}/{total_tasks}] {status} {model} / {reasoning_effort}"
        f" | elapsed {_format_duration(elapsed_s)}"
        f" | eta {_format_duration(eta_s)}"
    )


def _progress(message: str) -> None:
    print(message, flush=True)


def _resolve_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT_DIR / path


def _settings_for_combo(combo: ModelCombination, max_retries: int) -> Any:
    settings = load_settings()
    settings.vision_model = combo.vision_model
    settings.category_model = combo.category_model
    settings.product_data_model = combo.product_data_model
    settings.vision_fallback_models = []
    settings.category_fallback_models = []
    settings.product_data_fallback_models = []
    settings.product_data_fallback_model = ""
    settings.model_call_max_retries = max(0, max_retries)

    effort = (combo.reasoning_effort or "none").strip().lower()
    if effort == "none":
        settings.reasoning_enabled = False
        settings.reasoning_effort = None
        settings.reasoning_max_tokens = None
        settings.reasoning_summary = None
        settings.classification_reasoning_enabled = True
    else:
        settings.reasoning_enabled = True
        settings.reasoning_effort = effort
        settings.classification_reasoning_enabled = True
    return settings


def _build_analyzer(combo: ModelCombination, max_retries: int) -> MercariAnalyzer:
    settings = _settings_for_combo(combo, max_retries=max_retries)
    brand_store = BrandStore(str(_resolve_path(settings.brand_csv_path)))
    category_store = CategoryStore(str(_resolve_path(settings.category_csv_path)))
    vision_client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        timeout=settings.request_timeout,
        referer=settings.openrouter_referer,
        app_name=settings.openrouter_app_name,
        reasoning=settings.reasoning,
    )
    category_client = OpenRouterClient(
        api_key=settings.openrouter_api_key,
        base_url=settings.openrouter_base_url,
        timeout=settings.request_timeout,
        referer=settings.openrouter_referer,
        app_name=settings.openrouter_app_name,
        reasoning=settings.reasoning,
    )
    return MercariAnalyzer(
        settings=settings,
        brand_store=brand_store,
        category_store=category_store,
        vision_client=vision_client,
        category_client=category_client,
    )


def _download_case_images(
    case: Dict[str, str],
    *,
    timeout: int,
    max_bytes: int,
    threshold_bytes: int,
    allowed_mime_types: Sequence[str],
) -> List[Tuple[bytes, str]]:
    images: List[Tuple[bytes, str]] = []
    for url in image_urls_from_case(case):
        data, mime_type = fetch_image_from_url(
            url,
            timeout=timeout,
            max_bytes=max_bytes,
            allowed_mime_types=allowed_mime_types,
        )
        processed = compress_image_if_needed(data, mime_type, threshold_bytes)
        images.append((processed.data, processed.mime_type))
    if not images:
        raise ValueError("case has no image URLs.")
    return images


def _run_case(
    analyzer: MercariAnalyzer,
    combo: ModelCombination,
    case: Dict[str, str],
    images: List[Tuple[bytes, str]],
    *,
    language: str,
    debug: bool,
) -> Dict[str, str]:
    started = time.perf_counter()
    classification = analyzer.classify_first_image_categories(
        images=images,
        language=language,
        debug=debug,
        vision_model_override=combo.vision_model,
        category_model_override=combo.category_model,
    )
    product_data = analyzer.generate_product_data(
        images=images,
        language=language,
        debug=debug,
        model_override=combo.product_data_model,
        use_fallback_prompt=False,
    )
    total_duration_s = time.perf_counter() - started
    return build_result_row(
        case,
        combo,
        classification,
        product_data,
        total_duration_s=total_duration_s,
    )


def _dry_run_case(combo: ModelCombination, case: Dict[str, str]) -> Dict[str, str]:
    classification = {
        "best_category_id": case.get("genreId", ""),
        "timings": {"classification_ms": 0.0},
    }
    product_data = {
        "title": case.get("itemName", ""),
        "brand_name": case.get("brand", ""),
        "timings": {"product_data_ms": 0.0},
    }
    return build_result_row(case, combo, classification, product_data, total_duration_s=0.0)


def _load_combos(args: argparse.Namespace) -> List[ModelCombination]:
    if args.model_configs:
        return load_model_combinations_from_json(_resolve_path(args.model_configs))

    combos = build_model_combinations(
        vision_models=split_csv_arg(args.vision_models),
        category_models=split_csv_arg(args.category_models),
        product_data_models=split_csv_arg(args.product_data_models),
        reasoning_efforts=split_csv_arg(args.reasoning_efforts),
    )
    if not combos:
        raise ValueError("no model combinations configured.")
    for combo in combos:
        effort = combo.reasoning_effort.lower()
        if effort not in VALID_REASONING_EFFORTS:
            raise ValueError(
                f"unsupported reasoning effort '{combo.reasoning_effort}'. "
                f"Allowed: {', '.join(sorted(VALID_REASONING_EFFORTS))}"
            )
    return combos


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run image recognition model evaluation on a TSV test set."
    )
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="TSV/CSV test set path.")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Output directory.")
    parser.add_argument("--language", default="ja", help="Recognition language.")
    parser.add_argument("--limit", type=int, default=0, help="Limit cases; 0 means all.")
    parser.add_argument("--download-timeout", type=int, default=30, help="Image download timeout seconds.")
    parser.add_argument("--max-retries", type=int, default=0, help="Retries per stage for the same model.")
    parser.add_argument("--debug", action="store_true", help="Request model debug payloads internally.")
    parser.add_argument("--dry-run", action="store_true", help="Do not call models; echo source labels.")
    parser.add_argument(
        "--model-configs",
        default="",
        help=(
            "Optional JSON list. Each item has visionModel, categoryModel, "
            "productDataModel, reasoningEffort."
        ),
    )
    parser.add_argument("--vision-models", default="", help="Comma-separated vision models.")
    parser.add_argument("--category-models", default="", help="Comma-separated category models.")
    parser.add_argument("--product-data-models", default="", help="Comma-separated product data models.")
    parser.add_argument(
        "--reasoning-efforts",
        default="none",
        help="Comma-separated reasoning efforts: none,minimal,low,medium,high,xhigh.",
    )
    args = parser.parse_args(argv)

    input_path = _resolve_path(args.input)
    output_dir = _resolve_path(args.output_dir) / _stamp()
    result_path = output_dir / "results.csv"
    summary_path = output_dir / "summary.json"
    error_path = output_dir / "errors.jsonl"

    combos = _load_combos(args)
    cases = load_cases(input_path, limit=args.limit)
    if not cases:
        print(f"[error] no cases loaded from {input_path}", file=sys.stderr)
        return 1

    base_settings = load_settings()
    if not args.dry_run and not base_settings.openrouter_api_key:
        print("[error] OPENROUTER_API_KEY is not configured.", file=sys.stderr)
        return 1

    rows: List[Dict[str, str]] = []
    errors: List[Dict[str, Any]] = []
    image_cache: Dict[str, List[Tuple[bytes, str]]] = {}
    analyzers = {combo: _build_analyzer(combo, max_retries=args.max_retries) for combo in combos}

    started = time.perf_counter()
    total_tasks = len(cases) * len(combos)
    _progress(
        f"Starting {total_tasks} task(s), {len(cases)} case(s), "
        f"{len(combos)} model combination(s)."
    )
    _progress(f"Results will be written under: {output_dir}")
    task_index = 0
    for case_index, case in enumerate(cases, start=1):
        cache_key = case.get("image", "")
        images: List[Tuple[bytes, str]] = []
        if not args.dry_run:
            try:
                if cache_key not in image_cache:
                    image_cache[cache_key] = _download_case_images(
                        case,
                        timeout=args.download_timeout,
                        max_bytes=base_settings.max_image_bytes,
                        threshold_bytes=base_settings.image_compression_threshold_bytes,
                        allowed_mime_types=tuple(base_settings.allowed_mime_types),
                    )
                images = image_cache[cache_key]
            except Exception as exc:
                for combo in combos:
                    rows.append(build_result_row(case, combo))
                    errors.append(
                        {
                            "caseIndex": case_index,
                            "itemName": case.get("itemName", ""),
                            **combo.as_row_fields(),
                            "stage": "download",
                            "error": str(exc),
                        }
                )
                _progress(
                    f"[case {case_index}/{len(cases)}] download failed: "
                    f"{case.get('itemName', '')}"
                )
                continue

        for combo in combos:
            task_index += 1
            row_started = time.perf_counter()
            try:
                if args.dry_run:
                    row = _dry_run_case(combo, case)
                else:
                    row = _run_case(
                        analyzers[combo],
                        combo,
                        case,
                        images,
                        language=args.language,
                        debug=args.debug,
                    )
                rows.append(row)
                _progress(
                    format_progress_message(
                        task_index=task_index,
                        total_tasks=total_tasks,
                        status="ok",
                        model=combo.vision_model,
                        reasoning_effort=combo.reasoning_effort,
                        elapsed_s=time.perf_counter() - started,
                    )
                )
            except LLMAllAttemptsFailedError as exc:
                duration_s = time.perf_counter() - row_started
                rows.append(build_result_row(case, combo, total_duration_s=duration_s))
                errors.append(
                    {
                        "caseIndex": case_index,
                        "itemName": case.get("itemName", ""),
                        **combo.as_row_fields(),
                        "stage": exc.stage,
                        "attempts": [attempt.__dict__ for attempt in exc.attempts],
                    }
                )
                _progress(
                    format_progress_message(
                        task_index=task_index,
                        total_tasks=total_tasks,
                        status=f"failed:{exc.stage}",
                        model=combo.vision_model,
                        reasoning_effort=combo.reasoning_effort,
                        elapsed_s=time.perf_counter() - started,
                    )
                )
            except Exception as exc:
                duration_s = time.perf_counter() - row_started
                rows.append(build_result_row(case, combo, total_duration_s=duration_s))
                errors.append(
                    {
                        "caseIndex": case_index,
                        "itemName": case.get("itemName", ""),
                        **combo.as_row_fields(),
                        "stage": "unknown",
                        "error": str(exc),
                    }
                )
                _progress(
                    format_progress_message(
                        task_index=task_index,
                        total_tasks=total_tasks,
                        status=f"failed:{exc}",
                        model=combo.vision_model,
                        reasoning_effort=combo.reasoning_effort,
                        elapsed_s=time.perf_counter() - started,
                    )
                )

    summary = summarize_rows(rows)
    summary["input"] = str(input_path)
    summary["resultFile"] = str(result_path)
    summary["errorFile"] = str(error_path) if errors else ""
    summary["caseCount"] = len(cases)
    summary["modelCombinationCount"] = len(combos)
    summary["rowCount"] = len(rows)
    summary["errorCount"] = len(errors)
    summary["elapsedSeconds"] = round(time.perf_counter() - started, 2)

    write_result_rows(result_path, rows)
    write_summary(summary_path, summary)
    if errors:
        error_path.parent.mkdir(parents=True, exist_ok=True)
        with error_path.open("w", encoding="utf-8") as f:
            for item in errors:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

    _progress(f"\nResults: {result_path}")
    _progress(f"Summary: {summary_path}")
    if errors:
        _progress(f"Errors:  {error_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
