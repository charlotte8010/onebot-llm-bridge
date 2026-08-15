from __future__ import annotations

import re
from typing import Iterable


_REPEATED_PUNCTUATION = re.compile(r"([!！?？])\1{3,}")


def sanitize_bubbles(
    bubbles: Iterable[object],
    *,
    max_bubbles: int = 8,
    max_chars: int = 600,
) -> tuple[list[str], list[str]]:
    """Keep model bubbles usable in QQ without forcing a fixed writing style."""
    cleaned: list[str] = []
    warnings: list[str] = []
    for value in bubbles:
        text = str(value).replace("\r", " ").replace("\n", " ").strip()
        text = re.sub(r"\s+", " ", text)
        text = _REPEATED_PUNCTUATION.sub(lambda match: match.group(1) * 3, text)
        if len(text) > max_chars:
            text = text[:max_chars].rstrip()
            warnings.append("bubble_truncated")
        if not text:
            continue
        cleaned.append(text)
    if len(cleaned) > max_bubbles:
        cleaned = cleaned[:max_bubbles]
        warnings.append("too_many_bubbles")
    return cleaned, sorted(set(warnings))
