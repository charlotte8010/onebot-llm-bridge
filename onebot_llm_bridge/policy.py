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
    bot_qq: str = "",
    active_topic: bool = False,
    decision_mode: str = "heuristic",
) -> ReplyDecision:
    def reply_mode(default: str = "reply") -> str:
        return "quote_reply" if message.reply_to else default

    if message.is_self or (bot_qq and message.sender_id == bot_qq):
        return ReplyDecision(False, "self_message", "ignore")
    if message.conversation_type == "private":
        return ReplyDecision(True, "private_message", reply_mode())
    if message.conversation_id not in group_allowlist:
        return ReplyDecision(False, "group_not_allowlisted", "ignore")
    if group_mode == "off":
        return ReplyDecision(False, "group_mode_off", "ignore")
    at_targets = {
        str(segment.get("data", {}).get("qq", "")).strip()
        for segment in message.segments
        if segment.get("type") == "at"
    }
    mentioned_qq = bool(bot_qq and bot_qq in at_targets)
    if at_targets and not mentioned_qq:
        return ReplyDecision(False, "mentions_other_user", "ignore")
    if group_mode == "all":
        return ReplyDecision(True, "group_mode_all", reply_mode())
    addressed = (
        bool(message.reply_to)
        or
        message.to_me
        or mentioned_qq
        or any(name and name.lower() in message.text.lower() for name in address_names)
    )
    if addressed:
        return ReplyDecision(True, "addressed", reply_mode())
    if group_mode == "smart" and (active_topic or decision_mode == "model"):
        reason = "active_topic" if active_topic else "model_decision_pending"
        mode = reply_mode("active_topic" if active_topic else "smart_decision")
        return ReplyDecision(True, reason, mode)
    return ReplyDecision(False, "not_addressed", "ignore")
