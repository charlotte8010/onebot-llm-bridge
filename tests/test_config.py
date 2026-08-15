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

    def test_bot_identity_and_followup_settings_are_loaded(self) -> None:
        settings = Settings.from_values(
            {"BOT_QQ": "123", "BOT_NAMES": "御茗, ymm", "FOLLOWUP_SECONDS": "30", "TYPING_STATUS": "off"}
        )
        self.assertEqual(settings.bot_qq, "123")
        self.assertEqual(settings.bot_names, ("御茗", "ymm"))
        self.assertEqual(settings.followup_seconds, 30.0)
        self.assertFalse(settings.typing_status)

    def test_invalid_group_allowlist_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            Settings.from_values({"GROUP_ALLOWLIST": "friends"})

    def test_bot_validation_requires_model_settings(self) -> None:
        with self.assertRaises(ConfigError):
            Settings.from_values({}).validate_for_bot()

    def test_separate_vision_settings_are_loaded_and_validated(self) -> None:
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "chat-key",
                "LLM_BASE_URL": "https://chat.example/v1",
                "LLM_MODEL": "text-model",
                "VISION_MODE": "separate",
                "VISION_API_KEY": "vision-key",
                "VISION_BASE_URL": "https://vision.example/v1/",
                "VISION_MODEL": "vision-model",
            }
        )
        settings.validate_for_bot()
        self.assertEqual(settings.vision_base_url, "https://vision.example/v1")
        self.assertEqual(settings.vision_model, "vision-model")

    def test_invalid_vision_mode_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            Settings.from_values({"VISION_MODE": "maybe"})

    def test_vision_provider_can_reuse_main_api_credentials(self) -> None:
        settings = Settings.from_values(
            {
                "LLM_API_KEY": "shared-key",
                "LLM_BASE_URL": "https://shared.example/v1/",
                "LLM_MODEL": "text-model",
                "VISION_MODE": "separate",
                "VISION_MODEL": "vision-model",
            }
        )
        settings.validate_for_bot()
        self.assertEqual(settings.vision_api_key, "shared-key")
        self.assertEqual(settings.vision_base_url, "https://shared.example/v1")

    def test_memory_database_is_optional(self) -> None:
        settings = Settings.from_values({"MEMORY_DB": "./.local/context.sqlite3"})
        self.assertEqual(settings.memory_db, "./.local/context.sqlite3")

    def test_reactions_and_active_messages_are_configurable(self) -> None:
        settings = Settings.from_values(
            {
                "REACTION_MODE": "like",
                "ACTIVE_ENABLED": "true",
                "ACTIVE_INTERVAL_MINUTES": "15",
                "ACTIVE_TARGET_TYPE": "group",
                "ACTIVE_TARGET_ID": "999",
                "ACTIVE_PROMPT": "发一条近况",
            }
        )
        settings.validate_for_bridge()
        self.assertEqual(settings.reaction_mode, "like")
        self.assertTrue(settings.active_enabled)
        self.assertEqual(settings.active_interval_minutes, 15.0)

    def test_private_and_group_active_messages_are_independent(self) -> None:
        settings = Settings.from_values(
            {
                "ACTIVE_INTERVAL_MINUTES": "15",
                "ACTIVE_PRIVATE_ENABLED": "true",
                "ACTIVE_PRIVATE_TARGET_ID": "100",
                "ACTIVE_PRIVATE_PROMPT": "私聊近况",
                "ACTIVE_GROUP_ENABLED": "true",
                "ACTIVE_GROUP_TARGET_ID": "999",
                "ACTIVE_GROUP_PROMPT": "群聊近况",
            }
        )
        settings.validate_for_bridge()
        self.assertTrue(settings.active_private_enabled)
        self.assertTrue(settings.active_group_enabled)
        self.assertEqual(settings.active_private_target_id, "100")
        self.assertEqual(settings.active_group_target_id, "999")

    def test_active_messages_require_target_and_prompt(self) -> None:
        settings = Settings.from_values({"ACTIVE_ENABLED": "true"})
        with self.assertRaises(ConfigError):
            settings.validate_for_bridge()

    def test_active_target_must_be_numeric(self) -> None:
        settings = Settings.from_values(
            {
                "ACTIVE_ENABLED": "true",
                "ACTIVE_TARGET_ID": "not-a-qq-id",
                "ACTIVE_PROMPT": "say something",
            }
        )
        with self.assertRaises(ConfigError):
            settings.validate_for_bridge()

    def test_tool_allowlist_is_normalized(self) -> None:
        settings = Settings.from_values({"TOOL_ALLOWLIST": " get_time, Safe "})
        self.assertEqual(settings.tool_allowlist, ("get_time", "safe"))

    def test_remote_memory_and_summary_settings_are_loaded(self) -> None:
        settings = Settings.from_values(
            {
                "BOT_QQ": "100",
                "SUPABASE_URL": "https://project.supabase.co",
                "SUPABASE_SECRET_KEY": "sb_secret_test",
                "REMOTE_MEMORY_MODE": "local_first",
                "SUMMARY_ENABLED": "true",
                "SUMMARY_MIN_MESSAGES": "50",
                "SUMMARY_DELAY_SECONDS": "5",
            }
        )
        settings.validate_for_bridge()
        self.assertEqual(settings.supabase_url, "https://project.supabase.co")
        self.assertTrue(settings.summary_enabled)
        self.assertEqual(settings.summary_min_messages, 50)

    def test_remote_memory_requires_paired_credentials_and_bot_id(self) -> None:
        with self.assertRaises(ConfigError):
            Settings.from_values({"SUPABASE_URL": "https://project.supabase.co"}).validate_for_bridge()
        settings = Settings.from_values(
            {
                "SUPABASE_URL": "https://project.supabase.co",
                "SUPABASE_SECRET_KEY": "sb_secret_test",
            }
        )
        with self.assertRaises(ConfigError):
            settings.validate_for_bridge()
