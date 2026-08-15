from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class ConfigError(ValueError):
    """Raised when an environment value cannot be used safely."""


def parse_env_file(text: str) -> dict[str, str]:
    """Parse the small dotenv subset used by this project.

    It intentionally supports only KEY=value, comments, and matching quotes.
    This keeps startup dependency-free and makes the precedence easy to test.
    """
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ConfigError(f"invalid .env line: {raw_line!r}")
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            raise ConfigError(f"invalid environment name: {key!r}")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    return parse_env_file(path.read_text(encoding="utf-8"))


def merged_environment(
    root: Path,
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Load .env then .env.local, with real process variables taking priority."""
    result: dict[str, str] = {}
    result.update(load_env_file(root / ".env"))
    result.update(load_env_file(root / ".env.local"))
    result.update(dict(os.environ if environ is None else environ))
    return result


def _text(values: Mapping[str, str], key: str, default: str = "") -> str:
    return str(values.get(key, default)).strip()


def _int(values: Mapping[str, str], key: str, default: int, minimum: int, maximum: int) -> int:
    raw = _text(values, key, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}")
    return value


def _float(values: Mapping[str, str], key: str, default: float, minimum: float, maximum: float) -> float:
    raw = _text(values, key, str(default))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{key} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class Settings:
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    llm_max_tokens: int = 1024
    llm_timeout_seconds: float = 60.0
    napcat_api_url: str = "http://127.0.0.1:3000"
    napcat_access_token: str = ""
    napcat_event_token: str = ""
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8766
    bot_service_host: str = "127.0.0.1"
    bot_service_port: int = 8765
    bot_service_token: str = ""
    group_mode: str = "mention"
    group_allowlist: frozenset[str] = frozenset()
    context_messages: int = 20
    persona_file: str = ""

    @classmethod
    def from_values(cls, values: Mapping[str, str]) -> "Settings":
        group_mode = _text(values, "GROUP_MODE", "mention").lower()
        if group_mode not in {"mention", "smart", "all", "off"}:
            raise ConfigError("GROUP_MODE must be mention, smart, all, or off")
        allowlist = frozenset(
            item.strip() for item in _text(values, "GROUP_ALLOWLIST").split(",") if item.strip()
        )
        for group_id in allowlist:
            if not group_id.isdigit():
                raise ConfigError("GROUP_ALLOWLIST must contain comma-separated QQ numbers")
        return cls(
            llm_api_key=_text(values, "LLM_API_KEY"),
            llm_base_url=_text(values, "LLM_BASE_URL").rstrip("/"),
            llm_model=_text(values, "LLM_MODEL"),
            llm_max_tokens=_int(values, "LLM_MAX_TOKENS", 1024, 1, 32768),
            llm_timeout_seconds=_float(values, "LLM_TIMEOUT_SECONDS", 60.0, 1.0, 600.0),
            napcat_api_url=_text(values, "NAPCAT_API_URL", "http://127.0.0.1:3000").rstrip("/"),
            napcat_access_token=_text(values, "NAPCAT_ACCESS_TOKEN"),
            napcat_event_token=_text(values, "NAPCAT_EVENT_TOKEN"),
            bridge_host=_text(values, "BRIDGE_HOST", "127.0.0.1"),
            bridge_port=_int(values, "BRIDGE_PORT", 8766, 1, 65535),
            bot_service_host=_text(values, "BOT_SERVICE_HOST", "127.0.0.1"),
            bot_service_port=_int(values, "BOT_SERVICE_PORT", 8765, 1, 65535),
            bot_service_token=_text(values, "BOT_SERVICE_TOKEN"),
            group_mode=group_mode,
            group_allowlist=allowlist,
            context_messages=_int(values, "CONTEXT_MESSAGES", 20, 0, 100),
            persona_file=_text(values, "PERSONA_FILE"),
        )

    def validate_for_bot(self) -> None:
        missing = [
            name
            for name, value in (
                ("LLM_API_KEY", self.llm_api_key),
                ("LLM_BASE_URL", self.llm_base_url),
                ("LLM_MODEL", self.llm_model),
            )
            if not value
        ]
        if missing:
            raise ConfigError("missing required LLM settings: " + ", ".join(missing))

    def validate_for_bridge(self) -> None:
        if self.bridge_host not in {"127.0.0.1", "localhost", "::1"} and not self.napcat_event_token:
            raise ConfigError("NAPCAT_EVENT_TOKEN is required when BRIDGE_HOST is not loopback")

