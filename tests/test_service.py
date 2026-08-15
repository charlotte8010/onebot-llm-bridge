import unittest

from onebot_llm_bridge.config import Settings
from onebot_llm_bridge.services import BotService


class FakeProvider:
    def __init__(self, reply):
        self.reply_text = reply
        self.calls = []

    def complete(self, messages, images=None):
        self.calls.append((messages, images or []))
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
