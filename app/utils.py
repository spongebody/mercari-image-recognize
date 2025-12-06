import base64
import json
import re
import unicodedata
from typing import Any, Dict, Iterable, List, Optional

from .constants import DEFAULT_LANGUAGE, PRICE_MAX, PRICE_MIN


def compress_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.replace("\u00ae", "").replace("\u2122", "").replace("\u00a9", "")
    normalized = normalized.lower().strip()
    normalized = compress_whitespace(normalized)
    return normalized


def normalize_category_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return compress_whitespace(normalized.lower())


def parse_bool_param(raw: Optional[str], default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def image_bytes_to_data_url(data: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def safe_json_loads(raw: str) -> Any:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to remove leading/trailing text before/after braces
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            snippet = cleaned[start : end + 1]
            return json.loads(snippet)
        raise


def _coerce_price(value: Any) -> Optional[int]:
    try:
        price = int(float(value))
    except (TypeError, ValueError):
        return None
    if PRICE_MIN <= price <= PRICE_MAX:
        return price
    return None


def normalize_price_list(raw_prices: Any) -> List[int]:
    """Normalize various price structures into an ordered list (most likely first)."""
    numbers: List[int] = []

    def collect_ordered(values: Iterable[Any]) -> None:
        for item in values:
            price = _coerce_price(item)
            if price is not None:
                numbers.append(price)

    if isinstance(raw_prices, dict):
        for key in ("prices", "values", "list", "options", "candidates", "suggestions"):
            if isinstance(raw_prices.get(key), (list, tuple)):
                collect_ordered(raw_prices.get(key))
        for key in ("best", "primary", "main", "price", "value", "estimate"):
            price = _coerce_price(raw_prices.get(key))
            if price is not None:
                numbers.append(price)
        for key in ("low", "mid", "high", "median", "average", "avg", "mean", "min", "max"):
            price = _coerce_price(raw_prices.get(key))
            if price is not None:
                numbers.append(price)
    elif isinstance(raw_prices, (list, tuple)):
        collect_ordered(raw_prices)
    elif raw_prices is not None:
        value = _coerce_price(raw_prices)
        if value is not None:
            numbers.append(value)

    # Preserve order but deduplicate sequentially
    seen = set()
    ordered_unique: List[int] = []
    for price in numbers:
        if price in seen:
            continue
        seen.add(price)
        ordered_unique.append(price)

    if not ordered_unique:
        return []

    while len(ordered_unique) < 3:
        ordered_unique.append(ordered_unique[-1])

    return ordered_unique[:3]


def ensure_language(value: Optional[str]) -> str:
    if not value:
        return DEFAULT_LANGUAGE
    return value
