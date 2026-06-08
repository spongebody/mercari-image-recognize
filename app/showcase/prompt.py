from typing import Optional

from app.llm import prompt_store


def build_showcase_prompt(prompt_hint: Optional[str]) -> str:
    """Effective showcase instruction (editable override or default) plus the
    optional per-request hint appended at the end."""
    base = prompt_store.get("SHOWCASE_PROMPT")
    if prompt_hint:
        return f"{base}\n\nAdditional guidance: {prompt_hint}"
    return base
