import threading
import tempfile
import time
import unittest
from pathlib import Path

from onebot_llm_bridge.config import Settings
from onebot_llm_bridge.services import Bridge, _related_topic


class FakeNapCat:
    def __init__(self):
        self.sent = []
        self.quoted = []
        self.reactions = []

    def send_private(self, user_id, message, *, reply_to=None):
        self.sent.append(("private", user_id, message))
        if reply_to:
            self.quoted.append(("private", user_id, reply_to))
        return {"status": "ok"}

    def send_group(self, group_id, message, *, reply_to=None):
        self.sent.append(("group", group_id, message))
        if reply_to:
            self.quoted.append(("group", group_id, reply_to))
        return {"status": "ok"}

    def set_msg_emoji_like(self, message_id, emoji_id):
        self.reactions.append((message_id, emoji_id))
        return {"status": "ok"}


class FakeImageResolver:
    def __init__(self):
        self.segments = []

    def resolve_segments(self, segments):
        self.segments = list(segments)
        return ["data:image/png;base64,abc"]


class BridgeTests(unittest.TestCase):
    def settings(self):
        return Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "GROUP_MODE": "mention",
                "GROUP_ALLOWLIST": "999",
            }
        )

    def test_private_event_is_replied_as_bubbles(self):
        napcat = FakeNapCat()
        bridge = Bridge(
            self.settings(),
            napcat=napcat,
            bot_request=lambda payload: {"bubbles": ["第一句", "第二句"]},
        )
        result = bridge.handle_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 123,
                "message_id": 1,
                "message": "你好",
            }
        )
        self.assertTrue(result["handled"])
        self.assertEqual(napcat.sent, [("private", "123", "第一句"), ("private", "123", "第二句")])

    def test_active_messages_can_send_to_private_and_group_targets(self):
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "ACTIVE_INTERVAL_MINUTES": "1",
                "ACTIVE_PRIVATE_ENABLED": "true",
                "ACTIVE_PRIVATE_TARGET_ID": "100",
                "ACTIVE_PRIVATE_PROMPT": "私聊提示",
                "ACTIVE_GROUP_ENABLED": "true",
                "ACTIVE_GROUP_TARGET_ID": "999",
                "ACTIVE_GROUP_PROMPT": "群聊提示",
            }
        )
        napcat = FakeNapCat()
        calls = []
        bridge = Bridge(
            settings,
            napcat=napcat,
            bot_request=lambda payload: calls.append(payload) or {"bubbles": ["主动消息"]},
        )
        bridge._active_message_tick("private")
        bridge._active_message_tick("group")
        bridge.shutdown()
        self.assertEqual(napcat.sent, [("private", "100", "主动消息"), ("group", "999", "主动消息")])
        self.assertEqual([call["conversation"] for call in calls], ["private:100", "group:999"])

    def test_unaddressed_group_is_ignored(self):
        napcat = FakeNapCat()
        bridge = Bridge(self.settings(), napcat=napcat, bot_request=lambda payload: {"bubbles": ["no"]})
        result = bridge.handle_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 999,
                "user_id": 123,
                "message_id": 1,
                "message": "普通聊天",
            }
        )
        self.assertFalse(result["handled"])
        self.assertEqual(result["reason"], "not_addressed")
        self.assertEqual(napcat.sent, [])

    def test_private_messages_are_merged_during_debounce_window(self):
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "DEBOUNCE_SECONDS": "0.05",
            }
        )
        napcat = FakeNapCat()
        calls = []
        done = threading.Event()

        def bot_request(payload):
            calls.append(payload)
            done.set()
            return {"bubbles": ["合并回复"]}

        bridge = Bridge(settings, napcat=napcat, bot_request=bot_request)
        base = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 123,
        }
        first = {**base, "message_id": 1, "message": "第一句"}
        second = {**base, "message_id": 2, "message": "第二句"}
        self.assertTrue(bridge.enqueue_event(first)["accepted"])
        time.sleep(0.01)
        self.assertTrue(bridge.enqueue_event(second)["accepted"])
        self.assertTrue(done.wait(1.0))
        bridge.shutdown()
        self.assertEqual(calls[0]["message"], "第一句\n第二句")
        self.assertEqual(napcat.sent, [("private", "123", "合并回复")])

    def test_group_smart_mode_continues_after_a_reply(self):
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "GROUP_MODE": "smart",
                "GROUP_ALLOWLIST": "999",
                "FOLLOWUP_SECONDS": "30",
            }
        )
        napcat = FakeNapCat()
        bridge = Bridge(settings, napcat=napcat, bot_request=lambda payload: {"bubbles": ["好"]})
        first = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 999,
            "user_id": 123,
            "message_id": 10,
            "message": "[@bot] 开始聊",
            "to_me": True,
        }
        second = {**first, "message_id": 11, "message": "然后呢", "to_me": False}
        self.assertTrue(bridge.handle_event(first)["handled"])
        self.assertTrue(bridge.handle_event(second)["handled"])

    def test_group_smart_mode_does_not_continue_into_unrelated_question(self):
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "GROUP_MODE": "smart",
                "GROUP_ALLOWLIST": "999",
                "FOLLOWUP_SECONDS": "30",
            }
        )
        napcat = FakeNapCat()
        bridge = Bridge(settings, napcat=napcat, bot_request=lambda payload: {"bubbles": ["ok"]})
        first = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 999,
            "user_id": 123,
            "message_id": 10,
            "message": "[@bot] 讨论今天的电影",
            "to_me": True,
        }
        unrelated = {**first, "message_id": 11, "message": "最近公司搬家以后通勤时间变长了怎么办？", "to_me": False}
        self.assertTrue(bridge.handle_event(first)["handled"])
        result = bridge.handle_event(unrelated)
        self.assertFalse(result["handled"])
        self.assertEqual(result["reason"], "not_addressed")

    def test_topic_relation_uses_short_followups_and_shared_terms(self):
        self.assertTrue(_related_topic("真的吗", "今天聊电影"))
        self.assertTrue(_related_topic("那部电影的结局", "今天聊电影"))
        self.assertFalse(_related_topic("最近公司搬家以后通勤时间变长了怎么办？", "今天聊电影"))

    def test_group_reply_event_is_sent_with_quote(self):
        settings = self.settings()
        napcat = FakeNapCat()
        bridge = Bridge(settings, napcat=napcat, bot_request=lambda payload: {"bubbles": ["回答"]})
        event = {
            "post_type": "message",
            "message_type": "group",
            "group_id": 999,
            "user_id": 123,
            "message_id": 22,
            "message": "问题",
            "to_me": True,
            "reply": {"id": 21},
        }
        self.assertTrue(bridge.handle_event(event)["handled"])
        self.assertEqual(napcat.quoted, [("group", "999", "21")])

    def test_image_segments_are_resolved_before_bot_request(self):
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "VISION_MODE": "direct",
            }
        )
        napcat = FakeNapCat()
        resolver = FakeImageResolver()
        calls = []
        bridge = Bridge(
            settings,
            napcat=napcat,
            image_resolver=resolver,
            bot_request=lambda payload: calls.append(payload) or {"bubbles": ["看到了"]},
        )
        result = bridge.handle_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 123,
                "message_id": 1,
                "message": [{"type": "image", "data": {"file": "photo"}}],
            }
        )
        self.assertTrue(result["handled"])
        self.assertEqual(calls[0]["images"], ["data:image/png;base64,abc"])
        self.assertEqual(resolver.segments[0]["type"], "image")

    def test_memory_command_is_saved_without_calling_model(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings.from_values(
                {
                    "LLM_API_KEY": "key",
                    "LLM_BASE_URL": "https://example.test/v1",
                    "LLM_MODEL": "chat",
                    "MEMORY_DB": str(Path(directory) / "memory.sqlite3"),
                }
            )
            napcat = FakeNapCat()
            calls = []
            bridge = Bridge(settings, napcat=napcat, bot_request=lambda payload: calls.append(payload))
            result = bridge.handle_event(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": 123,
                    "message_id": 1,
                    "message": "记住：喜欢记录的地平线",
                }
            )
            bridge.shutdown()
            self.assertTrue(result["handled"])
            self.assertEqual(calls, [])
            self.assertEqual(napcat.sent[-1][2], "记住了")

    def test_reaction_result_uses_napcat_action_when_enabled(self):
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "REACTION_MODE": "like",
            }
        )
        napcat = FakeNapCat()
        bridge = Bridge(settings, napcat=napcat, bot_request=lambda payload: {"bubbles": [], "reaction_id": "128077"})
        result = bridge.handle_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 123,
                "message_id": 9,
                "message": "好好笑",
            }
        )
        self.assertTrue(result["handled"])
        self.assertEqual(napcat.reactions, [("9", "128077")])

    def test_model_decision_can_ignore_an_unaddressed_smart_group_message(self):
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "GROUP_MODE": "smart",
                "DECISION_MODE": "model",
                "GROUP_ALLOWLIST": "999",
            }
        )
        napcat = FakeNapCat()
        bridge = Bridge(
            settings,
            napcat=napcat,
            bot_request=lambda _payload: {"bubbles": ["should not be called"]},
            decision_request=lambda _payload: {"action": "ignore", "reason": "unrelated"},
        )
        result = bridge.handle_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 999,
                "user_id": 123,
                "message_id": 10,
                "message": "群里正在聊别的事情",
            }
        )
        self.assertFalse(result["handled"])
        self.assertEqual(result["reason"], "model_decision_ignore")
        self.assertEqual(napcat.sent, [])

    def test_model_decision_can_quote_reply(self):
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "GROUP_MODE": "smart",
                "DECISION_MODE": "model",
                "GROUP_ALLOWLIST": "999",
            }
        )
        napcat = FakeNapCat()
        bridge = Bridge(
            settings,
            napcat=napcat,
            bot_request=lambda _payload: {"bubbles": ["回复"]},
            decision_request=lambda _payload: {
                "action": "quote_reply",
                "target_message_id": "11",
                "reason": "引用当前消息",
            },
        )
        result = bridge.handle_event(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 999,
                "user_id": 123,
                "message_id": 11,
                "message": "这个可以聊吗",
            }
        )
        self.assertTrue(result["handled"])
        self.assertEqual(napcat.quoted, [("group", "999", "11")])
