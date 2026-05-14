import difflib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import Settings
from .constants import DEFAULT_LANGUAGE, PRICE_MAX, PRICE_MIN, SUPPORTED_LANGUAGES, TOP_LEVEL_CATEGORIES
from .data.brands import BrandStore, empty_brand_id_obj
from .data.categories import CategoryStore
from .errors import BadRequestError, LLMAllAttemptsFailedError
from .llm.client import OpenRouterClient
from .llm.resilient import AttemptRecord, ResilientCaller
from .llm.prompts import (
    CATEGORY_SYSTEM_PROMPT,
    CATEGORY_USER_PROMPT_TEMPLATE,
    FAST_CLASSIFICATION_SYSTEM_PROMPT,
    FAST_CLASSIFICATION_USER_PROMPT,
    PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT,
    PRODUCT_DATA_FALLBACK_USER_PROMPT,
    PRODUCT_DATA_REGENERATION_SYSTEM_PROMPT,
    PRODUCT_DATA_REGENERATION_USER_PROMPT,
    PRODUCT_DATA_SYSTEM_PROMPT,
    PRODUCT_DATA_USER_PROMPT,
    PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT,
    PRODUCT_TITLE_CATEGORY_USER_PROMPT,
    VISION_SYSTEM_PROMPT_WITH_PRICE,
    VISION_USER_PROMPT_WITH_PRICE,
)
from .utils import (
    compress_whitespace,
    fetch_image_from_url,
    image_bytes_to_data_url,
    normalize_category_label,
    normalize_price_list,
)


LANGUAGE_LABELS = {"ja": "Japanese", "en": "English", "zh": "Chinese"}
DESCRIPTION_DETAIL_FIELDS = (
    "brand",
    "product_name",
    "model_number",
    "target",
    "color",
    "size",
    "weight",
    "condition",
)
DESCRIPTION_DETAIL_LEGACY_MAP = {
    "brand": ("ブランド",),
    "product_name": ("商品名",),
    "model_number": ("型番",),
    "target": ("対象",),
    "color": ("カラー",),
    "size": ("サイズ",),
    "weight": ("重量",),
    "condition": ("程度",),
}
DESCRIPTION_SECTION_KEYS = {
    "details": ("product_details", "details", "商品详情", "商品詳細"),
    "intro": (
        "product_intro",
        "product_introduction",
        "introduction",
        "overview",
        "商品介绍 / 型号说明",
        "商品紹介 / 型号説明",
        "商品紹介 / 型号说明",
        "商品介绍/型号说明",
        "商品紹介/型号説明",
    ),
    "recommendation": (
        "recommendation",
        "recommendations",
        "selling_points",
        "推荐语",
        "おすすめポイント",
    ),
    "keywords": (
        "search_keywords",
        "keywords",
        "seo_keywords",
        "搜索关键词",
        "検索用キーワード",
    ),
}
MIN_PRODUCT_TITLE_CHARS = 80


def _language_label(language: str) -> str:
    return LANGUAGE_LABELS.get(language, "Japanese")


def _map_top_level_category(raw: str) -> Optional[str]:
    if not raw:
        return None
    normalized = normalize_category_label(raw)
    for name in TOP_LEVEL_CATEGORIES:
        if normalize_category_label(name) == normalized:
            return name

    best_score = 0.0
    best_name: Optional[str] = None
    for name in TOP_LEVEL_CATEGORIES:
        score = difflib.SequenceMatcher(None, normalized, normalize_category_label(name)).ratio()
        if score > best_score:
            best_score = score
            best_name = name

    if best_score >= 0.9:
        return best_name
    return None


def _clean_string(value: Any) -> str:
    if value is None:
        return ""
    return compress_whitespace(str(value))


