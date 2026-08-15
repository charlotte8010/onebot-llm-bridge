import unittest
from unittest.mock import patch

from onebot_llm_bridge.config import ConfigError, Settings, parse_env_file


class ConfigTests(unittest.TestCase):
    def test_parse_env_supports_comments_export_and_quotes(self) -> None:
        values = parse_env_file('''\n# comment\nexport LLM_MODEL="chat-model"\nGROUP_ALLOWLIST=123,456\n''')
        self.assertEqual(values["LLM_MODEL"], "chat-model")
        self.assertEqual(values["GROUP_ALLOWLIST"], "123,456")

    def test_settings_parse_ports_and_allowlist(self) -> None:
        settings = Settings.from_values(
            {
                "LLM_BASE_URL": "https://example.test/v1/",
                "GROUP_ALLOWLIST": "123, 456",
                "BRIDGE_PORT": "8876",
            }
        )
        self.assertEqual(settings.llm_base_url, "https://example.test/v1")
        self.assertEqual(settings.group_allowlist, frozenset({"123", "456"}))
        self.assertEqual(settings.bridge_port, 8876)

    def test_debounce_seconds_is_configurable(self) -> None:
        settings = Settings.from_values({"DEBOUNCE_SECONDS": "4.5"})
        self.assertEqual(settings.debounce_seconds, 4.5)

    def test_random_debounce_uses_only_three_to_six_seconds(self) -> None:
        settings = Settings.from_values({"DEBOUNCE_SECONDS": "random"})
        self.assertTrue(settings.debounce_random)
        with patch("onebot_llm_bridge.config.random.choice", return_value=5.0) as choice:
            self.assertEqual(settings.debounce_delay(), 5.0)
        choice.assert_called_once_with((3.0, 4.0, 5.0, 6.0))

    def test_invalid_group_allowlist_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            Settings.from_values({"GROUP_ALLOWLIST": "friends"})

    def test_bot_validation_requires_model_settings(self) -> None:
        with self.assertRaises(ConfigError):
            Settings.from_values({}).validate_for_bot()
