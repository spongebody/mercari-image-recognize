import difflib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import Settings
from .constants import DEFAULT_LANGUAGE, SUPPORTED_LANGUAGES, TOP_LEVEL_CATEGORIES
from .data.brands import BrandStore
from .data.categories import CategoryStore
from .errors import BadRequestError, LLMRequestError
from .llm.client import OpenRouterClient
from .llm.prompts import (
    CATEGORY_SYSTEM_PROMPT,
    CATEGORY_USER_PROMPT_TEMPLATE,
    PRICE_SYSTEM_PROMPT,
    PRICE_USER_PROMPT_TEMPLATE,
    PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT,
    PRODUCT_TITLE_CATEGORY_USER_PROMPT,
    VISION_SYSTEM_PROMPT,
    VISION_SYSTEM_PROMPT_WITH_PRICE,
    VISION_SYSTEM_PROMPT_WITH_SEARCH,
    VISION_USER_PROMPT_TEMPLATE,
    VISION_USER_PROMPT_WITH_PRICE,
    VISION_USER_PROMPT_TEMPLATE_WITH_WITH_SEARCH,
)
from .utils import (
    compress_whitespace,
    fetch_image_from_url,
    image_bytes_to_data_url,
    normalize_category_label,
    normalize_price_list,
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


def _paths_from_categories(
    categories: List[Dict[str, str]],
    include_alternatives: bool = True,
) -> Optional[Dict[str, Any]]:
    if not categories:
        return None
    ordered_paths: List[Tuple[str, str]] = []
    for category in categories:
        path = category.get("name") or ""
        cat_id = category.get("id") or ""
        path = compress_whitespace(path)
        if path and (path, cat_id) not in ordered_paths:
            ordered_paths.append((path, cat_id))
    if not ordered_paths:
        return None
    best_path, best_id = ordered_paths[0]
    payload: Dict[str, Any] = {
        "best_target_path": best_path,
        "best_category_id": best_id,
    }
    if include_alternatives:
        payload["alternatives"] = [
            {"target_path": path, "category_id": cat_id} for path, cat_id in ordered_paths[1:]
        ]
    return payload


def _extract_citations(raw_response: Optional[Dict[str, Any]]) -> List[Dict[str, str]]:
    citations: List[Dict[str, str]] = []
    try:
        choices = (raw_response or {}).get("choices") or []
        if not choices:
            return citations
        annotations = choices[0].get("message", {}).get("annotations", [])
        for ann in annotations:
            if not isinstance(ann, dict):
                continue
            if ann.get("type") != "url_citation":
                continue
            url_info = ann.get("url_citation") or {}
            url = url_info.get("url")
            if url:
                citations.append(
                    {
                        "url": url,
                        "title": url_info.get("title") or "",
                        "content": url_info.get("content") or "",
                    }
                )
    except Exception:
        return citations
    return citations


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
        price_strategy: str = "vision",
        vision_model_override: Optional[str] = None,
        category_model_override: Optional[str] = None,
        price_model_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        if language not in SUPPORTED_LANGUAGES:
            raise BadRequestError("Unsupported language.")
        category_limit = max(1, min(int(category_limit), 3))
        price_strategy = price_strategy or "vision"
        if price_strategy not in {"vision", "dedicated", "vision_online"}:
            price_strategy = "vision"

        data_url = image_bytes_to_data_url(image_bytes, mime_type)
        price_mode = "search" if price_strategy == "vision_online" else ("inline" if price_strategy == "vision" else "none")
        ai_raw, ai_full = self._call_vision_llm(
            data_url,
            language,
            force_online=price_strategy == "vision_online",
            model_override=vision_model_override,
            price_mode=price_mode,
        )

        title = _clean_string(ai_raw.get("title", ""))
        description = _clean_string(ai_raw.get("description", ""))
        prices = normalize_price_list(ai_raw.get("prices", []))
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
                model_override=category_model_override,
            )

        price_raw = None
        price_error = None
        price_citations = _extract_citations(ai_full)
        price_source = "vision_online" if price_mode == "search" else ("vision" if price_mode == "inline" else "none")
        if price_strategy == "dedicated":
            try:
                (
                    price_info_model,
                    price_raw,
                    price_error,
                    price_citations_model,
                ) = self._predict_price_with_model(
                    title=title,
                    description=description,
                    brand=brand_raw or brand_name,
                    group_name=group_name or "",
                    category_candidates=categories,
                    language=language,
                    price_model_override=price_model_override,
                    image_data_url=data_url,
                )
                if price_citations_model:
                    price_citations = price_citations_model
                if price_error or not price_info_model:
                    prices = []
                    price_source = "price_model_failed"
                else:
                    prices = price_info_model
                    price_source = "price_model"
            except Exception as exc:
                price_error = str(exc)
                self._log_raw("price_unhandled_error", {"error": price_error})
                price_source = "price_model_failed"
                prices = []

        result: Dict[str, Any] = {
            "title": title,
            "description": description,
            "prices": prices,
            "categories": categories,
            "brand_name": brand_name,
            "brand_id": brand_id,
            "price_citations": price_citations,
        }
        path_info = _paths_from_categories(categories, include_alternatives=False)
        if path_info:
            result.update(path_info)

        if debug:
            result["_debug"] = {
                "ai_raw": ai_raw,
                "group_name": group_name,
                "llm_category_raw": llm_category_raw,
                "price_raw": price_raw,
                "price_strategy": price_strategy,
                "price_citations": price_citations,
                "price_error": price_error,
                "price_source": price_source,
            }

        return result

    def analyze_title(
        self,
        title: str,
        image_url: Optional[str],
        language: str,
        category_limit: int = 3,
        category_model_override: Optional[str] = None,
        vision_model_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        if language not in SUPPORTED_LANGUAGES:
            raise BadRequestError("Unsupported language.")

        title_clean = _clean_string(title)
        if not title_clean:
            raise BadRequestError("Title is required.")

        category_limit = max(1, min(int(category_limit), 3))
        categories: List[Dict[str, str]] = []
        title_error: Optional[Exception] = None

        try:
            title_payload = self._call_title_category_llm(
                title=title_clean,
                language=language,
                model_override=category_model_override,
            )
            top_level_category = _clean_string(title_payload.get("top_level_category", ""))
            group_name = _map_top_level_category(top_level_category)
            if group_name:
                categories, _ = self._choose_categories(
                    title=title_clean,
                    description="",
                    brand_for_prompt="",
                    group_name=group_name,
                    category_limit=category_limit,
                    model_override=category_model_override,
                )
        except (BadRequestError, LLMRequestError) as exc:
            title_error = exc

        paths_result = _paths_from_categories(categories)
        if paths_result:
            return paths_result

        if not image_url:
            if title_error:
                if isinstance(title_error, LLMRequestError):
                    raise LLMRequestError(
                        f"{title_error} (image_url is required for fallback)"
                    ) from title_error
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
            category_limit=category_limit,
            vision_model_override=vision_model_override,
            category_model_override=category_model_override,
        )
        if fallback_result:
            return fallback_result

        raise BadRequestError("Image recognition failed to return a category path.")

    def _call_vision_llm(
        self,
        image_data_url: str,
        language: str,
        force_online: bool = False,
        model_override: Optional[str] = None,
        price_mode: str = "none",
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        price_mode = price_mode or "none"
        if price_mode not in {"none", "inline", "search"}:
            price_mode = "none"
        if price_mode == "search":
            user_prompt_template = VISION_USER_PROMPT_TEMPLATE_WITH_WITH_SEARCH
            system_prompt = VISION_SYSTEM_PROMPT_WITH_SEARCH
        elif price_mode == "inline":
            user_prompt_template = VISION_USER_PROMPT_WITH_PRICE
            system_prompt = VISION_SYSTEM_PROMPT_WITH_PRICE
        else:
            user_prompt_template = VISION_USER_PROMPT_TEMPLATE
            system_prompt = VISION_SYSTEM_PROMPT
        user_prompt = user_prompt_template.format(language_label=_language_label(language))
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ]
        model = model_override or (
            self.settings.vision_model_online
            if force_online and self.settings.vision_model_online
            else self.settings.vision_model
        )
        content, raw_response = self.vision_client.chat(
            model=model,
            messages=messages,
            temperature=0.2,
            max_tokens=16000,
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

    def _call_title_category_llm(
        self,
        title: str,
        language: str,
        model_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        user_prompt = PRODUCT_TITLE_CATEGORY_USER_PROMPT.format(
            title=title,
            language_label=_language_label(language),
        )
        messages = [
            {"role": "system", "content": PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        content, raw_response = self.category_client.chat(
            model=model_override or self.settings.category_model,
            messages=messages,
            temperature=0.3,
            max_tokens=16000,
        )
        self._log_raw("title_category_content", content)
        self._log_raw("title_category_raw_response", raw_response)
        try:
            parsed = safe_json_loads(content)
        except Exception as exc:
            self._log_raw("title_category_parse_error", {"error": str(exc), "content": content})
            raise BadRequestError("Failed to parse title category LLM JSON.") from exc
        if not isinstance(parsed, dict):
            raise BadRequestError("Title category LLM did not return a JSON object.")
        return parsed

    def _predict_price_with_model(
        self,
        title: str,
        description: str,
        brand: str,
        group_name: str,
        category_candidates: List[Dict[str, str]],
        language: str,
        price_model_override: Optional[str],
        image_data_url: Optional[str] = None,
    ) -> Tuple[List[int], Optional[Dict[str, Any]], Optional[str], List[Dict[str, str]]]:
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
        )
        user_content: Any
        if image_data_url:
            user_content = [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": image_data_url}},
            ]
        else:
            user_content = user_prompt

        messages = [
            {"role": "system", "content": PRICE_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]

        content: Optional[str] = None
        raw_response: Optional[Dict[str, Any]] = None
        parsed: Optional[Dict[str, Any]] = None
        error: Optional[str] = None
        citations: List[Dict[str, str]] = []

        model_to_use = price_model_override or self.settings.price_model
        if not model_to_use:
            raise BadRequestError("PRICE_MODEL is not configured.")

        try:
            content, raw_response = self.price_client.chat(
                model=model_to_use,
                messages=messages,
                temperature=0.3,
                max_tokens=16000,
            )
        except Exception as exc:
            error = str(exc)
            self._log_raw("price_call_error", {"error": error})
        else:
            self._log_raw("price_content", content)
            self._log_raw("price_raw_response", raw_response)
            citations = _extract_citations(raw_response)
            try:
                parsed = safe_json_loads(content or "")
            except Exception as exc:
                error = str(exc)
                self._log_raw("price_parse_error", {"error": error, "content": content})

        if isinstance(parsed, dict):
            price_block = parsed.get("prices") or parsed
        else:
            price_block = parsed

        normalized = normalize_price_list(price_block)
        return normalized, parsed, error, citations

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
        model_override: Optional[str] = None,
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

        max_retries = max(0, int(self.settings.category_llm_max_retries))
        attempts = 1 + max_retries if self.settings.category_llm_retry_enabled else 1
        parsed: Optional[Dict[str, Any]] = None

        base_delay_s = 0.2

        for attempt in range(1, attempts + 1):
            try:
                content, raw_response = self.category_client.chat(
                    model=model_override or self.settings.category_model,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=16000,
                )
            except LLMRequestError as exc:
                self._log_raw(
                    "category_call_error",
                    {"error": str(exc), "attempt": attempt, "max_attempts": attempts},
                )
                if attempt < attempts:
                    time.sleep(min(1.5, base_delay_s * (2 ** (attempt - 1))))
                    continue
                raise
            self._log_raw("category_content", content)
            self._log_raw("category_raw_response", raw_response)
            try:
                parsed = safe_json_loads(content)
            except Exception as exc:
                self._log_raw(
                    "category_parse_error",
                    {"error": str(exc), "attempt": attempt, "max_attempts": attempts},
                )
                if attempt < attempts:
                    time.sleep(min(1.5, base_delay_s * (2 ** (attempt - 1))))
                    continue
                raise BadRequestError("Failed to parse category LLM JSON.") from exc
            if not isinstance(parsed, dict):
                self._log_raw(
                    "category_parse_error",
                    {
                        "error": "Category LLM did not return a JSON object.",
                        "attempt": attempt,
                        "max_attempts": attempts,
                    },
                )
                if attempt < attempts:
                    parsed = None
                    time.sleep(min(1.5, base_delay_s * (2 ** (attempt - 1))))
                    continue
                raise BadRequestError("Category LLM did not return a JSON object.")
            break

        if parsed is None:
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

    def _classify_image_to_paths(
        self,
        image_bytes: bytes,
        mime_type: str,
        language: str,
        category_limit: int = 3,
        vision_model_override: Optional[str] = None,
        category_model_override: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        data_url = image_bytes_to_data_url(image_bytes, mime_type)
        ai_raw, _ = self._call_vision_llm(
            data_url,
            language,
            model_override=vision_model_override,
            price_mode="none",
        )

        title = _clean_string(ai_raw.get("title", ""))
        description = _clean_string(ai_raw.get("description", ""))
        top_level_category = _clean_string(ai_raw.get("top_level_category", ""))
        brand_raw = _clean_string(ai_raw.get("brand_name", ""))

        group_name = _map_top_level_category(top_level_category)
        if not group_name:
            return None

        categories, _ = self._choose_categories(
            title=title or ai_raw.get("title", ""),
            description=description or ai_raw.get("description", ""),
            brand_for_prompt=brand_raw,
            group_name=group_name,
            category_limit=category_limit,
            model_override=category_model_override,
        )

        return _paths_from_categories(categories)