def _normalize_direct_price(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        price = int(value)
        return price if PRICE_MIN <= price <= PRICE_MAX else None

    text = _clean_string(value)
    if not text:
        return None
    for match in re.finditer(r"\d[\d,，]*", text):
        digits = re.sub(r"[^\d]", "", match.group(0))
        if not digits:
            continue
        price = int(digits)
        if PRICE_MIN <= price <= PRICE_MAX:
            return price
    return None


def _normalize_price_fields(ai_raw: Dict[str, Any]) -> Dict[str, Any]:
    tax_excluded = _normalize_direct_price(ai_raw.get("tax_excluded"))
    tax_included = _normalize_direct_price(ai_raw.get("tax_included"))
    if tax_excluded is None:
        tax_included = None
    prices = [] if tax_excluded is not None else normalize_price_list(ai_raw.get("prices", []))
    return {
        "tax_excluded": tax_excluded,
        "tax_included": tax_included,
        "prices": prices,
    }


def _normalize_confidence(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(parsed, 1.0))


def _stringify_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return " ".join(parts)
    return str(value).strip()


def _normalize_keywords(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = []
        for item in value:
            text = _stringify_value(item)
            if text:
                items.append(text)
        return items
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        parts = re.split(r"[#\s,，、;；|/]+", raw)
        return [item for item in (p.strip() for p in parts) if item]
    text = _stringify_value(value)
    return [text] if text else []


def _format_product_details(details_raw: Any) -> Dict[str, str]:
    details_map = {field: "" for field in DESCRIPTION_DETAIL_FIELDS}
    if isinstance(details_raw, dict):
        for field in DESCRIPTION_DETAIL_FIELDS:
            keys = [field, f"◆{field}"]
            for legacy in DESCRIPTION_DETAIL_LEGACY_MAP.get(field, ()):
                keys.extend([legacy, f"◆{legacy}"])
            for key in keys:
                if key in details_raw and details_raw[key] is not None:
                    details_map[field] = _stringify_value(details_raw[key])
                    break
    elif isinstance(details_raw, str):
        text = details_raw.strip()
        if text:
            for line in text.splitlines():
                cleaned = line.strip()
                if not cleaned:
                    continue
                for field in DESCRIPTION_DETAIL_FIELDS:
                    labels = [f"◆{field}", field]
                    for legacy in DESCRIPTION_DETAIL_LEGACY_MAP.get(field, ()):
                        labels.extend([f"◆{legacy}", legacy])
                    for label in labels:
                        if cleaned.startswith(label):
                            remainder = cleaned[len(label):].lstrip()
                            if remainder.startswith("：") or remainder.startswith(":"):
                                remainder = remainder[1:].lstrip()
                            details_map[field] = remainder.strip()
                            break
                    if details_map[field]:
                        break
            if not any(details_map.values()):
                return details_map
    return details_map


def _normalize_description(value: Any) -> Dict[str, Any]:
    def pick(source: Dict[str, Any], keys: Tuple[str, ...]) -> Any:
        for key in keys:
            if key in source:
                return source[key]
        return None

    if value is None:
        return {
            "product_details": _format_product_details(None),
            "product_intro": "",
            "recommendation": "",
            "search_keywords": [],
        }

    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("{") and raw.endswith("}"):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return _normalize_description(parsed)
            except json.JSONDecodeError:
                pass
        return {
            "product_details": _format_product_details(None),
            "product_intro": raw,
            "recommendation": "",
            "search_keywords": [],
        }

    if isinstance(value, dict):
        details_raw = pick(value, DESCRIPTION_SECTION_KEYS["details"])
        intro_raw = pick(value, DESCRIPTION_SECTION_KEYS["intro"])
        recommendation_raw = pick(value, DESCRIPTION_SECTION_KEYS["recommendation"])
        keywords_raw = pick(value, DESCRIPTION_SECTION_KEYS["keywords"])
        return {
            "product_details": _format_product_details(details_raw),
            "product_intro": _stringify_value(intro_raw),
            "recommendation": _stringify_value(recommendation_raw),
            "search_keywords": _normalize_keywords(keywords_raw),
        }

    return {
        "product_details": _format_product_details(None),
        "product_intro": _stringify_value(value),
        "recommendation": "",
        "search_keywords": [],
    }


def _description_to_text(description: Any) -> str:
    if isinstance(description, str):
        return compress_whitespace(description)
    if isinstance(description, dict):
        parts: List[str] = []
        for key in ("product_details", "product_intro", "recommendation", "search_keywords"):
            value = description.get(key)
            if isinstance(value, dict):
                lines = []
                for field in DESCRIPTION_DETAIL_FIELDS:
                    field_value = _stringify_value(value.get(field))
                    if field_value:
                        lines.append(f"{field}: {field_value}")
                text = "\n".join(lines)
            elif isinstance(value, list):
                text = " ".join(_stringify_value(item) for item in value if _stringify_value(item))
            else:
                text = _stringify_value(value)
            if text:
                parts.append(text)
        return compress_whitespace("\n".join(parts))
    return compress_whitespace(_stringify_value(description))


def _title_snippet(value: Any, max_chars: int = 60) -> str:
    text = compress_whitespace(_stringify_value(value))
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip(" 、,.;；。")


def _extend_title_to_minimum(
    title: str,
    *,
    description: Dict[str, Any],
    brand_name: str = "",
    brand_raw: str = "",
    language: str = DEFAULT_LANGUAGE,
    min_chars: int = MIN_PRODUCT_TITLE_CHARS,
) -> str:
    title_clean = _clean_string(title)
    if len(title_clean) >= min_chars:
        return title_clean

    parts: List[str] = []
    details = description.get("product_details") if isinstance(description, dict) else {}
    if not isinstance(details, dict):
        details = {}

    for value in (
        brand_name,
        brand_raw,
        details.get("brand"),
        details.get("product_name"),
        details.get("model_number"),
        details.get("target"),
        details.get("color"),
        details.get("size"),
        details.get("weight"),
        details.get("condition"),
    ):
        part = _title_snippet(value)
        if part:
            parts.append(part)

    keywords = description.get("search_keywords") if isinstance(description, dict) else []
    if isinstance(keywords, list):
        parts.extend(_title_snippet(item, max_chars=30) for item in keywords)

    if isinstance(description, dict):
        parts.append(_title_snippet(description.get("product_intro"), max_chars=50))
        parts.append(_title_snippet(description.get("recommendation"), max_chars=50))

    generic_parts_by_language = {
        "ja": [
            "画像確認商品",
            "ブランド カラー 型番 サイズ 状態 情報入り",
            "メルカリ出品向け詳細タイトル",
            "写真から確認できる特徴を反映",
            "商品説明と合わせて確認しやすい出品タイトル",
        ],
        "zh": [
            "图片识别商品",
            "品牌 颜色 型号 尺寸 成色 信息补充",
            "适合商品发布的详细标题",
            "结合图片可确认特征",
            "便于买家检索和理解的发布标题",
        ],
        "en": [
            "image verified item",
            "brand color model size condition details included",
            "marketplace listing title",
            "visible product features reflected",
            "buyer friendly searchable listing title",
        ],
    }
    parts.extend(generic_parts_by_language.get(language, generic_parts_by_language["ja"]))

    seen = {title_clean.casefold()} if title_clean else set()
    title_parts = [title_clean] if title_clean else []
    for part in parts:
        cleaned = _clean_string(part)
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen or (title_clean and cleaned in title_clean):
            continue
        title_parts.append(cleaned)
        seen.add(key)
        candidate = _clean_string(" ".join(title_parts))
        if len(candidate) >= min_chars:
            return candidate

    candidate = _clean_string(" ".join(title_parts))
    filler = generic_parts_by_language.get(language, generic_parts_by_language["ja"])[-1]
    while len(candidate) < min_chars:
        candidate = _clean_string(f"{candidate} {filler}" if candidate else filler)
    return candidate


def _product_data_context_json(value: Optional[Dict[str, Any]]) -> str:
    if not value:
        return "(none)"
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def _paths_from_categories(
    categories: List[Dict[str, str]],
    include_alternatives: bool = True,
) -> Optional[Dict[str, Any]]:
    if not categories:
        return None
    ordered_paths: List[Dict[str, str]] = []
    for category in categories:
        item = {
            "target_path": compress_whitespace(category.get("name") or ""),
            "category_id": category.get("id") or "",
            "rakuten_id": category.get("rakuten_id") or category.get("id") or "",
            "meru_id": category.get("meru_id") or "",
            "rakuma_id": category.get("rakuma_id") or "",
            "zenplus_id": category.get("zenplus_id") or "",
            "meru_path": category.get("meru_path") or "",
            "rakuma_path": category.get("rakuma_path") or "",
            "zenplus_path": category.get("zenplus_path") or "",
        }
        if item["target_path"] and item not in ordered_paths:
            ordered_paths.append(item)
    if not ordered_paths:
        return None
    best = ordered_paths[0]
    payload: Dict[str, Any] = {
        "best_target_path": best["target_path"],
        "best_category_id": best["category_id"],
        "rakuten_id": best["rakuten_id"],
        "meru_id": best["meru_id"],
        "rakuma_id": best["rakuma_id"],
        "zenplus_id": best["zenplus_id"],
        "meru_path": best["meru_path"],
        "rakuma_path": best["rakuma_path"],
        "zenplus_path": best["zenplus_path"],
    }
    if include_alternatives:
        payload["alternatives"] = ordered_paths[1:]
    return payload


class MercariAnalyzer:
    def __init__(
        self,
        settings: Settings,
        brand_store: BrandStore,
        category_store: CategoryStore,
        vision_client: OpenRouterClient,
        category_client: OpenRouterClient,
    ):
        self.settings = settings
        self.brand_store = brand_store
        self.category_store = category_store
        self.vision_client = vision_client
        self.category_client = category_client
        self._logs_dir = Path(__file__).resolve().parent.parent / "logs"
        self.vision_caller = ResilientCaller(
            client=vision_client,
            max_retries=settings.model_call_max_retries,
            total_budget_s=settings.model_call_total_budget_seconds,
            per_attempt_timeout_s=settings.request_timeout,
        )
        self.category_caller = ResilientCaller(
            client=category_client,
            max_retries=settings.model_call_max_retries,
            total_budget_s=settings.model_call_total_budget_seconds,
            per_attempt_timeout_s=settings.request_timeout,
        )

    def classify_first_image_categories(
        self,
        images: List[Tuple[bytes, str]],
        language: str,
        debug: bool = False,
        vision_model_override: Optional[str] = None,
        category_model_override: Optional[str] = None,
        image_processing: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if language not in SUPPORTED_LANGUAGES:
            raise BadRequestError("Unsupported language.")
        if not images:
            raise BadRequestError("Image list is required.")
        total_started = time.monotonic()
        attempts_by_stage: Dict[str, List[AttemptRecord]] = {}

        first_data_url = image_bytes_to_data_url(images[0][0], images[0][1])
        ai_raw, vision_attempts = self._call_fast_classification_llm(
            first_data_url,
            language,
            model_override=vision_model_override,
        )
        attempts_by_stage["fast_vision"] = vision_attempts

        title = _clean_string(ai_raw.get("title", ""))
        simple_description = _clean_string(ai_raw.get("simple_description", ""))
        top_level_category = _clean_string(ai_raw.get("top_level_category", ""))
        group_name = _map_top_level_category(top_level_category)

        categories: List[Dict[str, Any]] = []
        llm_category_raw: Optional[Dict[str, Any]] = None
        if group_name:
            categories, llm_category_raw, category_attempts = self._choose_categories(
                title=title,
                description=simple_description,
                brand_for_prompt="",
                group_name=group_name,
                model_override=category_model_override,
            )
            attempts_by_stage["category"] = category_attempts

        classification_ms = round((time.monotonic() - total_started) * 1000, 2)
        result: Dict[str, Any] = {
            "status": "product_pending",
            "categories": categories,
            "timings": {
                "total_ms": classification_ms,
                "classification_ms": classification_ms,
            },
        }
        if image_processing:
            result["image_processing"] = image_processing
        path_info = _paths_from_categories(categories, include_alternatives=False)
        if path_info:
            result.update(path_info)
        if debug:
            result["_debug"] = {
                "fast_ai_raw": ai_raw,
                "group_name": group_name,
                "llm_category_raw": llm_category_raw,
                "attempts": {
                    stage: [a.__dict__ for a in attempts]
                    for stage, attempts in attempts_by_stage.items()
                },
            }
        return result

    def generate_product_data(
        self,
        images: List[Tuple[bytes, str]],
        language: str,
        debug: bool = False,
        model_override: Optional[str] = None,
        use_fallback_prompt: bool = False,
        started_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        if language not in SUPPORTED_LANGUAGES:
            raise BadRequestError("Unsupported language.")
        if not images:
            raise BadRequestError("Image list is required.")
        # Allow callers to pass the submit-time monotonic timestamp so that the
        # reported product_data_ms reflects wall time from submission rather than
        # from when the executor worker picked up the task.
        started = float(started_at) if started_at is not None else time.monotonic()
        data_urls = [
            image_bytes_to_data_url(image_bytes, mime_type)
            for image_bytes, mime_type in images
        ]
        ai_raw, ai_full, attempts = self._call_product_data_llm(
            data_urls,
            language,
            model_override=model_override,
            use_fallback_prompt=use_fallback_prompt,
        )

        brand_raw = _clean_string(ai_raw.get("brand_name", ""))
        description_struct = _normalize_description(ai_raw.get("description"))
        brand_match = self.brand_store.match(brand_raw)
        brand_name = brand_match["brand_name"] if brand_match else ""
        brand_id_obj = dict(brand_match["brand_id_obj"]) if brand_match else empty_brand_id_obj()
        title = _extend_title_to_minimum(
            ai_raw.get("title", ""),
            description=description_struct,
            brand_name=brand_name,
            brand_raw=brand_raw,
            language=language,
        )

        result: Dict[str, Any] = {
            "title": title,
            "description": description_struct,
            "brand_name": brand_name,
            "brand_id_obj": brand_id_obj,
            **_normalize_price_fields(ai_raw),
            "timings": {
                "product_data_ms": round((time.monotonic() - started) * 1000, 2),
            },
        }
        if debug:
            result["_debug"] = {
                "product_data_ai_raw": ai_raw,
                "product_data_ai_full": ai_full,
                "attempts": {"product_data": [a.__dict__ for a in attempts]},
            }
        return result

    def regenerate_product_data(
        self,
        images: List[Tuple[bytes, str]],
        language: str,
        original_product_data: Optional[Dict[str, Any]] = None,
        user_notes: str = "",
        debug: bool = False,
        model_override: Optional[str] = None,
        started_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        if language not in SUPPORTED_LANGUAGES:
            raise BadRequestError("Unsupported language.")
        if not images:
            raise BadRequestError("Image list is required.")
        started = float(started_at) if started_at is not None else time.monotonic()
        data_urls = [
            image_bytes_to_data_url(image_bytes, mime_type)
            for image_bytes, mime_type in images
        ]
        ai_raw, ai_full, attempts = self._call_product_data_regeneration_llm(
            data_urls,
            language,
            original_product_data=original_product_data,
            user_notes=user_notes,
            model_override=model_override,
        )

        brand_raw = _clean_string(ai_raw.get("brand_name", ""))
        description_struct = _normalize_description(ai_raw.get("description"))
        brand_match = self.brand_store.match(brand_raw)
        brand_name = brand_match["brand_name"] if brand_match else ""
        brand_id_obj = dict(brand_match["brand_id_obj"]) if brand_match else empty_brand_id_obj()
        title = _extend_title_to_minimum(
            ai_raw.get("title", ""),
            description=description_struct,
            brand_name=brand_name,
            brand_raw=brand_raw,
            language=language,
        )

        result: Dict[str, Any] = {
            "title": title,
            "description": description_struct,
            "brand_name": brand_name,
            "brand_id_obj": brand_id_obj,
            "timings": {
                "product_data_ms": round((time.monotonic() - started) * 1000, 2),
            },
        }
        if debug:
            result["_debug"] = {
                "product_data_ai_raw": ai_raw,
                "product_data_ai_full": ai_full,
                "attempts": {
                    "product_data_regeneration": [a.__dict__ for a in attempts]
                },
            }
        return result

    def analyze(
        self,
        images: List[Tuple[bytes, str]],
        language: str,
        debug: bool = False,
        vision_model_override: Optional[str] = None,
        category_model_override: Optional[str] = None,
        image_processing: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if language not in SUPPORTED_LANGUAGES:
            raise BadRequestError("Unsupported language.")
        if not images:
            raise BadRequestError("Image list is required.")
        total_started = time.monotonic()
        attempts_by_stage: Dict[str, List[AttemptRecord]] = {}

        data_urls = [
            image_bytes_to_data_url(image_bytes, mime_type)
            for image_bytes, mime_type in images
        ]
        vision_started = time.monotonic()
        ai_raw, ai_full, vision_attempts = self._call_vision_llm(
            data_urls,
            language,
            model_override=vision_model_override,
        )
        attempts_by_stage["vision"] = vision_attempts
        vision_ms = round((time.monotonic() - vision_started) * 1000, 2)

        title = _clean_string(ai_raw.get("title", ""))
        description_struct = _normalize_description(ai_raw.get("description"))
        description_text = _description_to_text(description_struct)
        price_fields = _normalize_price_fields(ai_raw)
        top_level_category = _clean_string(ai_raw.get("top_level_category", ""))
        brand_raw = _clean_string(ai_raw.get("brand_name", ""))

        brand_match = self.brand_store.match(brand_raw)
        brand_name = brand_match["brand_name"] if brand_match else ""
        brand_id_obj = dict(brand_match["brand_id_obj"]) if brand_match else empty_brand_id_obj()

        group_name = _map_top_level_category(top_level_category)

        categories: List[Dict[str, str]] = []
        llm_category_raw: Optional[Dict[str, Any]] = None
        category_ms = 0.0

        if group_name:
            category_started = time.monotonic()
            categories, llm_category_raw, category_attempts = self._choose_categories(
                title=title or ai_raw.get("title", ""),
                description=description_text or _description_to_text(ai_raw.get("description", "")),
                brand_for_prompt=brand_raw or brand_name,
                group_name=group_name,
                model_override=category_model_override,
            )
            attempts_by_stage["category"] = category_attempts
            category_ms = round((time.monotonic() - category_started) * 1000, 2)

        title = _extend_title_to_minimum(
            title,
            description=description_struct,
            brand_name=brand_name,
            brand_raw=brand_raw,
            language=language,
        )

        result: Dict[str, Any] = {
            "title": title,
            "description": description_struct,
            **price_fields,
            "categories": categories,
            "brand_name": brand_name,
            "brand_id_obj": brand_id_obj,
            "timings": {
                "total_ms": 0.0,
                "vision_ms": vision_ms,
                "category_ms": category_ms,
            },
        }
        if image_processing:
            result["image_processing"] = image_processing
        path_info = _paths_from_categories(categories, include_alternatives=False)
        if path_info:
            result.update(path_info)

        if debug:
            result["_debug"] = {
                "ai_raw": ai_raw,
                "group_name": group_name,
                "llm_category_raw": llm_category_raw,
                "attempts": {
                    stage: [a.__dict__ for a in attempts]
                    for stage, attempts in attempts_by_stage.items()
                },
            }

        result["timings"]["total_ms"] = round((time.monotonic() - total_started) * 1000, 2)
        return result

    def analyze_title(
        self,
        title: str,
        image_url: Optional[str],
        language: str,
        category_model_override: Optional[str] = None,
        vision_model_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        if language not in SUPPORTED_LANGUAGES:
            raise BadRequestError("Unsupported language.")

        title_clean = _clean_string(title)
        if not title_clean:
            raise BadRequestError("Title is required.")

        categories: List[Dict[str, str]] = []
        title_error: Optional[Exception] = None

        try:
            title_payload, _title_attempts = self._call_title_category_llm(
                title=title_clean,
                language=language,
                model_override=category_model_override,
            )
            top_level_category = _clean_string(title_payload.get("top_level_category", ""))
            group_name = _map_top_level_category(top_level_category)
            if group_name:
                categories, _, _category_attempts = self._choose_categories(
                    title=title_clean,
                    description="",
                    brand_for_prompt="",
                    group_name=group_name,
                    model_override=category_model_override,
                )
        except (BadRequestError, LLMAllAttemptsFailedError) as exc:
            title_error = exc

        paths_result = _paths_from_categories(categories)
        if paths_result:
            return paths_result

        if not image_url:
            if isinstance(title_error, LLMAllAttemptsFailedError):
                raise title_error
            if title_error is not None:
                raise BadRequestError(
                    f"Title classification failed; image_url is required for fallback. ({title_error})"
                ) from title_error
            raise BadRequestError("Title classification failed; image_url is required for fallback.")

        try:
            image_bytes, mime_type = fetch_image_from_url(
                image_url=image_url,
                timeout=self.settings.request_timeout,
                max_bytes=self.settings.max_image_bytes,
                allowed_mime_types=self.settings.allowed_mime_types,
            )
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc

        fallback_result = self._classify_image_to_paths(
            image_bytes=image_bytes,
            mime_type=mime_type,
            language=language,
            vision_model_override=vision_model_override,
            category_model_override=category_model_override,
        )
        if fallback_result:
            return fallback_result

        raise BadRequestError("Image recognition failed to return a category path.")

    def _call_vision_llm(
        self,
        image_data_urls: List[str],
        language: str,
        model_override: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[AttemptRecord]]:
        if not image_data_urls:
            raise BadRequestError("Image list is empty.")
        user_prompt = VISION_USER_PROMPT_WITH_PRICE.format(language_label=_language_label(language))
        image_payloads: List[Dict[str, Any]] = []
        image_count = len(image_data_urls)
        for index, url in enumerate(image_data_urls, start=1):
            image_payloads.append(
                {
                    "type": "text",
                    "text": (
                        f"Image {index} of {image_count}: inspect this image carefully. "
                        "Extract any unique evidence from it before merging with the other images."
                    ),
                }
            )
            image_payloads.append({"type": "image_url", "image_url": {"url": url}})
        messages = [
            {"role": "system", "content": VISION_SYSTEM_PROMPT_WITH_PRICE},
            {
                "role": "user",
                "content": [{"type": "text", "text": user_prompt}] + image_payloads,
            },
        ]
        primary = model_override or self.settings.vision_model
        fallbacks = self.settings.vision_fallback_models

        try:
            parsed, raw_response, attempts = self.vision_caller.call_and_parse(
                stage="vision",
                primary_model=primary,
                fallback_models=fallbacks,
                messages=messages,
                temperature=0.2,
                max_tokens=16000,
            )
        except LLMAllAttemptsFailedError as exc:
            self._log_raw("vision_attempts", [a.__dict__ for a in exc.attempts])
            raise
        self._log_raw("vision_parsed", parsed)
        self._log_raw("vision_raw_response", raw_response)
        self._log_raw("vision_attempts", [a.__dict__ for a in attempts])
        return parsed, raw_response, attempts

    def _call_fast_classification_llm(
        self,
        image_data_url: str,
        language: str,
        model_override: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], List[AttemptRecord]]:
        user_prompt = FAST_CLASSIFICATION_USER_PROMPT.format(
            language_label=_language_label(language)
        )
        messages = [
            {"role": "system", "content": FAST_CLASSIFICATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ]
        primary = model_override or self.settings.vision_model
        fallbacks = self.settings.vision_fallback_models

        try:
            parsed, raw_response, attempts = self.vision_caller.call_and_parse(
                stage="fast_vision",
                primary_model=primary,
                fallback_models=fallbacks,
                messages=messages,
                temperature=0.1,
                max_tokens=2000,
            )
        except LLMAllAttemptsFailedError as exc:
            self._log_raw("fast_vision_attempts", [a.__dict__ for a in exc.attempts])
            raise
        self._log_raw("fast_vision_parsed", parsed)
        self._log_raw("fast_vision_raw_response", raw_response)
        self._log_raw("fast_vision_attempts", [a.__dict__ for a in attempts])
        return parsed, attempts

    def _call_product_data_llm(
        self,
        image_data_urls: List[str],
        language: str,
        model_override: Optional[str] = None,
        use_fallback_prompt: bool = False,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[AttemptRecord]]:
        if not image_data_urls:
            raise BadRequestError("Image list is empty.")
        if use_fallback_prompt:
            system_prompt = PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT
            user_prompt = PRODUCT_DATA_FALLBACK_USER_PROMPT.format(
                language_label=_language_label(language)
            )
            stage = "product_data_fallback"
        else:
            system_prompt = PRODUCT_DATA_SYSTEM_PROMPT
            user_prompt = PRODUCT_DATA_USER_PROMPT.format(
                language_label=_language_label(language)
            )
            stage = "product_data"
        image_payloads: List[Dict[str, Any]] = []
        image_count = len(image_data_urls)
        for index, url in enumerate(image_data_urls, start=1):
            image_payloads.append(
                {
                    "type": "text",
                    "text": (
                        f"Image {index} of {image_count}: inspect this image carefully. "
                        "Extract unique evidence before merging it with the other images."
                    ),
                }
            )
            image_payloads.append({"type": "image_url", "image_url": {"url": url}})
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [{"type": "text", "text": user_prompt}] + image_payloads,
            },
        ]
        if model_override:
            primary = model_override
            # Even the explicit fallback model is allowed to retry+fall back to
            # other models if it fails outright. Prefer a dedicated chain
            # (PRODUCT_DATA_FALLBACK_MODELS) and gracefully degrade to the
            # vision fallback chain otherwise.
            fallbacks = self._product_data_fallback_chain(model_override)
        else:
            primary = getattr(self.settings, "product_data_model", "") or self.settings.vision_model
            fallbacks = self._product_data_primary_chain(primary)

        try:
            parsed, raw_response, attempts = self.vision_caller.call_and_parse(
                stage=stage,
                primary_model=primary,
                fallback_models=fallbacks,
                messages=messages,
                temperature=0.2,
                max_tokens=12000,
            )
        except LLMAllAttemptsFailedError as exc:
            self._log_raw(f"{stage}_attempts", [a.__dict__ for a in exc.attempts])
            raise
        self._log_raw(f"{stage}_parsed", parsed)
        self._log_raw(f"{stage}_raw_response", raw_response)
        self._log_raw(f"{stage}_attempts", [a.__dict__ for a in attempts])
        return parsed, raw_response, attempts

    def _call_product_data_regeneration_llm(
        self,
        image_data_urls: List[str],
        language: str,
        original_product_data: Optional[Dict[str, Any]] = None,
        user_notes: str = "",
        model_override: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[AttemptRecord]]:
        if not image_data_urls:
            raise BadRequestError("Image list is empty.")
        user_prompt = PRODUCT_DATA_REGENERATION_USER_PROMPT.format(
            language_label=_language_label(language),
            user_notes=_clean_string(user_notes) or "(none)",
            original_product_data_json=_product_data_context_json(original_product_data),
        )
        image_payloads: List[Dict[str, Any]] = []
        image_count = len(image_data_urls)
        for index, url in enumerate(image_data_urls, start=1):
            image_payloads.append(
                {
                    "type": "text",
                    "text": (
                        f"Image {index} of {image_count}: inspect this image carefully. "
                        "Extract unique evidence before regenerating the product data."
                    ),
                }
            )
            image_payloads.append({"type": "image_url", "image_url": {"url": url}})
        messages = [
            {"role": "system", "content": PRODUCT_DATA_REGENERATION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [{"type": "text", "text": user_prompt}] + image_payloads,
            },
        ]
        primary = (
            model_override
            or getattr(self.settings, "product_data_model", "")
            or self.settings.vision_model
        )
        fallbacks = self._product_data_primary_chain(primary)

        try:
            parsed, raw_response, attempts = self.vision_caller.call_and_parse(
                stage="product_data_regeneration",
                primary_model=primary,
                fallback_models=fallbacks,
                messages=messages,
                temperature=0.2,
                max_tokens=12000,
            )
        except LLMAllAttemptsFailedError as exc:
            self._log_raw(
                "product_data_regeneration_attempts",
                [a.__dict__ for a in exc.attempts],
            )
            raise
        self._log_raw("product_data_regeneration_parsed", parsed)
        self._log_raw("product_data_regeneration_raw_response", raw_response)
        self._log_raw(
            "product_data_regeneration_attempts",
            [a.__dict__ for a in attempts],
        )
        return parsed, raw_response, attempts

    def _product_data_primary_chain(self, primary_model: str) -> List[str]:
        configured = list(getattr(self.settings, "product_data_fallback_models", []) or [])
        if not configured:
            configured = list(self.settings.vision_fallback_models or [])
        explicit_fallback = (
            getattr(self.settings, "product_data_fallback_model", "") or ""
        ).strip()
        if explicit_fallback and explicit_fallback != primary_model:
            configured = [explicit_fallback] + [
                m for m in configured if m and m != explicit_fallback
            ]
        return [m for m in configured if m and m != primary_model]

    def _product_data_fallback_chain(self, fallback_model: str) -> List[str]:
        configured = list(getattr(self.settings, "product_data_fallback_models", []) or [])
        if not configured:
            configured = list(self.settings.vision_fallback_models or [])
        return [m for m in configured if m and m != fallback_model]

    def _call_title_category_llm(
        self,
        title: str,
        language: str,
        model_override: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], List[AttemptRecord]]:
        user_prompt = PRODUCT_TITLE_CATEGORY_USER_PROMPT.format(
            title=title,
            language_label=_language_label(language),
        )
        messages = [
            {"role": "system", "content": PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        primary = model_override or self.settings.category_model
        fallbacks = self.settings.category_fallback_models

        try:
            parsed, raw_response, attempts = self.category_caller.call_and_parse(
                stage="title_category",
                primary_model=primary,
                fallback_models=fallbacks,
                messages=messages,
                temperature=0.3,
                max_tokens=16000,
            )
        except LLMAllAttemptsFailedError as exc:
            self._log_raw("title_category_attempts", [a.__dict__ for a in exc.attempts])
            raise
        self._log_raw("title_category_parsed", parsed)
        self._log_raw("title_category_raw_response", raw_response)
        self._log_raw("title_category_attempts", [a.__dict__ for a in attempts])
        return parsed, attempts

    def _log_raw(self, name: str, payload: Any) -> None:
        if not self.settings.log_llm_raw:
            return
        try:
            self._logs_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
            path = self._logs_dir / f"{name}_{timestamp}.log"
            with path.open("w", encoding="utf-8") as f:
                if isinstance(payload, str):
                    f.write(payload)
                else:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            # Swallow logging errors to avoid breaking main flow.
            return

    def _choose_categories(
        self,
        title: str,
        description: str,
        brand_for_prompt: str,
        group_name: str,
        model_override: Optional[str] = None,
    ) -> Tuple[List[Dict[str, str]], Optional[Dict[str, Any]], List[AttemptRecord]]:
        candidates = self.category_store.get_categories_by_group(group_name)
        if not candidates:
            return [], None, []

        candidate_paths = [item["name"] for item in candidates]
        candidate_block = "\n".join(candidate_paths)
        user_prompt = CATEGORY_USER_PROMPT_TEMPLATE.format(
            title=title,
            description=description,
            brand=brand_for_prompt,
            group_name=group_name,
            candidate_paths=candidate_block,
        )
        messages = [
            {"role": "system", "content": CATEGORY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        primary = model_override or self.settings.category_model
        fallbacks = self.settings.category_fallback_models

        try:
            parsed, raw_response, attempts = self.category_caller.call_and_parse(
                stage="category",
                primary_model=primary,
                fallback_models=fallbacks,
                messages=messages,
                temperature=0.1,
                max_tokens=16000,
            )
        except LLMAllAttemptsFailedError as exc:
            self._log_raw("category_attempts", [a.__dict__ for a in exc.attempts])
            raise
        self._log_raw("category_parsed", parsed)
        self._log_raw("category_raw_response", raw_response)
        self._log_raw("category_attempts", [a.__dict__ for a in attempts])

        ordered_paths: List[Tuple[str, float]] = []
        best = parsed.get("best_target_path")
        if isinstance(best, str) and best.strip():
            ordered_paths.append((best, _normalize_confidence(parsed.get("confidence"))))

        alternatives = parsed.get("alternatives", [])
        if isinstance(alternatives, list):
            for alt in alternatives:
                if not isinstance(alt, dict):
                    continue
                path = alt.get("target_path")
                if isinstance(path, str) and path.strip():
                    ordered_paths.append((path, _normalize_confidence(alt.get("confidence"))))

        # Always cap at 3 — the prompt asks for top-3, but defensively trim in
        # case the model returns more (e.g. duplicates after candidate matching).
        max_results = 3
        seen = set()
        results: List[Dict[str, str]] = []
        for path, confidence in ordered_paths:
            path_clean = compress_whitespace(path)
            key = (group_name, path_clean)
            if key in seen:
                continue
            seen.add(key)
            match = self.category_store.find_category(group_name, path_clean)
            if match:
                results.append(
                    {
                        "id": match["id"],
                        "rakuten_id": match["id"],
                        "name": match["name"],
                        "meru_id": match.get("meru_id", ""),
                        "rakuma_id": match.get("rakuma_id", ""),
                        "zenplus_id": match.get("zenplus_id", ""),
                        "meru_path": match.get("meru_path", ""),
                        "rakuma_path": match.get("rakuma_path", ""),
                        "zenplus_path": match.get("zenplus_path", ""),
                        "confidence": confidence,
                    }
                )
            if len(results) >= max_results:
                break

        return results, parsed, attempts

    def _classify_image_to_paths(
        self,
        image_bytes: bytes,
        mime_type: str,
        language: str,
        vision_model_override: Optional[str] = None,
        category_model_override: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        data_url = image_bytes_to_data_url(image_bytes, mime_type)
        ai_raw, _, _ = self._call_vision_llm(
            [data_url],
            language,
            model_override=vision_model_override,
        )

        title = _clean_string(ai_raw.get("title", ""))
        description_struct = _normalize_description(ai_raw.get("description"))
        description_text = _description_to_text(description_struct)
        top_level_category = _clean_string(ai_raw.get("top_level_category", ""))
        brand_raw = _clean_string(ai_raw.get("brand_name", ""))

        group_name = _map_top_level_category(top_level_category)
        if not group_name:
            return None

        categories, _, _ = self._choose_categories(
            title=title or ai_raw.get("title", ""),
            description=description_text or _description_to_text(ai_raw.get("description", "")),
            brand_for_prompt=brand_raw,
            group_name=group_name,
            model_override=category_model_override,
        )

        return _paths_from_categories(categories)
