import difflib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .config import Settings
from .constants import DEFAULT_LANGUAGE, PRICE_MAX, PRICE_MIN, SUPPORTED_LANGUAGES, TOP_LEVEL_CATEGORIES
from .observability import context as obs_ctx
from .observability.recorder import Recorder
from .data.brands import BrandStore, empty_brand_id_obj
from .data.categories import CategoryStore
from .errors import BadRequestError, LLMAllAttemptsFailedError
from .llm.client import OpenRouterClient, USE_CLIENT_REASONING
from .llm.resilient import AttemptRecord, ResilientCaller
from .llm import prompt_store
from .utils import (
    compress_whitespace,
    fetch_image_from_url,
    image_bytes_to_data_url,
    normalize_category_label,
    normalize_text,
)


recorder: Optional[Recorder] = None  # set by main.py at startup


def set_recorder(r: Recorder) -> None:
    global recorder
    recorder = r


LANGUAGE_LABELS = {"ja": "Japanese", "en": "English", "zh": "Chinese"}
DESCRIPTION_DETAIL_FIELDS = (
    "brand",
    "product_name",
    "model_number",
    "color",
)
DESCRIPTION_DETAIL_LEGACY_MAP = {
    "brand": ("ブランド",),
    "product_name": ("商品名",),
    "model_number": ("型番",),
    "color": ("カラー",),
}
TITLE_EXCLUDED_DETAIL_FIELDS = {
    "target": ("対象",),
    "size": ("サイズ",),
    "weight": ("重量",),
    "condition": ("程度", "状態", "成色"),
}
TITLE_EXCLUDED_PATTERNS = (
    r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s?(?:g|kg|グラム|キログラム)(?![A-Za-z0-9])",
    r"(?:S|M|L|XL|XXL)サイズ",
    r"サイズ(?:S|M|L|XL|XXL)",
    r"(?:メンズ|レディース|ユニセックス)",
    r"(?:新品|未使用|中古|美品|良品|良好|傷なし|目立つ傷なし|使用感(?:あり|なし)?)",
    r"(?:成色|状態|程度)[:：]?\s?\S+",
    r"\S+向け",
)
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
MIN_PRODUCT_TITLE_CHARS = 75
MAX_PRODUCT_TITLE_CHARS = 85
TITLE_GENERIC_SUFFIX_KEYS = ("カメラ", "camera")


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


def _extract_price_numbers(value: Any) -> List[int]:
    if value is None or isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        price = _normalize_direct_price(value)
        return [price] if price is not None else []
    if isinstance(value, str):
        prices: List[int] = []
        for match in re.finditer(r"\d[\d,，]*", value):
            digits = re.sub(r"[^\d]", "", match.group(0))
            if not digits:
                continue
            price = _normalize_direct_price(int(digits))
            if price is not None:
                prices.append(price)
        return prices
    if isinstance(value, (list, tuple)):
        prices: List[int] = []
        for item in value:
            prices.extend(_extract_price_numbers(item))
        return prices
    if isinstance(value, dict):
        prices: List[int] = []
        for key in (
            "prices",
            "range",
            "values",
            "list",
            "options",
            "candidates",
            "suggestions",
        ):
            prices.extend(_extract_price_numbers(value.get(key)))
        for key in (
            "low",
            "lower",
            "lowest",
            "min",
            "minimum",
            "price_min",
            "reference_min",
            "reference_price_min",
            "high",
            "higher",
            "highest",
            "max",
            "maximum",
            "price_max",
            "reference_max",
            "reference_price_max",
            "best",
            "primary",
            "main",
            "price",
            "value",
            "estimate",
        ):
            prices.extend(_extract_price_numbers(value.get(key)))
        return prices
    return []


