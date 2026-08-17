import inspect
import os
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import control_panel
from control_panel import HELP_SECTIONS, HELP_TEXTS, OPTION_LABELS, ControlPanel, build_napcat_command, build_napcat_nt_command, build_napcat_utf8_console_command, discover_qq_executable, format_panel_error, generate_service_token, git_output_tail, is_local_service_host, local_url_port, load_env_file, load_theme, parse_git_ahead_behind, parse_port, parse_model_ids, parse_release_version, parse_update_manifest, probe_models, save_env_file, save_theme, service_base_url, vision_status


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

    def test_git_ahead_behind_parser_and_output_tail(self):
        self.assertEqual(parse_git_ahead_behind("2\t5\n"), (2, 5))
        self.assertEqual(git_output_tail("one\ntwo\nthree\nfour\n", limit=2), "three；four")
        with self.assertRaises(ValueError):
            parse_git_ahead_behind("not git output")

    def test_release_manifest_validates_version_and_update_type(self):
        manifest = parse_update_manifest({
            "version": "0.2.0",
            "update_type": "hot",
            "target_ref": "v0.2.0",
            "min_version": "0.1.0",
            "message": "服务重启优化",
            "changelog": ["服务重启优化", "补充更新说明"],
        })
        self.assertEqual(manifest["target_ref"], "v0.2.0")
        self.assertEqual(manifest["changelog"], "• 服务重启优化\n• 补充更新说明")
        self.assertEqual(parse_release_version("v1.2.3"), (1, 2, 3))
        with self.assertRaises(ValueError):
            parse_update_manifest({"version": "0.2", "update_type": "force", "target_ref": "v0.2", "min_version": "0.1.0", "message": ""})

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

    def test_bot_host_distinguishes_local_and_remote_modes(self):
        self.assertTrue(is_local_service_host("127.0.0.1"))
        self.assertTrue(is_local_service_host("[::1]"))
        self.assertTrue(is_local_service_host("localhost"))
        self.assertFalse(is_local_service_host("100.64.0.12"))
        self.assertFalse(is_local_service_host("bot.example.com"))
        self.assertEqual(service_base_url("100.64.0.12", 8765), "http://100.64.0.12:8765")
        self.assertEqual(service_base_url("2001:db8::1", 8765), "http://[2001:db8::1]:8765")

    def test_bot_host_rejects_url_and_path_instead_of_creating_bad_endpoint(self):
        with self.assertRaises(ValueError):
            service_base_url("https://bot.example.com", 8765)
        with self.assertRaises(ValueError):
            service_base_url("bot.example.com/reply", 8765)

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
        self.assertIn("控制台小功能", titles)
        self.assertIn("腾讯云 / 云端 Bot", titles)

    def test_help_explains_persona_editor_and_save_restart_flow(self):
        content = dict(HELP_SECTIONS)["Persona、记忆与词典"]
        self.assertIn("编辑 Persona", content)
        self.assertIn("保存只修改本地文件，不会自动重启服务", content)

    def test_generic_group_help_does_not_hardcode_persona_name(self):
        content = dict(HELP_SECTIONS)["群聊与消息形态"]
        self.assertNotIn("御茗", content)
        self.assertNotIn("975426289", content)

    def test_token_help_explains_where_each_token_belongs(self):
        self.assertIn("NapCat WebUI", HELP_TEXTS["NAPCAT_EVENT_TOKEN"])
        self.assertIn("不在 NapCat", HELP_TEXTS["BOT_SERVICE_TOKEN"])

    def test_remote_bot_help_explains_private_or_tailscale_address(self):
        self.assertIn("Tailscale", HELP_TEXTS["BOT_SERVICE_HOST"])
        self.assertIn("不要填 http://", HELP_TEXTS["BOT_SERVICE_HOST"])

    def test_cloud_bot_help_explains_local_and_full_cloud_deployment(self):
        content = dict(HELP_SECTIONS)["腾讯云 / 云端 Bot"]
        self.assertIn("不会替你创建腾讯云服务器", content)
        self.assertIn("完整搬到云端", content)
        self.assertIn("跳过本地 Bot", content)

    def test_allowlist_help_distinguishes_group_list_and_active_target(self):
        self.assertIn("半角逗号", HELP_TEXTS["GROUP_ALLOWLIST"])
        self.assertIn("只填写数字", HELP_TEXTS["ACTIVE_PRIVATE_TARGET_ID"])

    def test_generated_service_token_is_url_safe_and_nontrivial(self):
        first = generate_service_token()
        second = generate_service_token()
        self.assertGreaterEqual(len(first), 40)
        self.assertNotEqual(first, second)

    def test_panel_errors_include_reason_and_next_step(self):
        message = format_panel_error("模型检测", "HTTP 403")
        self.assertIn("原因：HTTP 403", message)
        self.assertIn("建议：", message)
        self.assertIn("API Key 权限", message)

    def test_panel_errors_explain_local_connection_failures(self):
        message = format_panel_error("诊断 · Bridge", "[WinError 10061] connection refused")
        self.assertIn("目标端口没有服务在监听", message)

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

    def test_bridge_shutdown_request_posts_event_token_to_local_service(self):
        shutdown = getattr(control_panel, "request_bridge_shutdown", None)
        self.assertIsNotNone(shutdown)

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return b'{"ok":true,"stopping":true}'

        with patch("control_panel.urlopen", return_value=Response()) as opened:
            payload = shutdown(8766, "event-token", timeout=2.0)

        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8766/shutdown")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer event-token")
        self.assertTrue(payload["stopping"])

    def test_service_process_waits_for_graceful_stop_before_terminating(self):
        parameters = inspect.signature(control_panel.ServiceProcess.stop).parameters
        self.assertIn("graceful_request", parameters)

        class FakeProcess:
            def __init__(self):
                self.exited = False
                self.terminate_calls = 0
                self.kill_calls = 0
                self.wait_timeouts = []

            def poll(self):
                return 0 if self.exited else None

            def wait(self, timeout):
                self.wait_timeouts.append(timeout)
                if not self.exited:
                    raise AssertionError("graceful request did not stop the process")
                return 0

            def terminate(self):
                self.terminate_calls += 1

            def kill(self):
                self.kill_calls += 1

        process = FakeProcess()
        service = control_panel.ServiceProcess("bridge", Path("app.py"), queue.Queue())
        service.process = process

        def graceful_request():
            process.exited = True

        service.stop(graceful_request=graceful_request, graceful_timeout=12.0)

        self.assertEqual(process.wait_timeouts, [12.0])
        self.assertEqual(process.terminate_calls, 0)
        self.assertEqual(process.kill_calls, 0)

    def test_stop_all_gracefully_stops_bridge_before_bot_service(self):
        events = []

        class Variable:
            def __init__(self, value):
                self.value = value

            def get(self):
                return self.value

        class FakeService:
            def __init__(self, name):
                self.name = name

            def stop(self, **kwargs):
                events.append((self.name, "stop"))
                graceful_request = kwargs.get("graceful_request")
                if graceful_request is not None:
                    graceful_request()

        panel = ControlPanel.__new__(ControlPanel)
        panel.bridge = FakeService("bridge")
        panel.bot = FakeService("bot")
        panel.bridge_port = Variable("8766")
        panel.event_token = Variable("event-token")
        panel.timeout = Variable("60")
        panel._append_log = lambda message: events.append(("log", message))

        with patch(
            "control_panel.request_bridge_shutdown",
            side_effect=lambda port, token: events.append(("request", port, token)) or {"ok": True},
        ):
            panel.stop_all()

        self.assertEqual(events[0], ("bridge", "stop"))
        self.assertEqual(events[1], ("request", 8766, "event-token"))
        self.assertEqual(events[2], ("bot", "stop"))


if __name__ == "__main__":
    unittest.main()
