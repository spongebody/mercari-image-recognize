import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Set

from dotenv import load_dotenv

from .constants import ALLOWED_MIME_TYPES

# Load .env early so os.getenv can pick up values defined there.
BASE_DIR = Path(__file__).resolve().parent.parent

# 明确指定 .env 路径
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
    request_timeout: int = _env_int("REQUEST_TIMEOUT", 60)
    enable_debug_param: bool = _env_bool("ENABLE_DEBUG", True)
    max_image_bytes: int = _env_int("MAX_IMAGE_BYTES", 5 * 1024 * 1024)
    image_compression_threshold_mb: int = _env_int("IMAGE_COMPRESSION_THRESHOLD_MB", 1)
    allowed_mime_types: Set[str] = field(default_factory=lambda: set(ALLOWED_MIME_TYPES))
    log_llm_raw: bool = _env_bool("LOG_LLM_RAW", False)
    log_requests: bool = _env_bool("LOG_REQUESTS", True)
    log_requests_retention_days: int = _env_int("LOG_REQUESTS_RETENTION_DAYS", 7)
    log_requests_max_files: int = _env_int("LOG_REQUESTS_MAX_FILES", 1000)
    category_llm_retry_enabled: bool = _env_bool("CATEGORY_LLM_RETRY_ENABLED", False)
    category_llm_max_retries: int = _env_int("CATEGORY_LLM_MAX_RETRIES", 1)
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
