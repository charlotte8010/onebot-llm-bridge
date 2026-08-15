import json
import unittest

from onebot_llm_bridge.models import NormalizedMessage
from onebot_llm_bridge.remote_memory import RemoteMemoryStore, SupabaseRestClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class RemoteMemoryTests(unittest.TestCase):
    def setUp(self):
        self.requests = []

        def opener(request, timeout):
            self.requests.append((request, timeout))
            path = request.full_url.split("/rest/v1/", 1)[1].split("?", 1)[0]
            if request.method == "GET" and path == "bridge_messages":
                return FakeResponse(
                    [
                        {
                            "conversation_type": "private",
                            "conversation_id": "42",
                            "sender_id": "42",
                            "sender_name": "friend",
                            "source_message_id": "8",
                            "content_text": "hello",
                            "segments": [{"type": "text", "data": {"text": "hello"}}],
                            "reply_to": None,
                            "occurred_at": 8,
                        }
                    ]
                )
            if request.method == "GET" and path == "bridge_summaries":
                return FakeResponse([{"summary": "A short summary", "version": 1}])
            if request.method == "GET" and path == "bridge_facts":
                return FakeResponse([{"fact": "likes mystery novels"}])
            if request.method == "GET" and path == "bridge_smart_groups":
                return FakeResponse([{"group_id": "999", "enabled": True}])
            return FakeResponse([{"id": 1}])

        self.client = SupabaseRestClient(
            "https://project.supabase.co",
            "sb_secret_test",
            opener=opener,
        )
        self.store = RemoteMemoryStore(self.client, bot_qq="100")

    def test_ingest_is_idempotent_request_and_uses_server_key(self):
        message = NormalizedMessage(
            event_id="e1",
            timestamp=1,
            conversation_type="private",
            conversation_id="42",
            sender_id="42",
            sender_name="friend",
            message_id="8",
            text="hello",
        )
        self.assertTrue(self.store.ingest(message))
        request, _ = self.requests[-1]
        self.assertEqual(request.get_header("Apikey"), "sb_secret_test")
        self.assertIn("on_conflict=bot_qq%2Cconversation_key%2Csource_message_id", request.full_url)

    def test_load_context_returns_summary_facts_and_normalized_messages(self):
        context = self.store.load_context("private:42", 20)
        self.assertEqual(context.summary, "A short summary")
        self.assertEqual(context.facts, ("likes mystery novels",))
        self.assertEqual(context.messages[0].message_id, "8")

    def test_smart_groups_are_numeric_and_facts_are_scoped(self):
        self.assertEqual(self.store.smart_groups(), frozenset({"999"}))
        self.store.add_fact("user:42", "likes mystery novels", "8")
        self.assertTrue(self.store.remove_fact("user:42", "likes mystery novels"))

    def test_coordinated_lease_can_be_claimed_and_released(self):
        self.assertTrue(self.store.claim_conversation("private:42", "worker-a", 90))
        self.store.release_conversation("private:42", "worker-a")
        paths = [request.full_url for request, _timeout in self.requests]
        self.assertTrue(any("bridge_leases" in path for path in paths))


if __name__ == "__main__":
    unittest.main()
