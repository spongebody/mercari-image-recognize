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


def normalize_price_info(raw_prices: Any, raw_range: Any = None) -> Dict[str, Any]:
    low: Optional[int] = None
    mid: Optional[int] = None
    high: Optional[int] = None
    range_min: Optional[int] = None
    range_max: Optional[int] = None
    numbers: List[int] = []

    def collect_from_iter(values: Iterable[Any]) -> None:
        for item in values:
            price = _coerce_price(item)
            if price is not None:
                numbers.append(price)

    if isinstance(raw_range, dict):
        range_min = _coerce_price(raw_range.get("min") or raw_range.get("low"))
        range_max = _coerce_price(raw_range.get("max") or raw_range.get("high"))
    elif raw_range is not None:
        value = _coerce_price(raw_range)
        if value is not None:
            range_min = range_min or value
            range_max = range_max or value

    if isinstance(raw_prices, dict):
        low = _coerce_price(
            raw_prices.get("low")
            or raw_prices.get("floor")
            or raw_prices.get("minimum")
            or raw_prices.get("min")
        )
        mid = _coerce_price(
            raw_prices.get("mid")
            or raw_prices.get("median")
            or raw_prices.get("average")
            or raw_prices.get("avg")
            or raw_prices.get("mean")
        )
        high = _coerce_price(
            raw_prices.get("high")
            or raw_prices.get("ceiling")
            or raw_prices.get("maximum")
            or raw_prices.get("max")
        )

        range_obj = raw_prices.get("range") or raw_prices.get("band") or {}
        if isinstance(range_obj, dict):
            range_min = _coerce_price(range_obj.get("min") or range_obj.get("low"))
            range_max = _coerce_price(range_obj.get("max") or range_obj.get("high"))

        for key in ("prices", "values", "list", "options", "suggestions"):
            if isinstance(raw_prices.get(key), (list, tuple)):
                collect_from_iter(raw_prices.get(key))

    elif isinstance(raw_prices, (list, tuple)):
        collect_from_iter(raw_prices)
    elif raw_prices is not None:
        # Single scalar
        value = _coerce_price(raw_prices)
        if value is not None:
            numbers.append(value)

    # Merge explicit low/mid/high into numbers for ordering
    for explicit in (low, mid, high):
        if explicit is not None:
            numbers.append(explicit)

    numbers = [p for p in numbers if p is not None]
    numbers.sort()

    if numbers:
        low = low or numbers[0]
        high = high or numbers[-1]
        if mid is None:
            if len(numbers) >= 3:
                mid = numbers[len(numbers) // 2]
            elif len(numbers) == 2:
                mid = int(sum(numbers) / 2)
            else:
                mid = numbers[0]

    if range_min is None and numbers:
        range_min = numbers[0]
    if range_max is None and numbers:
        range_max = numbers[-1]

    suggestions = [p for p in (low, mid, high) if p is not None]
    suggestions = sorted(suggestions)
    while suggestions and len(suggestions) < 3:
        suggestions.append(suggestions[-1])

    if suggestions:
        low, mid, high = suggestions[0], suggestions[1], suggestions[-1]

    tiers = {"low": low, "mid": mid, "high": high, "range": {"min": range_min, "max": range_max}}

    return {"tiers": tiers, "list": suggestions, "range": {"min": range_min, "max": range_max}}


def ensure_language(value: Optional[str]) -> str:
    if not value:
        return DEFAULT_LANGUAGE
    return value
