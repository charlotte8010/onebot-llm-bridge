from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping


class EventError(ValueError):
    """Raised when a OneBot event cannot be normalized."""


def _as_text(segment: Mapping[str, Any]) -> str:
    data = segment.get("data")
    if not isinstance(data, Mapping):
        return ""
    value = data.get("text")
    return value if isinstance(value, str) else ""


def _segments(message: Any) -> tuple[dict[str, Any], ...]:
    if isinstance(message, str):
        return ({"type": "text", "data": {"text": message}},)
    if not isinstance(message, list):
        raise EventError("message must be a string or segment list")
    result: list[dict[str, Any]] = []
    for item in message:
        if not isinstance(item, Mapping) or not isinstance(item.get("type"), str):
            raise EventError("message segment is invalid")
        data = item.get("data", {})
        if not isinstance(data, Mapping):
            raise EventError("message segment data is invalid")
        result.append({"type": item["type"], "data": dict(data)})
    return tuple(result)


@dataclass(frozen=True)
class NormalizedMessage:
    event_id: str
    timestamp: int
    conversation_type: str
    conversation_id: str
    sender_id: str
    sender_name: str
    message_id: str
    text: str
    segments: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    reply_to: str | None = None
    to_me: bool = False
    is_self: bool = False
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)

    @property
    def conversation_key(self) -> str:
        return f"{self.conversation_type}:{self.conversation_id}"

    @classmethod
    def from_onebot(cls, event: Mapping[str, Any]) -> "NormalizedMessage":
        if event.get("post_type") != "message":
            raise EventError("event is not a message event")
        conversation_type = event.get("message_type")
        if conversation_type not in {"private", "group"}:
            raise EventError("message_type must be private or group")
        sender = event.get("sender")
        if not isinstance(sender, Mapping):
            sender = {}
        user_id = event.get("user_id") or sender.get("user_id")
        if user_id is None:
            raise EventError("message has no user_id")
        conversation_id = event.get("group_id") if conversation_type == "group" else user_id
        if conversation_id is None:
            raise EventError("message has no conversation id")
        segments = _segments(event.get("message", ""))
        text_parts: list[str] = []
        for segment in segments:
            segment_type = segment["type"]
            if segment_type == "text":
                text_parts.append(_as_text(segment))
            elif segment_type == "image":
                text_parts.append("[图片]")
            elif segment_type == "at":
                text_parts.append(f"[@{segment.get('data', {}).get('qq', '')}]")
        reply = event.get("reply")
        reply_id = reply.get("id") if isinstance(reply, Mapping) else None
        event_id = str(event.get("self_id", "")) + ":" + str(event.get("message_id", time.time_ns()))
        return cls(
            event_id=event_id,
            timestamp=int(event.get("time", time.time())),
            conversation_type=str(conversation_type),
            conversation_id=str(conversation_id),
            sender_id=str(user_id),
            sender_name=str(sender.get("card") or sender.get("nickname") or user_id),
            message_id=str(event.get("message_id", "")),
            text="".join(text_parts).strip(),
            segments=segments,
            reply_to=str(reply_id) if reply_id is not None else None,
            to_me=bool(event.get("to_me")),
            is_self=bool(event.get("self_id")) and str(event.get("self_id")) == str(user_id),
            raw=dict(event),
        )

    def context_dict(self, bot_qq: str = "") -> dict[str, Any]:
        speaker = "unknown"
        if bot_qq or self.is_self:
            speaker = "bot" if self.is_self or self.sender_id == bot_qq else "other"
        return {
            "conversation": self.conversation_key,
            "sender_id": self.sender_id,
            "sender_name": self.sender_name,
            "message_id": self.message_id,
            "speaker": speaker,
            "text": self.text,
        }


def is_meaningful(message: NormalizedMessage) -> bool:
    return bool(message.text or any(segment["type"] in {"image", "record", "video"} for segment in message.segments))


def json_context(messages: list[NormalizedMessage], bot_qq: str = "") -> str:
    return json.dumps([message.context_dict(bot_qq) for message in messages], ensure_ascii=False)
