import difflib
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
    VISION_SYSTEM_PROMPT,
    VISION_USER_PROMPT_TEMPLATE,
)
from .utils import (
    clean_prices,
    compress_whitespace,
    image_bytes_to_data_url,
    normalize_category_label,
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
    ):
        self.settings = settings
        self.brand_store = brand_store
        self.category_store = category_store
        self.vision_client = vision_client
        self.category_client = category_client

    def analyze(
        self,
        image_bytes: bytes,
        mime_type: str,
        language: str,
        debug: bool = False,
        category_limit: int = 1,
    ) -> Dict[str, Any]:
        if language not in SUPPORTED_LANGUAGES:
            raise BadRequestError("Unsupported language.")
        category_limit = max(1, min(int(category_limit), 3))

        data_url = image_bytes_to_data_url(image_bytes, mime_type)
        ai_raw, ai_full = self._call_vision_llm(data_url, language)

        title = _clean_string(ai_raw.get("title", ""))
        description = _clean_string(ai_raw.get("description", ""))
        prices = clean_prices(ai_raw.get("prices", []))
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

        result: Dict[str, Any] = {
            "title": title,
            "description": description,
            "prices": prices,
            "categories": categories,
            "brand_name": brand_name,
            "brand_id": brand_id,
        }

        if debug:
            result["_debug"] = {
                "ai_raw": ai_raw,
                "group_name": group_name,
                "llm_category_raw": llm_category_raw,
            }

        return result

    def _call_vision_llm(
        self, image_data_url: str, language: str
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

        content, raw_response = self.vision_client.chat(
            model=self.settings.vision_model,
            messages=messages,
            temperature=0.2,
            max_tokens=800,
        )
        try:
            parsed = safe_json_loads(content)
        except Exception as exc:
            raise BadRequestError("Failed to parse vision LLM JSON.") from exc
        if not isinstance(parsed, dict):
            raise BadRequestError("Vision LLM did not return a JSON object.")
        return parsed, raw_response

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
        try:
            parsed = safe_json_loads(content)
        except Exception as exc:
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
