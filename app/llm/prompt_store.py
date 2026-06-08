from __future__ import annotations

import json
import logging
import os
import string
import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ..config import BASE_DIR
from . import prompts as _p
from .prompts import TOP_LEVEL_CATEGORY_OPTIONS

logger = logging.getLogger(__name__)

CATEGORY_OPTIONS_TOKEN = "[[TOP_LEVEL_CATEGORY_OPTIONS]]"
OVERRIDES_PATH = BASE_DIR / "data" / "prompt_overrides.json"


@dataclass(frozen=True)
class PromptDef:
    key: str
    label: str
    stage: str
    role: str  # "system" | "user"
    default_text: str
    required_tokens: Tuple[str, ...]


PROMPT_REGISTRY: Tuple[PromptDef, ...] = (
    PromptDef("FAST_CLASSIFICATION_SYSTEM_PROMPT", "快速分类 · System", "fast_classification", "system", _p.FAST_CLASSIFICATION_SYSTEM_PROMPT, (CATEGORY_OPTIONS_TOKEN,)),
    PromptDef("FAST_CLASSIFICATION_USER_PROMPT", "快速分类 · User", "fast_classification", "user", _p.FAST_CLASSIFICATION_USER_PROMPT, ("{language_label}",)),
    PromptDef("TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT", "标题图片兜底 · System", "title_image_fallback", "system", _p.TITLE_IMAGE_FALLBACK_SYSTEM_PROMPT, (CATEGORY_OPTIONS_TOKEN,)),
    PromptDef("TITLE_IMAGE_FALLBACK_USER_PROMPT", "标题图片兜底 · User", "title_image_fallback", "user", _p.TITLE_IMAGE_FALLBACK_USER_PROMPT, ("{language_label}",)),
    PromptDef("PRICE_ONLY_SYSTEM_PROMPT", "价格提取 · System", "price_only", "system", _p.PRICE_ONLY_SYSTEM_PROMPT, ()),
    PromptDef("PRICE_ONLY_USER_PROMPT", "价格提取 · User", "price_only", "user", _p.PRICE_ONLY_USER_PROMPT, ()),
    PromptDef("PRODUCT_DATA_SYSTEM_PROMPT", "商品数据 · System", "product_data", "system", _p.PRODUCT_DATA_SYSTEM_PROMPT, ()),
    PromptDef("PRODUCT_DATA_USER_PROMPT", "商品数据 · User", "product_data", "user", _p.PRODUCT_DATA_USER_PROMPT, ("{language_label}",)),
    PromptDef("PRODUCT_DATA_REGENERATION_SYSTEM_PROMPT", "商品数据再生成 · System", "product_data_regeneration", "system", _p.PRODUCT_DATA_REGENERATION_SYSTEM_PROMPT, ()),
    PromptDef("PRODUCT_DATA_REGENERATION_USER_PROMPT", "商品数据再生成 · User", "product_data_regeneration", "user", _p.PRODUCT_DATA_REGENERATION_USER_PROMPT, ("{language_label}", "{user_notes}", "{original_product_data_json}")),
    PromptDef("PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT", "商品数据兜底 · System", "product_data_fallback", "system", _p.PRODUCT_DATA_FALLBACK_SYSTEM_PROMPT, ()),
    PromptDef("PRODUCT_DATA_FALLBACK_USER_PROMPT", "商品数据兜底 · User", "product_data_fallback", "user", _p.PRODUCT_DATA_FALLBACK_USER_PROMPT, ("{language_label}",)),
    PromptDef("PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT", "标题选类目 · System", "title_category", "system", _p.PRODUCT_TITLE_CATEGORY_SYSTEM_PROMPT, (CATEGORY_OPTIONS_TOKEN,)),
    PromptDef("PRODUCT_TITLE_CATEGORY_USER_PROMPT", "标题选类目 · User", "title_category", "user", _p.PRODUCT_TITLE_CATEGORY_USER_PROMPT, ("{title}", "{language_label}")),
    PromptDef("CATEGORY_SYSTEM_PROMPT", "类目匹配 · System", "category", "system", _p.CATEGORY_SYSTEM_PROMPT, ()),
    PromptDef("CATEGORY_USER_PROMPT_TEMPLATE", "类目匹配 · User", "category", "user", _p.CATEGORY_USER_PROMPT_TEMPLATE, ("{title}", "{description}", "{brand}", "{group_name}", "{candidate_paths}")),
    PromptDef("SHOWCASE_PROMPT", "图片合成 · 指令", "showcase", "system", _p.SHOWCASE_PROMPT, ()),
)

