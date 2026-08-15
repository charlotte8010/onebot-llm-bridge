import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_panel import load_env_file, parse_port, parse_model_ids, probe_models, save_env_file


class ControlPanelHelperTests(unittest.TestCase):
    def test_env_round_trip_preserves_quoted_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env.local"
            values = {"LLM_BASE_URL": "https://example.test/v1", "TOKEN": 'a\\b"c'}
            save_env_file(path, values)
            self.assertEqual(load_env_file(path), values)

    def test_model_ids_are_sorted_and_deduplicated(self):
        self.assertEqual(
            parse_model_ids({"data": [{"id": "z"}, {"id": "a"}, {"id": "z"}]}),
            ["a", "z"],
        )
        self.assertEqual(parse_model_ids({"data": []}), [])
        self.assertEqual(parse_model_ids({"models": []}), [])

    def test_ports_are_validated_without_touching_tk(self):
        self.assertEqual(parse_port("", 8765), 8765)
        self.assertEqual(parse_port("3000", 8765), 3000)
        self.assertIsNone(parse_port("0", 8765))
        self.assertIsNone(parse_port("65536", 8765))
        self.assertIsNone(parse_port("oops", 8765))

    def test_probe_models_uses_bearer_key_and_counts_models(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"data":[{"id":"chat-a"},{"id":"chat-b"}]}'

        with patch("control_panel.urlopen", return_value=Response()) as opened:
            self.assertEqual(probe_models("https://example.test/v1", "secret"), 2)
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/v1/models")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")


if __name__ == "__main__":
    unittest.main()
