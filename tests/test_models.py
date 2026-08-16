import unittest

from onebot_llm_bridge.models import EventError, NormalizedMessage, is_meaningful


class ModelTests(unittest.TestCase):
    def test_normalizes_private_text_and_image(self) -> None:
        message = NormalizedMessage.from_onebot(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 123,
                "self_id": 123,
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

    def test_normalizes_reply_message_segment(self) -> None:
        message = NormalizedMessage.from_onebot(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 456,
                "message_id": 100,
                "message": [
                    {"type": "reply", "data": {"id": "99"}},
                    {"type": "text", "data": {"text": "继续说"}},
                ],
            }
        )
        self.assertEqual(message.reply_to, "99")

    def test_normalizes_reply_from_legacy_cq_raw_message(self) -> None:
        message = NormalizedMessage.from_onebot(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 123,
                "message_id": 2,
                "message": "[CQ:reply,id=98]继续",
                "raw_message": "[CQ:reply,id=98]继续",
            }
        )
        self.assertEqual(message.reply_to, "98")

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

    def test_context_marks_bot_and_other_speakers(self) -> None:
        message = NormalizedMessage.from_onebot(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 123,
                "self_id": 123,
                "message_id": 99,
                "message": "hello",
            }
        )
        self.assertEqual(message.context_dict("123")["speaker"], "bot")
        self.assertTrue(message.is_self)
        other = NormalizedMessage.from_onebot(
            {
                "post_type": "message",
                "message_type": "private",
                "user_id": 456,
                "message_id": 100,
                "message": "hello",
            }
        )
        self.assertEqual(other.context_dict("123")["speaker"], "other")
