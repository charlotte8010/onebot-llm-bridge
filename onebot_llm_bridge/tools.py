from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable


_TOOL_MARKER = re.compile(r"\[\[TOOL\s*:\s*([a-zA-Z0-9_.-]+)\]\]")


def parse_tool_calls(text: str) -> list[str]:
    """Read explicit, argument-free tool markers from model output."""
    return list(dict.fromkeys(match.group(1).lower() for match in _TOOL_MARKER.finditer(text)))


class ToolRegistry:
    """Small allowlisted tool boundary; tools never execute arbitrary commands."""

    def __init__(self, tools: dict[str, Callable[[], str]] | None = None) -> None:
        self._tools = tools or {"get_time": self._get_time}

    @staticmethod
    def _get_time() -> str:
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def run(self, name: str) -> str:
        tool = self._tools.get(name.lower())
        if tool is None:
            return "tool_not_available"
        try:
            return str(tool())[:2000]
        except Exception as exc:
            return f"tool_failed:{type(exc).__name__}"

    def run_allowed(self, names: list[str], allowlist: tuple[str, ...]) -> list[dict[str, Any]]:
        allowed = set(item.lower() for item in allowlist)
        return [{"name": name, "result": self.run(name)} for name in names if name in allowed]
