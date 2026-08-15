from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_emoji_catalog(path: str) -> dict[str, dict[str, str]]:
    """Load a small, user-editable name -> QQ reaction definition map."""
    if not path:
        return {}
    file_path = Path(path)
    if not file_path.is_file():
        return {}
    try:
        raw = json.loads(file_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, str]] = {}
    for name, value in raw.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(value, dict):
            continue
        emoji_id = str(value.get("id", "")).strip()
        if not emoji_id.isdigit() or len(emoji_id) > 8:
            continue
        result[name.strip()[:32]] = {
            "id": emoji_id,
            "meaning": str(value.get("meaning", "")).strip()[:160],
            "usage": str(value.get("usage", "")).strip()[:160],
        }
    return result


def catalog_for_prompt(catalog: dict[str, dict[str, str]]) -> list[dict[str, str]]:
    return [
        {"name": name, "meaning": item["meaning"], "usage": item["usage"]}
        for name, item in catalog.items()
    ]


def resolve_emoji(value: Any, catalog: dict[str, dict[str, str]]) -> str | None:
    """Resolve a model-selected name, while preserving numeric marker compatibility."""
    text = str(value or "").strip()
    if text.isdigit() and len(text) <= 8:
        return text
    item = catalog.get(text)
    return item["id"] if item else None
