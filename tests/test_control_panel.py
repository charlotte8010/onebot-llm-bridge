import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from control_panel import HELP_SECTIONS, HELP_TEXTS, OPTION_LABELS, ControlPanel, build_napcat_command, build_napcat_nt_command, build_napcat_utf8_console_command, discover_qq_executable, generate_service_token, local_url_port, load_env_file, load_theme, parse_port, parse_model_ids, probe_models, save_env_file, save_theme, vision_status


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

    def test_napcat_launcher_can_find_qq_itself(self):
        self.assertEqual(
            build_napcat_command("E:/Napcat/NapCat.Shell/launcher.bat"),
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", 'call "E:\\Napcat\\NapCat.Shell\\launcher.bat"'],
        )
        self.assertEqual(
            build_napcat_command("E:/Napcat/NapCat.Shell/launcher.bat", "ignored-qq", "ignored-hook"),
            [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", 'call "E:\\Napcat\\NapCat.Shell\\launcher.bat"'],
        )

    def test_napcat_boot_exe_keeps_explicit_qq_and_hook(self):
        self.assertEqual(
            build_napcat_command("E:/Napcat/NapCat.Shell/NapCatWinBootMain.exe", "E:/QQNT/QQ.exe", "E:/Napcat/NapCat.Shell/NapCatWinBootHook.dll"),
            ["E:\\Napcat\\NapCat.Shell\\NapCatWinBootMain.exe", "E:/QQNT/QQ.exe", "E:/Napcat/NapCat.Shell/NapCatWinBootHook.dll"],
        )

    def test_launcher_can_be_resolved_to_direct_qqnt_boot(self):
        self.assertEqual(
            build_napcat_nt_command(
                "E:/Napcat/NapCat.Shell/launcher.bat",
                "E:/QQNT/QQ.exe",
                "E:/Napcat/NapCat.Shell/NapCatWinBootHook.dll",
            ),
            [
                "E:\\Napcat\\NapCat.Shell\\NapCatWinBootMain.exe",
                "E:/QQNT/QQ.exe",
                "E:/Napcat/NapCat.Shell/NapCatWinBootHook.dll",
            ],
        )

    def test_napcat_console_command_is_left_unchanged_off_windows(self):
        command = ["NapCatWinBootMain.exe", "QQ.exe", "NapCatWinBootHook.dll"]
        with patch("control_panel.os.name", "posix"):
            self.assertEqual(build_napcat_utf8_console_command(command), command)

    def test_napcat_qq_discovery_uses_install_location_on_current_machine(self):
        if not Path("E:/QQNT/QQ.exe").is_file():
            self.skipTest("QQNT is not installed at the local test path")
        self.assertEqual(discover_qq_executable("E:/Napcat/NapCat.Shell/launcher.bat"), Path("E:/QQNT/QQ.exe"))

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

    def test_combo_labels_are_localized_without_changing_runtime_values(self):
        self.assertEqual(OPTION_LABELS["DECISION_MODE"]["heuristic"], "本地规则")
        self.assertEqual(OPTION_LABELS["VISION_MODE"]["separate"], "单独视觉模型")
        self.assertEqual(OPTION_LABELS["GROUP_MODE"]["mention"], "叫到才回")

    def test_vision_status_explains_direct_mode_as_main_model(self):
        self.assertEqual(vision_status("direct", ""), ("识图 · 交给主模型", True))
        self.assertEqual(vision_status("separate", ""), ("识图 · 单独视觉模型 / 未配置", False))
        self.assertEqual(vision_status("separate", "vision-model"), ("识图 · 单独视觉模型 / vision-model", True))

    def test_help_sections_cover_first_run_and_supabase(self):
        titles = {title for title, _content in HELP_SECTIONS}
        self.assertIn("第一次启动", titles)
        self.assertIn("配置 Supabase", titles)

    def test_generic_group_help_does_not_hardcode_persona_name(self):
        content = dict(HELP_SECTIONS)["群聊与消息形态"]
        self.assertNotIn("御茗", content)
        self.assertNotIn("975426289", content)

    def test_token_help_explains_where_each_token_belongs(self):
        self.assertIn("NapCat WebUI", HELP_TEXTS["NAPCAT_EVENT_TOKEN"])
        self.assertIn("不在 NapCat", HELP_TEXTS["BOT_SERVICE_TOKEN"])

    def test_allowlist_help_distinguishes_group_list_and_active_target(self):
        self.assertIn("半角逗号", HELP_TEXTS["GROUP_ALLOWLIST"])
        self.assertIn("只填写数字", HELP_TEXTS["ACTIVE_PRIVATE_TARGET_ID"])

    def test_generated_service_token_is_url_safe_and_nontrivial(self):
        first = generate_service_token()
        second = generate_service_token()
        self.assertGreaterEqual(len(first), 40)
        self.assertNotEqual(first, second)

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
