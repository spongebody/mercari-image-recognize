from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable

from .config import BASE_DIR


@dataclass(frozen=True)
class ConfigField:
    env_name: str
    settings_attr: str
    value_type: str
    min_value: int | None = None


CONFIG_FIELDS = (
    ConfigField("VISION_MODEL", "vision_model", "str"),
    ConfigField("CATEGORY_MODEL", "category_model", "str"),
    ConfigField("LOG_LLM_RAW", "log_llm_raw", "bool"),
    ConfigField("LOG_REQUESTS", "log_requests", "bool"),
    ConfigField("ENABLE_DEBUG", "enable_debug_param", "bool"),
    ConfigField("CATEGORY_LLM_RETRY_ENABLED", "category_llm_retry_enabled", "bool"),
    ConfigField("CATEGORY_LLM_MAX_RETRIES", "category_llm_max_retries", "int"),
    ConfigField("IMAGE_COMPRESSION_THRESHOLD_MB", "image_compression_threshold_mb", "int"),
    ConfigField("REQUEST_TIMEOUT", "request_timeout", "int", min_value=1),
)
CONFIG_FIELD_BY_ENV = {field.env_name: field for field in CONFIG_FIELDS}


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError("Expected a boolean value.")


def _parse_int(value: Any, field: ConfigField) -> int:
    if isinstance(value, bool):
        raise ValueError("Expected an integer value.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Expected an integer value.") from exc
    if parsed < 0:
        raise ValueError("Expected a non-negative integer value.")
    if field.min_value is not None and parsed < field.min_value:
        raise ValueError(f"{field.env_name} must be at least {field.min_value}.")
    return parsed


def _parse_value(field: ConfigField, value: Any) -> Any:
    if field.value_type == "bool":
        return _parse_bool(value)
    if field.value_type == "int":
        return _parse_int(value, field)
    if value is None:
        return ""
    parsed = str(value).strip()
    if "\n" in parsed or "\r" in parsed:
        raise ValueError(f"{field.env_name} must be a single-line value.")
    return parsed


def _serialize_value(field: ConfigField, value: Any) -> str:
    if field.value_type == "bool":
        return "true" if bool(value) else "false"
    return str(value)


def get_public_config(settings: Any) -> Dict[str, Any]:
    return {
        field.env_name: getattr(settings, field.settings_attr)
        for field in CONFIG_FIELDS
    }


def _set_env_lines(lines: Iterable[str], values: Dict[str, str]) -> str:
    remaining = dict(values)
    output = []
    for line in lines:
        stripped = line.strip()
        key = None
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line.rstrip("\n"))
    for key, value in remaining.items():
        output.append(f"{key}={value}")
    return "\n".join(output).rstrip() + "\n"


def update_runtime_config(
    settings: Any,
    updates: Dict[str, Any],
    *,
    env_path: Path | None = None,
    on_applied: Callable[[], None] | None = None,
) -> Dict[str, Any]:
    unknown = sorted(set(updates) - set(CONFIG_FIELD_BY_ENV))
    if unknown:
        raise ValueError(f"Unsupported config fields: {', '.join(unknown)}")

    parsed: Dict[ConfigField, Any] = {}
    serialized: Dict[str, str] = {}
    for env_name, value in updates.items():
        field = CONFIG_FIELD_BY_ENV[env_name]
        parsed_value = _parse_value(field, value)
        parsed[field] = parsed_value
        serialized[env_name] = _serialize_value(field, parsed_value)

    target_path = env_path or (BASE_DIR / ".env")
    existing_lines = []
    if target_path.exists():
        existing_lines = target_path.read_text(encoding="utf-8").splitlines()

    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(_set_env_lines(existing_lines, serialized), encoding="utf-8")

    for field, value in parsed.items():
        setattr(settings, field.settings_attr, value)
    if on_applied:
        on_applied()

    return get_public_config(settings)