def _normalize_reference_price_range(raw_prices: Any, covering_prices: Iterable[int]) -> List[int]:
    ordered_unique: List[int] = []
    seen = set()
    for price in _extract_price_numbers(raw_prices):
        if price in seen:
            continue
        seen.add(price)
        ordered_unique.append(price)
    if not ordered_unique:
        return []

    low = min(ordered_unique)
    high = max(ordered_unique)
    for price in covering_prices:
        if PRICE_MIN <= price <= PRICE_MAX:
            low = min(low, price)
            high = max(high, price)
    return [low, high]


def _normalize_price_fields(ai_raw: Dict[str, Any]) -> Dict[str, Any]:
    tax_excluded = _normalize_direct_price(ai_raw.get("tax_excluded"))
    tax_included = _normalize_direct_price(ai_raw.get("tax_included"))
    if tax_excluded is not None and tax_included is None:
        tax_included = tax_excluded
        tax_excluded = None
    visible_prices = [
        price for price in (tax_excluded, tax_included) if price is not None
    ]
    prices = _normalize_reference_price_range(
        ai_raw.get("prices", []),
        covering_prices=visible_prices,
    )
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


def _extract_title_exclusion_snippets(description_raw: Any) -> List[str]:
    if isinstance(description_raw, str):
        raw = description_raw.strip()
        if raw.startswith("{") and raw.endswith("}"):
            try:
                return _extract_title_exclusion_snippets(json.loads(raw))
            except json.JSONDecodeError:
                return []
        return []

    if not isinstance(description_raw, dict):
        return []

    details_raw = None
    for key in DESCRIPTION_SECTION_KEYS["details"]:
        if key in description_raw:
            details_raw = description_raw[key]
            break
    if not isinstance(details_raw, dict):
        return []

    snippets: List[str] = []
    for field, legacy_labels in TITLE_EXCLUDED_DETAIL_FIELDS.items():
        keys = [field, f"◆{field}"]
        for legacy in legacy_labels:
            keys.extend([legacy, f"◆{legacy}"])
        for key in keys:
            if key in details_raw and details_raw[key] is not None:
                snippet = _title_snippet(details_raw[key], max_chars=80)
                if len(snippet) > 1:
                    snippets.append(snippet)
                break
    return snippets


def _strip_title_excluded_terms(title: str, excluded_snippets: List[str]) -> str:
    cleaned = _clean_string(title)
    for snippet in excluded_snippets:
        if len(snippet) == 1:
            cleaned = re.sub(
                rf"(?<![A-Za-z0-9]){re.escape(snippet)}(?![A-Za-z0-9])",
                " ",
                cleaned,
            )
        elif snippet:
            cleaned = cleaned.replace(snippet, " ")
    for pattern in TITLE_EXCLUDED_PATTERNS:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.IGNORECASE)
    return _clean_string(cleaned)


def _truncate_title_to_max(
    title: str,
    max_chars: int = MAX_PRODUCT_TITLE_CHARS,
    min_chars: int = MIN_PRODUCT_TITLE_CHARS,
) -> str:
    cleaned = _clean_string(title)
    if len(cleaned) <= max_chars:
        return cleaned
    truncated = cleaned[:max_chars].rstrip(" 、,.;；。")
    for separator in (" ", "　"):
        boundary = truncated.rfind(separator)
        if boundary >= min_chars:
            return truncated[:boundary].rstrip(" 、,.;；。")
    return truncated or cleaned[:max_chars]


def _title_dedupe_key(value: Any) -> str:
    normalized = normalize_text(_stringify_value(value))
    return re.sub(r"[\s　\-_/・、,.]+", "", normalized)


def _title_key_covered_by_seen_tokens(key: str, seen_keys: List[str]) -> bool:
    if not key:
        return False
    remaining = key
    matched = False
    for seen_key in sorted(seen_keys, key=len, reverse=True):
        if len(seen_key) < 2:
            continue
        if remaining.startswith(seen_key):
            remaining = remaining[len(seen_key):]
            matched = True
            if not remaining:
                return True
    if matched and remaining in TITLE_GENERIC_SUFFIX_KEYS:
        return any(seen_key.endswith(remaining) for seen_key in seen_keys)
    return matched and not remaining


