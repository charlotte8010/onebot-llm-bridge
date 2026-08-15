import unittest

from onebot_llm_bridge.models import EventError, NormalizedMessage, is_meaningful


class ModelTests(unittest.TestCase):
    def test_normalizes_private_text_and_image(self) -> None:
        message = NormalizedMessage.from_onebot(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 123,
                "message_id": 99,
                "message": [
                    {"type": "text", "data": {"text": "你好"}},
                    {"type": "image", "data": {"file": "a.jpg"}},
                ],
                "sender": {"nickname": "小明"},
            }
        )
        self.assertEqual(message.conversation_key, "private:123")
        self.assertEqual(message.text, "你好[图片]")
        self.assertTrue(is_meaningful(message))

    def test_rejects_non_message_event(self) -> None:
        with self.assertRaises(EventError):
            NormalizedMessage.from_onebot({"post_type": "notice"})

    def test_group_context_uses_group_id(self) -> None:
        message = NormalizedMessage.from_onebot(
            {
                "post_type": "message",
                "message_type": "group",
                "group_id": 456,
                "user_id": 123,
                "message_id": 99,
                "message": "hello",
            }
        )
        self.assertEqual(message.conversation_key, "group:456")
        self.assertEqual(message.sender_id, "123")

