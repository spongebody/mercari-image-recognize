from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from ..errors import LLMParseError

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


def parse_llm_json(raw: str) -> Any:
    """Parse LLM output as JSON, tolerating common framing variants.

    Strategy (first match wins):
      1) json.loads on the stripped text.
      2) Strip ```json ... ``` or ``` ... ``` fence and parse the inside.
      3) Substring from first '{' to last '}'.
      4) Substring from first '[' to last ']'.
    Raises LLMParseError with a 200-char excerpt on failure.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise LLMParseError("LLM returned empty content.")

    stripped = raw.strip()
    candidates: List[str] = [stripped]

    fence = _FENCE_RE.search(stripped)
    if fence:
        candidates.append(fence.group(1).strip())

    if "{" in stripped and "}" in stripped:
        candidates.append(stripped[stripped.find("{"): stripped.rfind("}") + 1])

    if "[" in stripped and "]" in stripped:
        candidates.append(stripped[stripped.find("["): stripped.rfind("]") + 1])

    last_err: Optional[json.JSONDecodeError] = None
    for c in candidates:
        if not c:
            continue
        try:
            return json.loads(c)
        except json.JSONDecodeError as exc:
            last_err = exc

    excerpt = (raw[:200] + "…") if len(raw) > 200 else raw
    reason = str(last_err) if last_err else "no JSON candidate found"
    raise LLMParseError(f"JSON decode failed: {reason}. excerpt={excerpt!r}")
