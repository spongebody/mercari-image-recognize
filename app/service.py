import difflib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import Settings
from .constants import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, TOP_LEVEL_CATEGORIES
from .data.brands import BrandStore
from .data.categories import CategoryStore
from .errors import BadRequestError
from .llm.client import OpenRouterClient
from .llm.prompts import (
    CATEGORY_SYSTEM_PROMPT,
    CATEGORY_USER_PROMPT_TEMPLATE,
    PRICE_SYSTEM_PROMPT,
    PRICE_USER_PROMPT_TEMPLATE,
    VISION_SYSTEM_PROMPT,
    VISION_USER_PROMPT_TEMPLATE,
)
from .utils import (
    compress_whitespace,
    image_bytes_to_data_url,
    normalize_category_label,
    normalize_price_info,
    safe_json_loads,
)


LANGUAGE_LABELS = {"ja": "Japanese", "en": "English", "zh": "Chinese"}


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


class MercariAnalyzer:
    def __init__(
        self,
        settings: Settings,
        brand_store: BrandStore,
        category_store: CategoryStore,
        vision_client: OpenRouterClient,
        category_client: OpenRouterClient,
        price_client: OpenRouterClient,
    ):
        self.settings = settings
        self.brand_store = brand_store
        self.category_store = category_store
        self.vision_client = vision_client
        self.category_client = category_client
        self.price_client = price_client
        self._logs_dir = Path(__file__).resolve().parent.parent / "logs"

    def analyze(
        self,
        image_bytes: bytes,
        mime_type: str,
        language: str,
        debug: bool = False,
        category_limit: int = 1,
        price_strategy: str = "dedicated",
    ) -> Dict[str, Any]:
        if language not in SUPPORTED_LANGUAGES:
            raise BadRequestError("Unsupported language.")
        category_limit = max(1, min(int(category_limit), 3))
        price_strategy = price_strategy or "dedicated"
        if price_strategy not in {"dedicated", "vision_online"}:
            price_strategy = "dedicated"

        data_url = image_bytes_to_data_url(image_bytes, mime_type)
        use_online = price_strategy == "vision_online"
        ai_raw, ai_full = self._call_vision_llm(
            data_url,
            language,
            force_online=use_online,
        )

        title = _clean_string(ai_raw.get("title", ""))
        description = _clean_string(ai_raw.get("description", ""))
        price_info = normalize_price_info(ai_raw.get("prices", []), ai_raw.get("price_range"))
        prices = price_info["tiers"]
        price_points = price_info["list"]
        price_range = price_info["range"]
        top_level_category = _clean_string(ai_raw.get("top_level_category", ""))
        brand_raw = _clean_string(ai_raw.get("brand_name", ""))

        brand_match = self.brand_store.match(brand_raw)
        brand_name = brand_match["name"] if brand_match else ""
        brand_id = brand_match["id"] if brand_match else ""

        group_name = _map_top_level_category(top_level_category)

        categories: List[Dict[str, str]] = []
        llm_category_raw: Optional[Dict[str, Any]] = None

        if group_name:
            categories, llm_category_raw = self._choose_categories(
                title=title or ai_raw.get("title", ""),
                description=description or ai_raw.get("description", ""),
                brand_for_prompt=brand_raw or brand_name,
                group_name=group_name,
                category_limit=category_limit,
            )

        price_raw = None
        price_error = None
        price_source = "vision"
        if price_strategy == "dedicated":
            try:
                price_info_model, price_raw, price_error = self._predict_price_with_model(
                    title=title,
                    description=description,
                    brand=brand_raw or brand_name,
                    group_name=group_name or "",
                    category_candidates=categories,
                    language=language,
                    vision_prices=price_info,
                )
                prices = price_info_model["tiers"]
                price_points = price_info_model["list"]
                price_range = price_info_model["range"]
                price_source = "price_model" if not price_error else "price_model_with_error"
            except Exception as exc:
                price_error = str(exc)
                self._log_raw("price_unhandled_error", {"error": price_error})
                price_source = "price_model_failed"

        result: Dict[str, Any] = {
            "title": title,
            "description": description,
            "prices": prices,
            "price_points": price_points,
            "price_range": price_range,
            "price_low": prices.get("low"),
            "price_mid": prices.get("mid"),
            "price_high": prices.get("high"),
            "categories": categories,
            "brand_name": brand_name,
            "brand_id": brand_id,
            "price_strategy": price_strategy,
            "price_source": price_source,
            "price_error": price_error,
        }

        if debug:
            result["_debug"] = {
                "ai_raw": ai_raw,
                "group_name": group_name,
                "llm_category_raw": llm_category_raw,
                "price_raw": price_raw,
                "price_strategy": price_strategy,
            }

        return result

    def _call_vision_llm(
        self, image_data_url: str, language: str, force_online: bool = False
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        user_prompt = VISION_USER_PROMPT_TEMPLATE.format(language_label=_language_label(language))
        messages = [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ]
        model = (
            self.settings.vision_model_online
            if force_online and self.settings.vision_model_online
            else self.settings.vision_model
        )
        content, raw_response = self.vision_client.chat(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=800,
        )
        self._log_raw("vision_content", content)
        self._log_raw("vision_raw_response", raw_response)
        try:
            parsed = safe_json_loads(content)
        except Exception as exc:
            self._log_raw("vision_parse_error", {"error": str(exc), "content": content})
            raise BadRequestError("Failed to parse vision LLM JSON.") from exc
        if not isinstance(parsed, dict):
            raise BadRequestError("Vision LLM did not return a JSON object.")
        return parsed, raw_response

    def _predict_price_with_model(
        self,
        title: str,
        description: str,
        brand: str,
        group_name: str,
        category_candidates: List[Dict[str, str]],
        language: str,
        vision_prices: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]], Optional[str]]:
        if not self.settings.price_model:
            raise BadRequestError("PRICE_MODEL is not configured.")

        candidate_names = ", ".join(cat.get("name", "") for cat in category_candidates if cat.get("name"))
        user_prompt = PRICE_USER_PROMPT_TEMPLATE.format(
            title=title,
            description=description,
            brand=brand,
            group_name=group_name,
            category_candidates=candidate_names or "N/A",
            language_label=_language_label(language),
            vision_price_hints=vision_prices,
        )
        messages = [
            {"role": "system", "content": PRICE_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        content: Optional[str] = None
        raw_response: Optional[Dict[str, Any]] = None
        parsed: Optional[Dict[str, Any]] = None
        error: Optional[str] = None

        try:
            content, raw_response = self.price_client.chat(
                model=self.settings.price_model,
                messages=messages,
                temperature=0.3,
                max_tokens=700,
            )
        except Exception as exc:
            error = str(exc)
            self._log_raw("price_call_error", {"error": error})
        else:
            self._log_raw("price_content", content)
            self._log_raw("price_raw_response", raw_response)
            try:
                parsed = safe_json_loads(content or "")
            except Exception as exc:
                error = str(exc)
                self._log_raw("price_parse_error", {"error": error, "content": content})

        if isinstance(parsed, dict):
            price_block = parsed.get("prices") or parsed
        else:
            price_block = parsed

        normalized = normalize_price_info(price_block, None)
        return normalized, parsed, error

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
        category_limit: int,
    ) -> Tuple[List[Dict[str, str]], Optional[Dict[str, Any]]]:
        candidates = self.category_store.get_categories_by_group(group_name)
        if not candidates:
            return [], None

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

        content, raw_response = self.category_client.chat(
            model=self.settings.category_model,
            messages=messages,
            temperature=0.1,
            max_tokens=600,
        )
        self._log_raw("category_content", content)
        self._log_raw("category_raw_response", raw_response)
        try:
            parsed = safe_json_loads(content)
        except Exception as exc:
            self._log_raw("category_parse_error", {"error": str(exc), "content": content})
            raise BadRequestError("Failed to parse category LLM JSON.") from exc
        if not isinstance(parsed, dict):
            raise BadRequestError("Category LLM did not return a JSON object.")

        ordered_paths: List[str] = []
        best = parsed.get("best_target_path")
        if isinstance(best, str) and best.strip():
            ordered_paths.append(best)

        alternatives = parsed.get("alternatives", [])
        if isinstance(alternatives, list):
            for alt in alternatives:
                if not isinstance(alt, dict):
                    continue
                path = alt.get("target_path")
                if isinstance(path, str) and path.strip():
                    ordered_paths.append(path)

        category_limit = max(1, min(category_limit, 3))
        seen = set()
        results: List[Dict[str, str]] = []
        for path in ordered_paths:
            path_clean = compress_whitespace(path)
            key = (group_name, path_clean)
            if key in seen:
                continue
            seen.add(key)
            match = self.category_store.find_category(group_name, path_clean)
            if match:
                results.append({"id": match["id"], "name": match["name"]})
            if len(results) >= category_limit:
                break

        return results, parsed
