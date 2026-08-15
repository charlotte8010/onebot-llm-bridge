from __future__ import annotations

import re


_BUBBLE_MARKER = re.compile(r"\[\[BUBBLE\]\]", re.IGNORECASE)
_REACTION_MARKER = re.compile(r"\[\[REACTION\s*:\s*(\d{1,6})\]\]", re.IGNORECASE)


def parse_reply_actions(text: str, max_bubbles: int = 4, max_chars: int = 600) -> tuple[list[str], str | None]:
    """Parse safe text bubbles and an optional numeric QQ reaction marker."""
    reaction_match = _REACTION_MARKER.search(text)
    reaction_id = reaction_match.group(1) if reaction_match else None
    cleaned = _REACTION_MARKER.sub("", text)
    return split_bubbles(cleaned, max_bubbles=max_bubbles, max_chars=max_chars), reaction_id


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
