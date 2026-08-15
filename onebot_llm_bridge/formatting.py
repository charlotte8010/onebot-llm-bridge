from __future__ import annotations

import re


_BUBBLE_MARKER = re.compile(r"\[\[BUBBLE\]\]", re.IGNORECASE)


def split_bubbles(text: str, max_bubbles: int = 4, max_chars: int = 600) -> list[str]:
    """Turn model output into safe, non-empty QQ messages."""
    if max_bubbles < 1 or max_chars < 1:
        raise ValueError("max_bubbles and max_chars must be positive")
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not cleaned:
        return []
    if _BUBBLE_MARKER.search(cleaned):
        parts = _BUBBLE_MARKER.split(cleaned)
    else:
        parts = re.split(r"\n+", cleaned)
    result: list[str] = []
    for part in parts:
        value = part.strip()
        if not value:
            continue
        if len(value) > max_chars:
            value = value[: max_chars - 1].rstrip() + "…"
        result.append(value)
        if len(result) >= max_bubbles:
            break
    return result

