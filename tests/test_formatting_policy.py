import unittest

from onebot_llm_bridge.formatting import parse_reply_actions, split_bubbles
from onebot_llm_bridge.models import NormalizedMessage
from onebot_llm_bridge.policy import decide_reply


def message(
    *,
    kind: str = "private",
    group_id: str = "",
    text: str = "hello",
    to_me: bool = False,
    reply_to: str | None = None,
) -> NormalizedMessage:
    return NormalizedMessage(
        event_id="event",
        timestamp=1,
        conversation_type=kind,
        conversation_id=group_id or "123",
        sender_id="456",
        sender_name="friend",
        message_id="1",
        text=text,
        reply_to=reply_to,
        to_me=to_me,
    )


class FormattingAndPolicyTests(unittest.TestCase):
    def test_split_bubbles_accepts_explicit_markers_and_drops_empty_parts(self) -> None:
        self.assertEqual(split_bubbles("[[BUBBLE]]第一句\n[[BUBBLE]]\n第二句"), ["第一句", "第二句"])

    def test_plain_line_break_stays_in_one_bubble(self) -> None:
        self.assertEqual(split_bubbles("a\nb\nc", max_bubbles=2), ["a\nb\nc"])

    def test_only_explicit_markers_create_multiple_bubbles(self) -> None:
        self.assertEqual(split_bubbles("a\n[[BUBBLE]]b\n[[BUBBLE]]c", max_bubbles=2), ["a", "b"])

    def test_reply_actions_extracts_optional_reaction(self) -> None:
        self.assertEqual(
            parse_reply_actions("[[REACTION:128077]][[BUBBLE]]好耶"),
            (["好耶"], "128077"),
        )

    def test_reply_actions_accepts_semantic_reaction_name(self) -> None:
        self.assertEqual(
            parse_reply_actions("[[REACTION:赞]]太好了"),
            (["太好了"], "赞"),
        )

    def test_private_messages_reply(self) -> None:
        decision = decide_reply(message(), group_mode="mention", group_allowlist=frozenset())
        self.assertTrue(decision.should_reply)

    def test_private_message_reply_keeps_quote_target(self) -> None:
        decision = decide_reply(
            message(reply_to="42"),
            group_mode="mention",
            group_allowlist=frozenset(),
        )
        self.assertTrue(decision.should_reply)
        self.assertEqual(decision.mode, "quote_reply")

    def test_group_all_keeps_quote_target(self) -> None:
        decision = decide_reply(
            message(kind="group", group_id="123", reply_to="42"),
            group_mode="all",
            group_allowlist=frozenset({"123"}),
        )
        self.assertTrue(decision.should_reply)
        self.assertEqual(decision.mode, "quote_reply")

    def test_smart_active_topic_keeps_quote_target(self) -> None:
        decision = decide_reply(
            message(kind="group", group_id="123", text="继续", reply_to="42"),
            group_mode="smart",
            group_allowlist=frozenset({"123"}),
            active_topic=True,
        )
        self.assertTrue(decision.should_reply)
        self.assertEqual(decision.mode, "quote_reply")

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

    def test_model_decision_mode_accepts_unaddressed_smart_group_message(self) -> None:
        decision = decide_reply(
            message(kind="group", group_id="999"),
            group_mode="smart",
            group_allowlist=frozenset({"999"}),
            decision_mode="model",
        )
        self.assertTrue(decision.should_reply)
        self.assertEqual(decision.mode, "smart_decision")
