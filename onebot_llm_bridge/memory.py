from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from .models import NormalizedMessage


class SQLiteMemoryStore:
    """Small optional store for recent normalized messages.

    It stores only the normalized message fields needed to rebuild context,
    not the complete raw OneBot event. This keeps tokens, adapter metadata,
    and unrelated event fields out of the persistent context database.
    """

    def __init__(self, path: str | Path, *, max_messages: int = 100) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_messages = max(1, max_messages)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_key TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_key, id)"
        )
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scope_key TEXT NOT NULL,
                fact TEXT NOT NULL,
                source_message_id TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL
            )
            """
        )
        self._connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_scope ON facts(scope_key, id)"
        )
        self._connection.commit()

    def append(self, message: NormalizedMessage) -> None:
        payload = {
            "event_id": message.event_id,
            "timestamp": message.timestamp,
            "conversation_type": message.conversation_type,
            "conversation_id": message.conversation_id,
            "sender_id": message.sender_id,
            "sender_name": message.sender_name,
            "message_id": message.message_id,
            "text": message.text,
            "segments": list(message.segments),
            "reply_to": message.reply_to,
            "to_me": message.to_me,
            "is_self": message.is_self,
        }
        with self._lock:
            self._connection.execute(
                "INSERT INTO messages(conversation_key, payload) VALUES (?, ?)",
                (message.conversation_key, json.dumps(payload, ensure_ascii=False)),
            )
            self._connection.execute(
                """
                DELETE FROM messages
                WHERE conversation_key = ?
                  AND id NOT IN (
                    SELECT id FROM messages
                    WHERE conversation_key = ?
                    ORDER BY id DESC LIMIT ?
                  )
                """,
                (message.conversation_key, message.conversation_key, self.max_messages),
            )
            self._connection.commit()

    def load(self, conversation_key: str, limit: int) -> list[NormalizedMessage]:
        if limit <= 0:
            return []
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM messages WHERE conversation_key = ? ORDER BY id DESC LIMIT ?",
                (conversation_key, limit),
            ).fetchall()
        result: list[NormalizedMessage] = []
        for (raw_payload,) in reversed(rows):
            try:
                payload = json.loads(raw_payload)
                segments = tuple(payload.get("segments", ()))
                result.append(
                    NormalizedMessage(
                        event_id=str(payload["event_id"]),
                        timestamp=int(payload["timestamp"]),
                        conversation_type=str(payload["conversation_type"]),
                        conversation_id=str(payload["conversation_id"]),
                        sender_id=str(payload["sender_id"]),
                        sender_name=str(payload["sender_name"]),
                        message_id=str(payload["message_id"]),
                        text=str(payload["text"]),
                        segments=segments,
                        reply_to=payload.get("reply_to"),
                        to_me=bool(payload.get("to_me")),
                        is_self=bool(payload.get("is_self")),
                    )
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
        return result

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def add_fact(self, scope_key: str, fact: str, source_message_id: str = "") -> None:
        value = fact.strip()
        if not value:
            return
        with self._lock:
            self._connection.execute(
                "DELETE FROM facts WHERE scope_key = ? AND fact = ?",
                (scope_key, value),
            )
            self._connection.execute(
                "INSERT INTO facts(scope_key, fact, source_message_id, created_at) VALUES (?, ?, ?, strftime('%s', 'now'))",
                (scope_key, value, source_message_id),
            )
            self._connection.commit()

    def load_facts(self, scope_key: str, limit: int = 40) -> list[str]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT fact FROM facts WHERE scope_key = ? ORDER BY id DESC LIMIT ?",
                (scope_key, max(0, limit)),
            ).fetchall()
        return [str(row[0]) for row in reversed(rows)]

    def remove_fact(self, scope_key: str, fact: str) -> bool:
        value = fact.strip()
        with self._lock:
            cursor = self._connection.execute(
                "DELETE FROM facts WHERE scope_key = ? AND fact = ?",
                (scope_key, value),
            )
            self._connection.commit()
        return cursor.rowcount > 0
