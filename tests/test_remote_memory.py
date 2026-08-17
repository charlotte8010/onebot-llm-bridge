import json
import unittest
from io import BytesIO
from urllib.error import HTTPError

from onebot_llm_bridge.models import NormalizedMessage
from onebot_llm_bridge.remote_memory import RemoteMemoryError, RemoteMemoryStore, SupabaseRestClient


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class LeaseErrorClient:
    def __init__(self, status):
        self.status = status

    def request(self, method, resource, **_kwargs):
        if method == "POST" and resource == "bridge_leases":
            raise RemoteMemoryError(
                "http_error",
                f"remote memory returned HTTP {self.status}",
                status=self.status,
            )
        return None


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

    def test_ingest_uses_event_id_when_message_id_is_missing(self):
        message = NormalizedMessage(
            event_id="outbound:event-1",
            timestamp=1,
            conversation_type="private",
            conversation_id="42",
            sender_id="100",
            sender_name="Bot",
            message_id="",
            text="hello",
            is_self=True,
        )
        self.assertTrue(self.store.ingest(message))
        request, _ = self.requests[-1]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["source_message_id"], "outbound:event-1")

    def test_load_context_returns_summary_facts_and_normalized_messages(self):
        context = self.store.load_context("private:42", 20)
        self.assertEqual(context.summary, "A short summary")
        self.assertEqual(context.facts, ("likes mystery novels",))
        self.assertEqual(context.messages[0].message_id, "8")
        message_request = next(request for request, _ in self.requests if "bridge_messages" in request.full_url)
        self.assertIn("order=occurred_at.desc%2Cid.desc", message_request.full_url)

    def test_smart_groups_are_numeric_and_facts_are_scoped(self):
        self.assertEqual(self.store.smart_groups(), frozenset({"999"}))
        self.store.add_fact("user:42", "likes mystery novels", "8")
        self.assertTrue(self.store.remove_fact("user:42", "likes mystery novels"))

    def test_coordinated_lease_can_be_claimed_and_released(self):
        self.assertTrue(self.store.claim_conversation("private:42", "worker-a", 90))
        self.store.release_conversation("private:42", "worker-a")
        paths = [request.full_url for request, _timeout in self.requests]
        self.assertTrue(any("bridge_leases" in path for path in paths))

    def test_http_error_preserves_status_for_callers(self):
        def forbidden(request, timeout):
            raise HTTPError(
                request.full_url,
                403,
                "Forbidden",
                {},
                BytesIO(b'{"message":"permission denied"}'),
            )

        client = SupabaseRestClient(
            "https://project.supabase.co",
            "sb_secret_test",
            opener=forbidden,
        )

        with self.assertRaises(RemoteMemoryError) as raised:
            client.request("POST", "bridge_leases", payload={"conversation_key": "private:42"})

        self.assertEqual(raised.exception.code, "forbidden")
        self.assertEqual(getattr(raised.exception, "status", None), 403)

    def test_lease_conflict_means_another_worker_claimed_conversation(self):
        store = RemoteMemoryStore(LeaseErrorClient(409), bot_qq="100")

        self.assertFalse(store.claim_conversation("private:42", "worker-a", 90))

    def test_lease_permission_error_is_not_misreported_as_busy(self):
        store = RemoteMemoryStore(LeaseErrorClient(403), bot_qq="100")

        with self.assertRaises(RemoteMemoryError) as raised:
            store.claim_conversation("private:42", "worker-a", 90)

        self.assertEqual(raised.exception.status, 403)


if __name__ == "__main__":
    unittest.main()
