import json
import unittest

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

    def test_napcat_client_sends_access_token_and_action(self) -> None:
        requests = []

        def opener(request, timeout):
            requests.append(request)
            return FakeResponse({"status": "ok", "retcode": 0, "data": {}})

        client = NapCatClient("http://127.0.0.1:3000", "server-token", opener=opener)
        client.send_private("123", "hello")
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer server-token")
        self.assertTrue(requests[0].full_url.endswith("/send_private_msg"))