def _title_seen_token_keys(parts: List[str]) -> List[str]:
    keys: List[str] = []
    for part in parts:
        for token in re.split(r"[\s　]+", _clean_string(part)):
            key = _title_dedupe_key(token)
            if key and key not in keys:
                keys.append(key)
    return keys


def _dedupe_title_terms(title: str) -> str:
    tokens = [token for token in re.split(r"[\s　]+", _clean_string(title)) if token]
    if len(tokens) < 2:
        return _clean_string(title)

    parts: List[str] = []
    seen_keys: List[str] = []
    for token in tokens:
        key = _title_dedupe_key(token)
        if not key:
            continue
        if key in seen_keys or _title_key_covered_by_seen_tokens(key, seen_keys):
            continue
        parts.append(token)
        seen_keys.append(key)
    return _clean_string(" ".join(parts))


def _append_title_part(parts: List[str], part: Any, max_chars: int) -> bool:
    cleaned = _clean_string(part)
    if not cleaned:
        return False
    existing = _clean_string(" ".join(parts))
    key = cleaned.casefold()
    dedupe_key = _title_dedupe_key(cleaned)
    existing_dedupe_key = _title_dedupe_key(existing)
    seen_token_keys = _title_seen_token_keys(parts)
    if any(existing_part.casefold() == key for existing_part in parts):
        return False
    if dedupe_key and (
        dedupe_key in seen_token_keys
        or (existing_dedupe_key and dedupe_key in existing_dedupe_key)
        or _title_key_covered_by_seen_tokens(dedupe_key, seen_token_keys)
    ):
        return False
    if existing and cleaned in existing:
        return False
    candidate = _clean_string(" ".join(parts + [cleaned]))
    if len(candidate) > max_chars:
        return False
    parts.append(cleaned)
    return True


def _title_keyword_parts(value: Any, excluded_snippets: List[str]) -> List[str]:
    if not isinstance(value, list):
        return []
    parts: List[str] = []
    for item in value:
        part = _title_source_snippet(item, excluded_snippets, max_chars=30)
        if part:
            parts.append(part)
    return parts


def _title_tokens(title: str, excluded_snippets: List[str]) -> List[str]:
    stripped = _strip_title_excluded_terms(title, excluded_snippets)
    return [token for token in re.split(r"[\s　]+", stripped) if token]


def _title_source_snippet(
    value: Any,
    excluded_snippets: List[str],
    max_chars: int = 60,
) -> str:
    stripped = _strip_title_excluded_terms(_stringify_value(value), excluded_snippets)
    return _title_snippet(stripped, max_chars=max_chars)


def _dedupe_brand_candidates(values: List[str]) -> List[str]:
    """Clean, drop empties, and dedupe (case/whitespace-insensitive) keeping order."""
    seen: set = set()
    result: List[str] = []
    for value in values:
        cleaned = _clean_string(value)
        if not cleaned:
            continue
        key = normalize_text(cleaned)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(cleaned)
    return result


def _first_title_token(title: Any) -> str:
    cleaned = _clean_string(title)
    if not cleaned:
        return ""
    return cleaned.split(" ", 1)[0]


