import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Set

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


@dataclass
class Settings:
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    vision_model: str = os.getenv("VISION_MODEL", "")
    vision_model_online: str = os.getenv("VISION_MODEL_ONLINE", "")
    price_model: str = os.getenv("PRICE_MODEL", "openai/gpt-4.1:online")
    category_model: str = os.getenv("CATEGORY_MODEL", "")
    brand_csv_path: str = os.getenv("BRAND_CSV_PATH", "data/brand.csv")
    category_csv_path: str = os.getenv("CATEGORY_CSV_PATH", "data/category.csv")
    openrouter_base_url: str = os.getenv(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1/chat/completions"
    )
    openrouter_referer: str = os.getenv("OPENROUTER_REFERER", "")
    openrouter_app_name: str = os.getenv("OPENROUTER_APP_NAME", "mercari-image-backend")
    request_timeout: int = _env_int("REQUEST_TIMEOUT", 60)
    enable_debug_param: bool = _env_bool("ENABLE_DEBUG", True)
    max_image_bytes: int = _env_int("MAX_IMAGE_BYTES", 5 * 1024 * 1024)
    allowed_mime_types: Set[str] = field(default_factory=lambda: set(ALLOWED_MIME_TYPES))
    log_llm_raw: bool = _env_bool("LOG_LLM_RAW", False)
    log_requests: bool = _env_bool("LOG_REQUESTS", True)
    log_requests_retention_days: int = _env_int("LOG_REQUESTS_RETENTION_DAYS", 7)
    log_requests_max_files: int = _env_int("LOG_REQUESTS_MAX_FILES", 1000)
    category_llm_retry_enabled: bool = _env_bool("CATEGORY_LLM_RETRY_ENABLED", False)
    category_llm_max_retries: int = _env_int("CATEGORY_LLM_MAX_RETRIES", 1)

    def __post_init__(self) -> None:
        if not self.vision_model_online and self.vision_model:
            suffix = ":online"
            if self.vision_model.endswith(suffix):
                self.vision_model_online = self.vision_model
            else:
                self.vision_model_online = f"{self.vision_model}{suffix}"


def load_settings() -> Settings:
    return Settings()
