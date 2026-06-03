from __future__ import annotations

import csv
import itertools
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.utils import compress_whitespace


RESULT_FIELDS = [
    "itemName",
    "genreId",
    "image",
    "brand",
    "visionModel",
    "categoryModel",
    "productDataModel",
    "reasoningEffort",
    "aiCategory",
    "aiCategoryPath",
    "aiCategoryConfidence",
    "aiBrand",
    "aiTitle",
    "categoryDurationS",
    "productDataDurationS",
    "totalDurationS",
    "customerCategoryCheck",
    "customerBrandCheck",
]

MODEL_DIMENSIONS = (
    "visionModel",
    "categoryModel",
    "productDataModel",
    "reasoningEffort",
)


@dataclass(frozen=True)
class ModelCombination:
    vision_model: str
    category_model: str
    product_data_model: str
    reasoning_effort: str = "none"

    def as_row_fields(self) -> Dict[str, str]:
        return {
            "visionModel": self.vision_model,
            "categoryModel": self.category_model,
            "productDataModel": self.product_data_model,
            "reasoningEffort": self.reasoning_effort,
        }


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return compress_whitespace(str(value))


def _format_seconds(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number < 0:
        return ""
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _format_score(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{number:.3f}".rstrip("0").rstrip(".")


def _milliseconds_to_seconds(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value) / 1000
    except (TypeError, ValueError):
        return None


def normalize_brand_for_compare(value: Any) -> str:
    text = unicodedata.normalize("NFKC", _clean(value))
    text = text.replace("\u00ae", "").replace("\u2122", "").replace("\u00a9", "")
    return "".join(ch for ch in text.lower() if ch.isalnum())


def split_csv_arg(raw: str) -> List[str]:
    return [_clean(item) for item in raw.split(",") if _clean(item)]


def build_model_combinations(
    *,
    vision_models: Sequence[str],
    category_models: Sequence[str],
    product_data_models: Sequence[str],
    reasoning_efforts: Sequence[str],
) -> List[ModelCombination]:
    return [
        ModelCombination(
            vision_model=vision_model,
            category_model=category_model,
            product_data_model=product_data_model,
            reasoning_effort=reasoning_effort or "none",
        )
        for vision_model, category_model, product_data_model, reasoning_effort in itertools.product(
            vision_models,
            category_models,
            product_data_models,
            reasoning_efforts or ["none"],
        )
    ]


def load_model_combinations_from_json(path: Path) -> List[ModelCombination]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("model config JSON must be a list of objects.")

    combos: List[ModelCombination] = []
    for index, item in enumerate(payload, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"model config item #{index} must be an object.")
        combo = ModelCombination(
            vision_model=_clean(item.get("visionModel") or item.get("vision_model")),
            category_model=_clean(item.get("categoryModel") or item.get("category_model")),
            product_data_model=_clean(
                item.get("productDataModel") or item.get("product_data_model")
            ),
            reasoning_effort=_clean(
                item.get("reasoningEffort") or item.get("reasoning_effort") or "none"
            ),
        )
        if not combo.vision_model or not combo.category_model or not combo.product_data_model:
            raise ValueError(
                "each model config requires visionModel, categoryModel, and productDataModel."
            )
        combos.append(combo)
    return combos


def load_cases(path: Path, limit: int = 0) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter="\t")
        fieldnames = reader.fieldnames or []
        missing = [
            field
            for field in ("itemName", "genreId", "image", "brand")
            if field not in fieldnames
        ]
        if missing:
            raise ValueError(f"input file missing required columns: {', '.join(missing)}")
        rows = []
        for row in reader:
            case = {field: _clean(row.get(field)) for field in ("itemName", "genreId", "image", "brand")}
            if not case["itemName"] or not case["genreId"] or not case["image"]:
                continue
            rows.append(case)
            if limit > 0 and len(rows) >= limit:
                break
    return rows


def image_urls_from_case(case: Dict[str, str]) -> List[str]:
    return [url.strip() for url in (case.get("image") or "").split("|") if url.strip()]


def _extract_ai_category(classification: Dict[str, Any]) -> str:
    for key in ("best_category_id", "rakuten_id"):
        value = _clean(classification.get(key))
        if value:
            return value
    categories = classification.get("categories")
    if isinstance(categories, list):
        for item in categories:
            if isinstance(item, dict):
                value = _clean(item.get("id") or item.get("rakuten_id"))
                if value:
                    return value
    return ""


def _category_items(classification: Dict[str, Any]) -> List[Dict[str, Any]]:
    categories = classification.get("categories")
    if not isinstance(categories, list):
        return []
    return [item for item in categories if isinstance(item, dict)]


def _extract_ai_category_path(classification: Dict[str, Any]) -> str:
    path = _clean(classification.get("best_target_path"))
    if path:
        return path
    for item in _category_items(classification):
        path = _clean(item.get("name") or item.get("target_path"))
        if path:
            return path
    return ""


def _extract_ai_category_confidence(classification: Dict[str, Any], ai_category: str) -> str:
    items = _category_items(classification)
    for item in items:
        item_id = _clean(item.get("id") or item.get("rakuten_id") or item.get("category_id"))
        if item_id and item_id == ai_category:
            return _format_score(item.get("confidence"))
    for item in items:
        confidence = _format_score(item.get("confidence"))
        if confidence:
            return confidence
    return _format_score(classification.get("confidence"))


def _extract_ai_brand(product_data: Dict[str, Any]) -> str:
    brand = _clean(product_data.get("brand_name"))
    if brand:
        return brand
    debug = product_data.get("_debug")
    if isinstance(debug, dict):
        raw = debug.get("product_data_ai_raw")
        if isinstance(raw, dict):
            brand = _clean(raw.get("brand_name"))
            if brand:
                return brand
    return ""


def build_result_row(
    case: Dict[str, str],
    combo: ModelCombination,
    classification: Optional[Dict[str, Any]] = None,
    product_data: Optional[Dict[str, Any]] = None,
    total_duration_s: Optional[float] = None,
) -> Dict[str, str]:
    classification = classification or {}
    product_data = product_data or {}
    classification_timings = classification.get("timings")
    product_data_timings = product_data.get("timings")
    if not isinstance(classification_timings, dict):
        classification_timings = {}
    if not isinstance(product_data_timings, dict):
        product_data_timings = {}
    ai_category = _extract_ai_category(classification)
    row: Dict[str, str] = {
        "itemName": _clean(case.get("itemName")),
        "genreId": _clean(case.get("genreId")),
        "image": _clean(case.get("image")),
        "brand": _clean(case.get("brand")),
        **combo.as_row_fields(),
        "aiCategory": ai_category,
        "aiCategoryPath": _extract_ai_category_path(classification),
        "aiCategoryConfidence": _extract_ai_category_confidence(classification, ai_category),
        "aiBrand": _extract_ai_brand(product_data),
        "aiTitle": _clean(product_data.get("title")),
        "categoryDurationS": _format_seconds(
            _milliseconds_to_seconds(classification_timings.get("classification_ms"))
        ),
        "productDataDurationS": _format_seconds(
            _milliseconds_to_seconds(product_data_timings.get("product_data_ms"))
        ),
        "totalDurationS": _format_seconds(total_duration_s),
        "customerCategoryCheck": "",
        "customerBrandCheck": "",
    }
    return {field: row.get(field, "") for field in RESULT_FIELDS}


def _is_category_correct(row: Dict[str, Any]) -> bool:
    return _clean(row.get("genreId")) == _clean(row.get("aiCategory"))


def _is_brand_correct(row: Dict[str, Any]) -> bool:
    expected = normalize_brand_for_compare(row.get("brand"))
    actual = normalize_brand_for_compare(row.get("aiBrand"))
    return bool(expected and actual and expected == actual)


def _summary_bucket(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(rows)
    category_correct = sum(1 for row in rows if _is_category_correct(row))
    brand_correct = sum(1 for row in rows if _is_brand_correct(row))
    return {
        "total": total,
        "categoryCorrect": category_correct,
        "brandCorrect": brand_correct,
        "categoryAccuracy": round(category_correct / total, 6) if total else 0.0,
        "brandAccuracy": round(brand_correct / total, 6) if total else 0.0,
        "categoryNeedsCustomerCheck": total - category_correct,
        "brandNeedsCustomerCheck": total - brand_correct,
    }


def summarize_rows(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    groups: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        key = tuple(_clean(row.get(field)) for field in MODEL_DIMENSIONS)
        groups.setdefault(key, []).append(row)

    by_model: List[Dict[str, Any]] = []
    for key in sorted(groups):
        bucket = _summary_bucket(groups[key])
        by_model.append(
            {
                "visionModel": key[0],
                "categoryModel": key[1],
                "productDataModel": key[2],
                "reasoningEffort": key[3],
                **bucket,
            }
        )

    return {
        "overall": _summary_bucket(list(rows)),
        "byModel": by_model,
    }


def write_result_rows(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=RESULT_FIELDS,
            delimiter="\t",
            lineterminator="\n",
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _clean(row.get(field)) for field in RESULT_FIELDS})


def write_summary(path: Path, summary: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
