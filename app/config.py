import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set

from dotenv import load_dotenv

from .constants import ALLOWED_MIME_TYPES, DEFAULT_FALLBACK_MODELS

# Load .env early so os.getenv can pick up values defined there.
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_int_min(name: str, default: int, minimum: int) -> int:
    value = _env_int(name, default)
    return value if value >= minimum else default


def _env_optional_bool(name: str) -> Optional[bool]:
    raw = os.getenv(name)
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    if cleaned in {"1", "true", "yes", "on"}:
        return True
    if cleaned in {"0", "false", "no", "off"}:
        return False
    return None


def _env_optional_int(name: str) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _env_optional_enum(name: str, allowed: Set[str]) -> Optional[str]:
    raw = os.getenv(name)
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    return cleaned if cleaned in allowed else None


def _env_str_list(name: str, default: Sequence[str]) -> List[str]:
    raw = os.getenv(name)
    if raw is None:
        return list(default)
    items = [item.strip() for item in raw.split(",")]
    items = [item for item in items if item]
    return items if items else list(default)


@dataclass
class Settings:
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    vision_model: str = os.getenv("VISION_MODEL", "")
    category_model: str = os.getenv("CATEGORY_MODEL", "")
    brand_csv_path: str = os.getenv("BRAND_CSV_PATH", "data/mercari_brand.csv")
    category_csv_path: str = os.getenv("CATEGORY_CSV_PATH", "data/category_rakuten.csv")
    openrouter_base_url: str = os.getenv(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
    )
    openrouter_referer: str = os.getenv("OPENROUTER_REFERER", "")
    openrouter_app_name: str = os.getenv("OPENROUTER_APP_NAME", "mercari-image-backend")
    request_timeout: int = _env_int_min("REQUEST_TIMEOUT", 60, 1)
    enable_debug_param: bool = _env_bool("ENABLE_DEBUG", True)
    max_image_bytes: int = _env_int("MAX_IMAGE_BYTES", 5 * 1024 * 1024)
    image_compression_threshold_mb: int = _env_int("IMAGE_COMPRESSION_THRESHOLD_MB", 1)
    allowed_mime_types: Set[str] = field(default_factory=lambda: set(ALLOWED_MIME_TYPES))
    log_llm_raw: bool = _env_bool("LOG_LLM_RAW", False)
    log_requests: bool = _env_bool("LOG_REQUESTS", True)
    log_requests_retention_days: int = _env_int("LOG_REQUESTS_RETENTION_DAYS", 7)
    log_requests_max_files: int = _env_int("LOG_REQUESTS_MAX_FILES", 1000)

    vision_fallback_models: List[str] = field(
        default_factory=lambda: _env_str_list("VISION_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS)
    )
    category_fallback_models: List[str] = field(
        default_factory=lambda: _env_str_list("CATEGORY_FALLBACK_MODELS", DEFAULT_FALLBACK_MODELS)
    )
    model_call_max_retries: int = _env_int_min("MODEL_CALL_MAX_RETRIES", 3, 0)
    model_call_total_budget_seconds: int = _env_int_min(
        "MODEL_CALL_TOTAL_BUDGET_SECONDS", 120, 1
    )

    # Showcase image generation
    showcase_model: str = os.getenv(
        "SHOWCASE_MODEL", "google/gemini-3.1-flash-image-preview"
    )
    showcase_storage_root: str = os.getenv("SHOWCASE_STORAGE_ROOT", "storage")
    showcase_retain_input_files: bool = _env_bool("SHOWCASE_RETAIN_INPUT_FILES", False)
    showcase_retain_output_files: bool = _env_bool("SHOWCASE_RETAIN_OUTPUT_FILES", False)
    showcase_request_timeout: int = _env_int_min("SHOWCASE_REQUEST_TIMEOUT", 120, 1)
    showcase_max_retries: int = _env_int_min("SHOWCASE_MAX_RETRIES", 3, 1)
    showcase_timezone: str = os.getenv("SHOWCASE_TIMEZONE", "Asia/Shanghai")

    reasoning_enabled: Optional[bool] = field(default_factory=lambda: _env_optional_bool("REASONING_ENABLED"))
    reasoning_effort: Optional[str] = field(
        default_factory=lambda: _env_optional_enum(
            "REASONING_EFFORT",
            {"minimal", "low", "medium", "high", "xhigh", "none"},
        )
    )
    reasoning_max_tokens: Optional[int] = field(
        default_factory=lambda: _env_optional_int("REASONING_MAX_TOKENS")
    )
    reasoning_summary: Optional[str] = field(
        default_factory=lambda: _env_optional_enum(
            "REASONING_SUMMARY",
            {"auto", "concise", "detailed"},
        )
    )

    @property
    def reasoning(self) -> Optional[Dict[str, Any]]:
        reasoning: Dict[str, Any] = {}
        if self.reasoning_enabled is not None:
            reasoning["enabled"] = self.reasoning_enabled
        if self.reasoning_effort is not None:
            reasoning["effort"] = self.reasoning_effort
        if self.reasoning_max_tokens is not None:
            reasoning["max_tokens"] = self.reasoning_max_tokens
        if self.reasoning_summary is not None:
            reasoning["summary"] = self.reasoning_summary
        return reasoning or None

    @property
    def image_compression_threshold_bytes(self) -> int:
        return max(0, int(self.image_compression_threshold_mb)) * 1024 * 1024


def load_settings() -> Settings:
    return Settings()
