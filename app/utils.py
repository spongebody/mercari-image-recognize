import base64
import json
import re
import unicodedata
from typing import Any, Iterable, List, Optional

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


def clean_prices(values: Iterable[Any]) -> List[int]:
    prices = []
    for item in values:
        try:
            price = int(float(item))
        except (TypeError, ValueError):
            continue
        if PRICE_MIN <= price <= PRICE_MAX:
            prices.append(price)

    if not prices:
        return []

    prices.sort()
    while len(prices) < 3:
        prices.append(prices[-1])
    return prices[:3]


def ensure_language(value: Optional[str]) -> str:
    if not value:
        return DEFAULT_LANGUAGE
    return value
