import tempfile
import unittest
from pathlib import Path

from onebot_llm_bridge.memory import SQLiteMemoryStore
from onebot_llm_bridge.models import NormalizedMessage


def sample(number: int) -> NormalizedMessage:
    return NormalizedMessage(
        event_id=f"event-{number}",
        timestamp=number,
        conversation_type="private",
        conversation_id="123",
        sender_id="123",
        sender_name="friend",
        message_id=str(number),
        text=f"message-{number}",
        segments=({"type": "text", "data": {"text": f"message-{number}"}},),
    )


class SQLiteMemoryStoreTests(unittest.TestCase):
    def test_recent_messages_survive_close_and_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.sqlite3"
            store = SQLiteMemoryStore(path, max_messages=3)
            for number in range(5):
                store.append(sample(number))
            store.close()

            reopened = SQLiteMemoryStore(path, max_messages=3)
            messages = reopened.load("private:123", 20)
            reopened.close()
            self.assertEqual([message.text for message in messages], ["message-2", "message-3", "message-4"])
            self.assertEqual(messages[0].segments[0]["type"], "text")

    def test_different_conversations_are_isolated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteMemoryStore(Path(directory) / "context.sqlite3")
            other = NormalizedMessage(
                **{**sample(1).__dict__, "conversation_id": "456"}
            )
            store.append(sample(1))
            store.append(other)
            self.assertEqual([item.text for item in store.load("private:123", 20)], ["message-1"])
            self.assertEqual([item.text for item in store.load("private:456", 20)], ["message-1"])
            store.close()

    def test_facts_can_be_saved_loaded_and_removed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteMemoryStore(Path(directory) / "context.sqlite3")
            store.add_fact("user:123", "喜欢记录的地平线", "42")
            store.add_fact("user:123", "喜欢记录的地平线", "43")
            self.assertEqual(store.load_facts("user:123"), ["喜欢记录的地平线"])
            self.assertTrue(store.remove_fact("user:123", "喜欢记录的地平线"))
            self.assertEqual(store.load_facts("user:123"), [])
            store.close()

    def test_processed_event_receipt_survives_close_and_reopen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "context.sqlite3"
            store = SQLiteMemoryStore(path)
            try:
                mark_processed = getattr(store, "mark_event_processed", None)
                is_processed = getattr(store, "is_event_processed", None)
                self.assertIsNotNone(mark_processed)
                self.assertIsNotNone(is_processed)
                mark_processed("group:999", "message-42")
                self.assertTrue(is_processed("group:999", "message-42"))
                self.assertFalse(is_processed("group:999", "message-43"))
                self.assertFalse(is_processed("group:888", "message-42"))
            finally:
                store.close()

            reopened = SQLiteMemoryStore(path)
            self.assertTrue(reopened.is_event_processed("group:999", "message-42"))
            reopened.close()