def _resolve_brand(
    brand_store: BrandStore,
    ai_raw: Dict[str, Any],
    description: Dict[str, Any],
) -> Tuple[str, Dict[str, Any], str]:
    """Resolve brand from the LLM payload via ordered candidates + cross-field fallback.

    Candidates are tried most-specific to most-general; the first one that matches
    the brand table wins. Returns (brand_name, brand_id_obj, brand_raw), where
    brand_raw is the original printed brand kept for title extension (behavior
    unchanged vs. matching only the printed name).
    """
    brand_raw = _clean_string(ai_raw.get("brand_name", ""))

    raw_candidates: List[str] = [brand_raw]
    candidates_field = ai_raw.get("brand_candidates")
    if isinstance(candidates_field, list):
        raw_candidates.extend(str(item) for item in candidates_field)

    details = description.get("product_details") if isinstance(description, dict) else {}
    if isinstance(details, dict):
        raw_candidates.append(_clean_string(details.get("brand", "")))

    # Title first token is the weakest signal: consulted only after everything
    # more specific has failed (it dedupes away when it equals an earlier value).
    raw_candidates.append(_first_title_token(ai_raw.get("title", "")))

    for candidate in _dedupe_brand_candidates(raw_candidates):
        match = brand_store.match(candidate)
        if match:
            return match["brand_name"], dict(match["brand_id_obj"]), brand_raw

    return "", empty_brand_id_obj(), brand_raw


