import hashlib
import hmac
import io
import tempfile
import threading
import time
import unittest
from email.message import Message
from pathlib import Path
from types import SimpleNamespace

from onebot_llm_bridge.config import Settings
from onebot_llm_bridge.models import NormalizedMessage
from onebot_llm_bridge.services import (
    Bridge,
    JsonHandler,
    _event_auth_matches,
    _merge_context_messages,
    _related_topic,
)


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
    def test_remote_and_local_context_are_merged_without_duplicate_turns(self):
        def message(message_id, text):
            return NormalizedMessage(
                event_id=f"event-{message_id}",
                timestamp=int(message_id),
                conversation_type="private",
                conversation_id="123",
                sender_id="123",
                sender_name="friend",
                message_id=str(message_id),
                text=text,
            )

        merged = _merge_context_messages(
            [message(1, "远程旧话题"), message(2, "远程重复")],
            [message(2, "本地重复"), message(3, "刚刚收到")],
            10,
        )
        self.assertEqual([item.text for item in merged], ["远程旧话题", "远程重复", "刚刚收到"])

    def test_context_merge_keeps_only_latest_limit(self):
        messages = [
            NormalizedMessage(
                event_id=f"event-{index}",
                timestamp=index,
                conversation_type="private",
                conversation_id="123",
                sender_id="123",
                sender_name="friend",
                message_id=str(index),
                text=str(index),
            )
            for index in range(5)
        ]
        self.assertEqual([item.text for item in _merge_context_messages(messages, [], 2)], ["3", "4"])

    def test_json_handler_decodes_chunked_request_body(self):
        body = b'{"post_type":"message"}'
        encoded = f"{len(body):X}".encode() + b"\r\n" + body + b"\r\n0\r\n\r\n"
        handler = JsonHandler.__new__(JsonHandler)
        handler.headers = Message()
        handler.headers["Transfer-Encoding"] = "chunked"
        handler.rfile = io.BytesIO(encoded)
        self.assertEqual(handler.read_body(), body)

    def test_event_auth_accepts_napcat_hmac_signature(self):
        body = b'{"post_type":"message"}'
        token = "event-token"
        digest = hmac.new(token.encode("utf-8"), body, hashlib.sha1).hexdigest()
        self.assertTrue(_event_auth_matches("", f"sha1={digest}", token, body))

    def test_event_auth_rejects_invalid_napcat_signature(self):
        body = b'{"post_type":"message"}'
        self.assertFalse(_event_auth_matches("", "sha1=bad", "event-token", body))

    def test_event_auth_keeps_bearer_compatibility(self):
        body = b"{}"
        self.assertTrue(_event_auth_matches("Bearer event-token", "", "event-token", body))

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

    def test_active_messages_can_send_to_multiple_private_targets(self):
        settings = Settings.from_values(
            {
                "ACTIVE_INTERVAL_MINUTES": "1",
                "ACTIVE_PRIVATE_ENABLED": "true",
                "ACTIVE_PRIVATE_TARGET_ID": "100, 200",
                "ACTIVE_PRIVATE_PROMPT": "私聊提示",
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
        bridge.shutdown()
        self.assertEqual(
            napcat.sent,
            [("private", "100", "主动消息"), ("private", "200", "主动消息")],
        )
        self.assertEqual([call["conversation"] for call in calls], ["private:100", "private:200"])

    def test_active_message_uses_target_context_and_records_bot_turn(self):
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "ACTIVE_INTERVAL_MINUTES": "1",
                "ACTIVE_PRIVATE_ENABLED": "true",
                "ACTIVE_PRIVATE_TARGET_ID": "100",
                "ACTIVE_PRIVATE_PROMPT": "找个话题继续聊",
            }
        )
        napcat = FakeNapCat()
        calls = []

        def bot_request(payload):
            calls.append(payload)
            return {"bubbles": ["记得刚才的话题"]}

        bridge = Bridge(settings, napcat=napcat, bot_request=bot_request)
        bridge.handle_event(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 100,
                "message_id": 1,
                "message": "我们刚才聊到如龙了",
            }
        )
        bridge._active_message_tick("private", "100")
        bridge.shutdown()

        active_call = calls[-1]
        self.assertTrue(active_call["active"])
        self.assertEqual(active_call["conversation"], "private:100")
        context_text = [item["text"] for item in active_call["context"]]
        self.assertEqual(context_text, ["我们刚才聊到如龙了", "记得刚才的话题"])
        self.assertEqual(
            napcat.sent,
            [
                ("private", "100", "记得刚才的话题"),
                ("private", "100", "记得刚才的话题"),
            ],
        )

    def test_outbound_messages_without_api_ids_keep_distinct_turns(self):
        bridge = Bridge(self.settings(), napcat=FakeNapCat(), bot_request=lambda payload: {})
        bridge._record_outbound_message("private", "123", "first outbound")
        bridge._record_outbound_message("private", "123", "second outbound")
        with bridge._state_lock:
            context = list(bridge._context_for("private:123"))
        bridge.shutdown()
        self.assertEqual([item.text for item in context], ["first outbound", "second outbound"])
        self.assertTrue(all(item.message_id for item in context))
        self.assertNotEqual(context[0].message_id, context[1].message_id)

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

    def test_bot_own_message_is_ignored(self):
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "BOT_QQ": "100",
            }
        )
        napcat = FakeNapCat()
        calls = []
        bridge = Bridge(settings, napcat=napcat, bot_request=lambda payload: calls.append(payload))
        result = bridge.handle_event(
            {
                "post_type": "message",
                "message_type": "private",
                "self_id": 100,
                "user_id": 100,
                "message_id": 101,
                "message": "这是机器人自己发的",
            }
        )
        bridge.shutdown()
        self.assertFalse(result["handled"])
        self.assertEqual(result["reason"], "self_message")
        self.assertEqual(calls, [])
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

    def test_duplicate_onebot_event_is_ignored(self):
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "DEBOUNCE_SECONDS": "0.01",
            }
        )
        napcat = FakeNapCat()
        calls = []
        done = threading.Event()

        def bot_request(payload):
            calls.append(payload)
            done.set()
            return {"bubbles": ["只回一次"]}

        bridge = Bridge(settings, napcat=napcat, bot_request=bot_request)
        event = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 123,
            "message_id": 77,
            "message": "重复上报",
        }
        self.assertTrue(bridge.enqueue_event(event)["accepted"])
        duplicate = bridge.enqueue_event(event)
        self.assertFalse(duplicate["accepted"])
        self.assertEqual(duplicate["reason"], "duplicate_event")
        self.assertTrue(done.wait(1.0))
        bridge.shutdown()
        self.assertEqual(len(calls), 1)
        self.assertEqual(napcat.sent, [("private", "123", "只回一次")])

    def test_repeated_user_auto_reply_is_handled_once(self):
        settings = self.settings()
        napcat = FakeNapCat()
        calls = []
        bridge = Bridge(
            settings,
            napcat=napcat,
            bot_request=lambda payload: calls.append(payload) or {"bubbles": ["收到"]},
        )
        first = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 123,
            "message_id": 101,
            "message": "我现在有事不在，一会再联系",
        }
        second = {
            **first,
            "message_id": 102,
            "message": "暂时无法回复，稍后联系",
        }
        self.assertTrue(bridge.handle_event(first)["handled"])
        result = bridge.handle_event(second)
        bridge.shutdown()
        self.assertFalse(result["handled"])
        self.assertEqual(result["reason"], "repeated_auto_reply")
        self.assertEqual(len(calls), 1)
        self.assertEqual(napcat.sent, [("private", "123", "收到")])

    def test_auto_reply_event_subtype_is_handled_once(self):
        settings = self.settings()
        napcat = FakeNapCat()
        bridge = Bridge(
            settings,
            napcat=napcat,
            bot_request=lambda _payload: {"bubbles": ["好"]},
        )
        first = {
            "post_type": "message",
            "message_type": "private",
            "user_id": 123,
            "message_id": 201,
            "sub_type": "auto_reply",
            "message": "你好",
        }
        second = {**first, "message_id": 202}
        self.assertTrue(bridge.handle_event(first)["handled"])
        result = bridge.handle_event(second)
        bridge.shutdown()
        self.assertFalse(result["handled"])
        self.assertEqual(result["reason"], "repeated_auto_reply")
        self.assertEqual(napcat.sent, [("private", "123", "好")])

    def test_failed_coordinated_batch_releases_remote_lease(self):
        class FakeLeaseMemory:
            def __init__(self):
                self.released = []

            def ingest(self, _message):
                return True

            def claim_conversation(self, *_args):
                return True

            def release_conversation(self, conversation_key, owner_id):
                self.released.append((conversation_key, owner_id))

            def load_context(self, _conversation_key, _limit):
                return SimpleNamespace(messages=(), summary="", facts=())

            def load_facts(self, _scope_key):
                return []

        settings = Settings.from_values(
            {
                "LLM_API_KEY": "key",
                "LLM_BASE_URL": "https://example.test/v1",
                "LLM_MODEL": "chat",
                "REMOTE_MEMORY_MODE": "coordinated",
            }
        )
        remote = FakeLeaseMemory()
        bridge = Bridge(
            settings,
            napcat=FakeNapCat(),
            remote_memory=remote,
            bot_request=lambda _payload: (_ for _ in ()).throw(RuntimeError("model unavailable")),
        )
        with self.assertRaises(RuntimeError):
            bridge.handle_event(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": 123,
                    "message_id": 78,
                    "message": "请求失败",
                }
            )
        bridge.shutdown()
        self.assertTrue(remote.released)

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
