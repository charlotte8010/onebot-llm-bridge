import json
import unittest
from urllib.error import URLError

from onebot_llm_bridge.napcat import NapCatClient
from onebot_llm_bridge.providers import OpenAICompatibleProvider


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.payload


class ClientTests(unittest.TestCase):
    def test_provider_retries_transient_network_failure(self) -> None:
        attempts = []

        def opener(request, timeout):
            attempts.append(1)
            if len(attempts) == 1:
                raise URLError("temporary")
            return FakeResponse({"choices": [{"message": {"content": "ok"}}]})

        provider = OpenAICompatibleProvider(
            api_key="secret",
            base_url="https://example.test/v1",
            model="chat",
            max_retries=1,
            opener=opener,
        )
        self.assertEqual(provider.complete([{"role": "user", "content": "hi"}]), "ok")
        self.assertEqual(len(attempts), 2)

    def test_provider_sends_bearer_and_reads_content(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append((request, timeout))
            return FakeResponse({"choices": [{"message": {"content": "你好"}}]})

        provider = OpenAICompatibleProvider(
            api_key="secret",
            base_url="https://example.test/v1/",
            model="chat",
            opener=opener,
        )
        self.assertEqual(provider.complete([{"role": "user", "content": "hi"}]), "你好")
        self.assertEqual(requests[0][0].get_header("Authorization"), "Bearer secret")
        self.assertIn("/chat/completions", requests[0][0].full_url)

    def test_provider_encodes_images_as_openai_compatible_content(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse({"choices": [{"message": {"content": "看到了"}}]})

        provider = OpenAICompatibleProvider(
            api_key="secret",
            base_url="https://example.test/v1",
            model="vision-chat",
            opener=opener,
        )
        provider.complete(
            [{"role": "user", "content": "这是什么"}],
            images=["data:image/png;base64,abc"],
        )
        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(payload["messages"][0]["content"][1]["type"], "image_url")
        self.assertEqual(
            payload["messages"][0]["content"][1]["image_url"]["url"],
            "data:image/png;base64,abc",
        )

    def test_provider_lists_model_ids(self) -> None:
        def opener(request, timeout):
            self.assertTrue(request.full_url.endswith("/models"))
            self.assertEqual(request.get_header("Authorization"), "Bearer secret")
            return FakeResponse({"data": [{"id": "z-model"}, {"id": "a-model"}, {"id": "z-model"}]})

        provider = OpenAICompatibleProvider(
            api_key="secret",
            base_url="https://example.test/v1",
            model="chat",
            opener=opener,
        )
        self.assertEqual(provider.list_models(), ["a-model", "z-model"])

    def test_napcat_client_sends_access_token_and_action(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse({"status": "ok", "retcode": 0, "data": {}})

        client = NapCatClient("http://127.0.0.1:3000", "server-token", opener=opener)
        client.send_private("123", "hello")
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer server-token")
        self.assertTrue(requests[0].full_url.endswith("/send_private_msg"))

    def test_napcat_client_builds_quote_message_segments(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse({"status": "ok", "retcode": 0, "data": {}})

        client = NapCatClient("http://127.0.0.1:3000", opener=opener)
        client.send_group("456", "reply", reply_to="789")
        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(payload["message"][0], {"type": "reply", "data": {"id": "789"}})

    def test_napcat_client_can_send_emoji_like(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse({"status": "ok", "retcode": 0, "data": {}})

        client = NapCatClient("http://127.0.0.1:3000", opener=opener)
        client.set_msg_emoji_like("789", "128077")
        payload = json.loads(requests[0].data.decode("utf-8"))
        self.assertEqual(payload["message_id"], "789")
        self.assertEqual(payload["emoji_id"], "128077")