def _extend_title_to_minimum(
    title: str,
    *,
    description: Dict[str, Any],
    brand_name: str = "",
    brand_raw: str = "",
    language: str = DEFAULT_LANGUAGE,
    min_chars: int = MIN_PRODUCT_TITLE_CHARS,
    max_chars: int = MAX_PRODUCT_TITLE_CHARS,
    excluded_snippets: Optional[List[str]] = None,
) -> str:
    title_clean = _dedupe_title_terms(
        _strip_title_excluded_terms(title, excluded_snippets or [])
    )
    title_over_max = len(title_clean) > max_chars
    if len(title_clean) >= min_chars:
        if len(title_clean) <= max_chars:
            return title_clean

    parts: List[str] = [] if title_over_max else ([title_clean] if title_clean else [])
    details = description.get("product_details") if isinstance(description, dict) else {}
    if not isinstance(details, dict):
        details = {}
    excluded = excluded_snippets or []

    core_parts = [
        brand_raw,
        details.get("brand"),
        brand_name,
        details.get("product_name"),
        details.get("model_number"),
        details.get("color"),
    ]

    keywords = description.get("search_keywords") if isinstance(description, dict) else []
    candidate_parts = (
        [_title_snippet(value) for value in core_parts]
        + _title_keyword_parts(keywords, excluded)
    )
    if title_over_max:
        candidate_parts.extend(
            token for token in _title_tokens(title_clean, excluded)
            if len(token) <= 8
        )

    for part in candidate_parts:
        _append_title_part(parts, part, max_chars)

    candidate = _clean_string(" ".join(parts))
    if len(candidate) > max_chars:
        return _truncate_title_to_max(candidate, max_chars, min_chars)
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

    def _classification_reasoning(self) -> Any:
        """Reasoning override for the classification stages (read live).

        Defaults to the client's reasoning when the settings object predates the
        classification reasoning switch (e.g. lightweight test doubles).
        """
        return getattr(self.settings, "classification_reasoning", USE_CLIENT_REASONING)

    def _record_stage(
        self,
        stage: str,
        attempts: list,
        messages: list,
        raw_response: Optional[Dict[str, Any]] = None,
        parsed: Optional[Dict[str, Any]] = None,
    ) -> None:
        if recorder is None:
            return
        recorder.record_llm_stage(
            request_id=obs_ctx.get_request_id() or "",
            stage=stage,
            attempts=attempts,
            messages=messages,
            raw_response=raw_response,
            parsed=parsed,
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

        # Category is decided from the first image only (the prompt already treats
        # it as the primary evidence). Sending just the first image keeps the
        # vision call lean and fast; prices are handled by the dedicated price
        # link, so the extra images are no longer needed here.
        first_image_bytes, first_mime = images[0]
        data_urls = [image_bytes_to_data_url(first_image_bytes, first_mime)]
        ai_raw, vision_attempts = self._call_fast_classification_llm(
            data_urls,
            language,
            model_override=vision_model_override,
        )
        attempts_by_stage["fast_vision"] = vision_attempts

        title = _clean_string(ai_raw.get("title", ""))
        simple_description = _clean_string(ai_raw.get("simple_description", ""))
        top_level_category = _clean_string(ai_raw.get("top_level_category", ""))
        # Size comes from the fast vision stage and is only trusted when the model
        # found explicit size text in the image; otherwise it stays null.
        product_size = _clean_string(ai_raw.get("product_size", "")) or None
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
            # Product size extracted from the first image by the fast vision stage;
            # null when no explicit size information was visible.
            "product_size": product_size,
            # Price now comes from the dedicated price link; these fields are kept
            # (null/empty) only so existing clients do not break.
            "tax_excluded": None,
            "tax_included": None,
            "prices": [],
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

    def extract_prices(
        self,
        images: List[Tuple[bytes, str]],
        debug: bool = False,
        model_override: Optional[str] = None,
        started_at: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Standalone fast price link: a single vision call reading visible prices.

        Returns only price fields (no title/brand/category/description), so the
        app can show a price quickly without waiting on the full analyze flow.
        Direct prices are visible-only; prices is an AI reference range.
        """
        if not images:
            raise BadRequestError("Image list is required.")
        started = float(started_at) if started_at is not None else time.monotonic()
        data_urls = [
            image_bytes_to_data_url(image_bytes, mime_type)
            for image_bytes, mime_type in images
        ]
        ai_raw, attempts = self._call_price_only_llm(
            data_urls,
            model_override=model_override,
        )
        result: Dict[str, Any] = {
            **_normalize_price_fields(ai_raw),
            "timings": {
                "price_ms": round((time.monotonic() - started) * 1000, 2),
            },
        }
        if debug:
            result["_debug"] = {
                "price_ai_raw": ai_raw,
                "attempts": {"price_only": [a.__dict__ for a in attempts]},
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

        description_raw = ai_raw.get("description")
        description_struct = _normalize_description(description_raw)
        brand_name, brand_id_obj, brand_raw = _resolve_brand(
            self.brand_store, ai_raw, description_struct
        )
        title = _extend_title_to_minimum(
            ai_raw.get("title", ""),
            description=description_struct,
            brand_name=brand_name,
            brand_raw=brand_raw,
            language=language,
            excluded_snippets=_extract_title_exclusion_snippets(description_raw),
        )

        result: Dict[str, Any] = {
            "title": title,
            "description": description_struct,
            "brand_name": brand_name,
            "brand_id_obj": brand_id_obj,
            # Price is handled by the dedicated price link; kept null/empty here
            # only so the merged response preserves the fields for clients.
            "tax_excluded": None,
            "tax_included": None,
            "prices": [],
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

        description_raw = ai_raw.get("description")
        description_struct = _normalize_description(description_raw)
        brand_name, brand_id_obj, brand_raw = _resolve_brand(
            self.brand_store, ai_raw, description_struct
        )
        title = _extend_title_to_minimum(
            ai_raw.get("title", ""),
            description=description_struct,
            brand_name=brand_name,
            brand_raw=brand_raw,
            language=language,
            excluded_snippets=_extract_title_exclusion_snippets(description_raw),
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

        fallback_result = self._classify_title_fallback_image_to_paths(
            image_bytes=image_bytes,
            mime_type=mime_type,
            language=language,
            vision_model_override=vision_model_override,
            category_model_override=category_model_override,
        )
        if fallback_result:
            return fallback_result

        raise BadRequestError("Image recognition failed to return a category path.")

    def _call_title_image_fallback_llm(
        self,
        image_data_urls: List[str],
        language: str,
        model_override: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any], List[AttemptRecord]]:
        if not image_data_urls:
            raise BadRequestError("Image list is empty.")
        user_prompt = prompt_store.get("TITLE_IMAGE_FALLBACK_USER_PROMPT").format(
            language_label=_language_label(language)
        )
        image_payloads: List[Dict[str, Any]] = []
        image_count = len(image_data_urls)
        for index, url in enumerate(image_data_urls, start=1):
            image_payloads.append(
                {
                    "type": "text",
                    "text": (
                        f"Image {index} of {image_count}: inspect this image carefully. "
                        "Extract only the evidence needed for category matching."
                    ),
                }
            )
            image_payloads.append({"type": "image_url", "image_url": {"url": url}})
        messages = [
            {"role": "system", "content": prompt_store.render_system("TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT")},
            {
                "role": "user",
                "content": [{"type": "text", "text": user_prompt}] + image_payloads,
            },
        ]
        primary = model_override or self.settings.vision_model
        fallbacks = self.settings.vision_fallback_models

        try:
            parsed, raw_response, attempts = self.vision_caller.call_and_parse(
                stage="title_image_fallback",
                primary_model=primary,
                fallback_models=fallbacks,
                messages=messages,
                temperature=0.2,
                max_tokens=3000,
            )
        except LLMAllAttemptsFailedError as exc:
            self._record_stage(
                stage="title_image_fallback",
                attempts=[a.__dict__ for a in exc.attempts],
                messages=messages,
            )
            raise
        self._record_stage(
            stage="title_image_fallback",
            attempts=[a.__dict__ for a in attempts],
            messages=messages,
            raw_response=raw_response,
            parsed=parsed,
        )
        return parsed, raw_response, attempts

    def _call_fast_classification_llm(
        self,
        image_data_urls: List[str],
        language: str,
        model_override: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], List[AttemptRecord]]:
        if not image_data_urls:
            raise BadRequestError("Image list is empty.")
        user_prompt = prompt_store.get("FAST_CLASSIFICATION_USER_PROMPT").format(
            language_label=_language_label(language)
        )
        image_payloads: List[Dict[str, Any]] = []
        for url in image_data_urls:
            image_payloads.append(
                {
                    "type": "text",
                    "text": "Use this image as the primary category evidence.",
                }
            )
            image_payloads.append({"type": "image_url", "image_url": {"url": url}})
        messages = [
            {"role": "system", "content": prompt_store.render_system("FAST_CLASSIFICATION_SYSTEM_PROMPT")},
            {
                "role": "user",
                "content": [{"type": "text", "text": user_prompt}] + image_payloads,
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
                reasoning=self._classification_reasoning(),
            )
        except LLMAllAttemptsFailedError as exc:
            self._record_stage(
                stage="fast_vision",
                attempts=[a.__dict__ for a in exc.attempts],
                messages=messages,
            )
            raise
        self._record_stage(
            stage="fast_vision",
            attempts=[a.__dict__ for a in attempts],
            messages=messages,
            raw_response=raw_response,
            parsed=parsed,
        )
        return parsed, attempts

    def _call_price_only_llm(
        self,
        image_data_urls: List[str],
        model_override: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], List[AttemptRecord]]:
        if not image_data_urls:
            raise BadRequestError("Image list is empty.")
        image_payloads: List[Dict[str, Any]] = []
        image_count = len(image_data_urls)
        for index, url in enumerate(image_data_urls, start=1):
            image_payloads.append(
                {
                    "type": "text",
                    "text": (
                        f"Image {index} of {image_count}: inspect this image for any "
                        "clearly visible actual product price and product evidence for "
                        "a realistic AI reference price range."
                    ),
                }
            )
            image_payloads.append({"type": "image_url", "image_url": {"url": url}})
        messages = [
            {"role": "system", "content": prompt_store.render_system("PRICE_ONLY_SYSTEM_PROMPT")},
            {
                "role": "user",
                "content": [{"type": "text", "text": prompt_store.get("PRICE_ONLY_USER_PROMPT")}] + image_payloads,
            },
        ]
        primary = (
            model_override
            or getattr(self.settings, "price_model", "")
            or self.settings.vision_model
        )
        fallbacks = self.settings.vision_fallback_models

        try:
            parsed, raw_response, attempts = self.vision_caller.call_and_parse(
                stage="price_only",
                primary_model=primary,
                fallback_models=fallbacks,
                messages=messages,
                temperature=0.1,
                max_tokens=300,
            )
        except LLMAllAttemptsFailedError as exc:
            self._record_stage(
                stage="price_only",
                attempts=[a.__dict__ for a in exc.attempts],
                messages=messages,
            )
            raise
        self._record_stage(
            stage="price_only",
            attempts=[a.__dict__ for a in attempts],
            messages=messages,
            raw_response=raw_response,
            parsed=parsed,
        )
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
            system_prompt = prompt_store.render_system("PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT")
            user_prompt = prompt_store.get("PRODUCT_DATA_FALLBACK_USER_PROMPT").format(
                language_label=_language_label(language)
            )
            stage = "product_data_fallback"
        else:
            system_prompt = prompt_store.render_system("PRODUCT_DATA_SYSTEM_PROMPT")
            user_prompt = prompt_store.get("PRODUCT_DATA_USER_PROMPT").format(
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
            self._record_stage(
                stage=stage,
                attempts=[a.__dict__ for a in exc.attempts],
                messages=messages,
            )
            raise
        self._record_stage(
            stage=stage,
            attempts=[a.__dict__ for a in attempts],
            messages=messages,
            raw_response=raw_response,
            parsed=parsed,
        )
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
        user_prompt = prompt_store.get("PRODUCT_DATA_REGENERATION_USER_PROMPT").format(
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
            {"role": "system", "content": prompt_store.render_system("PRODUCT_DATA_REGENERATION_SYSTEM_PROMPT")},
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
            self._record_stage(
                stage="product_data_regeneration",
                attempts=[a.__dict__ for a in exc.attempts],
                messages=messages,
            )
            raise
        self._record_stage(
            stage="product_data_regeneration",
            attempts=[a.__dict__ for a in attempts],
            messages=messages,
            raw_response=raw_response,
            parsed=parsed,
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
        user_prompt = prompt_store.get("PRODUCT_TITLE_CATEGORY_USER_PROMPT").format(
            title=title,
            language_label=_language_label(language),
        )
        messages = [
            {"role": "system", "content": prompt_store.render_system("PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT")},
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
                reasoning=self._classification_reasoning(),
            )
        except LLMAllAttemptsFailedError as exc:
            self._record_stage(
                stage="title_category",
                attempts=[a.__dict__ for a in exc.attempts],
                messages=messages,
            )
            raise
        self._record_stage(
            stage="title_category",
            attempts=[a.__dict__ for a in attempts],
            messages=messages,
            raw_response=raw_response,
            parsed=parsed,
        )
        return parsed, attempts

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
        user_prompt = prompt_store.get("CATEGORY_USER_PROMPT_TEMPLATE").format(
            title=title,
            description=description,
            brand=brand_for_prompt,
            group_name=group_name,
            candidate_paths=candidate_block,
        )
        messages = [
            {"role": "system", "content": prompt_store.render_system("CATEGORY_SYSTEM_PROMPT")},
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
                reasoning=self._classification_reasoning(),
            )
        except LLMAllAttemptsFailedError as exc:
            self._record_stage(
                stage="category",
                attempts=[a.__dict__ for a in exc.attempts],
                messages=messages,
            )
            raise
        self._record_stage(
            stage="category",
            attempts=[a.__dict__ for a in attempts],
            messages=messages,
            raw_response=raw_response,
            parsed=parsed,
        )

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

    def _classify_title_fallback_image_to_paths(
        self,
        image_bytes: bytes,
        mime_type: str,
        language: str,
        vision_model_override: Optional[str] = None,
        category_model_override: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        data_url = image_bytes_to_data_url(image_bytes, mime_type)
        ai_raw, _, _ = self._call_title_image_fallback_llm(
            [data_url],
            language,
            model_override=vision_model_override,
        )

        title = _clean_string(ai_raw.get("title", ""))
        description_text = _clean_string(
            ai_raw.get("simple_description", "") or ai_raw.get("description", "")
        )
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
