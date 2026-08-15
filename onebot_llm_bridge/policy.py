from __future__ import annotations

from dataclasses import dataclass

from .models import NormalizedMessage


@dataclass(frozen=True)
class ReplyDecision:
    should_reply: bool
    reason: str
    mode: str = "reply"


def decide_reply(
    message: NormalizedMessage,
    *,
    group_mode: str,
    group_allowlist: frozenset[str],
    address_names: tuple[str, ...] = (),
) -> ReplyDecision:
    if message.conversation_type == "private":
        return ReplyDecision(True, "private_message")
    if message.conversation_id not in group_allowlist:
        return ReplyDecision(False, "group_not_allowlisted", "ignore")
    if group_mode == "off":
        return ReplyDecision(False, "group_mode_off", "ignore")
    if group_mode == "all":
        return ReplyDecision(True, "group_mode_all")
    addressed = message.to_me or any(name and name.lower() in message.text.lower() for name in address_names)
    if addressed:
        mode = "quote_reply" if message.reply_to else "reply"
        return ReplyDecision(True, "addressed", mode)
    return ReplyDecision(False, "not_addressed", "ignore")

