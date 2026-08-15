from __future__ import annotations

import json
import ssl
import time
from dataclasses import dataclass
from http.client import HTTPException
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import NormalizedMessage


class RemoteMemoryError(RuntimeError):
    """A sanitized remote-memory failure."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)

    @property
    def retryable(self) -> bool:
        return self.code in {"network_error", "timeout", "server_error", "rate_limited"}


class SupabaseRestClient:
    """Dependency-free PostgREST client for a private Supabase project."""

    def __init__(
        self,
        url: str,
        api_key: str,
        *,
        timeout: float = 10.0,
        opener: Callable[..., Any] = urlopen,
    ) -> None:
        self.base_url = f"{url.rstrip('/')}/rest/v1/"
        self.api_key = api_key
        self.timeout = timeout
        self.opener = opener

    def request(
        self,
        method: str,
        resource: str,
        *,
        payload: Any = None,
        query: Mapping[str, Any] | None = None,
        prefer: str = "",
    ) -> Any:
        url = self.base_url + resource.lstrip("/")
        if query:
            url += "?" + urlencode(dict(query), doseq=True)
        headers = {"Accept": "application/json", "apikey": self.api_key}
        if not self.api_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.api_key}"
        if prefer:
            headers["Prefer"] = prefer
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        request = Request(url, data=data, headers=headers, method=method.upper())
        try:
            with self.opener(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            code = "http_error"
            if exc.code == 429:
                code = "rate_limited"
            elif exc.code >= 500:
                code = "server_error"
            raise RemoteMemoryError(code, f"remote memory returned HTTP {exc.code}") from exc
        except (HTTPException, URLError, TimeoutError, ConnectionError, OSError, ssl.SSLError) as exc:
            raise RemoteMemoryError("network_error", "remote memory request failed") from exc
        try:
            return json.loads(raw.decode("utf-8")) if raw else None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RemoteMemoryError("invalid_json", "remote memory returned invalid JSON") from exc


@dataclass(frozen=True)
class RemoteContext:
    messages: tuple[NormalizedMessage, ...]
    summary: str = ""
    facts: tuple[str, ...] = ()


class RemoteMemoryStore:
    """Best-effort cross-machine context store with idempotent message writes."""

    def __init__(self, client: SupabaseRestClient, *, bot_qq: str) -> None:
        if not bot_qq or not bot_qq.isdigit():
            raise ValueError("bot_qq must be numeric for remote memory")
        self.client = client
        self.bot_qq = bot_qq

    def ingest(self, message: NormalizedMessage) -> bool:
        row = {
            "bot_qq": self.bot_qq,
            "conversation_key": message.conversation_key,
            "conversation_type": message.conversation_type,
            "conversation_id": message.conversation_id,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name[:128],
            "source_message_id": message.message_id,
            "is_self": message.sender_id == self.bot_qq,
            "content_text": message.text[:4000],
            "segments": list(message.segments),
            "reply_to": message.reply_to,
            "occurred_at": message.timestamp,
        }
        result = self.client.request(
            "POST",
            "bridge_messages",
            payload=row,
            query={"on_conflict": "bot_qq,conversation_key,source_message_id"},
            prefer="resolution=ignore-duplicates,return=representation",
        )
        return isinstance(result, list) and bool(result)

    def load_context(self, conversation_key: str, limit: int = 20) -> RemoteContext:
        if limit < 1 or limit > 100:
            raise ValueError("context limit is invalid")
        rows = self.client.request(
            "GET",
            "bridge_messages",
            query={
                "select": "conversation_key,conversation_type,conversation_id,sender_id,sender_name,source_message_id,content_text,segments,reply_to,occurred_at,is_self",
                "bot_qq": f"eq.{self.bot_qq}",
                "conversation_key": f"eq.{conversation_key}",
                "order": "occurred_at.desc",
                "limit": limit,
            },
        )
        if not isinstance(rows, list):
            raise RemoteMemoryError("invalid_response", "remote context was not a list")
        messages: list[NormalizedMessage] = []
        for row in reversed(rows):
            if not isinstance(row, Mapping):
                continue
            try:
                segments = row.get("segments", [])
                if not isinstance(segments, list):
                    segments = []
                messages.append(
                    NormalizedMessage(
                        event_id=f"remote:{row['source_message_id']}",
                        timestamp=int(row.get("occurred_at", 0)),
                        conversation_type=str(row["conversation_type"]),
                        conversation_id=str(row["conversation_id"]),
                        sender_id=str(row["sender_id"]),
                        sender_name=str(row.get("sender_name", row["sender_id"])),
                        message_id=str(row["source_message_id"]),
                        text=str(row.get("content_text", "")),
                        segments=tuple(segments),
                        reply_to=str(row["reply_to"]) if row.get("reply_to") else None,
                        to_me=False,
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        summary_rows = self.client.request(
            "GET",
            "bridge_summaries",
            query={
                "select": "summary",
                "bot_qq": f"eq.{self.bot_qq}",
                "conversation_key": f"eq.{conversation_key}",
                "order": "version.desc",
                "limit": 1,
            },
        )
        summary = ""
        if isinstance(summary_rows, list) and summary_rows and isinstance(summary_rows[0], Mapping):
            summary = str(summary_rows[0].get("summary", ""))[:4000]
        facts = self.load_facts(conversation_key)
        return RemoteContext(tuple(messages), summary, tuple(facts))

    def load_facts(self, scope_key: str, limit: int = 40) -> list[str]:
        rows = self.client.request(
            "GET",
            "bridge_facts",
            query={
                "select": "fact",
                "bot_qq": f"eq.{self.bot_qq}",
                "scope_key": f"eq.{scope_key}",
                "order": "id.desc",
                "limit": max(0, min(limit, 100)),
            },
        )
        if not isinstance(rows, list):
            return []
        return [str(row["fact"]) for row in reversed(rows) if isinstance(row, Mapping) and row.get("fact")]

    def add_fact(self, scope_key: str, fact: str, source_message_id: str = "") -> None:
        value = fact.strip()[:1000]
        if not value:
            return
        self.client.request(
            "POST",
            "bridge_facts",
            payload={
                "bot_qq": self.bot_qq,
                "scope_key": scope_key,
                "fact": value,
                "source_message_id": source_message_id,
            },
            query={"on_conflict": "bot_qq,scope_key,fact"},
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def remove_fact(self, scope_key: str, fact: str) -> bool:
        result = self.client.request(
            "DELETE",
            "bridge_facts",
            query={
                "bot_qq": f"eq.{self.bot_qq}",
                "scope_key": f"eq.{scope_key}",
                "fact": f"eq.{fact.strip()}",
            },
            prefer="return=representation",
        )
        return isinstance(result, list) and bool(result)

    def smart_groups(self) -> frozenset[str]:
        rows = self.client.request(
            "GET",
            "bridge_smart_groups",
            query={"select": "group_id", "bot_qq": f"eq.{self.bot_qq}", "enabled": "eq.true", "limit": 1000},
        )
        if not isinstance(rows, list):
            return frozenset()
        return frozenset(str(row["group_id"]) for row in rows if isinstance(row, Mapping) and str(row.get("group_id", "")).isdigit())

    def claim_conversation(self, conversation_key: str, owner_id: str, lease_seconds: int = 90) -> bool:
        """Best-effort single-worker lease for coordinated mode.

        A conflict means another bridge is currently processing this conversation.
        Network failures still raise so callers can fall back to local-first behavior.
        """
        now = int(time.time())
        self.client.request(
            "DELETE",
            "bridge_leases",
            query={
                "bot_qq": f"eq.{self.bot_qq}",
                "conversation_key": f"eq.{conversation_key}",
                "lease_until": f"lt.{now}",
            },
            prefer="return=minimal",
        )
        try:
            self.client.request(
                "POST",
                "bridge_leases",
                payload={
                    "bot_qq": self.bot_qq,
                    "conversation_key": conversation_key,
                    "owner_id": owner_id,
                    "lease_until": now + max(30, min(900, lease_seconds)),
                },
                prefer="return=minimal",
            )
            return True
        except RemoteMemoryError as exc:
            if exc.code == "http_error":
                return False
            raise

    def release_conversation(self, conversation_key: str, owner_id: str) -> None:
        self.client.request(
            "DELETE",
            "bridge_leases",
            query={
                "bot_qq": f"eq.{self.bot_qq}",
                "conversation_key": f"eq.{conversation_key}",
                "owner_id": f"eq.{owner_id}",
            },
            prefer="return=minimal",
        )

    def set_smart_group(self, group_id: str, enabled: bool) -> None:
        self.client.request(
            "POST",
            "bridge_smart_groups",
            payload={"bot_qq": self.bot_qq, "group_id": group_id, "enabled": enabled},
            query={"on_conflict": "bot_qq,group_id"},
            prefer="resolution=merge-duplicates,return=minimal",
        )

    def save_summary(self, conversation_key: str, summary: str, facts: list[str] | tuple[str, ...] = ()) -> None:
        value = summary.strip()[:4000]
        if not value:
            return
        version_rows = self.client.request(
            "GET",
            "bridge_summaries",
            query={
                "select": "version",
                "bot_qq": f"eq.{self.bot_qq}",
                "conversation_key": f"eq.{conversation_key}",
                "order": "version.desc",
                "limit": 1,
            },
        )
        version = 1
        if isinstance(version_rows, list) and version_rows and isinstance(version_rows[0], Mapping):
            try:
                version = int(version_rows[0].get("version", 0)) + 1
            except (TypeError, ValueError):
                version = 1
        self.client.request(
            "POST",
            "bridge_summaries",
            payload={
                "bot_qq": self.bot_qq,
                "conversation_key": conversation_key,
                "version": version,
                "summary": value,
            },
            prefer="return=minimal",
        )
        scope = f"conversation:{conversation_key}"
        for fact in facts[:40]:
            self.add_fact(scope, str(fact))
