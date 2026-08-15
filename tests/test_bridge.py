import threading
import time
import unittest

from onebot_llm_bridge.config import Settings
from onebot_llm_bridge.services import Bridge


class FakeNapCat:
    def __init__(self):
        self.sent = []

    def send_private(self, user_id, message):
        self.sent.append(("private", user_id, message))
        return {"status": "ok"}

    def send_group(self, group_id, message):
        self.sent.append(("group", group_id, message))
        return {"status": "ok"}


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
