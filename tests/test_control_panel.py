import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_panel import ControlPanel, local_url_port, load_env_file, load_theme, parse_port, parse_model_ids, probe_models, save_env_file, save_theme


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

    def test_local_url_port_only_probes_local_napcat_addresses(self):
        self.assertEqual(local_url_port("http://127.0.0.1:3000", 3000), 3000)
        self.assertEqual(local_url_port("http://localhost/api", 3000), 3000)
        self.assertIsNone(local_url_port("https://remote.example/api", 443))
        self.assertIsNone(local_url_port("not a url", 3000))

    def test_theme_defaults_to_morandi_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "theme.json"
            self.assertEqual(load_theme(path), "morandi")
            save_theme(path, "dark")
            self.assertEqual(load_theme(path), "dark")

    def test_theme_palettes_use_requested_base_colors(self):
        self.assertEqual(
            [ControlPanel.THEMES["morandi"][key] for key in ("surface_alt", "surface", "background", "border", "input")],
            ["#E8E2DA", "#EDEBE3", "#F3F4EE", "#E4E7EE", "#EFEFF4"],
        )
        self.assertEqual(
            [ControlPanel.THEMES["dark"][key] for key in ("background", "surface", "surface_alt", "input", "border")],
            ["#2D2D39", "#35343D", "#3D3B40", "#434343", "#4A4A4A"],
        )

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
