from __future__ import annotations

import json
import os
import queue
import socket
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env.local"
PRESETS_FILE = ROOT / ".model_presets.json"
BOT_SCRIPT = ROOT / "bot_service.py"
BRIDGE_SCRIPT = ROOT / "app.py"
DEFAULT_NAPCAT_API_URL = "http://127.0.0.1:3000"


def load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        value = value.replace('\\"', '"').replace('\\\\', '\\')
        values[key.strip()] = value
    return values


def save_env_file(path: Path, values: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f'{key}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"' for key, value in values.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_presets(path: Path) -> dict[str, dict[str, str]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return {
        str(name): {str(key): str(value) for key, value in data.items()}
        for name, data in payload.items()
        if isinstance(data, dict)
    }


def save_presets(path: Path, presets: dict[str, dict[str, str]]) -> None:
    path.write_text(json.dumps(presets, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_model_ids(payload: object) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    return sorted({
        str(item.get("id")).strip()
        for item in payload["data"]
        if isinstance(item, dict) and str(item.get("id", "")).strip()
    })


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.4):
            return True
    except OSError:
        return False


class ServiceProcess:
    def __init__(self, name: str, script: Path, log: queue.Queue[str]) -> None:
        self.name = name
        self.script = script
        self.log = log
        self.process: subprocess.Popen[str] | None = None

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(self, env: dict[str, str]) -> None:
        if self.running():
            return
        self.process = subprocess.Popen(
            [sys.executable, str(self.script)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        threading.Thread(target=self._read_output, daemon=True).start()

    def _read_output(self) -> None:
        process = self.process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            self.log.put(f"[{self.name}] {line.rstrip()}")
        self.log.put(f"[{self.name}] exited with code {process.poll()}")

    def stop(self) -> None:
        if not self.running():
            return
        assert self.process is not None
        self.process.terminate()
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.kill()


class ControlPanel(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("OneBot LLM Bridge 控制台")
        self.geometry("1180x860")
        self.minsize(900, 650)
        self.values = load_env_file(ENV_FILE)
        self.presets = load_presets(PRESETS_FILE)
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.bot = ServiceProcess("bot", BOT_SCRIPT, self.log_queue)
        self.bridge = ServiceProcess("bridge", BRIDGE_SCRIPT, self.log_queue)
        self.napcat_process: subprocess.Popen[bytes] | None = None
        self._build_ui()
        self.after(200, self._drain_logs)
        self.after(1000, self._refresh_status)
        self.protocol("WM_DELETE_WINDOW", self._close)

    def _value(self, key: str, default: str = "") -> str:
        return self.values.get(key, os.environ.get(key, default)).strip()

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Subtitle.TLabel", foreground="#666666")
        style.configure("Status.TLabel", padding=(8, 7))
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="OneBot LLM Bridge 控制台", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="保存配置只写入 .env.local，不会重启服务；需要应用时请手动点击“重启全部”。",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 10))

        canvas = tk.Canvas(outer, highlightthickness=0, height=510)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.settings = ttk.Frame(canvas)
        window = canvas.create_window((0, 0), window=self.settings, anchor="nw")
        self.settings.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))

        model = ttk.LabelFrame(self.settings, text="模型连接", padding=10)
        model.pack(fill="x")
        model.columnconfigure(1, weight=1)
        self.preset = tk.StringVar()
        self._label(model, 0, 0, "模型预设")
        self.preset_box = ttk.Combobox(model, textvariable=self.preset, values=[*sorted(self.presets), "+"], state="readonly")
        self.preset_box.grid(row=0, column=1, sticky="ew", pady=4)
        self.preset_box.bind("<<ComboboxSelected>>", self._preset_selected)
        preset_bar = ttk.Frame(model)
        preset_bar.grid(row=0, column=2, padx=(10, 0))
        ttk.Button(preset_bar, text="保存为新预设", command=self.save_preset).pack(side="left", padx=3)
        ttk.Button(preset_bar, text="重命名", command=self.rename_preset).pack(side="left", padx=3)
        ttk.Button(preset_bar, text="删除", command=self.delete_preset).pack(side="left", padx=3)
        self.api_key = self._entry(model, 1, "API Key", "LLM_API_KEY", secret=True)
        self.base_url = self._entry(model, 2, "Base URL", "LLM_BASE_URL")
        self.model = self._model_entry(model, 3, "模型", "LLM_MODEL", self.detect_chat_models)
        self.max_tokens = self._entry(model, 4, "输出预算", "LLM_MAX_TOKENS", "1024")
        self.timeout = self._entry(model, 5, "超时秒数", "LLM_TIMEOUT_SECONDS", "60")

        vision = ttk.LabelFrame(self.settings, text="图片识图", padding=10)
        vision.pack(fill="x", pady=(10, 0))
        vision.columnconfigure(1, weight=1)
        self.vision_mode = tk.StringVar(value=self._value("VISION_MODE", "off"))
        self._label(vision, 0, 0, "识图模式")
        ttk.Combobox(vision, textvariable=self.vision_mode, values=("off", "direct", "separate"), state="readonly").grid(row=0, column=1, sticky="w", pady=4)
        self.vision_api_key = self._entry(vision, 1, "视觉 API Key", "VISION_API_KEY", secret=True)
        self.vision_base_url = self._entry(vision, 2, "视觉 Base URL", "VISION_BASE_URL")
        self.vision_model = self._model_entry(vision, 3, "视觉模型", "VISION_MODEL", self.detect_vision_models)
        self.vision_max_tokens = self._entry(vision, 4, "视觉输出预算", "VISION_MAX_TOKENS", "512")
        self.vision_timeout = self._entry(vision, 5, "视觉超时秒数", "VISION_TIMEOUT_SECONDS", "30")
        ttk.Label(vision, text="separate 会先让视觉模型描述图片，再交给主聊天模型；视觉 Key 和地址留空时复用主模型。", style="Subtitle.TLabel").grid(row=6, column=0, columnspan=3, sticky="w", pady=(3, 0))

        behavior = ttk.LabelFrame(self.settings, text="回复与记忆", padding=10)
        behavior.pack(fill="x", pady=(10, 0))
        behavior.columnconfigure(1, weight=1)
        behavior.columnconfigure(3, weight=1)
        self.group_mode = self._combo(behavior, 0, 0, "群聊模式", "GROUP_MODE", ("mention", "smart", "all", "off"), "mention")
        self.group_allowlist = self._entry(behavior, 1, "群聊白名单", "GROUP_ALLOWLIST")
        self.bot_qq = self._entry(behavior, 2, "Bot QQ", "BOT_QQ")
        self.bot_names = self._entry(behavior, 3, "Bot 名称", "BOT_NAMES")
        self.debounce = self._combo(behavior, 4, 0, "防抖延迟", "DEBOUNCE_SECONDS", ("random", "3", "4", "5", "6"), "random")
        self.followup = self._entry(behavior, 4, "继续话题秒数", "FOLLOWUP_SECONDS", "120", column=2)
        self.context_messages = self._entry(behavior, 5, "上下文条数", "CONTEXT_MESSAGES", "20")
        self.memory_db = self._entry(behavior, 5, "持久化记忆库", "MEMORY_DB", "", column=2)
        self.typing = tk.BooleanVar(value=self._value("TYPING_STATUS", "true").lower() in {"1", "true", "yes", "on"})
        ttk.Checkbutton(behavior, text="显示输入状态", variable=self.typing).grid(row=6, column=0, columnspan=2, sticky="w", pady=4)
        self.persona = self._entry(behavior, 7, "Persona 文件", "PERSONA_FILE", "")

        network = ttk.LabelFrame(self.settings, text="服务与 Token", padding=10)
        network.pack(fill="x", pady=(10, 0))
        network.columnconfigure(1, weight=1)
        network.columnconfigure(3, weight=1)
        self.napcat_url = self._entry(network, 0, "NapCat API", "NAPCAT_API_URL", DEFAULT_NAPCAT_API_URL)
        self.napcat_access = self._entry(network, 1, "NapCat Access Token", "NAPCAT_ACCESS_TOKEN", secret=True)
        self.event_token = self._entry(network, 2, "事件上报 Token", "NAPCAT_EVENT_TOKEN", secret=True)
        self.service_token = self._entry(network, 3, "Bot 服务 Token", "BOT_SERVICE_TOKEN", secret=True)
        self.bridge_port = self._entry(network, 4, "Bridge 端口", "BRIDGE_PORT", "8766")
        self.bot_port = self._entry(network, 5, "Bot 端口", "BOT_SERVICE_PORT", "8765")
        self.napcat_boot = self._entry(network, 6, "NapCat 启动程序", "NAPCAT_BOOT")
        self.napcat_qq = self._entry(network, 7, "QQ 程序", "NAPCAT_QQ")
        self.napcat_hook = self._entry(network, 8, "NapCat Hook", "NAPCAT_HOOK")
        ttk.Button(network, text="选择", command=lambda: self._select_path(self.napcat_boot, "选择 NapCat 启动程序")).grid(row=6, column=2, padx=(8, 0), pady=4)
        ttk.Button(network, text="选择", command=lambda: self._select_path(self.napcat_qq, "选择 QQ 程序")).grid(row=7, column=2, padx=(8, 0), pady=4)
        ttk.Button(network, text="选择", command=lambda: self._select_path(self.napcat_hook, "选择 NapCat Hook")).grid(row=8, column=2, padx=(8, 0), pady=4)

        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=10)
        ttk.Button(actions, text="保存配置（不重启）", command=self.save_config).pack(side="left")
        ttk.Button(actions, text="启动全部", command=self.start_all).pack(side="left", padx=6)
        ttk.Button(actions, text="启动 NapCat", command=self.start_napcat).pack(side="left", padx=6)
        ttk.Button(actions, text="重启全部", command=self.restart_all).pack(side="left", padx=6)
        ttk.Button(actions, text="停止全部", command=self.stop_all).pack(side="left", padx=6)

        status = ttk.LabelFrame(outer, text="状态", padding=8)
        status.pack(fill="x")
        self.bot_status = self._status(status, 0, "Bot")
        self.bridge_status = self._status(status, 1, "Bridge")
        self.vision_status = self._status(status, 2, "识图")

        log_frame = ttk.LabelFrame(outer, text="实时日志", padding=8)
        log_frame.pack(fill="both", expand=True, pady=(10, 0))
        ttk.Button(log_frame, text="清空日志", command=self.clear_log).pack(anchor="e")
        self.log = tk.Text(log_frame, height=8, wrap="none", state="disabled", font=("Cascadia Mono", 10))
        self.log.pack(fill="both", expand=True)

    def _label(self, parent: ttk.Frame, row: int, column: int, text: str) -> None:
        ttk.Label(parent, text=text).grid(row=row, column=column, sticky="w", padx=(0, 8), pady=4)

    def _entry(self, parent: ttk.Frame, row: int, label: str, key: str, default: str = "", secret: bool = False, column: int = 0) -> tk.StringVar:
        self._label(parent, row, column, label)
        variable = tk.StringVar(value=self._value(key, default))
        ttk.Entry(parent, textvariable=variable, show="*" if secret else "").grid(row=row, column=column + 1, sticky="ew", padx=(0, 16) if column == 0 else (0, 0), pady=4)
        return variable

    def _model_entry(self, parent: ttk.Frame, row: int, label: str, key: str, detect: Callable[[], None]) -> tk.StringVar:
        self._label(parent, row, 0, label)
        variable = tk.StringVar(value=self._value(key))
        box = ttk.Combobox(parent, textvariable=variable, state="normal")
        box.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Button(parent, text="检测模型", command=detect).grid(row=row, column=2, padx=(8, 0), pady=4)
        if key == "LLM_MODEL":
            self.model_box = box
        else:
            self.vision_model_box = box
        return variable

    def _combo(self, parent: ttk.Frame, row: int, column: int, label: str, key: str, values: tuple[str, ...], default: str) -> tk.StringVar:
        self._label(parent, row, column, label)
        variable = tk.StringVar(value=self._value(key, default))
        ttk.Combobox(parent, textvariable=variable, values=values, state="readonly").grid(row=row, column=column + 1, sticky="ew", padx=(0, 16) if column == 0 else (0, 0), pady=4)
        return variable

    def _status(self, parent: ttk.Frame, column: int, label: str) -> ttk.Label:
        parent.columnconfigure(column, weight=1)
        widget = ttk.Label(parent, text=f"{label}: 检查中", style="Status.TLabel")
        widget.grid(row=0, column=column, sticky="ew", padx=(0, 8))
        return widget

    def _current_config(self) -> dict[str, str]:
        return {
            "LLM_API_KEY": self.api_key.get().strip(), "LLM_BASE_URL": self.base_url.get().strip(), "LLM_MODEL": self.model.get().strip(),
            "LLM_MAX_TOKENS": self.max_tokens.get().strip(), "LLM_TIMEOUT_SECONDS": self.timeout.get().strip(),
            "VISION_MODE": self.vision_mode.get().strip(), "VISION_API_KEY": self.vision_api_key.get().strip(), "VISION_BASE_URL": self.vision_base_url.get().strip(),
            "VISION_MODEL": self.vision_model.get().strip(), "VISION_MAX_TOKENS": self.vision_max_tokens.get().strip(), "VISION_TIMEOUT_SECONDS": self.vision_timeout.get().strip(),
            "GROUP_MODE": self.group_mode.get().strip(), "GROUP_ALLOWLIST": self.group_allowlist.get().strip(), "BOT_QQ": self.bot_qq.get().strip(), "BOT_NAMES": self.bot_names.get().strip(),
            "DEBOUNCE_SECONDS": self.debounce.get().strip(), "FOLLOWUP_SECONDS": self.followup.get().strip(), "CONTEXT_MESSAGES": self.context_messages.get().strip(), "MEMORY_DB": self.memory_db.get().strip(),
            "TYPING_STATUS": "true" if self.typing.get() else "false", "PERSONA_FILE": self.persona.get().strip(),
            "NAPCAT_API_URL": self.napcat_url.get().strip(), "NAPCAT_ACCESS_TOKEN": self.napcat_access.get().strip(), "NAPCAT_EVENT_TOKEN": self.event_token.get().strip(),
            "BOT_SERVICE_TOKEN": self.service_token.get().strip(), "BRIDGE_PORT": self.bridge_port.get().strip(), "BOT_SERVICE_PORT": self.bot_port.get().strip(),
            "NAPCAT_BOOT": self.napcat_boot.get().strip(), "NAPCAT_QQ": self.napcat_qq.get().strip(), "NAPCAT_HOOK": self.napcat_hook.get().strip(),
        }

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        for key, value in self.values.items():
            if value:
                env[key] = value
        for key, value in self._current_config().items():
            if value:
                env[key] = value
            else:
                env.pop(key, None)
        return env

    def save_config(self) -> bool:
        values = dict(self.values)
        values.update(self._current_config())
        try:
            if values.get("VISION_MODE") not in {"off", "direct", "separate"}:
                raise ValueError("VISION_MODE 必须是 off、direct 或 separate")
            for key in ("BRIDGE_PORT", "BOT_SERVICE_PORT"):
                port = int(values.get(key, ""))
                if not 1 <= port <= 65535:
                    raise ValueError(f"{key} 必须是 1 到 65535 之间的端口")
        except ValueError as exc:
            messagebox.showwarning("配置无效", str(exc), parent=self)
            return False
        save_env_file(ENV_FILE, values)
        self.values = values
        self._append_log(f"配置已保存到 {ENV_FILE}；服务保持当前状态，未自动重启")
        return True

    def _preset_config(self) -> dict[str, str]:
        return {key: value for key, value in self._current_config().items() if key.startswith(("LLM_", "VISION_"))}

    def _preset_selected(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        name = self.preset.get().strip()
        if name == "+":
            for variable in (self.api_key, self.base_url, self.model, self.vision_api_key, self.vision_base_url, self.vision_model):
                variable.set("")
            self._append_log("已清空主模型和视觉模型配置；服务未改变")
            return
        data = self.presets.get(name)
        if not data:
            return
        variables = {
            "LLM_API_KEY": self.api_key, "LLM_BASE_URL": self.base_url, "LLM_MODEL": self.model, "LLM_MAX_TOKENS": self.max_tokens, "LLM_TIMEOUT_SECONDS": self.timeout,
            "VISION_MODE": self.vision_mode, "VISION_API_KEY": self.vision_api_key, "VISION_BASE_URL": self.vision_base_url, "VISION_MODEL": self.vision_model, "VISION_MAX_TOKENS": self.vision_max_tokens, "VISION_TIMEOUT_SECONDS": self.vision_timeout,
        }
        for key, variable in variables.items():
            if key in data:
                variable.set(data[key])
        self._append_log(f"已加载预设 {name}；请保存或重启以应用")

    def save_preset(self) -> None:
        name = simpledialog.askstring("保存预设", "预设名称：", parent=self)
        if not name or not name.strip() or name.strip() == "+":
            return
        self.presets[name.strip()] = self._preset_config()
        save_presets(PRESETS_FILE, self.presets)
        self.preset_box.configure(values=[*sorted(self.presets), "+"])
        self.preset.set(name.strip())
        self._append_log(f"预设已保存：{name.strip()}")

    def rename_preset(self) -> None:
        old = self.preset.get().strip()
        if old not in self.presets:
            return
        new = simpledialog.askstring("重命名预设", "新名称：", initialvalue=old, parent=self)
        if not new or not new.strip() or new.strip() in self.presets or new.strip() == "+":
            return
        self.presets[new.strip()] = self.presets.pop(old)
        save_presets(PRESETS_FILE, self.presets)
        self.preset_box.configure(values=[*sorted(self.presets), "+"])
        self.preset.set(new.strip())

    def delete_preset(self) -> None:
        name = self.preset.get().strip()
        if name not in self.presets:
            return
        del self.presets[name]
        save_presets(PRESETS_FILE, self.presets)
        self.preset_box.configure(values=[*sorted(self.presets), "+"])
        self.preset.set("")

    def _detect_models(self, kind: str) -> None:
        if kind == "chat":
            base, key, button = self.base_url.get().strip().rstrip("/"), self.api_key.get().strip(), None
        else:
            base = self.vision_base_url.get().strip().rstrip("/") or self.base_url.get().strip().rstrip("/")
            key = self.vision_api_key.get().strip() or self.api_key.get().strip()
            button = None
        if not base or not key:
            self._append_log(f"{kind} 模型检测需要 Base URL 和 API Key")
            return
        endpoint = f"{base}/models"
        self._append_log(f"正在检测 {kind} 模型：{endpoint}")
        threading.Thread(target=self._fetch_models, args=(kind, endpoint, key), daemon=True).start()

    def detect_chat_models(self) -> None:
        self._detect_models("chat")

    def detect_vision_models(self) -> None:
        self._detect_models("vision")

    def _fetch_models(self, kind: str, endpoint: str, key: str) -> None:
        try:
            request = Request(endpoint, headers={"Accept": "application/json", "Authorization": f"Bearer {key}"}, method="GET")
            with urlopen(request, timeout=12) as response:
                models = parse_model_ids(json.loads(response.read().decode("utf-8")))
            if not models:
                raise ValueError("/models 没有返回 data[].id")
            self.after(0, lambda: self._models_detected(kind, models))
        except HTTPError as exc:
            self.after(0, lambda: self._models_failed(kind, f"HTTP {exc.code}"))
        except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            self.after(0, lambda: self._models_failed(kind, str(exc)))

    def _models_detected(self, kind: str, models: list[str]) -> None:
        box = self.model_box if kind == "chat" else self.vision_model_box
        box.configure(values=models)
        self._append_log(f"{kind} 检测到 {len(models)} 个模型，可从下拉框选择")

    def _models_failed(self, kind: str, reason: str) -> None:
        self._append_log(f"{kind} 模型检测失败：{reason}；当前模型输入不会被清空")

    def start_all(self) -> None:
        if not self.save_config():
            return
        env = self._environment()
        bot_port = self._port_value(self.bot_port, 8765)
        bridge_port = self._port_value(self.bridge_port, 8766)
        if bot_port is None or bridge_port is None:
            self._append_log("端口无效，请填写 1 到 65535 之间的数字")
            return
        if not port_open(bot_port):
            self.bot.start(env)
        else:
            self._append_log("Bot 端口已被占用，未重复启动")
        if not port_open(bridge_port):
            self.bridge.start(env)
        else:
            self._append_log("Bridge 端口已被占用，未重复启动")

    def restart_all(self) -> None:
        self.stop_all()
        self.after(500, self.start_all)

    def stop_all(self) -> None:
        self.bot.stop()
        self.bridge.stop()
        self._append_log("已停止控制台启动的 Bot 和 Bridge")

    @staticmethod
    def _port_value(variable: tk.StringVar, default: int) -> int | None:
        raw = variable.get().strip() or str(default)
        try:
            value = int(raw)
        except ValueError:
            return None
        return value if 1 <= value <= 65535 else None

    def _select_path(self, variable: tk.StringVar, title: str) -> None:
        selected = filedialog.askopenfilename(parent=self, title=title)
        if selected:
            variable.set(selected)

    def start_napcat(self) -> None:
        api_url = self.napcat_url.get().strip() or DEFAULT_NAPCAT_API_URL
        try:
            api_port = urlsplit(api_url).port or 3000
        except ValueError:
            api_port = 3000
        if port_open(api_port):
            self._append_log(f"NapCat API 已经在运行（端口 {api_port}），没有重复启动")
            return
        if self.napcat_process and self.napcat_process.poll() is None:
            self._append_log("NapCat 已经由本控制台启动")
            return

        paths = [self.napcat_boot.get().strip(), self.napcat_qq.get().strip(), self.napcat_hook.get().strip()]
        if not all(paths):
            messagebox.showwarning(
                "NapCat 路径未配置",
                "请先填写 NapCat 启动程序、QQ 程序和 Hook 路径。\n"
                "也可以直接在配置文件中填写 NAPCAT_BOOT、NAPCAT_QQ、NAPCAT_HOOK。",
                parent=self,
            )
            return
        missing = [path for path in paths if not Path(path).is_file()]
        if missing:
            messagebox.showwarning(
                "NapCat 文件不存在",
                "下面的路径找不到：\n\n" + "\n".join(missing),
                parent=self,
            )
            return
        boot, qq, hook = (Path(path) for path in paths)
        try:
            self.napcat_process = subprocess.Popen(
                [str(boot), str(qq), str(hook)],
                cwd=boot.parent,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except OSError as exc:
            messagebox.showerror("NapCat 启动失败", str(exc), parent=self)
            return
        self._append_log(f"已启动 NapCat：{boot}")

    def _refresh_status(self) -> None:
        bot_port = self._port_value(self.bot_port, 8765)
        bridge_port = self._port_value(self.bridge_port, 8766)
        self.bot_status.configure(text="Bot: 端口无效" if bot_port is None else f"Bot: {'运行中' if port_open(bot_port) else '未运行'}")
        self.bridge_status.configure(text="Bridge: 端口无效" if bridge_port is None else f"Bridge: {'运行中' if port_open(bridge_port) else '未运行'}")
        self.vision_status.configure(text=f"识图: {self.vision_mode.get()} / {self.vision_model.get() or '未配置'}")
        self.after(2000, self._refresh_status)

    def _drain_logs(self) -> None:
        try:
            while True:
                self._append_log(self.log_queue.get_nowait())
        except queue.Empty:
            pass
        self.after(200, self._drain_logs)

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _close(self) -> None:
        if messagebox.askyesno("退出", "是否同时停止本控制台启动的服务？", parent=self):
            self.stop_all()
        self.destroy()


if __name__ == "__main__":
    ControlPanel().mainloop()
