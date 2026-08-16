import json
import tempfile
import unittest
from pathlib import Path

from onebot_llm_bridge.config import Settings
from onebot_llm_bridge.providers import ProviderError
from onebot_llm_bridge.services import BotService


class FakeProvider:
    def __init__(self, reply):
        self.reply_text = reply
        self.calls = []

    def complete(self, messages, images=None):
        self.calls.append((messages, images or []))
        return self.reply_text


class FailingVisionProvider(FakeProvider):
    def complete(self, messages, images=None):
        self.calls.append((messages, images or []))
        raise ProviderError("vision unavailable")


class ImageRejectingProvider(FakeProvider):
    def complete(self, messages, images=None):
        self.calls.append((messages, images or []))
        if images:
            raise ProviderError("vision unsupported")
        return self.reply_text


class BotServiceVisionTests(unittest.TestCase):
    def settings(self, mode):
        return Settings.from_values(
            {
                "LLM_API_KEY": "chat-key",
                "LLM_BASE_URL": "https://chat.example/v1",
                "LLM_MODEL": "chat-model",
                "VISION_MODE": mode,
                "VISION_API_KEY": "vision-key",
                "VISION_BASE_URL": "https://vision.example/v1",
                "VISION_MODEL": "vision-model",
            }
        )

    def payload(self):
        return {"message": "这是什么", "context": [], "images": ["data:image/png;base64,abc"]}

    def test_separate_mode_describes_then_uses_text_model(self):
        chat = FakeProvider("聊天回复")
        vision = FakeProvider("一张测试图片")
        service = BotService(self.settings("separate"), provider=chat, vision_provider=vision)
        result = service.reply(self.payload())
        self.assertEqual(result["bubbles"], ["聊天回复"])
        self.assertEqual(vision.calls[0][1], ["data:image/png;base64,abc"])
        self.assertEqual(chat.calls[0][1], [])
        self.assertIn("一张测试图片", chat.calls[0][0][-1]["content"])

    def test_direct_mode_passes_images_to_the_main_model(self):
        chat = FakeProvider("看到了")
        service = BotService(self.settings("direct"), provider=chat)
        service.reply(self.payload())
        self.assertEqual(chat.calls[0][1], ["data:image/png;base64,abc"])

    def test_off_mode_drops_images(self):
        chat = FakeProvider("普通回复")
        service = BotService(self.settings("off"), provider=chat)
        service.reply(self.payload())
        self.assertEqual(chat.calls[0][1], [])

    def test_reply_prompt_exposes_bot_history_as_assistant_turn(self):
        chat = FakeProvider("answer")
        service = BotService(self.settings("off"), provider=chat)
        service.reply(
            {
                "message": "what now",
                "context": [
                    {"speaker": "other", "text": "Is it good?"},
                    {"speaker": "bot", "text": "I already said the topping is good."},
                ],
            }
        )
        prompt = chat.calls[0][0][-1]["content"]
        self.assertIn("user: Is it good?", prompt)
        self.assertIn("assistant: I already said the topping is good.", prompt)
        self.assertIn("Current message from user:\nwhat now", prompt)

    def test_reply_prompt_includes_builtin_human_chat_worldbook_before_persona(self):
        with tempfile.TemporaryDirectory() as directory:
            persona = Path(directory) / "persona.txt"
            persona.write_text("个人规则：逗号少一点", encoding="utf-8")
            settings = Settings.from_values(
                {
                    "LLM_API_KEY": "chat-key",
                    "LLM_BASE_URL": "https://chat.example/v1",
                    "LLM_MODEL": "chat-model",
                    "PERSONA_FILE": str(persona),
                }
            )
            chat = FakeProvider("answer")
            BotService(settings, provider=chat).reply({"message": "你好", "context": []})
            prompt = chat.calls[0][0][0]["content"]
            self.assertIn("Built-in human-chat worldbook:", prompt)
            self.assertIn("禁止机械套用固定开场", prompt)
            self.assertIn("User-provided persona:", prompt)
            self.assertLess(prompt.index("Built-in human-chat worldbook:"), prompt.index("User-provided persona:"))

    def test_separate_mode_falls_back_when_vision_provider_fails(self):
        chat = FakeProvider("只根据文字回复")
        service = BotService(
            self.settings("separate"),
            provider=chat,
            vision_provider=FailingVisionProvider("unused"),
        )
        result = service.reply(self.payload())
        self.assertEqual(result["bubbles"], ["只根据文字回复"])
        self.assertEqual(chat.calls[0][1], [])
        self.assertNotIn("Image understanding", chat.calls[0][0][-1]["content"])

    def test_direct_mode_retries_without_images_when_main_model_rejects_them(self):
        chat = ImageRejectingProvider("文字降级回复")
        service = BotService(self.settings("direct"), provider=chat)
        result = service.reply(self.payload())
        self.assertEqual(result["bubbles"], ["文字降级回复"])
        self.assertEqual([call[1] for call in chat.calls], [["data:image/png;base64,abc"], []])

    def test_allowlisted_tool_is_resolved_before_final_reply(self):
        chat = FakeProvider("[[TOOL:get_time]]")
        chat.reply_text = "[[TOOL:get_time]]"  # first response remains the tool request

        responses = iter(["[[TOOL:get_time]]", "工具结果已收到"])

        def complete(messages, images=None):
            chat.calls.append((messages, images or []))
            return next(responses)

        chat.complete = complete
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "chat-key",
                "LLM_BASE_URL": "https://chat.example/v1",
                "LLM_MODEL": "chat-model",
                "TOOLS_ENABLED": "true",
                "TOOL_ALLOWLIST": "get_time",
            }
        )
        result = BotService(settings, provider=chat).reply({"message": "现在几点", "context": []})
        self.assertEqual(result["bubbles"], ["工具结果已收到"])
        self.assertEqual(len(chat.calls), 2)

    def test_time_query_uses_allowlisted_tool_when_model_omits_marker(self):
        responses = iter(["我直接回答一下", "现在是工具返回的时间"])
        chat = FakeProvider("")

        def complete(messages, images=None):
            chat.calls.append((messages, images or []))
            return next(responses)

        chat.complete = complete
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "chat-key",
                "LLM_BASE_URL": "https://chat.example/v1",
                "LLM_MODEL": "chat-model",
                "TOOLS_ENABLED": "true",
                "TOOL_ALLOWLIST": "get_time",
            }
        )
        result = BotService(settings, provider=chat).reply({"message": "现在几点", "context": []})
        self.assertEqual(result["bubbles"], ["现在是工具返回的时间"])
        self.assertEqual(len(chat.calls), 2)
        self.assertIn("get_time:", chat.calls[1][0][-1]["content"])

    def test_summary_endpoint_shape_is_bounded(self):
        chat = FakeProvider('{"summary":"short","facts":["likes books"]}')
        service = BotService(self.settings("off"), provider=chat)
        result = service.summarize({"context": [{"text": "hello"}]})
        self.assertEqual(result, {"summary": "short", "facts": ["likes books"]})

    def test_summary_rejects_non_json_model_output(self):
        service = BotService(self.settings("off"), provider=FakeProvider("not json"))
        with self.assertRaises(ProviderError):
            service.summarize({"context": [{"text": "hello"}]})

    def test_decision_endpoint_returns_valid_routing_action(self):
        chat = FakeProvider('{"action":"quote_reply","target_message_id":"2","emoji_id":"","reason":"direct follow-up"}')
        service = BotService(self.settings("off"), provider=chat)
        result = service.decide(
            {
                "conversation": "private:123",
                "message": "继续刚才那个",
                "context": [{"message_id": "1", "text": "刚才的话题"}],
                "target_message_ids": ["2"],
                "allow_reactions": False,
            }
        )
        self.assertEqual(result["action"], "quote_reply")
        self.assertEqual(result["target_message_id"], "2")

    def test_decision_rejects_invalid_target(self):
        service = BotService(
            self.settings("off"),
            provider=FakeProvider('{"action":"reply","target_message_id":"999"}'),
        )
        with self.assertRaises(ProviderError):
            service.decide({"message": "hello", "context": [], "target_message_ids": ["1"]})

    def test_named_reaction_is_resolved_through_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / "emojis.json"
            catalog.write_text(
                json.dumps({"赞": {"id": "128077", "meaning": "认可", "usage": "偶尔使用"}}),
                encoding="utf-8",
            )
            settings = Settings.from_values(
                {
                    "LLM_API_KEY": "chat-key",
                    "LLM_BASE_URL": "https://chat.example/v1",
                    "LLM_MODEL": "chat-model",
                    "REACTION_MODE": "like",
                    "EMOJI_CATALOG": str(catalog),
                }
            )
            service = BotService(settings, provider=FakeProvider("[[REACTION:赞]]"))
            result = service.reply({"message": "太好了", "context": []})
            self.assertEqual(result["bubbles"], [])
            self.assertEqual(result["reaction_id"], "128077")
