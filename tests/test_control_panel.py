import tempfile
import unittest
from pathlib import Path

from control_panel import load_env_file, parse_model_ids, save_env_file


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


if __name__ == "__main__":
    unittest.main()
