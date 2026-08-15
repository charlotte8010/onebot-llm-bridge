import unittest

from onebot_llm_bridge.formatting import split_bubbles
from onebot_llm_bridge.models import NormalizedMessage
from onebot_llm_bridge.policy import decide_reply


def message(*, kind: str = "private", group_id: str = "", text: str = "hello", to_me: bool = False) -> NormalizedMessage:
    return NormalizedMessage(
        event_id="event",
        timestamp=1,
        conversation_type=kind,
        conversation_id=group_id or "123",
        sender_id="456",
        sender_name="friend",
        message_id="1",
        text=text,
        to_me=to_me,
    )


class FormattingAndPolicyTests(unittest.TestCase):
    def test_split_bubbles_accepts_explicit_markers_and_drops_empty_parts(self) -> None:
        self.assertEqual(split_bubbles("[[BUBBLE]]第一句\n[[BUBBLE]]\n第二句"), ["第一句", "第二句"])

    def test_split_bubbles_falls_back_to_lines_and_limits_count(self) -> None:
        self.assertEqual(split_bubbles("a\nb\nc", max_bubbles=2), ["a", "b"])

    def test_private_messages_reply(self) -> None:
        decision = decide_reply(message(), group_mode="mention", group_allowlist=frozenset())
        self.assertTrue(decision.should_reply)

    def test_group_requires_allowlist_and_address(self) -> None:
        group = message(kind="group", group_id="999", to_me=True)
        self.assertFalse(
            decide_reply(group, group_mode="mention", group_allowlist=frozenset()).should_reply
        )
        self.assertTrue(
            decide_reply(group, group_mode="mention", group_allowlist=frozenset({"999"})).should_reply
        )

    def test_at_bot_and_smart_followup_can_address_a_group(self) -> None:
        addressed = message(kind="group", group_id="999", text="[@123] 你好")
        addressed = NormalizedMessage(
            **{**addressed.__dict__, "segments": ({"type": "at", "data": {"qq": "123"}},)}
        )
        decision = decide_reply(
            addressed,
            group_mode="mention",
            group_allowlist=frozenset({"999"}),
            bot_qq="123",
        )
        self.assertTrue(decision.should_reply)
        continuation = decide_reply(
            message(kind="group", group_id="999"),
            group_mode="smart",
            group_allowlist=frozenset({"999"}),
            active_topic=True,
        )
        self.assertEqual(continuation.reason, "active_topic")
