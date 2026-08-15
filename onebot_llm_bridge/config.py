from __future__ import annotations

import os
import random
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
    vision_mode: str = "off"
    vision_api_key: str = ""
    vision_base_url: str = ""
    vision_model: str = ""
    vision_max_tokens: int = 512
    vision_timeout_seconds: float = 30.0
    napcat_api_url: str = "http://127.0.0.1:3000"
    napcat_access_token: str = ""
    napcat_event_token: str = ""
    bridge_host: str = "127.0.0.1"
    bridge_port: int = 8766
    bot_service_host: str = "127.0.0.1"
    bot_service_port: int = 8765
    bot_service_token: str = ""
    bot_qq: str = ""
    bot_names: tuple[str, ...] = ()
    group_mode: str = "mention"
    decision_mode: str = "heuristic"
    group_allowlist: frozenset[str] = frozenset()
    debounce_seconds: float = 3.0
    debounce_random: bool = False
    followup_seconds: float = 120.0
    reaction_mode: str = "off"
    active_enabled: bool = False
    active_interval_minutes: float = 60.0
    active_target_type: str = "private"
    active_target_id: str = ""
    active_prompt: str = ""
    tools_enabled: bool = False
    tool_allowlist: tuple[str, ...] = ()
    typing_status: bool = True
    context_messages: int = 20
    persona_file: str = ""
    memory_db: str = ""
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_timeout_seconds: float = 10.0
    remote_memory_mode: str = "local_first"
    summary_enabled: bool = False
    summary_min_messages: int = 40
    summary_delay_seconds: float = 10.0

    @classmethod
    def from_values(cls, values: Mapping[str, str]) -> "Settings":
        group_mode = _text(values, "GROUP_MODE", "mention").lower()
        if group_mode not in {"mention", "smart", "all", "off"}:
            raise ConfigError("GROUP_MODE must be mention, smart, all, or off")
        decision_mode = _text(values, "DECISION_MODE", "heuristic").lower()
        if decision_mode not in {"heuristic", "model"}:
            raise ConfigError("DECISION_MODE must be heuristic or model")
        reaction_mode = _text(values, "REACTION_MODE", "off").lower()
        if reaction_mode not in {"off", "like"}:
            raise ConfigError("REACTION_MODE must be off or like")
        active_target_type = _text(values, "ACTIVE_TARGET_TYPE", "private").lower()
        if active_target_type not in {"private", "group"}:
            raise ConfigError("ACTIVE_TARGET_TYPE must be private or group")
        remote_memory_mode = _text(values, "REMOTE_MEMORY_MODE", "local_first").lower()
        if remote_memory_mode not in {"local_first", "coordinated"}:
            raise ConfigError("REMOTE_MEMORY_MODE must be local_first or coordinated")
        vision_mode = _text(values, "VISION_MODE", "off").lower()
        if vision_mode not in {"off", "direct", "separate"}:
            raise ConfigError("VISION_MODE must be off, direct, or separate")
        allowlist = frozenset(
            item.strip() for item in _text(values, "GROUP_ALLOWLIST").split(",") if item.strip()
        )
        for group_id in allowlist:
            if not group_id.isdigit():
                raise ConfigError("GROUP_ALLOWLIST must contain comma-separated QQ numbers")
        bot_qq = _text(values, "BOT_QQ")
        if bot_qq and not bot_qq.isdigit():
            raise ConfigError("BOT_QQ must be a QQ number")
        bot_names = tuple(
            item.strip() for item in _text(values, "BOT_NAMES").split(",") if item.strip()
        )
        tool_allowlist = tuple(
            item.strip().lower() for item in _text(values, "TOOL_ALLOWLIST").split(",") if item.strip()
        )
        debounce_value = _text(values, "DEBOUNCE_SECONDS", "3")
        debounce_random = debounce_value.lower() == "random"
        debounce_seconds = (
            3.0
            if debounce_random
            else _float(values, "DEBOUNCE_SECONDS", 3.0, 0.0, 60.0)
        )
        llm_api_key = _text(values, "LLM_API_KEY")
        llm_base_url = _text(values, "LLM_BASE_URL").rstrip("/")
        supabase_url = _text(values, "SUPABASE_URL").rstrip("/")
        supabase_key = _text(values, "SUPABASE_SECRET_KEY") or _text(values, "SUPABASE_KEY")
        vision_api_key = _text(values, "VISION_API_KEY") or llm_api_key
        vision_base_url = _text(values, "VISION_BASE_URL").rstrip("/") or llm_base_url
        return cls(
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=_text(values, "LLM_MODEL"),
            llm_max_tokens=_int(values, "LLM_MAX_TOKENS", 1024, 1, 32768),
            llm_timeout_seconds=_float(values, "LLM_TIMEOUT_SECONDS", 60.0, 1.0, 600.0),
            vision_mode=vision_mode,
            vision_api_key=vision_api_key,
            vision_base_url=vision_base_url,
            vision_model=_text(values, "VISION_MODEL"),
            vision_max_tokens=_int(values, "VISION_MAX_TOKENS", 512, 1, 8192),
            vision_timeout_seconds=_float(values, "VISION_TIMEOUT_SECONDS", 30.0, 1.0, 600.0),
            napcat_api_url=_text(values, "NAPCAT_API_URL", "http://127.0.0.1:3000").rstrip("/"),
            napcat_access_token=_text(values, "NAPCAT_ACCESS_TOKEN"),
            napcat_event_token=_text(values, "NAPCAT_EVENT_TOKEN"),
            bridge_host=_text(values, "BRIDGE_HOST", "127.0.0.1"),
            bridge_port=_int(values, "BRIDGE_PORT", 8766, 1, 65535),
            bot_service_host=_text(values, "BOT_SERVICE_HOST", "127.0.0.1"),
            bot_service_port=_int(values, "BOT_SERVICE_PORT", 8765, 1, 65535),
            bot_service_token=_text(values, "BOT_SERVICE_TOKEN"),
            bot_qq=bot_qq,
            bot_names=bot_names,
            group_mode=group_mode,
            decision_mode=decision_mode,
            group_allowlist=allowlist,
            debounce_seconds=debounce_seconds,
            debounce_random=debounce_random,
            followup_seconds=_float(values, "FOLLOWUP_SECONDS", 120.0, 0.0, 3600.0),
            reaction_mode=reaction_mode,
            active_enabled=_text(values, "ACTIVE_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            active_interval_minutes=_float(values, "ACTIVE_INTERVAL_MINUTES", 60.0, 1.0, 10080.0),
            active_target_type=active_target_type,
            active_target_id=_text(values, "ACTIVE_TARGET_ID"),
            active_prompt=_text(values, "ACTIVE_PROMPT"),
            tools_enabled=_text(values, "TOOLS_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            tool_allowlist=tool_allowlist,
            typing_status=_text(values, "TYPING_STATUS", "true").lower()
            in {"1", "true", "yes", "on"},
            context_messages=_int(values, "CONTEXT_MESSAGES", 20, 0, 100),
            persona_file=_text(values, "PERSONA_FILE"),
            memory_db=_text(values, "MEMORY_DB"),
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            supabase_timeout_seconds=_float(values, "SUPABASE_TIMEOUT_SECONDS", 10.0, 1.0, 120.0),
            remote_memory_mode=remote_memory_mode,
            summary_enabled=_text(values, "SUMMARY_ENABLED", "false").lower() in {"1", "true", "yes", "on"},
            summary_min_messages=_int(values, "SUMMARY_MIN_MESSAGES", 40, 10, 1000),
            summary_delay_seconds=_float(values, "SUMMARY_DELAY_SECONDS", 10.0, 0.0, 300.0),
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
        if self.vision_mode == "separate":
            vision_missing = [
                name
                for name, value in (
                    ("VISION_API_KEY", self.vision_api_key),
                    ("VISION_BASE_URL", self.vision_base_url),
                    ("VISION_MODEL", self.vision_model),
                )
                if not value
            ]
            if vision_missing:
                raise ConfigError("missing required vision settings: " + ", ".join(vision_missing))

    def validate_for_bridge(self) -> None:
        if self.bridge_host not in {"127.0.0.1", "localhost", "::1"} and not self.napcat_event_token:
            raise ConfigError("NAPCAT_EVENT_TOKEN is required when BRIDGE_HOST is not loopback")
        if self.active_enabled and (not self.active_target_id or not self.active_prompt):
            raise ConfigError("ACTIVE_TARGET_ID and ACTIVE_PROMPT are required when ACTIVE_ENABLED is true")
        if self.active_enabled and not self.active_target_id.isdigit():
            raise ConfigError("ACTIVE_TARGET_ID must be a numeric QQ or group id")
        if bool(self.supabase_url) != bool(self.supabase_key):
            raise ConfigError("SUPABASE_URL and SUPABASE_SECRET_KEY must be set together")
        if self.supabase_url and not self.supabase_url.startswith("https://"):
            raise ConfigError("SUPABASE_URL must use HTTPS")
        if self.supabase_url and not self.bot_qq:
            raise ConfigError("BOT_QQ is required when Supabase remote memory is enabled")

    def debounce_delay(self) -> float:
        if self.debounce_random:
            return random.choice((3.0, 4.0, 5.0, 6.0))
        return self.debounce_seconds