_REGISTRY_BY_KEY: Dict[str, PromptDef] = {d.key: d for d in PROMPT_REGISTRY}
_overrides: Dict[str, str] = {}
_lock = threading.Lock()


def load_overrides() -> None:
    """Load the sparse override file into memory. Never raises."""
    global _overrides
    try:
        raw = OVERRIDES_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
    except FileNotFoundError:
        _overrides = {}
        return
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Ignoring unreadable prompt overrides at %s: %s", OVERRIDES_PATH, exc)
        _overrides = {}
        return
    if not isinstance(data, dict):
        _overrides = {}
        return
    _overrides = {
        key: value
        for key, value in data.items()
        if key in _REGISTRY_BY_KEY and isinstance(value, str)
    }


def _user_prompt_fields(text: str) -> set:
    return {
        field_name
        for _, field_name, _, _ in string.Formatter().parse(text)
        if field_name is not None
    }


def _validate(key: str, text: str) -> None:
    definition = _REGISTRY_BY_KEY.get(key)
    if definition is None:
        raise ValueError(f"Unknown prompt key: {key}")
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"{key} must be a non-empty string.")
    for token in definition.required_tokens:
        if token not in text:
            raise ValueError(f"{key} must contain the placeholder {token}.")
    if definition.role == "user":
        allowed = {tok.strip("{}") for tok in definition.required_tokens}
        try:
            fields = _user_prompt_fields(text)
        except ValueError as exc:
            raise ValueError(f"{key} has invalid template formatting: {exc}") from exc
        extra = fields - allowed
        if extra:
            raise ValueError(
                f"{key} contains unsupported placeholders: "
                + ", ".join("{" + name + "}" for name in sorted(extra))
            )


def _persist() -> None:
    OVERRIDES_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = OVERRIDES_PATH.with_name(OVERRIDES_PATH.name + ".tmp")
    tmp_path.write_text(
        json.dumps(_overrides, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(tmp_path, OVERRIDES_PATH)


def update(updates: Dict[str, str]) -> List[dict]:
    if not isinstance(updates, dict) or not updates:
        raise ValueError("No prompt updates provided.")
    for key, text in updates.items():
        _validate(key, text)
    with _lock:
        _overrides.update(updates)
        _persist()
    return list_prompts()


def reset(keys: Optional[List[str]]) -> List[dict]:
    if keys is not None and not isinstance(keys, list):
        raise ValueError("keys must be a list or null.")
    if keys:
        unknown = [k for k in keys if k not in _REGISTRY_BY_KEY]
        if unknown:
            raise ValueError(f"Unknown prompt key(s): {', '.join(unknown)}")
    with _lock:
        if not keys:
            _overrides.clear()
        else:
            for key in keys:
                _overrides.pop(key, None)
        _persist()
    return list_prompts()


def get(key: str) -> str:
    """Effective unrendered template (override if set, else default)."""
    definition = _REGISTRY_BY_KEY.get(key)
    if definition is None:
        raise KeyError(key)
    return _overrides.get(key, definition.default_text)


def render_system(key: str) -> str:
    """get(key) with the category-options sentinel replaced by the live list.

    For system prompts without the sentinel this is a no-op, so it is safe to
    call uniformly for every system prompt.
    """
    return get(key).replace(CATEGORY_OPTIONS_TOKEN, TOP_LEVEL_CATEGORY_OPTIONS)


def is_overridden(key: str) -> bool:
    return key in _overrides


def list_prompts() -> List[dict]:
    return [
        {
            "key": d.key,
            "label": d.label,
            "stage": d.stage,
            "role": d.role,
            "value": _overrides.get(d.key, d.default_text),
            "default": d.default_text,
            "required_tokens": list(d.required_tokens),
            "is_overridden": d.key in _overrides,
        }
        for d in PROMPT_REGISTRY
    ]


load_overrides()
