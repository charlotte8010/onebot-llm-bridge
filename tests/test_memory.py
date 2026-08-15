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
