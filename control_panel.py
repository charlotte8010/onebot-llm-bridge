from __future__ import annotations

import json
import os
import queue
import secrets
import socket
import subprocess
import sys
import threading
import tkinter as tk
import ctypes
import zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from onebot_llm_bridge import __version__
from onebot_llm_bridge.emoji_catalog import load_emoji_catalog
from onebot_llm_bridge.backup import create_backup, restore_backup


ROOT = Path(__file__).resolve().parent
APP_TITLE = "OneBot LLM Bridge"
ICON_PATH = ROOT / "assets" / "console_icon.png"
ICON_ICO_PATH = ROOT / "assets" / "console_icon.ico"
ENV_FILE = ROOT / ".env.local"
PRESETS_FILE = ROOT / ".model_presets.json"
THEME_FILE = ROOT / ".control_panel_theme.json"
BOT_SCRIPT = ROOT / "bot_service.py"
BRIDGE_SCRIPT = ROOT / "app.py"
DEFAULT_NAPCAT_API_URL = "http://127.0.0.1:3000"
DEFAULT_WINDOW_GEOMETRY = "1180x1040"
DEFAULT_UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/charlotte8010/onebot-llm-bridge/main/update_manifest.json"
UPDATE_TYPES = {"hot", "normal", "force"}


def run_git_command(*args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    """Run a git command in this checkout without invoking a shell."""
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def parse_git_ahead_behind(output: str) -> tuple[int, int]:
    """Parse ``git rev-list --left-right --count`` output."""
    parts = output.split()
    if len(parts) != 2 or any(not part.isdigit() for part in parts):
        raise ValueError(f"无法解析 Git 提交差异：{output.strip() or '<empty>'}")
    return int(parts[0]), int(parts[1])


def git_output_tail(output: str, limit: int = 3) -> str:
    """Keep update errors readable without dumping a whole Git trace into the UI."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return "；".join(lines[-limit:])


def parse_release_version(value: str) -> tuple[int, int, int]:
    """Parse the simple semantic versions used by the release manifest."""
    normalized = value.strip().lstrip("v")
    parts = normalized.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"版本号无效：{value or '<empty>'}")
    return tuple(int(part) for part in parts)  # type: ignore[return-value]


def parse_update_manifest(payload: object) -> dict[str, str]:
    """Validate the small public update manifest before using it."""
    if not isinstance(payload, dict):
        raise ValueError("更新清单不是 JSON 对象")
    required = ("version", "update_type", "target_ref", "min_version", "message")
    if any(not isinstance(payload.get(key), str) for key in required):
        raise ValueError("更新清单缺少必要字段")
    manifest = {key: str(payload[key]).strip() for key in required}
    parse_release_version(manifest["version"])
    parse_release_version(manifest["min_version"])
    if manifest["update_type"] not in UPDATE_TYPES:
        raise ValueError(f"更新类型无效：{manifest['update_type']}")
    if not manifest["target_ref"] or manifest["target_ref"].startswith("-") or any(char in manifest["target_ref"] for char in ("\n", "\r", " ")):
        raise ValueError("更新目标引用无效")
    return manifest


def vision_status(mode: str, model: str) -> tuple[str, bool]:
    """Return a user-facing vision status and whether its configuration is ready."""
    normalized_mode = mode.strip().lower()
    if normalized_mode == "off":
        return "识图 · 已关闭", True
    if normalized_mode == "direct":
        return "识图 · 交给主模型", True
    model_name = model.strip()
    return f"识图 · 单独视觉模型 / {model_name or '未配置'}", bool(model_name)


# The UI shows readable Chinese labels while the values written to .env.local
# stay stable for the runtime and existing installations.
OPTION_LABELS: dict[str, dict[str, str]] = {
    "VISION_MODE": {"off": "关闭识图", "direct": "直接识图", "separate": "单独视觉模型"},
    "GROUP_MODE": {"mention": "叫到才回", "smart": "智能跟随", "all": "群聊都回", "off": "关闭群聊"},
    "DECISION_MODE": {"heuristic": "本地规则", "model": "模型判断"},
    "DEBOUNCE_SECONDS": {"random": "随机 3-6 秒", "3": "3 秒", "4": "4 秒", "5": "5 秒", "6": "6 秒"},
    "REACTION_MODE": {"off": "关闭", "like": "点赞回应"},
    "ACTIVE_TARGET_TYPE": {"private": "私聊", "group": "群聊"},
    "REMOTE_MEMORY_MODE": {"local_first": "本地优先", "coordinated": "协调模式"},
}


TOOL_LABELS: dict[str, str] = {
    "get_time": "查询当前时间",
}


HELP_TEXTS: dict[str, str] = {
    "MODEL_PRESET": "预设只负责快速填入模型相关字段。选中后仍可以继续修改，保存配置并启动或重启服务后才会影响正在运行的服务。",
    "LLM_API_KEY": "模型中转站或服务商提供的密钥。只保存在本机配置里，不要把它发到群里或提交到 GitHub。",
    "LLM_BASE_URL": "模型接口地址，通常以 /v1 结尾。点击“检测模型”会请求这个地址的 /models。",
    "LLM_MODEL": "实际使用的模型名称。检测模型会从当前 Base URL 读取可用模型，也可以手动输入。",
    "LLM_MAX_TOKENS": "限制模型最多输出多少 token。太小可能截断，太大通常会更慢。",
    "LLM_TIMEOUT_SECONDS": "等待模型返回的最长秒数。思考模型可以调大，普通聊天模型不必太大。",
    "VISION_MODE": "关闭识图：不发送图片给模型；直接识图：交给主模型；单独视觉模型：用另一套视觉 API 先描述图片。",
    "VISION_BASE_URL": "单独视觉模型的接口地址。留空时复用主模型地址。",
    "VISION_MODEL": "单独视觉模型的名称。只有启用“单独视觉模型”时才需要填写。",
    "GROUP_MODE": "叫到才回：需要 @ 或名字；智能跟随：叫过一次后短时间继续当前话题；群聊都回：白名单群里的消息都参与判断；关闭群聊：不处理群聊。",
    "DECISION_MODE": "本地规则速度快、成本低；模型判断更灵活，可以判断要不要回、是否继续话题、是否引用或加 reaction。",
    "GROUP_ALLOWLIST": "允许自动回复的群号。多个群号用半角逗号分隔，例如 123456789,987654321；这里填群号，不是群名。智能跟随和群聊都回只对这里的群生效。当前没有单独的主动消息白名单。",
    "DEBOUNCE_SECONDS": "收到消息后先等一小段时间，把对方连续发的几条合并成一次输入。随机模式会在 3、4、5、6 秒中随机选择。",
    "FOLLOWUP_SECONDS": "机器人回复后，在这段时间内同一话题可以不再 @ 继续聊；无关话题仍会被忽略。",
    "CONTEXT_MESSAGES": "每次请求附带的最近消息条数。太少会断上下文，太多会增加延迟和输入费用。",
    "MEMORY_DB": "本地 SQLite 记忆库路径。留空也可以运行，只是不保存跨重启的本地记忆。",
    "REACTION_MODE": "点赞回应只是在已有消息上加 reaction，不会额外发送一条表情消息。",
    "REPLY_TO_MESSAGE": "开启后，群聊普通回复会引用触发这次回复的那条消息；私聊只有在对方引用消息时才会跟随引用。关闭后仍会保留对方主动引用的目标。",
    "PERSONA_FILE": "稳定的人设提示词文件。建议把长期不变的人设、说话方式和明确禁忌放在这里。",
    "EMOJI_CATALOG": "表情词典路径。可以点击“编辑词典”，让模型知道“笑死”“无语”等词对应哪个 NapCat 表情 ID。",
    "ACTIVE_ENABLED": "旧版单目标主动消息开关。新版请分别使用“启用私聊主动消息”和“启用群聊主动消息”。",
    "ACTIVE_INTERVAL_MINUTES": "每隔多少分钟触发一次主动消息任务。建议先设置 60 分钟或更长，避免消息过于频繁。",
    "ACTIVE_TARGET_TYPE": "旧版单目标主动消息类型。新版在私聊和群聊两行分别配置。",
    "ACTIVE_TARGET_ID": "旧版单目标主动消息 ID。新版请分别填写私聊目标和群聊目标。",
    "ACTIVE_PROMPT": "旧版单目标主动消息提示。新版请分别填写私聊提示和群聊提示。",
    "ACTIVE_PRIVATE_ENABLED": "是否按间隔主动给私聊目标发消息。关闭后不会向私聊发送主动消息。",
    "ACTIVE_PRIVATE_TARGET_ID": "私聊主动消息的接收者 QQ 号，每个目标只填写数字。多个目标用逗号分隔，例如 123456789,987654321；也可以使用中文逗号。",
    "ACTIVE_PRIVATE_PROMPT": "给私聊主动消息使用的任务要求，不是固定原文；模型会结合 Persona 生成最终气泡。",
    "ACTIVE_GROUP_ENABLED": "是否按间隔主动给群聊目标发消息。关闭后不会向群聊发送主动消息。",
    "ACTIVE_GROUP_TARGET_ID": "群聊主动消息的接收群号，每个目标只填写数字。多个目标用逗号分隔，例如 123456789,987654321；也可以使用中文逗号。",
    "ACTIVE_GROUP_PROMPT": "给群聊主动消息使用的任务要求，不是固定原文；模型会结合 Persona 生成最终气泡。",
    "TOOLS_ENABLED": "只允许工具白名单里的工具被模型调用。不了解工具用途时建议关闭。",
    "TOOL_ALLOWLIST": "勾选后，模型才可以请求对应工具；没有勾选的工具即使被模型请求也不会执行。",
    "NAPCAT_API_URL": "NapCat 的 HTTP Server 地址，通常是 http://127.0.0.1:3000。控制台和 Bridge 通过它发消息。",
    "NAPCAT_ACCESS_TOKEN": "NapCat HTTP Server 的访问 Token。它和“事件上报 Token”不是同一个东西。",
    "NAPCAT_EVENT_TOKEN": "去 NapCat WebUI → 网络配置 → OneBot11 → HTTP 上报服务（HTTP Client），打开上报到 8766/onebot 的配置，复制其中的 Token 到这里。它不是 3000 的 NapCat API Token，也不是 Bot 服务 Token。",
    "BOT_SERVICE_TOKEN": "这个 Token 不在 NapCat 里。它由本项目的 Bot 服务（8765）和 Bridge 共用；可以手动填写或点击“生成”。生成后还要保存配置并重启 Bot 与 Bridge。",
    "BOT_SERVICE_HOST": "Bot 服务地址。单机运行填 127.0.0.1；Bot 放在腾讯云时填云服务器的内网或 Tailscale 地址，只填主机名/IP，不要填 http://、路径或 /reply。公网直连建议先配置 HTTPS，不要用裸 HTTP 传 Token。",
    "BRIDGE_PORT": "Bridge 接收 NapCat 事件的端口，默认 8766。",
    "BOT_SERVICE_PORT": "Bot 服务提供模型回复的端口，默认 8765。",
    "NAPCAT_BOOT": "NapCat 启动程序路径。填写 launcher.bat 时只启动 launcher，由它自己查找 QQ；只有直接填写 NapCatWinBootMain.exe 时才需要 QQ 和 Hook。",
    "NAPCAT_QQ": "QQ.exe 的路径。要和 NapCat 使用的 QQNT 安装保持一致。",
    "NAPCAT_HOOK": "NapCatWinBootHook.dll 的路径，用于注入 NapCat。",
    "SUPABASE_URL": "Supabase 项目 URL，例如 https://xxxx.supabase.co。只填写项目地址，不要填 /rest/v1。",
    "SUPABASE_SECRET_KEY": "Supabase 后台的 Secret Key，只放在本机配置中。不要使用 anon key，也不要提交到 GitHub。",
    "SUPABASE_TIMEOUT_SECONDS": "访问 Supabase 的最长等待时间。网络不稳定时可以适当调大。",
    "REMOTE_MEMORY_MODE": "本地优先：远端不可用时仍继续回复；协调模式：多台 Bridge 共用远端租约，减少重复回复。",
    "SUMMARY_ENABLED": "达到触发条数后，用模型把远端聊天压缩成摘要和事实，减少长期记忆占用。",
    "SUMMARY_MIN_MESSAGES": "累计多少条远端消息后触发一次摘要。数字越小越频繁，模型调用也越多。",
    "SUMMARY_DELAY_SECONDS": "摘要任务开始前的等待时间，用来合并短时间内连续产生的消息。",
}


HELP_SECTIONS: tuple[tuple[str, str], ...] = (
    (
        "第一次启动",
        """第一次使用时，建议严格按下面的顺序配置。不要一开始就同时改很多选项，这样出错时比较难判断是哪一步的问题。

先认识三个服务：
• NapCat：登录 QQ、接收消息、发送消息。
• Bridge：接收 NapCat 的消息，决定要不要回复，并把请求转给 Bot。
• Bot 服务：调用大模型，生成回复。

完整消息路径是：QQ → NapCat → Bridge（8766）→ Bot（8765）→ Bridge → NapCat（3000）→ QQ。

第一次配置：
1. 先启动 NapCat 并登录 QQ。NapCat 控制台会显示 WebUI 地址，通常是 http://127.0.0.1:6100/webui?token=...；浏览器打开它。
2. 在 NapCat WebUI 的“网络配置 → OneBot11”里配置 HTTP Server：地址保持 127.0.0.1，端口保持 3000，并记下它的 Token。控制台“连接与服务”里的 NapCat API 和 NapCat Access Token 要与它一致。
3. 同一个页面配置 HTTP Client：目标地址填 http://127.0.0.1:8766/onebot，并记下 HTTP Client 的 Token。这个 Token 填到控制台的“事件上报 Token”。
4. 回到控制台“模型与识图”，填写 API Key 和 Base URL，点击“检测模型”，再选择模型。
5. 在“连接与服务”确认 Bridge 端口是 8766、Bot 服务地址是 127.0.0.1、Bot 端口是 8765。只有使用云端 Bot 时，才把地址改成云服务器的私网或 Tailscale 地址；这时控制台不会在本地启动 Bot。第一次可以先不要配置 Supabase、主动消息和工具。
6. 点击“保存配置”。保存只写入本地配置，不会自动重启任何服务。
7. 点击“启动全部”启动 Bot 和 Bridge；NapCat 可以用“启动 NapCat”启动，也可以继续使用你原来的 launcher。
8. 先给自己的 QQ 私聊发送“1”。看到 NapCat 收到消息、Bridge 有日志、Bot 有 /reply 请求，最后 QQ 收到回复，说明基础链路成功。
9. 基础私聊成功后，再配置 Persona、群聊白名单、记忆和图片识别。

如果只想先测试模型，可以只填模型配置并点击“检测模型”；如果要让 QQ 真正收发消息，NapCat 的两个网络配置和三个端口都要对应上。""",
    ),
    (
        "模型与识图",
        """这里配置大模型。最少需要三个值：
• API Key：模型服务商发给你的密钥，像密码一样保管，不要发到群里。
• Base URL：模型服务商的 OpenAI 兼容接口地址，通常以 /v1 结尾；以服务商文档为准。
• 模型：实际调用的模型名称。

操作方法：
1. 先输入 API Key 和 Base URL。
2. 点击“检测模型”。控制台会请求当前 URL 的 /models，不会固定显示某几个模型。
3. 检测成功后，在模型下拉框选择一个；也可以直接手动输入模型名。
4. 选择思考模式、输出预算和超时。普通聊天模型建议关闭思考，输出预算先用 2048 或 4096；思考模型响应慢时再提高超时。
5. 如果以后经常切换模型，可以点击“保存为新预设”。预设只保存模型和识图相关配置，不会保存 Token。下拉框选中预设后仍然可以继续修改；“+”会清空模型字段，用来新建预设。
6. 点击“保存配置”，再手动重启 Bot 和 Bridge，运行中的服务才会使用新模型。保存配置本身不会自动重启。

识图模式：
• 关闭识图：图片不会送给模型，模型只会知道对方发了一张图片。
• 直接识图：图片和文字一起交给主模型；主模型必须真的支持视觉输入。
• 单独视觉模型：先用另一套视觉 API 描述图片，再把描述交给聊天模型；适合 DeepSeek 等文本模型。

如果检测返回 403，优先检查 API Key、Base URL 是否属于同一个中转站，以及中转站是否允许 /models。能聊天不代表一定允许模型列表接口。图片读不到时，不要只看主模型名称，先确认它是否支持图片；不支持就改用“单独视觉模型”。""",
    ),
    (
        "NapCat 连接",
        """这里最容易混淆的是端口和 Token。可以把它们理解成三条不同的线路：

1. NapCat HTTP Server：默认 127.0.0.1:3000。
   Bridge 通过它调用 NapCat 发送私聊、群聊、reaction 和输入状态。NapCat WebUI 里 HTTP Server 的 Token，要填到控制台“NapCat Access Token”。
2. Bridge HTTP 上报入口：默认 127.0.0.1:8766/onebot。
   NapCat 通过 HTTP Client 把收到的 QQ 消息推给 Bridge。NapCat WebUI 里 HTTP Client 的 Token，要填到控制台“HTTP Client Token（事件上报）”。
3. Bot 服务：默认 127.0.0.1:8765/reply。
   Bridge 通过它调用大模型。Bot 服务 Token不是 NapCat 的 Token，控制台点“生成”即可生成一串；Bot 和 Bridge 必须使用同一串。

NapCat WebUI 的配置路径：网络配置 → OneBot11。HTTP Server 和 HTTP Client 是两项不同的配置，不要把 Server 的地址填到 Client，也不要把 8765 填成上报地址。

常见日志对应关系：
• NapCat 报 401：事件上报 Token 不一致，检查 HTTP Client 的 Token。
• Bridge 报 401：NapCat 访问 Bridge 时的事件 Token 不一致。
• Bridge 报连接 8765 refused：Bot 没启动、Bot 已退出或端口不一致。
• NapCat 报 403：NapCat Access Token 不对，或 HTTP Server 地址不对。
• 8766 refused：Bridge 没启动，或者 HTTP Client 仍然指向旧端口。

改了 Token 或端口后要保存配置并手动重启相关服务。控制台保存不会自动重启。""",
    ),
    (
        "配置 Supabase",
        """Supabase 是可选的远端记忆库。不开也能正常聊天；开启后，可以在重启后保留记忆，也可以让多台机器共享记忆。

第一次配置：
1. 在 Supabase 新建一个私有项目，等待项目完成初始化。
2. 打开项目的 SQL Editor，把仓库里的 supabase/migrations/202608150001_bridge_memory.sql 全部复制进去并执行。
3. 在项目 Settings → API 找到 Project URL，填入控制台 Supabase URL。
4. 找到后台 Secret Key，填入 Supabase Secret Key。不要填 anon key，不要把 Secret Key 发给别人，也不要提交到 GitHub。
5. 在控制台填写 Bot QQ。这是记忆归属于哪个 QQ 账号的标识，只填数字。
6. 选择记忆模式：本地优先适合单机，远端暂时不可用时仍然继续回复；协调模式适合多台机器共用一个 Bot，可以减少重复处理。
7. 第一次建议关闭自动摘要，确认普通记忆正常后，再打开自动摘要并设置触发条数。
8. 点击“保存配置”，手动重启 Bot 和 Bridge，再通过私聊测试。

本地 SQLite 和 Supabase 不是二选一的同一个文件：SQLite 是这台电脑上的本地记忆，Supabase 是远端记忆。网络不稳定时优先使用本地优先模式。看到 Supabase 错误时，先检查 URL、Secret Key、迁移是否执行，以及 Bot QQ 是否填写。""",
    ),
    (
        "群聊与消息形态",
        """这里控制群聊什么时候回复，以及一条回复如何发送。

群聊模式：
• 叫到才回：只有 @ 机器人，或消息里出现 Bot 名称时才进入回复流程。
• 智能跟随：先被叫到后，在“继续话题秒数”内可以继续聊天；插入的无关话题会被忽略。
• 群聊都回：白名单群里的消息都交给模型判断，仍可能因为智能判断而选择不回复。
• 关闭群聊：完全不处理群聊消息，只保留私聊。

群聊白名单：
1. 填群号，不是群名；例如 123456789。
2. 多个群号用半角逗号分隔，例如 123456789,987654321。
3. 群聊白名单为空时，群聊模式不会替你猜群号；先填白名单再测试。

消息形态相关选项：
• 防抖延迟：收到消息后先等待，把对方短时间连续发的几条合并成一次输入，避免“发两句回两次”。随机模式会在 3、4、5、6 秒中选择。
• 回复延迟：模型生成后，发送前再等待一小段时间，只影响发送时机。
• 引用回复：默认开启时，群聊普通回复会引用触发回复的那条消息；私聊只有对方已经引用消息时才会引用。关闭后仍保留对方主动引用的目标。
• 上下文条数：每次请求带入的近期消息数量。太少容易接不上话，太多会增加延迟和费用。
• 表情回应：是对已有消息添加 reaction，不是发送一个新的表情气泡。
• 显示输入状态：等待和生成期间显示“正在输入”；关闭后不会发送这个状态。

如果发现对方连续发三句话，机器人却回复三次，先把防抖延迟调到 4 或 5 秒；如果群里完全不回，依次检查群聊模式、群聊白名单、是否真的 @ 到了机器人。""",
    ),
    (
        "Persona、记忆与词典",
        """Persona 是长期稳定的人设文件，适合写身份、说话方式、明确喜欢和讨厌的东西，以及禁止编造的事实。它不是聊天记录，也不是每次临时输入。

使用 Persona：
1. 在“回复与记忆”里可以点击“选择”使用已有文本文件，也可以点击“编辑 Persona”直接在控制台打开编辑器；不需要手动打开记事本改配置。
2. 编辑器里可以写身份、稳定的兴趣和禁忌、说话方式、消息形态和真实聊天例子。事实性内容只写确定说过的，不要让模型自行补全。
3. 点击编辑器里的“保存 Persona”会写入 Persona 文件，并同步保存路径；然后点击主窗口“保存配置”。保存只修改本地文件，不会自动重启服务。
4. 修改后要手动重启 Bot 服务，新的 Persona 才会进入后续请求；Bridge 通常不需要因为 Persona 改动而重启，但一起重启也可以。
5. 规则尽量写成“什么时候这样说”，不要写成“每句话必须带某个口癖”。例如“偶尔使用括号表达语气”比“每句都加括号”更自然。
6. Persona 文件路径可以是相对项目目录的路径，例如 .local/persona_prompt.txt；留空时使用这个默认路径。文件不存在时可以直接在编辑器里新建。

三种记忆的区别：
• 上下文条数：本次请求临时带入的最近消息，重启后不会单独保存。
• 持久化记忆库：本机的 SQLite 文件，适合单机长期使用；留空也能运行。
• Supabase：跨重启、跨设备的远端记忆，需要在“连接与服务”配置。

表情词典：点击“编辑词典”维护表情名称和 NapCat reaction ID。模型只能使用词典里存在的表情，不要让它凭空猜 ID。关闭“表情回应”时，即使模型提出 reaction，也不会真的发送。

如果模型越来越像说明书，先检查 Persona 是否写得太像规则清单，再减少上下文条数或删掉重复的人设内容。""",
    ),
    (
        "控制台小功能",
        """控制台尽量把常用操作放在按钮里，不需要频繁手动编辑 .env.local。

模型预设：
• “保存为新预设”：把当前主模型、识图模型、思考模式、输出预算和超时保存成一个名字。
• “重命名”：修改当前预设的显示名称。
• “删除”：删除当前预设，不会删除正在运行的服务。
• 下拉框最后的“+”：清空模型和识图字段，用来填写一套新的配置。
• 预设只是填表，不会自动应用；改完后仍要点击“保存配置”，需要运行新配置时再手动重启服务。

服务按钮：
• “保存配置”：只写入本地配置，不启动、不停止、不重启任何服务。
• “启动全部”：本机地址时启动 Bot 和 Bridge；Bot 服务地址填写云端时，会跳过本地 Bot，只启动本地 Bridge，并在日志里写明原因。
• “启动 NapCat”：可以填写 launcher.bat，让 NapCat 自己查找 QQ；也可以填写 NapCatWinBootMain.exe、QQ.exe 和 Hook。
• “一键诊断”：只检查服务、端口、Token 和模型接口，不保存配置，也不会重启。
• “重启全部”：停止并重新启动控制台启动的 Bot 和 Bridge。
• “停止全部”：只停止由当前控制台启动的服务，不会卸载 NapCat 或删除配置。

其他按钮和区域：
• 右上角“操作说明”：打开这份说明，左侧目录可以切换章节。
• 右上角“切换浅色/夜间”：只切换控制台外观，会保存主题选择，不影响 Bot 行为。
• “编辑词典”：维护表情名称、NapCat reaction ID、含义和使用场景；保存后仍需按提示保存配置并重启相关服务。
• “生成”Bot 服务 Token：生成供 Bot 和 Bridge 共用的本地 Token；生成后要保存配置，并重启 Bot 与 Bridge。
• “实时日志”：显示控制台和子服务输出。错误会尽量写出原因和建议；清空日志只清除画面，不会删除服务日志或记忆。

通用操作顺序：先改选项 → 点击“保存配置” → 手动启动或重启受影响的服务 → 看运行状态和实时日志 → 再发送一条新的测试消息。""",
    ),
    (
        "主动消息",
        """主动消息用于让机器人按固定间隔主动发起一条消息，不需要等别人先说话。私聊和群聊是两套独立配置。

配置方法：
1. 填“主动消息间隔（分钟）”，例如 60 表示某个目标在最近一次聊天结束后，至少等待约 60 分钟再主动发消息。每个私聊或群聊目标独立计时，不会因为同时配置了两个人就同一时刻发送。
2. 私聊一行：勾选“启用私聊主动消息”，填写接收者 QQ 号和私聊主动提示。
3. 群聊一行：勾选“启用群聊主动消息”，填写接收群号和群聊主动提示。
4. 提示词是给模型的生成要求，不是固定原文。例如“根据最近聊天，自然地问候一下对方”。
5. 每一行都可以单独关闭；不想发群消息时，只关闭群聊那一行即可。
6. 填写后点击“保存配置”，再手动重启 Bot 和 Bridge。保存本身不会启动定时任务。

第一次建议只启用私聊，并把间隔设长一些。主动消息会带上该目标最近的聊天上下文和 Bot 已经发出的内容，提示词只需要写方向，不要写成固定回复。目标必须是数字；群聊目标填群号，不填群名。如果日志里能看到服务正常但没有主动消息，先确认对应开关、目标、提示词都填写完整，而且重启过 Bot 和 Bridge。""",
    ),
    (
        "常见故障",
        """排查时不要一次重装所有东西，按消息路径从前往后看：NapCat → Bridge → Bot → 模型。

• NapCat 没登录：先看 NapCat 窗口是否显示已登录，QQ 账号是否在线。
• NapCat 收到消息但 Bridge 没日志：检查 HTTP Client 是否启用，目标是否为 http://127.0.0.1:8766/onebot，事件上报 Token 是否一致。
• NapCat 报 401：这是上报认证失败，检查 HTTP Client 的 Token；不要拿 Bot 服务 Token 代替。
• Bridge 报 8766 refused：Bridge 没启动、已退出，或 HTTP Client 仍指向旧端口。
• Bridge 收到消息但 Bot 没请求：检查 Bot 是否监听 8765、Bot 服务 Token 是否和 Bridge 一致。
• Bridge 报 8765/reply timed out：模型接口太慢或思考时间太长，可关闭思考、降低输出预算或提高超时秒数。
• NapCat 报 403：检查 NapCat API 地址和 NapCat Access Token；HTTP Server 默认是 3000。
• 模型检测 403：检查当前 URL、Key 和中转站是否允许 /models；能聊天不代表能列模型。
• 图片读不到：主模型可能不支持视觉输入，改成“单独视觉模型”并填写视觉模型配置。
• QQ 版本兼容性警告：这是 NapCat 与 QQNT 版本适配提示，按 NapCat release 说明选择兼容版本；它不一定等于 Bot 代码出错。
• 群聊不回：检查群聊模式、群聊白名单、Bot 名称和是否 @ 到机器人。

改配置后的固定动作：点击“保存配置” → 手动重启受影响的服务 → 等待服务显示运行中 → 再发一条新的测试消息。不要只刷新控制台页面，正在运行的旧进程不会自动读取新配置。""",
    ),
)


def enable_windows_dpi_awareness() -> None:
    """Keep Tk from being bitmap-scaled by Windows on high-DPI displays."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (AttributeError, OSError):
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass


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


def load_theme(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return "morandi"
    return str(payload.get("theme", "morandi")) if isinstance(payload, dict) and payload.get("theme") in {"dark", "morandi"} else "morandi"


def save_theme(path: Path, theme: str) -> None:
    path.write_text(json.dumps({"theme": theme}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def parse_port(raw: str, default: int) -> int | None:
    value_text = raw.strip() or str(default)
    try:
        value = int(value_text)
    except ValueError:
        return None
    return value if 1 <= value <= 65535 else None


def local_url_port(raw_url: str, default: int | None = None) -> int | None:
    try:
        parsed = urlsplit(raw_url.strip())
    except ValueError:
        return None
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return None
    return parsed.port or default


def is_local_service_host(raw_host: str) -> bool:
    """Whether a configured Bot host points back to this computer."""
    value = raw_host.strip().lower().strip("[]")
    return value in {"127.0.0.1", "localhost", "::1"}


def service_base_url(raw_host: str, port: int) -> str:
    """Build the HTTP URL used by the control panel to probe a Bot service."""
    host = raw_host.strip()
    if not host or "://" in host or "/" in host:
        raise ValueError("Bot 服务地址只能填写主机名或 IP，不要带协议和路径")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def missing_vision_config() -> str:
    raise ValueError("识图 API Key 或 Base URL 未填写")


def format_panel_error(stage: str, error: BaseException | str) -> str:
    """Turn a technical failure into a compact, actionable console message."""
    if isinstance(error, HTTPError):
        reason = f"HTTP {error.code} {error.reason or ''}".strip()
    elif isinstance(error, URLError):
        reason = f"网络连接失败：{error.reason}"
    elif isinstance(error, json.JSONDecodeError):
        reason = "服务返回的内容不是有效 JSON"
    elif isinstance(error, TimeoutError):
        reason = "请求超时，服务在限定时间内没有返回"
    else:
        reason = str(error).strip() or type(error).__name__

    lowered = reason.lower()
    if "401" in lowered or "unauthorized" in lowered:
        advice = "认证信息不正确或已失效。检查对应服务的 Token/API Key，并确认没有把 NapCat、事件上报和 Bot Token 混用。"
    elif "403" in lowered or "forbidden" in lowered:
        advice = "服务拒绝了请求。检查 API Key 权限、接口地址和中转站是否允许这个接口；/models 被禁用时可以手动填写模型名。"
    elif "chat/completions" in lowered or "具体接口路径" in lowered:
        advice = "Base URL 应填写服务根地址，例如 https://example.com/v1，不要填写 /chat/completions、/responses 或其他具体请求路径。"
    elif "404" in lowered or "not found" in lowered:
        advice = "接口地址或路径不对。检查 Base URL 是否应该包含 /v1，端口是否正确，以及目标服务是否真的提供这个接口。"
    elif "timed out" in lowered or "timeout" in lowered or "超时" in lowered:
        advice = "服务响应太慢或网络不通。先确认服务已启动，再检查代理、网络和超时秒数；思考模型可以适当提高超时。"
    elif "refused" in lowered or "10061" in lowered or "连接失败" in lowered:
        advice = "目标端口没有服务在监听。检查对应服务是否启动，以及控制台端口和 NapCat 的上报地址是否一致。"
    elif "json" in lowered or "decode" in lowered:
        advice = "收到的不是接口约定的 JSON，常见原因是 URL 填到了网页地址、被代理返回了错误页面，或接口格式不兼容。"
    elif "supabase" in stage.lower():
        advice = "检查 Supabase URL、Secret Key、Bot QQ 和数据库迁移是否正确执行。"
    elif "配置" in stage or "persona" in stage.lower() or "词典" in stage:
        advice = "检查输入格式、文件路径和文件权限；修改后再保存一次。"
    else:
        advice = "先看这条错误前后的日志，确认是哪一个服务、端口或配置项失败，再按上面的原因排查。"
    return f"[失败] {stage}\n原因：{reason}\n建议：{advice}"


def _json_request(request: Request, timeout: float = 6.0) -> dict[str, object]:
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("返回内容不是 JSON 对象")
    return payload


def probe_service(base_url: str, token: str = "") -> str:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = _json_request(Request(f"{base_url.rstrip('/')}/health", headers=headers, method="GET"))
    if payload.get("ok") is False:
        raise ValueError(str(payload.get("error", "服务返回失败")))
    return str(payload.get("service", "ok"))


def probe_models(base_url: str, api_key: str) -> int:
    request = Request(
        f"{base_url.rstrip('/')}/models",
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    models = parse_model_ids(_json_request(request))
    if not models:
        raise ValueError("/models 没有返回可用模型")
    return len(models)


def probe_napcat(base_url: str, access_token: str = "") -> str:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    payload = _json_request(
        Request(
            f"{base_url.rstrip('/')}/get_status",
            data=b"{}",
            headers=headers,
            method="POST",
        )
    )
    if payload.get("status") == "failed" or payload.get("retcode", 0) not in {0, None}:
        raise ValueError("NapCat 拒绝了 get_status 请求")
    return "NapCat API 可用"


def build_napcat_command(boot: str, qq: str = "", hook: str = "") -> list[str]:
    """Build a Windows NapCat command for either a launcher script or boot exe."""
    boot_path = Path(boot)
    if boot_path.suffix.lower() in {".bat", ".cmd"}:
        command_shell = os.environ.get("COMSPEC", "cmd.exe")
        return [command_shell, "/d", "/c", f'call "{boot_path}"']
    return [str(boot_path), qq, hook]


def build_napcat_nt_command(boot: str, qq: str, hook: str) -> list[str]:
    """Build a direct QQNT boot command for a standard NapCat launcher folder."""
    boot_path = Path(boot)
    boot_exe = boot_path if boot_path.suffix.lower() not in {".bat", ".cmd"} else boot_path.parent / "NapCatWinBootMain.exe"
    return [str(boot_exe), qq, hook]


def build_napcat_utf8_console_command(command: list[str]) -> list[str]:
    """Start a NapCat command in a UTF-8 Windows console so Chinese logs stay readable."""
    if os.name != "nt":
        return command
    shell = os.environ.get("COMSPEC", "cmd.exe")
    return [shell, "/d", "/c", "chcp 65001 > nul & " + subprocess.list2cmdline(command)]


def discover_qq_executable(boot: str) -> Path | None:
    """Find QQNT without assuming that it shares a drive with NapCat."""
    candidates: list[Path] = []
    boot_path = Path(boot)
    drive_roots: list[Path] = []
    if boot_path.anchor:
        drive_roots.append(Path(boot_path.anchor))
    if os.name == "nt":
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            drive_root = Path(f"{letter}:/")
            if drive_root.exists() and drive_root not in drive_roots:
                drive_roots.append(drive_root)
    for drive_root in drive_roots:
        candidates.extend(
            (
                drive_root / "QQNT" / "QQ.exe",
                drive_root / "Program Files" / "Tencent" / "QQNT" / "QQ.exe",
                drive_root / "Program Files (x86)" / "Tencent" / "QQNT" / "QQ.exe",
                drive_root / "Tencent" / "QQNT" / "QQ.exe",
                drive_root / "QQ" / "QQNT" / "QQ.exe",
            )
        )
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Tencent" / "QQNT" / "QQ.exe")
    try:
        import winreg

        registry_locations = (
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\QQ"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\QQ"),
        )
        for root, key_name in registry_locations:
            try:
                with winreg.OpenKey(root, key_name) as key:
                    uninstall, _ = winreg.QueryValueEx(key, "UninstallString")
                uninstall_path = Path(str(uninstall).strip().strip('"'))
                candidates.append(uninstall_path.parent / "QQ.exe")
            except (FileNotFoundError, OSError):
                continue
    except ImportError:
        pass

    seen: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate.is_file():
            return candidate
    return None


def generate_service_token() -> str:
    """Generate a URL-safe token for the local Bot service and Bridge."""
    return secrets.token_urlsafe(32)


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
        self.log.put(f"[{self.name}] 正在启动 {self.script.name}")
        child_env = dict(env)
        child_env["PYTHONUNBUFFERED"] = "1"
        try:
            self.process = subprocess.Popen(
                [sys.executable, "-u", str(self.script)],
                cwd=ROOT,
                env=child_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            self.process = None
            self.log.put(format_panel_error(f"{self.name} 服务启动", exc))
            return
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


class FixedTabNotebook(tk.Frame):
    """A fixed-height tab strip that does not inherit native focus geometry."""

    TAB_HEIGHT = 46

    def __init__(self, master: tk.Misc, colors: dict[str, str]) -> None:
        super().__init__(master, background=colors["background"], highlightthickness=0)
        self._colors = colors
        self._tabs: list[tuple[tk.Misc, tk.Frame, tk.Label]] = []
        self._selected = -1
        self._tab_changed_callback: Callable[[object], None] | None = None
        self._tab_bar = tk.Frame(self, background=colors["background"], height=self.TAB_HEIGHT)
        self._tab_bar.grid(row=0, column=0, sticky="ew")
        self._tab_bar.grid_propagate(False)
        self._tab_bar.rowconfigure(0, minsize=self.TAB_HEIGHT, weight=1)
        self._content = tk.Frame(self, background=colors["background"], highlightthickness=0)
        self._content.grid(row=1, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, minsize=self.TAB_HEIGHT, weight=0)
        self.rowconfigure(1, weight=1)

    def content_parent(self) -> tk.Frame:
        return self._content

    def bind(self, sequence: str | None = None, func: Callable[..., object] | None = None, add: str | bool | None = None):  # type: ignore[override]
        if sequence == "<<NotebookTabChanged>>":
            self._tab_changed_callback = func  # type: ignore[assignment]
            return ""
        return super().bind(sequence, func, add)

    def add(self, child: tk.Misc, text: str = "") -> None:
        index = len(self._tabs)
        tab_frame = tk.Frame(
            self._tab_bar,
            background=self._colors["background"],
            highlightbackground=self._colors["border"],
            highlightcolor=self._colors["border"],
            highlightthickness=1,
            bd=0,
            height=self.TAB_HEIGHT,
        )
        tab_frame.grid(row=0, column=index, sticky="nsew", padx=(0, 1))
        tab_frame.grid_propagate(False)
        tab_label = tk.Label(
            tab_frame,
            text=text,
            background=self._colors["background"],
            foreground=self._colors["muted"],
            font=("Microsoft YaHei UI", 10, "bold"),
            anchor="center",
            cursor="hand2",
            bd=0,
            highlightthickness=0,
        )
        tab_label.pack(fill="both", expand=True)
        self._tab_bar.columnconfigure(index, weight=1)
        child.grid(in_=self._content, row=0, column=0, sticky="nsew")
        self._content.columnconfigure(0, weight=1)
        self._content.rowconfigure(0, weight=1)
        self._tabs.append((child, tab_frame, tab_label))
        tab_frame.bind("<Button-1>", lambda _event, selected=index: self.select(selected))
        tab_label.bind("<Button-1>", lambda _event, selected=index: self.select(selected))
        if self._selected == -1:
            self.select(0)

    def select(self, tab: int | tk.Misc | None = None) -> tk.Misc | None:
        if tab is None:
            return self._tabs[self._selected][0] if self._selected >= 0 else None
        index = self.index(tab)
        if index == self._selected:
            return self._tabs[index][0]
        self._selected = index
        for current_index, (child, frame, label) in enumerate(self._tabs):
            selected = current_index == index
            frame.configure(background=self._colors["surface"] if selected else self._colors["background"])
            label.configure(
                background=self._colors["surface"] if selected else self._colors["background"],
                foreground=self._colors["accent"] if selected else self._colors["muted"],
            )
            if selected:
                child.tkraise()
        if self._tab_changed_callback is not None:
            event = tk.Event()
            event.widget = self
            self._tab_changed_callback(event)
        return self._tabs[index][0]

    def index(self, tab: int | tk.Misc) -> int:
        if isinstance(tab, int):
            if 0 <= tab < len(self._tabs):
                return tab
            raise tk.TclError("tab index out of range")
        for index, (child, _frame, _label) in enumerate(self._tabs):
            if child == tab:
                return index
        raise tk.TclError("unknown tab")

    def apply_theme(self, colors: dict[str, str]) -> None:
        self._colors = colors
        self.configure(background=colors["background"])
        self._tab_bar.configure(background=colors["background"])
        self._content.configure(background=colors["background"])
        for index, (_child, frame, label) in enumerate(self._tabs):
            selected = index == self._selected
            background = colors["surface"] if selected else colors["background"]
            frame.configure(background=background, highlightbackground=colors["border"], highlightcolor=colors["border"])
            label.configure(background=background, foreground=colors["accent"] if selected else colors["muted"])


class Tooltip:
    """Small delayed hover explanation used by the inline help badges."""

    def __init__(self, widget: tk.Misc, text: str, owner: object) -> None:
        self.widget = widget
        self.text = text
        self.owner = owner
        self.window: tk.Toplevel | None = None
        self._job: str | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def _schedule(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self._cancel()
        self._job = self.widget.after(350, self.show)

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    def show(self) -> None:
        self._job = None
        if self.window is not None or not self.widget.winfo_exists():
            return
        colors = getattr(self.owner, "COLORS")
        window = tk.Toplevel(self.widget)
        self.window = window
        window.wm_overrideredirect(True)
        window.attributes("-topmost", True)
        window.configure(background=colors["border"])
        label = tk.Label(
            window,
            text=self.text,
            justify="left",
            anchor="w",
            wraplength=330,
            padx=10,
            pady=7,
            background=colors["surface_alt"],
            foreground=colors["text"],
            font=("Microsoft YaHei UI", 9),
        )
        label.pack(padx=1, pady=1)
        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        window.geometry(f"+{x}+{y}")

    def hide(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        self._cancel()
        if self.window is not None:
            self.window.destroy()
            self.window = None


class HelpBadge(tk.Canvas):
    """A tiny circled question mark that does not change row height."""

    def __init__(self, parent: tk.Misc, owner: object, text: str) -> None:
        self.owner = owner
        super().__init__(
            parent,
            width=17,
            height=17,
            highlightthickness=0,
            bd=0,
            relief="flat",
            cursor="question_arrow",
            background=getattr(owner, "COLORS")["surface"],
        )
        self._draw()
        Tooltip(self, text, owner)

    def _draw(self) -> None:
        colors = getattr(self.owner, "COLORS")
        self.delete("all")
        self.configure(background=colors["surface"])
        self.create_oval(1, 1, 16, 16, outline=colors["muted"], width=1)
        self.create_text(8.5, 8.5, text="?", fill=colors["muted"], font=("Segoe UI", 8, "bold"))

    def apply_theme(self) -> None:
        self._draw()


class RoundedScrollbar(tk.Canvas):
    """A lightweight rectangular scrollbar with consistent drag geometry."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        owner: "ControlPanel",
        command: Callable[..., object],
        orient: str = "vertical",
        **_kwargs: object,
    ) -> None:
        if orient != "vertical":
            raise ValueError("RoundedScrollbar only supports vertical scrolling")
        self.owner = owner
        self.command = command
        self.first = 0.0
        self.last = 1.0
        self._thumb_bounds = (0, 0, 0, 0)
        self._hover = False
        self._last_drag_target: float | None = None
        super().__init__(
            master,
            width=12,
            highlightthickness=0,
            borderwidth=0,
            relief="flat",
            takefocus=0,
            cursor="hand2",
        )
        owner._rounded_scrollbars.append(self)
        self.bind("<Configure>", lambda _event: self._draw(), add="+")
        self.bind("<Button-1>", self._press, add="+")
        self.bind("<B1-Motion>", self._drag, add="+")
        self.bind("<MouseWheel>", self._wheel, add="+")
        self.bind("<Enter>", self._enter, add="+")
        self.bind("<Leave>", self._leave, add="+")
        self.bind("<Button-4>", lambda _event: self._scroll(-3), add="+")
        self.bind("<Button-5>", lambda _event: self._scroll(3), add="+")
        self._drag_offset = 0
        self._track_color = owner.COLORS["surface_alt"]
        self._thumb_color = owner.COLORS["muted"]
        self._thumb_active_color = owner.COLORS["accent"]
        self._track_id = self.create_rectangle(0, 0, 0, 0, outline="", fill=self._track_color)
        self._thumb_id = self.create_rectangle(0, 0, 0, 0, outline="", fill=self._thumb_color)
        self.apply_theme(owner.COLORS)

    def apply_theme(self, colors: dict[str, str]) -> None:
        self.configure(background=colors["surface_alt"])
        self._track_color = colors["surface_alt"]
        self._thumb_color = colors["muted"]
        self._thumb_active_color = colors["accent"]
        self.itemconfigure(self._track_id, fill=self._track_color)
        self._update_thumb_color()
        self._draw()

    def set(self, first: str | float, last: str | float) -> None:
        self.first = float(first)
        self.last = float(last)
        self._draw()

    def get(self) -> tuple[float, float]:
        return self.first, self.last

    def _draw(self, _event: tk.Event[tk.Misc] | None = None) -> None:
        if not self.winfo_exists():
            return
        width = max(self.winfo_width(), 12)
        height = max(self.winfo_height(), 1)
        self.coords(self._track_id, 0, 0, width, height)
        self.itemconfigure(self._track_id, state="normal")
        visible = max(0.0, min(1.0, self.last - self.first))
        if visible >= 0.999:
            self._thumb_bounds = (0, 0, 0, 0)
            self.itemconfigure(self._thumb_id, state="hidden")
            return
        thumb_height = max(26, int(visible * height))
        thumb_height = min(height, thumb_height)
        travel = max(height - thumb_height, 0)
        top = int(max(0.0, min(1.0, self.first)) * travel)
        bottom = top + thumb_height
        left, right = 1, max(width - 1, 2)
        self.coords(self._thumb_id, left, top, right, bottom)
        self.itemconfigure(self._thumb_id, state="normal")
        self._thumb_bounds = (left, top, right, bottom)

    def _update_thumb_color(self) -> None:
        if hasattr(self, "_thumb_id"):
            color = self._thumb_active_color if self._hover else self._thumb_color
            self.itemconfigure(self._thumb_id, fill=color)

    def _press(self, event: tk.Event[tk.Misc]) -> None:
        left, top, right, bottom = self._thumb_bounds
        self._last_drag_target = None
        if bottom > top and left <= event.x <= right and top <= event.y <= bottom:
            self._drag_offset = event.y - top
            return
        self._scroll(-1 if bottom <= top or event.y < top else 1, pages=True)

    def _drag(self, event: tk.Event[tk.Misc]) -> None:
        if self.last - self.first >= 0.999:
            return
        height = max(self.winfo_height(), 1)
        visible = self.last - self.first
        thumb_height = min(height, max(26, int(visible * height)))
        travel = max(height - thumb_height, 1)
        target = (event.y - self._drag_offset) / travel
        target = max(0.0, min(1.0 - visible, target))
        if self._last_drag_target is not None and abs(target - self._last_drag_target) < 0.0005:
            return
        self._last_drag_target = target
        self.command("moveto", target)

    def _wheel(self, event: tk.Event[tk.Misc]) -> str:
        units = -3 if event.delta > 0 else 3
        self._scroll(units)
        return "break"

    def _enter(self, _event: tk.Event[tk.Misc]) -> None:
        self._hover = True
        self._update_thumb_color()

    def _leave(self, _event: tk.Event[tk.Misc]) -> None:
        self._hover = False
        self._update_thumb_color()

    def _scroll(self, amount: int, pages: bool = False) -> None:
        self.command("scroll", amount, "pages" if pages else "units")


class ControlPanel(tk.Tk):
    THEMES = {
        "morandi": {
            "background": "#F3F4EE",
            "surface": "#EDEBE3",
            "surface_alt": "#E8E2DA",
            "input": "#EFEFF4",
            "border": "#E4E7EE",
            "text": "#34363A",
            "muted": "#73777D",
            "accent": "#586572",
            "accent_active": "#46535F",
            "accent_soft": "#E4E7EE",
            "amber": "#A96860",
            "amber_active": "#8F504A",
            "amber_soft": "#F1DEDB",
            "danger": "#B85650",
            "danger_active": "#C96961",
            "danger_soft": "#F2D8D4",
            "button_active": "#E4E7EE",
            "button_pressed": "#D8DCE5",
            "log": "#EFEFF4",
        },
        "dark": {
            "background": "#2D2D39",
            "surface": "#35343D",
            "surface_alt": "#3D3B40",
            "input": "#434343",
            "border": "#4A4A4A",
            "text": "#F3F4EE",
            "muted": "#A59C95",
            "accent": "#D9DEE3",
            "accent_active": "#EEF0F2",
            "accent_soft": "#5E685F",
            "amber": "#D58B84",
            "amber_active": "#E7A29A",
            "amber_soft": "#5B3D3D",
            "danger": "#F08A83",
            "danger_active": "#FFAAA2",
            "danger_soft": "#633A3A",
            "button_active": "#687480",
            "button_pressed": "#7D8892",
            "log": "#35343D",
        },
    }
    COLORS = THEMES["morandi"]

    def __init__(self) -> None:
        enable_windows_dpi_awareness()
        super().__init__()
        self._icon_image = self._load_window_icon()
        if self._icon_image is not None:
            self.iconphoto(True, self._icon_image)
        if ICON_ICO_PATH.is_file():
            try:
                self.iconbitmap(default=str(ICON_ICO_PATH))
            except tk.TclError:
                pass
        self.title(f"{APP_TITLE} | 控制台")
        self.geometry(DEFAULT_WINDOW_GEOMETRY)
        self.minsize(960, 700)
        self.values = load_env_file(ENV_FILE)
        self.presets = load_presets(PRESETS_FILE)
        self.theme_name = load_theme(THEME_FILE)
        self.COLORS = dict(self.THEMES[self.theme_name])
        self.log_queue: queue.Queue[str] = queue.Queue()
        self._scrollbar_visibility_job: str | None = None
        self._scroll_job: str | None = None
        self._pending_scroll_units = 0
        self._help_badges: list[HelpBadge] = []
        self._rounded_scrollbars: list[RoundedScrollbar] = []
        self._help_window: tk.Toplevel | None = None
        self.bot = ServiceProcess("bot", BOT_SCRIPT, self.log_queue)
        self.bridge = ServiceProcess("bridge", BRIDGE_SCRIPT, self.log_queue)
        self.napcat_process: subprocess.Popen[bytes] | None = None
        self._status_probe_in_flight = False
        self._model_detection_generation = {"chat": 0, "vision": 0}
        self._model_detection_jobs: dict[str, str | None] = {"chat": None, "vision": None}
        self._diagnostics_generation = 0
        self._diagnostics_timeout_job: str | None = None
        self._update_busy = False
        self._required_update = False
        self._build_ui()
        self.after_idle(self._set_default_splitter_position)
        self.after(200, self._drain_logs)
        self.after(1000, self._refresh_status)
        self.protocol("WM_DELETE_WINDOW", self._close)

    @staticmethod
    def _load_window_icon() -> tk.PhotoImage | None:
        """Load the bundled icon while keeping direct-run startup optional."""
        if not ICON_PATH.is_file():
            return None
        try:
            return tk.PhotoImage(file=str(ICON_PATH))
        except tk.TclError:
            return None

    def _value(self, key: str, default: str = "") -> str:
        return self.values.get(key, os.environ.get(key, default)).strip()

    def _active_value(self, key: str, legacy_key: str, target_type: str, default: str = "") -> str:
        if key in self.values or key in os.environ:
            return self._value(key, default)
        if self._value("ACTIVE_TARGET_TYPE", "private").lower() == target_type:
            return self._value(legacy_key, default)
        return default

    def toggle_theme(self) -> None:
        self.theme_name = "dark" if self.theme_name == "morandi" else "morandi"
        self.COLORS = dict(self.THEMES[self.theme_name])
        save_theme(THEME_FILE, self.theme_name)
        self._apply_theme_styles()
        self._append_log(f"已切换到{'夜间' if self.theme_name == 'dark' else '莫兰迪浅色'}模式")

    def _apply_theme_styles(self) -> None:
        style = self.style
        colors = self.COLORS
        self.configure(background=colors["background"])
        style.configure("TFrame", background=colors["surface"])
        style.configure("App.TFrame", background=colors["background"])
        style.configure("Surface.TFrame", background=colors["surface"])
        style.configure("Command.TFrame", background=colors["surface_alt"])
        style.configure("Title.TLabel", background=colors["background"], foreground=colors["text"])
        style.configure("Eyebrow.TLabel", background=colors["background"], foreground=colors["accent"])
        style.configure("Subtitle.TLabel", background=colors["background"], foreground=colors["muted"])
        style.configure("Form.TLabel", background=colors["surface"], foreground=colors["text"])
        style.configure("Hint.TLabel", background=colors["surface"], foreground=colors["muted"])
        style.configure("CommandHint.TLabel", background=colors["surface_alt"], foreground=colors["muted"])
        style.configure("Section.TLabelframe", background=colors["surface"], foreground=colors["border"], bordercolor=colors["border"])
        style.configure("Section.TLabelframe.Label", background=colors["surface"], foreground=colors["accent"])
        style.configure("TLabel", background=colors["surface"], foreground=colors["text"])
        style.configure("TEntry", fieldbackground=colors["input"], foreground=colors["text"], bordercolor=colors["input"], lightcolor=colors["input"], darkcolor=colors["input"], borderwidth=0, relief="flat")
        style.map(
            "TEntry",
            bordercolor=[("focus", colors["border"])],
            lightcolor=[("focus", colors["border"])],
            darkcolor=[("focus", colors["border"])],
        )
        style.configure("TCombobox", fieldbackground=colors["input"], foreground=colors["text"], bordercolor=colors["input"], lightcolor=colors["input"], darkcolor=colors["input"], borderwidth=0, relief="flat")
        style.map(
            "TCombobox",
            bordercolor=[("focus", colors["border"])],
            lightcolor=[("focus", colors["border"])],
            darkcolor=[("focus", colors["border"])],
        )
        style.map("TCombobox", fieldbackground=[("readonly", colors["input"])], foreground=[("readonly", colors["text"])])
        style.configure("TButton", background=colors["surface"], foreground=colors["text"], bordercolor=colors["surface"], lightcolor=colors["surface"], darkcolor=colors["surface"], borderwidth=0, relief="flat")
        style.map("TButton", background=[("active", colors["button_active"]), ("pressed", colors["button_pressed"])], foreground=[("disabled", colors["muted"])])
        style.configure("Primary.TButton", background=colors["accent"], foreground=colors["background"], bordercolor=colors["accent"], lightcolor=colors["accent"], darkcolor=colors["accent"], borderwidth=0, relief="flat")
        style.map("Primary.TButton", background=[("active", colors["accent_active"]), ("pressed", colors["accent"])])
        style.configure("Danger.TButton", background=colors["danger_soft"], foreground=colors["danger"], bordercolor=colors["danger_soft"], lightcolor=colors["danger_soft"], darkcolor=colors["danger_soft"], borderwidth=0, relief="flat")
        style.map("Danger.TButton", background=[("active", colors["danger_active"]), ("pressed", colors["danger"])])
        style.configure("TCheckbutton", background=colors["surface"], foreground=colors["text"], indicatorcolor=colors["input"], indicatormargin=(2, 2, 6, 2))
        style.map("TCheckbutton", background=[("active", colors["surface"])], foreground=[("active", colors["accent"])], indicatorcolor=[("selected", colors["accent"]), ("pressed", colors["accent_active"]), ("active", colors["border"])])
        style.configure("Horizontal.TProgressbar", troughcolor=colors["surface_alt"], background=colors["accent"], bordercolor=colors["surface_alt"], lightcolor=colors["accent"], darkcolor=colors["accent"], thickness=6)
        self._configure_notebook_style()
        self._configure_flat_control_layouts(style)
        style.configure("Status.TLabel", background=colors["surface_alt"], foreground=colors["muted"])
        style.configure("StatusOnline.TLabel", background=colors["accent_soft"], foreground=colors["accent"])
        style.configure("StatusOffline.TLabel", background=colors["danger_soft"], foreground=colors["danger"])
        style.configure("StatusInfo.TLabel", background=colors["amber_soft"], foreground=colors["amber"])
        style.configure(
            "Panel.Vertical.TScrollbar",
            width=8,
            troughcolor=colors["surface_alt"],
            background=colors["muted"],
            bordercolor=colors["surface_alt"],
            lightcolor=colors["muted"],
            darkcolor=colors["muted"],
            arrowcolor=colors["muted"],
            gripcount=0,
            relief="flat",
        )
        style.map(
            "Panel.Vertical.TScrollbar",
            background=[("active", colors["accent"]), ("pressed", colors["accent"])],
        )
        style.layout(
            "Panel.Vertical.TScrollbar",
            [
                (
                    "Vertical.Scrollbar.trough",
                    {
                        "sticky": "ns",
                        "children": [("Vertical.Scrollbar.thumb", {"sticky": "nswe"})],
                    },
                )
            ],
        )
        if hasattr(self, "log"):
            self.log.configure(background=colors["log"], foreground=colors["text"], insertbackground=colors["accent"], selectbackground=colors["accent_soft"])
        if hasattr(self, "main_splitter"):
            self.main_splitter.configure(background=colors["background"])
        for scrollbar in getattr(self, "_rounded_scrollbars", []):
            if scrollbar.winfo_exists():
                scrollbar.apply_theme(colors)
        for canvas in getattr(self, "_tab_canvases", []):
            canvas.configure(background=colors["background"])
        if hasattr(self, "notebook"):
            self.notebook.apply_theme(colors)
        for badge in getattr(self, "_help_badges", []):
            if badge.winfo_exists():
                badge.apply_theme()
        if hasattr(self, "theme_button"):
            self.theme_button.configure(text="切换夜间" if self.theme_name == "morandi" else "切换浅色")

    def _configure_notebook_style(self) -> None:
        colors = self.COLORS
        self.style.configure(
            "TNotebook",
            background=colors["background"],
            borderwidth=0,
            tabmargins=(0, 0, 0, 0),
        )
        self.style.configure(
            "TNotebook.Tab",
            background=colors["background"],
            foreground=colors["muted"],
            padding=(18, 9),
            borderwidth=0,
            width=14,
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        # The default clam layout inserts Notebook.focus, which draws the
        # dotted focus rectangle around the selected tab.
        self.style.layout(
            "TNotebook.Tab",
            [
                (
                    "Notebook.tab",
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                "Notebook.padding",
                                {
                                    "side": "top",
                                    "sticky": "nswe",
                                    "children": [("Notebook.label", {"side": "top", "sticky": ""})],
                                },
                            )
                        ],
                    },
                )
            ],
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", colors["surface"])],
            foreground=[("selected", colors["accent"])],
        )

    @staticmethod
    def _configure_flat_control_layouts(style: ttk.Style) -> None:
        """Remove legacy focus frames while keeping keyboard focus functional."""
        style.layout(
            "TButton",
            [
                (
                    "Button.border",
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                "Button.padding",
                                {"sticky": "nswe", "children": [("Button.label", {"sticky": "nswe"})]},
                            )
                        ],
                    },
                )
            ],
        )
        style.layout(
            "TCheckbutton",
            [
                (
                    "Checkbutton.padding",
                    {
                        "sticky": "nswe",
                        "children": [
                            ("Checkbutton.indicator", {"side": "left", "sticky": ""}),
                            ("Checkbutton.label", {"side": "left", "sticky": "nswe"}),
                        ],
                    },
                )
            ],
        )
        style.layout(
            "TEntry",
            [
                (
                    "Entry.field",
                    {
                        "sticky": "nswe",
                        "children": [
                            (
                                "Entry.padding",
                                {"sticky": "nswe", "children": [("Entry.textarea", {"sticky": "nswe"})]},
                            )
                        ],
                    },
                )
            ],
        )
        style.layout(
            "TCombobox",
            [
                (
                    "Combobox.field",
                    {
                        "sticky": "nswe",
                        "children": [
                            ("Combobox.downarrow", {"side": "right", "sticky": "ns"}),
                            (
                                "Combobox.padding",
                                {"sticky": "nswe", "children": [("Combobox.textarea", {"sticky": "nswe"})]},
                            ),
                        ],
                    },
                )
            ],
        )

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        self.style = style
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        colors = self.COLORS
        self.configure(background=colors["background"])
        style.configure("TFrame", background=colors["surface"])
        style.configure("App.TFrame", background=colors["background"])
        style.configure("Surface.TFrame", background=colors["surface"])
        style.configure("Command.TFrame", background=colors["surface_alt"])
        style.configure("Title.TLabel", background=colors["background"], foreground=colors["text"], font=("Microsoft YaHei UI", 22, "bold"))
        style.configure("Eyebrow.TLabel", background=colors["background"], foreground=colors["accent"], font=("Cascadia Mono", 9, "bold"))
        style.configure("Subtitle.TLabel", background=colors["background"], foreground=colors["muted"], font=("Microsoft YaHei UI", 10))
        style.configure("Form.TLabel", background=colors["surface"], foreground=colors["text"], font=("Microsoft YaHei UI", 10))
        style.configure("Hint.TLabel", background=colors["surface"], foreground=colors["muted"], font=("Microsoft YaHei UI", 9))
        style.configure("CommandHint.TLabel", background=colors["surface_alt"], foreground=colors["muted"], font=("Microsoft YaHei UI", 9))
        style.configure("Section.TLabelframe", background=colors["surface"], foreground=colors["border"], bordercolor=colors["border"], relief="solid", borderwidth=1)
        style.configure("Section.TLabelframe.Label", background=colors["surface"], foreground=colors["accent"], font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TLabel", background=colors["surface"], foreground=colors["text"])
        style.configure("TEntry", fieldbackground=colors["input"], foreground=colors["text"], bordercolor=colors["input"], lightcolor=colors["input"], darkcolor=colors["input"], borderwidth=0, relief="flat", padding=(8, 6))
        style.map(
            "TEntry",
            bordercolor=[("focus", colors["border"])],
            lightcolor=[("focus", colors["border"])],
            darkcolor=[("focus", colors["border"])],
        )
        style.configure("TCombobox", fieldbackground=colors["input"], foreground=colors["text"], bordercolor=colors["input"], lightcolor=colors["input"], darkcolor=colors["input"], borderwidth=0, relief="flat", padding=(7, 5))
        style.map(
            "TCombobox",
            bordercolor=[("focus", colors["border"])],
            lightcolor=[("focus", colors["border"])],
            darkcolor=[("focus", colors["border"])],
        )
        style.map("TCombobox", fieldbackground=[("readonly", colors["input"])], foreground=[("readonly", colors["text"])])
        style.configure("TButton", background=colors["surface"], foreground=colors["text"], bordercolor=colors["surface"], lightcolor=colors["surface"], darkcolor=colors["surface"], borderwidth=0, relief="flat", padding=(12, 7), font=("Microsoft YaHei UI", 9))
        style.map("TButton", background=[("active", colors["button_active"]), ("pressed", colors["button_pressed"])], foreground=[("disabled", colors["muted"])])
        style.configure("Primary.TButton", background=colors["accent"], foreground=colors["background"], bordercolor=colors["accent"], lightcolor=colors["accent"], darkcolor=colors["accent"], borderwidth=0, relief="flat", padding=(14, 7), font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Primary.TButton", background=[("active", colors["accent_active"]), ("pressed", colors["accent"])])
        style.configure("Danger.TButton", background=colors["danger_soft"], foreground=colors["danger"], bordercolor=colors["danger_soft"], lightcolor=colors["danger_soft"], darkcolor=colors["danger_soft"], borderwidth=0, relief="flat", padding=(12, 7))
        style.map("Danger.TButton", background=[("active", colors["danger_active"]), ("pressed", colors["danger"])])
        style.configure("TCheckbutton", background=colors["surface"], foreground=colors["text"], indicatorcolor=colors["input"], indicatormargin=(2, 2, 6, 2), font=("Microsoft YaHei UI", 9))
        style.map("TCheckbutton", background=[("active", colors["surface"])], foreground=[("active", colors["accent"])], indicatorcolor=[("selected", colors["accent"]), ("pressed", colors["accent_active"]), ("active", colors["border"])])
        style.configure("Horizontal.TProgressbar", troughcolor=colors["surface_alt"], background=colors["accent"], bordercolor=colors["surface_alt"], lightcolor=colors["accent"], darkcolor=colors["accent"], thickness=6)
        self._configure_notebook_style()
        self._configure_flat_control_layouts(style)
        style.configure("Status.TLabel", background=colors["surface_alt"], foreground=colors["muted"], padding=(12, 10), font=("Cascadia Mono", 9))
        style.configure("StatusOnline.TLabel", background=colors["accent_soft"], foreground=colors["accent"], padding=(12, 10), font=("Cascadia Mono", 9, "bold"))
        style.configure("StatusOffline.TLabel", background=colors["danger_soft"], foreground=colors["danger"], padding=(12, 10), font=("Cascadia Mono", 9, "bold"))
        style.configure("StatusInfo.TLabel", background=colors["amber_soft"], foreground=colors["amber"], padding=(12, 10), font=("Cascadia Mono", 9))
        style.configure(
            "Panel.Vertical.TScrollbar",
            width=8,
            troughcolor=colors["surface_alt"],
            background=colors["muted"],
            bordercolor=colors["surface_alt"],
            lightcolor=colors["muted"],
            darkcolor=colors["muted"],
            arrowcolor=colors["muted"],
            gripcount=0,
            relief="flat",
        )
        style.map(
            "Panel.Vertical.TScrollbar",
            background=[("active", colors["accent"]), ("pressed", colors["accent"])],
        )
        style.layout(
            "Panel.Vertical.TScrollbar",
            [
                (
                    "Vertical.Scrollbar.trough",
                    {
                        "sticky": "ns",
                        "children": [("Vertical.Scrollbar.thumb", {"sticky": "nswe"})],
                    },
                )
            ],
        )
        outer = ttk.Frame(self, padding=(24, 20, 24, 18), style="App.TFrame")
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=1)
        header = ttk.Frame(outer, style="App.TFrame")
        header.grid(row=0, column=0, sticky="ew")
        header_top = ttk.Frame(header, style="App.TFrame")
        header_top.pack(fill="x")
        ttk.Label(header_top, text="LOCAL OPERATOR CONSOLE", style="Eyebrow.TLabel").pack(side="left")
        header_updates = ttk.Frame(header_top, style="App.TFrame")
        header_updates.pack(side="right")
        self.update_check_button = ttk.Button(header_updates, text="检查更新", command=self.check_updates)
        self.update_check_button.pack(side="left", padx=(0, 8))
        self.theme_button = ttk.Button(header_top, command=self.toggle_theme)
        self.theme_button.pack(side="right")
        ttk.Button(header_top, text="操作说明", command=self.open_help).pack(side="right", padx=(0, 8))
        ttk.Label(header, text=APP_TITLE, style="Title.TLabel").pack(anchor="w", pady=(3, 0))
        subtitle = ttk.Label(
            outer,
            text="模型、QQ 通道和本地服务。保存只写配置，启动与重启都由你明确触发。",
            style="Subtitle.TLabel",
        )
        subtitle.grid(row=1, column=0, sticky="w", pady=(4, 14))

        actions = ttk.Frame(outer, style="Command.TFrame", padding=(10, 8))
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        primary_actions = ttk.Frame(actions, style="Command.TFrame")
        primary_actions.grid(row=0, column=0, sticky="w")
        ttk.Button(primary_actions, text="启动全部", command=self.start_all).pack(side="left")
        ttk.Button(primary_actions, text="启动 NapCat", command=self.start_napcat).pack(side="left", padx=(8, 0))
        ttk.Button(primary_actions, text="重启全部", command=self.restart_all).pack(side="left", padx=(8, 0))
        ttk.Button(primary_actions, text="停止全部", command=self.stop_all, style="Danger.TButton").pack(side="left", padx=(8, 0))
        actions.columnconfigure(0, weight=1)

        self.main_splitter = tk.PanedWindow(
            outer,
            orient="vertical",
            showhandle=False,
            handlesize=0,
            # Resize panes live so the log area follows the pointer naturally.
            # The sash itself uses the surrounding background, so it stays
            # visually unobtrusive while retaining a usable hit area.
            opaqueresize=True,
            sashwidth=5,
            sashpad=0,
            sashrelief="flat",
            sashcursor="sb_v_double_arrow",
            borderwidth=0,
            relief="flat",
            background=colors["background"],
        )
        self.main_splitter.grid(row=3, column=0, sticky="nsew")
        settings_pane = ttk.Frame(self.main_splitter, style="App.TFrame")
        bottom_pane = ttk.Frame(self.main_splitter, style="App.TFrame")
        settings_pane.columnconfigure(0, weight=1)
        settings_pane.rowconfigure(0, weight=1)
        bottom_pane.columnconfigure(0, weight=1)
        bottom_pane.rowconfigure(1, weight=1)
        self.main_splitter.add(settings_pane, stretch="always")
        self.main_splitter.add(bottom_pane, stretch="always")
        tab_area = ttk.Frame(settings_pane, style="App.TFrame")
        tab_area.grid(row=0, column=0, sticky="nsew")
        tab_area.columnconfigure(0, weight=1)
        tab_area.rowconfigure(0, weight=1)
        notebook = FixedTabNotebook(tab_area, colors)
        self.notebook = notebook
        notebook.grid(row=0, column=0, sticky="nsew")
        scrollbar = RoundedScrollbar(
            tab_area,
            owner=self,
            orient="vertical",
            command=lambda *args: self._active_canvas.yview(*args),
        )
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        model_tab, model_content, model_canvas = self._scrollable_tab(notebook)
        behavior_tab, behavior_content, behavior_canvas = self._scrollable_tab(notebook)
        network_tab, network_content, network_canvas = self._scrollable_tab(notebook)
        self._tab_canvases = [model_canvas, behavior_canvas, network_canvas]
        self._active_canvas = model_canvas
        self._settings_scrollbar = scrollbar
        for canvas in self._tab_canvases:
            canvas.configure(yscrollcommand=scrollbar.set)
        self.bind_all("<MouseWheel>", self._scroll_event, add="+")
        self.bind_all("<Button-4>", self._scroll_event, add="+")
        self.bind_all("<Button-5>", self._scroll_event, add="+")
        self.bind_all("<ButtonPress-2>", self._middle_scroll_start, add="+")
        self.bind_all("<B2-Motion>", self._middle_scroll_drag, add="+")
        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        notebook.add(model_tab, text="模型与识图")
        notebook.add(behavior_tab, text="回复与记忆")
        notebook.add(network_tab, text="连接与服务")
        self.settings = model_content

        model = ttk.LabelFrame(self.settings, text="模型连接", padding=14, style="Section.TLabelframe")
        model.pack(fill="x")
        model.columnconfigure(1, weight=1)
        self.preset = tk.StringVar()
        self._label(model, 0, 0, "模型预设", "MODEL_PRESET")
        self.preset_box = ttk.Combobox(model, textvariable=self.preset, values=[*sorted(self.presets), "+"], state="readonly")
        self.preset_box.grid(row=0, column=1, sticky="ew", pady=4)
        self.preset_box.bind("<<ComboboxSelected>>", self._preset_selected)
        preset_bar = ttk.Frame(model)
        preset_bar.grid(row=1, column=1, columnspan=2, sticky="w", pady=(2, 7))
        ttk.Button(preset_bar, text="保存为新预设", command=self.save_preset).pack(side="left", padx=(0, 6))
        ttk.Button(preset_bar, text="重命名", command=self.rename_preset).pack(side="left", padx=(0, 6))
        ttk.Button(preset_bar, text="删除", command=self.delete_preset, style="Danger.TButton").pack(side="left")
        self.api_key = self._entry(model, 2, "API Key", "LLM_API_KEY", secret=True)
        self.base_url = self._entry(model, 3, "Base URL", "LLM_BASE_URL")
        self.model = self._model_entry(model, 4, "模型", "LLM_MODEL", self.detect_chat_models)
        self.max_tokens = self._entry(model, 5, "输出预算", "LLM_MAX_TOKENS", "1024")
        self.timeout = self._entry(model, 6, "超时秒数", "LLM_TIMEOUT_SECONDS", "60")

        vision = ttk.LabelFrame(self.settings, text="图片识图", padding=14, style="Section.TLabelframe")
        vision.pack(fill="x", pady=(10, 0))
        vision.columnconfigure(1, weight=1)
        self.vision_mode = self._combo(vision, 0, 0, "识图模式", "VISION_MODE", ("off", "direct", "separate"), "off")
        self.vision_api_key = self._entry(vision, 1, "视觉 API Key", "VISION_API_KEY", secret=True)
        self.vision_base_url = self._entry(vision, 2, "视觉 Base URL", "VISION_BASE_URL")
        self.vision_model = self._model_entry(vision, 3, "视觉模型", "VISION_MODEL", self.detect_vision_models)
        self.vision_max_tokens = self._entry(vision, 4, "视觉输出预算", "VISION_MAX_TOKENS", "512")
        self.vision_timeout = self._entry(vision, 5, "视觉超时秒数", "VISION_TIMEOUT_SECONDS", "30")
        ttk.Label(vision, text="separate 会先描述图片，再交给主聊天模型；视觉 Key 和地址留空时复用主模型。", style="Hint.TLabel").grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self.settings = behavior_content
        behavior = ttk.LabelFrame(self.settings, text="回复与记忆", padding=14, style="Section.TLabelframe")
        behavior.pack(fill="x", pady=(10, 0))
        behavior.columnconfigure(1, weight=1)
        behavior.columnconfigure(3, weight=1)
        self.group_mode = self._combo(behavior, 0, 0, "群聊模式", "GROUP_MODE", ("mention", "smart", "all", "off"), "mention")
        self.decision_mode = self._combo(behavior, 0, 2, "智能判断", "DECISION_MODE", ("heuristic", "model"), "heuristic")
        self.group_allowlist = self._entry(behavior, 1, "群聊白名单", "GROUP_ALLOWLIST", input_columnspan=5)
        self.bot_qq = self._entry(behavior, 2, "Bot QQ", "BOT_QQ")
        self.bot_names = self._entry(behavior, 3, "Bot 名称", "BOT_NAMES", input_columnspan=5)
        self.debounce = self._combo(behavior, 4, 0, "防抖延迟", "DEBOUNCE_SECONDS", ("random", "3", "4", "5", "6"), "random")
        self.followup = self._entry(behavior, 4, "继续话题秒数", "FOLLOWUP_SECONDS", "120", column=2)
        self.context_messages = self._entry(behavior, 5, "上下文条数", "CONTEXT_MESSAGES", "20")
        self.memory_db = self._entry(behavior, 5, "持久化记忆库", "MEMORY_DB", "", column=2, input_columnspan=2)
        self.reaction_mode = self._combo(behavior, 6, 2, "表情回应", "REACTION_MODE", ("off", "like"), "off")
        self.typing = tk.BooleanVar(value=self._value("TYPING_STATUS", "true").lower() in {"1", "true", "yes", "on"})
        self._checkbutton(behavior, 6, 0, "显示输入状态", self.typing, columnspan=1)
        self.reply_to_message = tk.BooleanVar(
            value=self._value("REPLY_TO_MESSAGE", "true").lower() in {"1", "true", "yes", "on"}
        )
        self._checkbutton(behavior, 6, 1, "引用回复", self.reply_to_message, "REPLY_TO_MESSAGE", columnspan=1)
        self.tools_enabled = tk.BooleanVar(value=self._value("TOOLS_ENABLED", "false").lower() in {"1", "true", "yes", "on"})
        self._tool_selector(behavior, 7, 0)
        self.active_interval = self._entry(behavior, 8, "主动消息间隔(分钟)", "ACTIVE_INTERVAL_MINUTES", "60")
        self.active_private_enabled = tk.BooleanVar(
            value=self._active_value("ACTIVE_PRIVATE_ENABLED", "ACTIVE_ENABLED", "private", "false").lower()
            in {"1", "true", "yes", "on"}
        )
        self._checkbutton(behavior, 9, 0, "启用私聊主动消息", self.active_private_enabled, "ACTIVE_PRIVATE_ENABLED")
        self.active_private_target_id = self._entry(
            behavior, 9, "私聊目标 QQ", "ACTIVE_PRIVATE_TARGET_ID",
            self._active_value("ACTIVE_PRIVATE_TARGET_ID", "ACTIVE_TARGET_ID", "private"),
            column=2, input_columnspan=3,
        )
        self.active_private_prompt = self._entry(
            behavior, 10, "私聊主动提示", "ACTIVE_PRIVATE_PROMPT",
            self._active_value("ACTIVE_PRIVATE_PROMPT", "ACTIVE_PROMPT", "private"),
            input_columnspan=5,
        )
        self.active_group_enabled = tk.BooleanVar(
            value=self._active_value("ACTIVE_GROUP_ENABLED", "ACTIVE_ENABLED", "group", "false").lower()
            in {"1", "true", "yes", "on"}
        )
        self._checkbutton(behavior, 11, 0, "启用群聊主动消息", self.active_group_enabled, "ACTIVE_GROUP_ENABLED")
        self.active_group_target_id = self._entry(
            behavior, 11, "群聊目标群号", "ACTIVE_GROUP_TARGET_ID",
            self._active_value("ACTIVE_GROUP_TARGET_ID", "ACTIVE_TARGET_ID", "group"),
            column=2, input_columnspan=3,
        )
        self.active_group_prompt = self._entry(
            behavior, 12, "群聊主动提示", "ACTIVE_GROUP_PROMPT",
            self._active_value("ACTIVE_GROUP_PROMPT", "ACTIVE_PROMPT", "group"),
            input_columnspan=5,
        )
        self.persona = self._entry(behavior, 13, "Persona 文件", "PERSONA_FILE", "", input_columnspan=3)
        self.emoji_catalog = self._entry(behavior, 15, "表情词典文件", "EMOJI_CATALOG", "", input_columnspan=3)
        ttk.Button(
            behavior,
            text="编辑词典",
            command=self.edit_emoji_catalog,
        ).grid(row=15, column=4, padx=(8, 4), pady=5, sticky="w")
        ttk.Button(
            behavior,
            text="选择",
            command=lambda: self._select_path(self.memory_db, "选择本地记忆库"),
        ).grid(row=5, column=5, padx=(0, 4), pady=5, sticky="w")
        ttk.Button(
            behavior,
            text="选择",
            command=lambda: self._select_path(self.persona, "选择 Persona 文件"),
        ).grid(row=13, column=4, padx=(0, 4), pady=5, sticky="w")
        ttk.Button(
            behavior,
            text="编辑 Persona",
            command=self.edit_persona,
        ).grid(row=13, column=5, padx=(0, 4), pady=5, sticky="w")

        self.settings = network_content
        network = ttk.LabelFrame(self.settings, text="服务与 Token", padding=14, style="Section.TLabelframe")
        network.pack(fill="x", pady=(10, 0))
        network.columnconfigure(1, weight=1)
        network.columnconfigure(3, weight=1)
        self.napcat_url = self._entry(network, 0, "NapCat API", "NAPCAT_API_URL", DEFAULT_NAPCAT_API_URL)
        self.napcat_access = self._entry(network, 1, "NapCat Access Token", "NAPCAT_ACCESS_TOKEN", secret=True)
        self.event_token = self._entry(network, 2, "HTTP Client Token（事件上报）", "NAPCAT_EVENT_TOKEN", secret=True)
        self.service_token = self._entry(network, 3, "Bot 服务 Token", "BOT_SERVICE_TOKEN", secret=True)
        ttk.Button(network, text="生成", command=self.generate_bot_token).grid(row=3, column=2, padx=(8, 0), pady=4)
        self.bridge_port = self._entry(network, 4, "Bridge 端口", "BRIDGE_PORT", "8766")
        self.bot_host = self._entry(network, 4, "Bot 服务地址", "BOT_SERVICE_HOST", "127.0.0.1", column=2)
        self.bot_port = self._entry(network, 5, "Bot 端口", "BOT_SERVICE_PORT", "8765")
        self.napcat_boot = self._entry(network, 6, "NapCat 启动程序", "NAPCAT_BOOT")
        self.napcat_qq = self._entry(network, 7, "QQ 程序", "NAPCAT_QQ")
        self.napcat_hook = self._entry(network, 8, "NapCat Hook", "NAPCAT_HOOK")
        self.supabase_url = self._entry(network, 9, "Supabase URL", "SUPABASE_URL")
        self.supabase_key = self._entry(network, 10, "Supabase Secret Key", "SUPABASE_SECRET_KEY", secret=True)
        self.supabase_timeout = self._entry(network, 11, "Supabase 超时秒数", "SUPABASE_TIMEOUT_SECONDS", "10", column=2)
        self.remote_memory_mode = self._combo(network, 12, 0, "远端记忆", "REMOTE_MEMORY_MODE", ("local_first", "coordinated"), "local_first")
        self.summary_enabled = tk.BooleanVar(value=self._value("SUMMARY_ENABLED", "false").lower() in {"1", "true", "yes", "on"})
        self._checkbutton(network, 13, 0, "启用自动摘要", self.summary_enabled, "SUMMARY_ENABLED", columnspan=2)
        self.summary_min_messages = self._entry(network, 14, "摘要触发条数", "SUMMARY_MIN_MESSAGES", "40")
        self.summary_delay = self._entry(network, 14, "摘要等待秒数", "SUMMARY_DELAY_SECONDS", "10", column=2)
        ttk.Button(network, text="选择", command=self._select_napcat_boot).grid(row=6, column=2, padx=(8, 0), pady=4)
        ttk.Button(network, text="选择", command=lambda: self._select_path(self.napcat_qq, "选择 QQ 程序")).grid(row=7, column=2, padx=(8, 0), pady=4)
        ttk.Button(network, text="选择", command=lambda: self._select_path(self.napcat_hook, "选择 NapCat Hook")).grid(row=8, column=2, padx=(8, 0), pady=4)

        utility_actions = ttk.Frame(settings_pane, style="Command.TFrame", padding=(10, 8))
        utility_actions.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        utility_buttons = ttk.Frame(utility_actions, style="Command.TFrame")
        utility_buttons.pack(side="right")
        ttk.Button(utility_buttons, text="保存配置", command=self.save_config, style="Primary.TButton").pack(side="left")
        ttk.Button(utility_buttons, text="备份配置", command=self.backup_config).pack(side="left", padx=(8, 0))
        ttk.Button(utility_buttons, text="恢复配置", command=self.restore_config).pack(side="left", padx=(8, 0))
        self.diagnostics_button = ttk.Button(utility_buttons, text="一键诊断", command=self.run_diagnostics)
        self.diagnostics_button.pack(side="left", padx=(8, 0))

        status = ttk.LabelFrame(bottom_pane, text="运行状态", padding=(8, 10), style="Section.TLabelframe")
        status.grid(row=0, column=0, sticky="ew")
        self.bot_status = self._status(status, 0, "Bot")
        self.bridge_status = self._status(status, 1, "Bridge")
        self.vision_status = self._status(status, 2, "识图")
        self.napcat_status = self._status(status, 3, "NapCat")

        log_shell = ttk.Frame(bottom_pane, style="Surface.TFrame")
        log_shell.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        log_header = ttk.Frame(log_shell, style="Surface.TFrame")
        log_header.pack(fill="x", pady=(0, 6))
        ttk.Label(log_header, text="实时日志", style="Section.TLabelframe.Label").pack(side="left")
        ttk.Button(log_header, text="清空日志", command=self.clear_log).pack(side="right")
        log_frame = ttk.LabelFrame(log_shell, text="", padding=(10, 8), style="Section.TLabelframe")
        log_frame.pack(fill="both", expand=True)
        self.log = tk.Text(
            log_frame,
            height=9,
            wrap="none",
            state="disabled",
            font=("Cascadia Mono", 10),
            background=self.COLORS["log"],
            foreground=self.COLORS["text"],
            insertbackground=self.COLORS["accent"],
            selectbackground=self.COLORS["accent_soft"],
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=8,
        )
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log.grid(row=0, column=0, sticky="nsew")
        self.log_scrollbar = RoundedScrollbar(
            log_frame,
            owner=self,
            orient="vertical",
            command=self.log.yview,
        )
        self.log_scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))
        self.log.configure(yscrollcommand=self.log_scrollbar.set)
        self.log.bind("<MouseWheel>", self._log_scroll_event, add="+")
        self.log.bind("<Button-4>", lambda _event: self._log_scroll(-3), add="+")
        self.log.bind("<Button-5>", lambda _event: self._log_scroll(3), add="+")
        self._apply_theme_styles()
        self.after_idle(self._update_scrollbar_visibility)

    def _log_scroll(self, units: int) -> str:
        self.log.yview_scroll(units, "units")
        return "break"

    def _log_scroll_event(self, event: tk.Event[tk.Misc]) -> str:
        return self._log_scroll(-3 if event.delta > 0 else 3)

    def _update_scrollbar_visibility(self, canvas: tk.Canvas | None = None) -> None:
        if not hasattr(self, "_settings_scrollbar"):
            return
        target = canvas or self._active_canvas
        if target is not self._active_canvas:
            return
        bbox = target.bbox("all")
        scrollable = bool(bbox and bbox[3] - bbox[1] > target.winfo_height() + 1)
        if scrollable:
            self._settings_scrollbar.grid()
        else:
            self._settings_scrollbar.grid_remove()
        if scrollable:
            self._settings_scrollbar.set(*target.yview())

    def _schedule_scrollbar_visibility(self, canvas: tk.Canvas | None = None) -> None:
        if not hasattr(self, "_settings_scrollbar"):
            return
        target = canvas or self._active_canvas
        if target is not self._active_canvas or self._scrollbar_visibility_job is not None:
            return
        # A vertical sash drag can emit dozens of Configure events per second.
        # Give the canvas a short quiet period before measuring its scrollable
        # area again, keeping the drag responsive without hiding the scrollbar.
        self._scrollbar_visibility_job = self.after(45, self._run_scrollbar_visibility)

    def _run_scrollbar_visibility(self) -> None:
        self._scrollbar_visibility_job = None
        self._update_scrollbar_visibility()

    def _scroll_event(self, event: tk.Event[tk.Misc]) -> str | None:
        canvas = self._active_canvas
        pointer_x = canvas.winfo_pointerx()
        pointer_y = canvas.winfo_pointery()
        inside = (
            canvas.winfo_rootx() <= pointer_x <= canvas.winfo_rootx() + canvas.winfo_width()
            and canvas.winfo_rooty() <= pointer_y <= canvas.winfo_rooty() + canvas.winfo_height()
        )
        if not inside:
            return None
        delta = getattr(event, "delta", 0)
        direction = -1 if delta > 0 or getattr(event, "num", 0) == 4 else 1
        units = max(1, abs(delta) // 120) if delta else 1
        self._pending_scroll_units += direction * units
        if self._scroll_job is None:
            self._scroll_job = self.after_idle(self._flush_scroll)
        return "break"

    def _flush_scroll(self) -> None:
        self._scroll_job = None
        units = max(-12, min(12, self._pending_scroll_units))
        self._pending_scroll_units = 0
        if units:
            self._active_canvas.yview_scroll(units, "units")

    def _middle_scroll_start(self, _event: tk.Event[tk.Misc]) -> str | None:
        canvas = self._active_canvas
        pointer_x = canvas.winfo_pointerx()
        pointer_y = canvas.winfo_pointery()
        if not (
            canvas.winfo_rootx() <= pointer_x <= canvas.winfo_rootx() + canvas.winfo_width()
            and canvas.winfo_rooty() <= pointer_y <= canvas.winfo_rooty() + canvas.winfo_height()
        ):
            return None
        canvas.scan_mark(pointer_x - canvas.winfo_rootx(), pointer_y - canvas.winfo_rooty())
        return "break"

    def _middle_scroll_drag(self, _event: tk.Event[tk.Misc]) -> str | None:
        canvas = self._active_canvas
        pointer_x = canvas.winfo_pointerx()
        pointer_y = canvas.winfo_pointery()
        canvas.scan_dragto(pointer_x - canvas.winfo_rootx(), pointer_y - canvas.winfo_rooty(), gain=1)
        return "break"

    def _scrollable_tab(self, notebook: FixedTabNotebook) -> tuple[ttk.Frame, ttk.Frame, tk.Canvas]:
        tab = ttk.Frame(notebook.content_parent(), style="App.TFrame")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(0, weight=1)
        canvas = tk.Canvas(tab, highlightthickness=0, borderwidth=0, background=self.COLORS["background"])
        canvas.grid(row=0, column=0, sticky="nsew")
        content = ttk.Frame(canvas, padding=(0, 12, 14, 12), style="App.TFrame")
        window = canvas.create_window((0, 0), window=content, anchor="nw")
        content_width = -1

        def update_region(_event: tk.Event[tk.Misc] | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))
            self._schedule_scrollbar_visibility(canvas)

        content.bind("<Configure>", update_region)

        def resize_canvas(event: tk.Event[tk.Misc]) -> None:
            nonlocal content_width
            if event.width != content_width:
                content_width = event.width
                canvas.itemconfigure(window, width=event.width)
            self._schedule_scrollbar_visibility(canvas)

        canvas.bind("<Configure>", resize_canvas)
        return tab, content, canvas

    def _on_tab_changed(self, _event: tk.Event[tk.Misc]) -> None:
        notebook = _event.widget
        index = notebook.index(notebook.select())
        self._active_canvas = self._tab_canvases[index]
        self._schedule_scrollbar_visibility()

    def _label(self, parent: ttk.Frame, row: int, column: int, text: str, help_key: str = "") -> None:
        holder = ttk.Frame(parent, style="Surface.TFrame")
        holder.grid(row=row, column=column, sticky="w", padx=(0, 10), pady=5)
        ttk.Label(holder, text=text, style="Form.TLabel").pack(side="left")
        help_text = HELP_TEXTS.get(help_key, "")
        if help_text:
            badge = HelpBadge(holder, self, help_text)
            badge.pack(side="left", padx=(4, 0))
            self._help_badges.append(badge)

    def _checkbutton(
        self,
        parent: ttk.Frame,
        row: int,
        column: int,
        text: str,
        variable: tk.BooleanVar,
        help_key: str = "",
        columnspan: int = 2,
    ) -> None:
        holder = ttk.Frame(parent, style="Surface.TFrame")
        holder.grid(row=row, column=column, columnspan=columnspan, sticky="w", pady=4)
        ttk.Checkbutton(holder, text=text, variable=variable).pack(side="left")
        help_text = HELP_TEXTS.get(help_key, "")
        if help_text:
            badge = HelpBadge(holder, self, help_text)
            badge.pack(side="left", padx=(4, 0))
            self._help_badges.append(badge)

    def _tool_selector(self, parent: ttk.Frame, row: int, column: int) -> None:
        """Render known safe tools as checkboxes and keep the env format stable."""
        holder = ttk.Frame(parent, style="Surface.TFrame")
        holder.grid(row=row, column=column, columnspan=6, sticky="ew", pady=4)
        ttk.Checkbutton(holder, text="启用白名单工具", variable=self.tools_enabled).pack(side="left")
        badge = HelpBadge(holder, self, HELP_TEXTS["TOOLS_ENABLED"])
        badge.pack(side="left", padx=(4, 10))
        ttk.Label(holder, text="允许：", style="Hint.TLabel").pack(side="left")

        configured = {
            item.strip().lower()
            for item in self._value("TOOL_ALLOWLIST", "get_time").split(",")
            if item.strip()
        }
        self._unknown_tools = configured.difference(TOOL_LABELS)
        self._tool_vars: dict[str, tk.BooleanVar] = {}
        for name, label in TOOL_LABELS.items():
            variable = tk.BooleanVar(value=name in configured)
            self._tool_vars[name] = variable
            ttk.Checkbutton(
                holder,
                text=label,
                variable=variable,
                command=self._sync_tool_allowlist,
            ).pack(side="left", padx=(0, 8))

        self.tool_allowlist = tk.StringVar(value="")
        self._sync_tool_allowlist()
        self._help_badges.append(badge)

    def _sync_tool_allowlist(self) -> None:
        selected = [name for name, variable in self._tool_vars.items() if variable.get()]
        selected.extend(sorted(self._unknown_tools))
        self.tool_allowlist.set(",".join(selected))

    def _entry(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        key: str,
        default: str = "",
        secret: bool = False,
        column: int = 0,
        input_columnspan: int = 1,
    ) -> tk.StringVar:
        self._label(parent, row, column, label, key)
        variable = tk.StringVar(value=self._value(key, default))
        ttk.Entry(parent, textvariable=variable, show="*" if secret else "").grid(
            row=row,
            column=column + 1,
            columnspan=input_columnspan,
            sticky="ew",
            padx=(0, 18) if column == 0 else (0, 0),
            pady=5,
        )
        return variable

    def _model_entry(self, parent: ttk.Frame, row: int, label: str, key: str, detect: Callable[[], None]) -> tk.StringVar:
        self._label(parent, row, 0, label, key)
        variable = tk.StringVar(value=self._value(key))
        box = ttk.Combobox(parent, textvariable=variable, state="normal")
        box.grid(row=row, column=1, sticky="ew", pady=5)
        button = ttk.Button(parent, text="检测模型", command=detect)
        button.grid(row=row, column=2, padx=(10, 0), pady=5)
        if key == "LLM_MODEL":
            self.model_box = box
            self.model_detect_button = button
        else:
            self.vision_model_box = box
            self.vision_detect_button = button
        return variable

    def _combo(self, parent: ttk.Frame, row: int, column: int, label: str, key: str, values: tuple[str, ...], default: str) -> tk.StringVar:
        self._label(parent, row, column, label, key)
        internal_value = self._value(key, default)
        labels = OPTION_LABELS.get(key, {})
        display_values = tuple(labels.get(value, value) for value in values)
        display_value = labels.get(internal_value, internal_value)
        variable = tk.StringVar(value=internal_value)
        display_variable = tk.StringVar(value=display_value)
        box = ttk.Combobox(
            parent,
            textvariable=display_variable,
            values=display_values,
            state="readonly",
        )
        box.grid(row=row, column=column + 1, sticky="ew", padx=(0, 18) if column == 0 else (0, 0), pady=5)
        reverse = {label: value for value, label in zip(values, display_values)}
        box.bind("<<ComboboxSelected>>", lambda _event: variable.set(reverse.get(display_variable.get(), display_variable.get())))
        variable.trace_add("write", lambda *_args: display_variable.set(labels.get(variable.get(), variable.get())))
        return variable

    def open_help(self) -> None:
        if self._help_window is not None and self._help_window.winfo_exists():
            self._help_window.deiconify()
            self._help_window.lift()
            return

        colors = self.COLORS
        window = tk.Toplevel(self)
        self._help_window = window
        if self._icon_image is not None:
            window.iconphoto(True, self._icon_image)
        if ICON_ICO_PATH.is_file():
            try:
                window.iconbitmap(default=str(ICON_ICO_PATH))
            except tk.TclError:
                pass
        window.title(f"{APP_TITLE} | 操作说明")
        window.geometry("980x680")
        window.minsize(760, 520)
        window.configure(background=colors["background"])
        window.protocol("WM_DELETE_WINDOW", lambda: self._close_help(window))
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)

        header = tk.Frame(window, background=colors["background"], padx=24, pady=18)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(header, text="操作说明", background=colors["background"], foreground=colors["text"], font=("Microsoft YaHei UI", 20, "bold")).pack(anchor="w")
        tk.Label(header, text="按左侧步骤配置即可，不需要直接编辑配置文件。", background=colors["background"], foreground=colors["muted"], font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(4, 0))

        rule = tk.Frame(window, background=colors["border"], height=1)
        rule.grid(row=1, column=0, sticky="ew")

        body = tk.Frame(window, background=colors["background"], padx=24, pady=18)
        body.grid(row=2, column=0, sticky="nsew")
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)

        sidebar = tk.Frame(body, background=colors["surface"], padx=10, pady=10, width=210)
        sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 14))
        sidebar.grid_propagate(False)
        tk.Label(sidebar, text="目录", background=colors["surface"], foreground=colors["accent"], font=("Microsoft YaHei UI", 10, "bold"), anchor="w").pack(fill="x", padx=6, pady=(2, 8))
        listbox = tk.Listbox(
            sidebar,
            # Keep the directory selection when the document Text widget gets
            # the focus and the user selects text on the right.
            exportselection=False,
            activestyle="none",
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            selectborderwidth=0,
            background=colors["surface"],
            foreground=colors["text"],
            selectbackground=colors["accent_soft"],
            selectforeground=colors["text"],
            font=("Microsoft YaHei UI", 10),
        )
        listbox.pack(fill="both", expand=True)
        for title, _content in HELP_SECTIONS:
            listbox.insert("end", title)

        document = tk.Frame(body, background=colors["surface"], padx=18, pady=16)
        document.grid(row=0, column=1, sticky="nsew")
        document.columnconfigure(0, weight=1)
        document.rowconfigure(1, weight=1)
        section_title = tk.Label(document, text="", background=colors["surface"], foreground=colors["accent"], anchor="w", font=("Microsoft YaHei UI", 14, "bold"))
        section_title.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        text = tk.Text(
            document,
            wrap="word",
            relief="flat",
            borderwidth=0,
            padx=4,
            pady=2,
            background=colors["surface"],
            foreground=colors["text"],
            insertbackground=colors["accent"],
            selectbackground=colors["accent_soft"],
            font=("Microsoft YaHei UI", 10),
            spacing1=2,
            spacing3=5,
        )
        text.grid(row=1, column=0, sticky="nsew")
        document_scrollbar = RoundedScrollbar(
            document,
            owner=self,
            orient="vertical",
            command=text.yview,
        )
        document_scrollbar.grid(row=1, column=1, sticky="ns", padx=(10, 0))
        text.configure(yscrollcommand=document_scrollbar.set)

        def render(_event: tk.Event[tk.Misc] | None = None) -> None:
            selection = listbox.curselection()
            if not selection:
                return
            index = selection[0]
            title, content = HELP_SECTIONS[index]
            section_title.configure(text=title)
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("1.0", content)
            text.configure(state="disabled")
            text.yview_moveto(0)

        listbox.bind("<<ListboxSelect>>", render)
        listbox.selection_set(0)
        render()

        footer = tk.Frame(window, background=colors["background"], padx=24, pady=18)
        footer.grid(row=3, column=0, sticky="ew")
        tk.Button(footer, text="关闭", command=lambda: self._close_help(window), relief="flat", bd=0, padx=16, pady=6, background=colors["accent"], foreground=colors["background"], activebackground=colors["accent_active"], activeforeground=colors["background"], font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")

    def _close_help(self, window: tk.Toplevel) -> None:
        if window.winfo_exists():
            window.destroy()
        if self._help_window is window:
            self._help_window = None

    def _status(self, parent: ttk.Frame, column: int, label: str) -> ttk.Label:
        parent.columnconfigure(column, weight=1)
        widget = ttk.Label(parent, text=f"{label} · 检查中", style="StatusInfo.TLabel")
        widget.grid(row=0, column=column, sticky="ew", padx=(0, 8))
        return widget

    def _current_config(self) -> dict[str, str]:
        return {
            "LLM_API_KEY": self.api_key.get().strip(), "LLM_BASE_URL": self.base_url.get().strip(), "LLM_MODEL": self.model.get().strip(),
            "LLM_MAX_TOKENS": self.max_tokens.get().strip(), "LLM_TIMEOUT_SECONDS": self.timeout.get().strip(),
            "VISION_MODE": self.vision_mode.get().strip(), "VISION_API_KEY": self.vision_api_key.get().strip(), "VISION_BASE_URL": self.vision_base_url.get().strip(),
            "VISION_MODEL": self.vision_model.get().strip(), "VISION_MAX_TOKENS": self.vision_max_tokens.get().strip(), "VISION_TIMEOUT_SECONDS": self.vision_timeout.get().strip(),
            "GROUP_MODE": self.group_mode.get().strip(), "DECISION_MODE": self.decision_mode.get().strip(), "GROUP_ALLOWLIST": self.group_allowlist.get().strip(), "BOT_QQ": self.bot_qq.get().strip(), "BOT_NAMES": self.bot_names.get().strip(),
            "DEBOUNCE_SECONDS": self.debounce.get().strip(), "FOLLOWUP_SECONDS": self.followup.get().strip(), "CONTEXT_MESSAGES": self.context_messages.get().strip(), "MEMORY_DB": self.memory_db.get().strip(),
            "REACTION_MODE": self.reaction_mode.get().strip(),
            "REPLY_TO_MESSAGE": "true" if self.reply_to_message.get() else "false",
            "ACTIVE_INTERVAL_MINUTES": self.active_interval.get().strip(),
            "ACTIVE_PRIVATE_ENABLED": "true" if self.active_private_enabled.get() else "false",
            "ACTIVE_PRIVATE_TARGET_ID": self.active_private_target_id.get().strip(),
            "ACTIVE_PRIVATE_PROMPT": self.active_private_prompt.get().strip(),
            "ACTIVE_GROUP_ENABLED": "true" if self.active_group_enabled.get() else "false",
            "ACTIVE_GROUP_TARGET_ID": self.active_group_target_id.get().strip(),
            "ACTIVE_GROUP_PROMPT": self.active_group_prompt.get().strip(),
            "TOOLS_ENABLED": "true" if self.tools_enabled.get() else "false", "TOOL_ALLOWLIST": self.tool_allowlist.get().strip(),
            "EMOJI_CATALOG": self.emoji_catalog.get().strip(),
            "TYPING_STATUS": "true" if self.typing.get() else "false", "PERSONA_FILE": self.persona.get().strip(),
            "NAPCAT_API_URL": self.napcat_url.get().strip(), "NAPCAT_ACCESS_TOKEN": self.napcat_access.get().strip(), "NAPCAT_EVENT_TOKEN": self.event_token.get().strip(),
            "BOT_SERVICE_TOKEN": self.service_token.get().strip(), "BOT_SERVICE_HOST": self.bot_host.get().strip(), "BRIDGE_PORT": self.bridge_port.get().strip(), "BOT_SERVICE_PORT": self.bot_port.get().strip(),
            "NAPCAT_BOOT": self.napcat_boot.get().strip(), "NAPCAT_QQ": self.napcat_qq.get().strip(), "NAPCAT_HOOK": self.napcat_hook.get().strip(),
            "SUPABASE_URL": self.supabase_url.get().strip(), "SUPABASE_SECRET_KEY": self.supabase_key.get().strip(),
            "SUPABASE_TIMEOUT_SECONDS": self.supabase_timeout.get().strip(), "REMOTE_MEMORY_MODE": self.remote_memory_mode.get().strip(),
            "SUMMARY_ENABLED": "true" if self.summary_enabled.get() else "false", "SUMMARY_MIN_MESSAGES": self.summary_min_messages.get().strip(),
            "SUMMARY_DELAY_SECONDS": self.summary_delay.get().strip(),
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
                    raise ValueError(f"{key} must be between 1 and 65535")
            service_base_url(values.get("BOT_SERVICE_HOST", "127.0.0.1"), int(values.get("BOT_SERVICE_PORT", "8765")))
            if values.get("DECISION_MODE", "heuristic") not in {"heuristic", "model"}:
                raise ValueError("DECISION_MODE must be heuristic or model")
            if bool(values.get("SUPABASE_URL")) != bool(values.get("SUPABASE_SECRET_KEY")):
                raise ValueError("SUPABASE_URL and SUPABASE_SECRET_KEY must be set together")
            if values.get("SUPABASE_URL") and not values.get("BOT_QQ", "").isdigit():
                raise ValueError("BOT_QQ is required when Supabase memory is enabled")
        except ValueError as exc:
            self._append_log(format_panel_error("保存配置", exc))
            messagebox.showwarning("配置无效", str(exc), parent=self)
            return False
        try:
            save_env_file(ENV_FILE, values)
        except OSError as exc:
            self._append_log(format_panel_error("保存配置", exc))
            messagebox.showerror("配置保存失败", str(exc), parent=self)
            return False
        self.values = values
        self._append_log("配置已保存；服务保持当前状态，未自动重启")
        self._append_log(f"配置文件：{ENV_FILE}")
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
            base, key = self.base_url.get().strip().rstrip("/"), self.api_key.get().strip()
            button = self.model_detect_button
        else:
            base = self.vision_base_url.get().strip().rstrip("/") or self.base_url.get().strip().rstrip("/")
            key = self.vision_api_key.get().strip() or self.api_key.get().strip()
            button = self.vision_detect_button
        if not base or not key:
            self._append_log(
                format_panel_error(
                    f"{kind} 模型检测",
                    "Base URL 或 API Key 为空；请求没有发出",
                )
            )
            return
        if any(base.lower().endswith(suffix) for suffix in ("/chat/completions", "/responses", "/models")):
            self._append_log(
                format_panel_error(
                    f"{kind} 模型检测",
                    "Base URL 填成了具体接口路径，而不是服务根地址",
                )
            )
            return
        if button.instate(["disabled"]):
            return
        endpoint = f"{base}/models"
        self._model_detection_generation[kind] += 1
        generation = self._model_detection_generation[kind]
        button.configure(text="检测中...")
        button.state(["disabled"])
        self._append_log(f"正在检测 {kind} 模型：{endpoint}")
        self._model_detection_jobs[kind] = self.after(
            15000,
            lambda kind=kind, generation=generation: self._model_detection_timeout(kind, generation),
        )
        threading.Thread(target=self._fetch_models, args=(kind, endpoint, key, generation), daemon=True).start()

    def detect_chat_models(self) -> None:
        self._detect_models("chat")

    def detect_vision_models(self) -> None:
        self._detect_models("vision")

    def _fetch_models(self, kind: str, endpoint: str, key: str, generation: int) -> None:
        try:
            request = Request(endpoint, headers={"Accept": "application/json", "Authorization": f"Bearer {key}"}, method="GET")
            with urlopen(request, timeout=12) as response:
                models = parse_model_ids(json.loads(response.read().decode("utf-8")))
            if not models:
                raise ValueError("/models 没有返回 data[].id")
            self.after(0, lambda: self._models_detected(kind, models, generation))
        except HTTPError as exc:
            self.after(0, lambda error=exc: self._models_failed(kind, error, generation))
        except (OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
            self.after(0, lambda error=exc: self._models_failed(kind, error, generation))
        except Exception as exc:
            self.after(0, lambda error=exc: self._models_failed(kind, error, generation))

    def _models_detected(self, kind: str, models: list[str], generation: int) -> None:
        if generation != self._model_detection_generation[kind]:
            return
        box = self.model_box if kind == "chat" else self.vision_model_box
        box.configure(values=models)
        self._finish_model_detection(kind, generation)
        self._append_log(f"{kind} 检测到 {len(models)} 个模型，可从下拉框选择")

    def _models_failed(self, kind: str, reason: BaseException | str, generation: int) -> None:
        if generation != self._model_detection_generation[kind]:
            return
        self._finish_model_detection(kind, generation)
        self._append_log(format_panel_error(f"{kind} 模型检测", reason))
        self._append_log("当前模型输入不会被清空；可以修正地址或密钥后重新检测")

    def _model_detection_timeout(self, kind: str, generation: int) -> None:
        if generation != self._model_detection_generation[kind]:
            return
        button = self.model_detect_button if kind == "chat" else self.vision_detect_button
        if not button.instate(["disabled"]):
            return
        self._finish_model_detection(kind, generation)
        self._append_log(format_panel_error(f"{kind} 模型检测", "请求超过 15 秒仍未完成"))
        self._append_log("如果服务日志同时出现 HTTP 400，请把 Base URL 改成服务根地址，不要带 /chat/completions")

    def _finish_model_detection(self, kind: str, generation: int | None = None) -> None:
        if generation is not None and generation != self._model_detection_generation[kind]:
            return
        job = self._model_detection_jobs[kind]
        if job is not None:
            try:
                self.after_cancel(job)
            except tk.TclError:
                pass
            self._model_detection_jobs[kind] = None
        button = self.model_detect_button if kind == "chat" else self.vision_detect_button
        button.configure(text="检测模型")
        button.state(["!disabled"])

    def start_all(self) -> None:
        if self._required_update:
            message = "当前版本需要先完成强制更新，已暂时阻止启动服务"
            self._append_log(message)
            messagebox.showwarning("需要更新", "检测到强制更新，请点击右上角“检查更新”，然后选择立即更新。", parent=self)
            return
        if not self.save_config():
            return
        env = self._environment()
        bot_port = self._port_value(self.bot_port, 8765)
        bridge_port = self._port_value(self.bridge_port, 8766)
        if bot_port is None or bridge_port is None:
            self._append_log(format_panel_error("启动全部 · 端口配置", "Bot 或 Bridge 端口无效"))
            return
        bot_host = self.bot_host.get().strip() or "127.0.0.1"
        if is_local_service_host(bot_host) and not port_open(bot_port):
            self.bot.start(env)
        elif not is_local_service_host(bot_host):
            self._append_log(f"Bot 服务地址为远程 {bot_host}:{bot_port}，跳过本地 Bot 启动；请确认腾讯云上的 bot_service.py 已运行")
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
        self._append_log("已停止控制台启动的 Bot 和 Bridge；远程 Bot 不会被此按钮停止")

    @staticmethod
    def _port_value(variable: tk.StringVar, default: int) -> int | None:
        return parse_port(variable.get(), default)

    def _select_path(self, variable: tk.StringVar, title: str) -> None:
        selected = filedialog.askopenfilename(parent=self, title=title)
        if selected:
            variable.set(selected)

    def generate_bot_token(self) -> None:
        if self.service_token.get().strip() and not messagebox.askyesno(
            "生成 Bot 服务 Token",
            "生成新的 Token 会让当前运行中的 Bot 和 Bridge 失去认证。\n生成后请保存配置并重启它们。\n\n确定要替换吗？",
            parent=self,
        ):
            return
        self.service_token.set(generate_service_token())
        self._append_log("已生成新的 Bot 服务 Token；请保存配置后重启 Bot 和 Bridge")

    def _select_napcat_boot(self) -> None:
        selected = filedialog.askopenfilename(parent=self, title="选择 NapCat 启动程序")
        if selected:
            self.napcat_boot.set(selected)
            if Path(selected).suffix.lower() in {".bat", ".cmd"}:
                self._append_log("已选择 NapCat launcher，启动时由 launcher 自己查找 QQ")
            else:
                self._autofill_napcat_paths(selected)

    def _autofill_napcat_paths(self, boot: str) -> None:
        """Fill QQ and Hook as soon as a NapCat launcher path is selected."""
        discovered_qq = discover_qq_executable(boot)
        if not self.napcat_qq.get().strip() and discovered_qq:
            self.napcat_qq.set(str(discovered_qq))
            self._append_log(f"已自动找到 QQ：{discovered_qq}")

        hook = Path(boot).parent / "NapCatWinBootHook.dll"
        if not self.napcat_hook.get().strip() and hook.is_file():
            self.napcat_hook.set(str(hook))
            self._append_log(f"已自动找到 NapCat Hook：{hook}")

    def _project_path(self, raw: str) -> Path:
        path = Path(raw.strip())
        return path if path.is_absolute() else ROOT / path

    def _emoji_catalog_seed(self) -> dict[str, dict[str, str]]:
        configured = self.emoji_catalog.get().strip()
        if configured:
            return load_emoji_catalog(str(self._project_path(configured)))
        example = ROOT / "examples" / "emoji_catalog.example.json"
        return load_emoji_catalog(str(example))

    def _emoji_item_dialog(
        self,
        parent: tk.Toplevel,
        current: dict[str, str] | None = None,
        current_name: str = "",
    ) -> tuple[str, dict[str, str]] | None:
        dialog = tk.Toplevel(parent)
        dialog.title("编辑表情")
        dialog.transient(parent)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16, style="Surface.TFrame")
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)
        values = current or {"id": "", "meaning": "", "usage": ""}
        name_var = tk.StringVar(value=current_name)
        id_var = tk.StringVar(value=values.get("id", ""))
        meaning_var = tk.StringVar(value=values.get("meaning", ""))
        usage_var = tk.StringVar(value=values.get("usage", ""))
        for row, label, variable in (
            (0, "名称", name_var),
            (1, "NapCat ID", id_var),
            (2, "含义", meaning_var),
            (3, "使用场景", usage_var),
        ):
            ttk.Label(frame, text=label, style="Form.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            ttk.Entry(frame, textvariable=variable, width=42).grid(row=row, column=1, sticky="ew", pady=5)
        result: list[tuple[str, dict[str, str]] | None] = [None]

        def confirm() -> None:
            name = name_var.get().strip()
            emoji_id = id_var.get().strip()
            if not name or not emoji_id.isdigit():
                messagebox.showwarning("信息不完整", "名称不能为空，NapCat ID 必须是数字。", parent=dialog)
                return
            result[0] = (
                name,
                {
                    "id": emoji_id,
                    "meaning": meaning_var.get().strip(),
                    "usage": usage_var.get().strip(),
                },
            )
            dialog.destroy()

        buttons = ttk.Frame(frame, style="Surface.TFrame")
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(buttons, text="确定", command=confirm).pack(side="right")
        dialog.bind("<Return>", lambda _event: confirm())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        self._apply_theme_styles()
        self.wait_window(dialog)
        return result[0]

    def edit_emoji_catalog(self) -> None:
        """Edit semantic reactions without asking the user to hand-edit JSON."""
        dialog = tk.Toplevel(self)
        dialog.title("表情回应词典")
        dialog.transient(self)
        dialog.geometry("760x500")
        dialog.minsize(620, 400)
        dialog.grab_set()
        outer = ttk.Frame(dialog, padding=16, style="Surface.TFrame")
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(
            outer,
            text="给每个表情起一个名字，模型会根据含义选择名字，程序再转换成 NapCat ID。",
            style="Hint.TLabel",
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 10))
        columns = ("name", "id", "meaning", "usage")
        tree = ttk.Treeview(outer, columns=columns, show="headings", selectmode="browse")
        headings = {"name": "名称", "id": "NapCat ID", "meaning": "含义", "usage": "使用场景"}
        widths = {"name": 110, "id": 100, "meaning": 220, "usage": 280}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], minwidth=70, anchor="w")
        tree.grid(row=1, column=0, sticky="nsew")
        scroll = RoundedScrollbar(
            outer,
            owner=self,
            orient="vertical",
            command=tree.yview,
        )
        scroll.grid(row=1, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        catalog = self._emoji_catalog_seed()

        def refresh() -> None:
            tree.delete(*tree.get_children())
            for name, item in catalog.items():
                tree.insert("", "end", iid=name, values=(name, item["id"], item["meaning"], item["usage"]))

        def edit_selected() -> None:
            selected = tree.selection()
            if not selected:
                messagebox.showinfo("先选择表情", "请选择一行再编辑。", parent=dialog)
                return
            old_name = selected[0]
            result = self._emoji_item_dialog(dialog, catalog[old_name], old_name)
            if result is None:
                return
            new_name, item = result
            if new_name != old_name and new_name in catalog:
                messagebox.showwarning("名称重复", "这个表情名称已经存在。", parent=dialog)
                return
            if new_name != old_name:
                del catalog[old_name]
            catalog[new_name] = item
            refresh()
            tree.selection_set(new_name)

        def add_item() -> None:
            result = self._emoji_item_dialog(dialog)
            if result is None:
                return
            name, item = result
            if name in catalog:
                messagebox.showwarning("名称重复", "这个表情名称已经存在。", parent=dialog)
                return
            catalog[name] = item
            refresh()
            tree.selection_set(name)

        def remove_item() -> None:
            selected = tree.selection()
            if not selected:
                return
            name = selected[0]
            if messagebox.askyesno("删除表情", f"确定删除“{name}”吗？", parent=dialog):
                catalog.pop(name, None)
                refresh()

        def save_catalog() -> None:
            raw_path = self.emoji_catalog.get().strip() or ".local/emoji_catalog.json"
            path = self._project_path(raw_path)
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            except OSError as exc:
                self._append_log(format_panel_error("表情词典保存", exc))
                messagebox.showerror("保存失败", str(exc), parent=dialog)
                return
            self.emoji_catalog.set(raw_path)
            if self.save_config():
                self._append_log(f"表情词典已保存到 {path}")
                dialog.destroy()

        actions = ttk.Frame(outer, style="Surface.TFrame")
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="新增", command=add_item).pack(side="left")
        ttk.Button(actions, text="编辑", command=edit_selected).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="删除", command=remove_item).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(actions, text="保存词典", command=save_catalog).pack(side="right", padx=(0, 8))
        refresh()
        self._apply_theme_styles()
        self.wait_window(dialog)

    def edit_persona(self) -> None:
        """Edit the persona prompt in a bounded text editor inside the console."""
        raw_path = self.persona.get().strip() or ".local/persona_prompt.txt"
        path = self._project_path(raw_path)
        try:
            content = path.read_text(encoding="utf-8") if path.is_file() else ""
        except (OSError, UnicodeError) as exc:
            self._append_log(format_panel_error("读取 Persona", exc))
            messagebox.showerror("读取 Persona 失败", str(exc), parent=self)
            return
        dialog = tk.Toplevel(self)
        dialog.title("编辑 Persona")
        dialog.transient(self)
        dialog.geometry("760x560")
        dialog.minsize(560, 400)
        dialog.grab_set()
        outer = ttk.Frame(dialog, padding=16, style="Surface.TFrame")
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(1, weight=1)
        outer.columnconfigure(0, weight=1)
        ttk.Label(
            outer,
            text="这里填写稳定的人设、说话方式和边界。事实性兴趣请写进记忆，不要让模型自行补全。",
            style="Hint.TLabel",
        ).grid(row=0, column=0, sticky="w", pady=(0, 10))
        text = tk.Text(
            outer,
            wrap="word",
            undo=True,
            font=("Microsoft YaHei UI", 10),
            background=self.COLORS["log"],
            foreground=self.COLORS["text"],
            insertbackground=self.COLORS["accent"],
            selectbackground=self.COLORS["accent_soft"],
            relief="flat",
            borderwidth=0,
            padx=10,
            pady=10,
        )
        text.grid(row=1, column=0, sticky="nsew")
        text.insert("1.0", content)

        def save_persona() -> None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text.get("1.0", "end-1c"), encoding="utf-8")
            except OSError as exc:
                self._append_log(format_panel_error("Persona 保存", exc))
                messagebox.showerror("保存失败", str(exc), parent=dialog)
                return
            self.persona.set(raw_path)
            if self.save_config():
                self._append_log(f"Persona 已保存到 {path}")
                dialog.destroy()

        actions = ttk.Frame(outer, style="Surface.TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(side="right")
        ttk.Button(actions, text="保存 Persona", command=save_persona).pack(side="right", padx=(0, 8))
        self._apply_theme_styles()
        self.wait_window(dialog)

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

        boot = self.napcat_boot.get().strip()
        qq = self.napcat_qq.get().strip()
        hook = self.napcat_hook.get().strip()
        launcher_mode = Path(boot).suffix.lower() in {".bat", ".cmd"}
        paths = [boot] if launcher_mode else [boot, qq, hook]
        if not all(paths):
            self._append_log(
                format_panel_error(
                    "NapCat 启动配置",
                    "NapCat 启动程序、QQ 程序或 Hook 路径未填写",
                )
            )
            messagebox.showwarning(
                "NapCat 路径未配置",
                "请先填写 NapCat 启动程序、QQ 程序和 Hook 路径。\n"
                "如果填写的是 launcher.bat，只需要填写 NapCat 启动程序。\n"
                "也可以直接在配置文件中填写 NAPCAT_BOOT、NAPCAT_QQ、NAPCAT_HOOK。",
                parent=self,
            )
            return
        missing = [path for path in paths if not Path(path).is_file()]
        if missing:
            self._append_log(format_panel_error("NapCat 启动配置", "文件不存在：" + ", ".join(missing)))
            messagebox.showwarning(
                "NapCat 文件不存在",
                "下面的路径找不到：\n\n" + "\n".join(missing),
                parent=self,
            )
            return
        boot_path = Path(boot)
        command_boot, command_qq, command_hook = boot, qq, hook
        launch_env = os.environ.copy()
        if launcher_mode:
            discovered_qq = Path(qq) if qq and Path(qq).is_file() else discover_qq_executable(boot)
            direct_hook = Path(hook) if hook else boot_path.parent / "NapCatWinBootHook.dll"
            direct_boot = boot_path.parent / "NapCatWinBootMain.exe"
            if discovered_qq and direct_boot.is_file() and direct_hook.is_file():
                command_qq = str(discovered_qq)
                command_hook = str(direct_hook)
                command = build_napcat_utf8_console_command(
                    build_napcat_nt_command(command_boot, command_qq, command_hook)
                )
                main_path = (boot_path.parent / "napcat.mjs").resolve()
                launch_env.update(
                    {
                        "NAPCAT_PATCH_PACKAGE": str(boot_path.parent / "qqnt.json"),
                        "NAPCAT_LOAD_PATH": str(boot_path.parent / "loadNapCat.js"),
                        "NAPCAT_INJECT_PATH": command_hook,
                        "NAPCAT_LAUNCHER_PATH": str(direct_boot),
                        "NAPCAT_MAIN_PATH": main_path.as_posix(),
                    }
                )
                load_path = boot_path.parent / "loadNapCat.js"
                try:
                    load_path.write_text(
                        f'(async () => {{await import("file:///{main_path.as_posix().lstrip("/")}")}})()\n',
                        encoding="utf-8",
                    )
                except OSError as exc:
                    self._append_log(format_panel_error("NapCat 启动文件准备", exc))
                    messagebox.showerror("NapCat 启动失败", str(exc), parent=self)
                    return
                self._append_log(f"launcher 已自动选择 QQNT：{command_qq}")
            else:
                command = build_napcat_command(command_boot, command_qq, command_hook)
                self._append_log("使用 NapCat launcher，由 launcher 自己查找 QQ")
        else:
            command = build_napcat_command(command_boot, command_qq, command_hook)
        try:
            self.napcat_process = subprocess.Popen(
                command,
                cwd=boot_path.parent,
                env=launch_env,
                creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
            )
        except OSError as exc:
            self._append_log(format_panel_error("NapCat 启动", exc))
            messagebox.showerror("NapCat 启动失败", str(exc), parent=self)
            return
        self._append_log(f"已启动 NapCat：{boot_path}")

    def run_diagnostics(self) -> None:
        if self.diagnostics_button.instate(["disabled"]):
            return
        self.diagnostics_button.configure(text="诊断中...")
        self.diagnostics_button.state(["disabled"])
        self._append_log("开始诊断：不会保存配置，也不会重启服务")
        self._diagnostics_generation += 1
        generation = self._diagnostics_generation
        values = self._current_config()
        values["BRIDGE_PORT"] = self.bridge_port.get().strip()
        values["BOT_SERVICE_HOST"] = self.bot_host.get().strip()
        values["BOT_SERVICE_PORT"] = self.bot_port.get().strip()
        self._diagnostics_timeout_job = self.after(30000, self._diagnostics_timeout)
        threading.Thread(target=self._diagnostics_worker, args=(values, generation), daemon=True).start()

    def _diagnostics_worker(self, values: dict[str, str], generation: int) -> None:
        try:
            checks: list[tuple[str, Callable[[], str]]] = []
            bot_host = values.get("BOT_SERVICE_HOST", "127.0.0.1").strip() or "127.0.0.1"
            bot_port = parse_port(values.get("BOT_SERVICE_PORT", ""), 8765)
            bridge_port = parse_port(values.get("BRIDGE_PORT", ""), 8766)
            if bot_port is not None:
                checks.append(("Bot service", lambda: probe_service(service_base_url(bot_host, bot_port), values.get("BOT_SERVICE_TOKEN", ""))))
            if bridge_port is not None:
                checks.append(("Bridge", lambda: probe_service(f"http://127.0.0.1:{bridge_port}")))
            napcat_url = values.get("NAPCAT_API_URL", "")
            if napcat_url:
                checks.append(("NapCat", lambda: probe_napcat(napcat_url, values.get("NAPCAT_ACCESS_TOKEN", ""))))
            llm_base = values.get("LLM_BASE_URL", "")
            llm_key = values.get("LLM_API_KEY", "")
            if llm_base and llm_key:
                checks.append(("Chat model", lambda: f"发现 {probe_models(llm_base, llm_key)} 个模型"))
            if values.get("VISION_MODE", "off") != "off":
                vision_base = values.get("VISION_BASE_URL", "") or llm_base
                vision_key = values.get("VISION_API_KEY", "") or llm_key
                if vision_base and vision_key:
                    checks.append(("Vision model", lambda: f"发现 {probe_models(vision_base, vision_key)} 个模型"))
                else:
                    checks.append(("Vision model", missing_vision_config))
            for label, check in checks:
                try:
                    result = check()
                    self.after(0, lambda label=label, result=result: self._append_log(f"[通过] {label}: {result}"))
                except (HTTPError, OSError, URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                    self.after(0, lambda label=label, error=exc: self._append_log(format_panel_error(f"诊断 · {label}", error)))
                except Exception as exc:
                    self.after(0, lambda label=label, error=exc: self._append_log(format_panel_error(f"诊断 · {label}", error)))
        except Exception as exc:
            self.after(0, lambda error=exc: self._append_log(format_panel_error("一键诊断流程", error)))
        finally:
            self.after(0, lambda: self._diagnostics_finished(generation))

    def _diagnostics_finished(self, generation: int | None = None) -> None:
        if generation is not None and generation != self._diagnostics_generation:
            return
        if self._diagnostics_timeout_job is not None:
            try:
                self.after_cancel(self._diagnostics_timeout_job)
            except tk.TclError:
                pass
            self._diagnostics_timeout_job = None
        self.diagnostics_button.configure(text="一键诊断")
        self.diagnostics_button.state(["!disabled"])
        self._append_log("诊断完成")

    def _diagnostics_timeout(self) -> None:
        self._diagnostics_timeout_job = None
        if not self.diagnostics_button.instate(["disabled"]):
            return
        generation = self._diagnostics_generation
        self._diagnostics_finished(generation)
        self._append_log(format_panel_error("一键诊断", "诊断超过 30 秒仍未完成"))

    def _refresh_status(self) -> None:
        if self._status_probe_in_flight:
            self.after(2000, self._refresh_status)
            return
        bot_port = self._port_value(self.bot_port, 8765)
        bot_host = self.bot_host.get().strip() or "127.0.0.1"
        bridge_port = self._port_value(self.bridge_port, 8766)
        napcat_url = self.napcat_url.get().strip()
        napcat_port = local_url_port(napcat_url, 3000)
        vision_mode = self.vision_mode.get()
        vision_model = self.vision_model.get()
        napcat_access_token = self.napcat_access.get().strip()
        bot_service_token = self.service_token.get().strip()
        self._status_probe_in_flight = True
        threading.Thread(
            target=self._probe_status_worker,
            args=(bot_host, bot_port, bridge_port, napcat_url, napcat_port, napcat_access_token, bot_service_token, vision_mode, vision_model),
            daemon=True,
        ).start()
        self.after(2000, self._refresh_status)

    def _probe_status_worker(
        self,
        bot_host: str,
        bot_port: int | None,
        bridge_port: int | None,
        napcat_url: str,
        napcat_port: int | None,
        napcat_access_token: str,
        bot_service_token: str,
        vision_mode: str,
        vision_model: str,
    ) -> None:
        bot_is_remote = not is_local_service_host(bot_host)
        bot_running = False
        if bot_port is not None:
            if bot_is_remote:
                try:
                    probe_service(service_base_url(bot_host, bot_port), bot_service_token)
                    bot_running = True
                except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
                    bot_running = False
            else:
                bot_running = port_open(bot_port)
        bridge_running = bridge_port is not None and port_open(bridge_port)
        napcat_running = napcat_port is not None and port_open(napcat_port)
        napcat_api_ready = False
        if napcat_running and napcat_url:
            try:
                probe_napcat(napcat_url, napcat_access_token)
                napcat_api_ready = True
            except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
                napcat_api_ready = False
        self.after(
            0,
            lambda: self._apply_status(
                bot_host,
                bot_port,
                bridge_port,
                bot_running,
                bot_is_remote,
                bridge_running,
                napcat_url,
                napcat_port,
                napcat_running,
                napcat_api_ready,
                vision_mode,
                vision_model,
            ),
        )

    def _apply_status(
        self,
        bot_host: str,
        bot_port: int | None,
        bridge_port: int | None,
        bot_running: bool,
        bot_is_remote: bool,
        bridge_running: bool,
        napcat_url: str,
        napcat_port: int | None,
        napcat_running: bool,
        napcat_api_ready: bool,
        vision_mode: str,
        vision_model: str,
    ) -> None:
        self._status_probe_in_flight = False
        bot_label = "Bot 服务 · 云端可用" if bot_is_remote and bot_running else (
            "Bot 服务 · 云端不可用" if bot_is_remote else ("Bot 服务 · 运行中" if bot_running else ("Bot 服务 · 端口无效" if bot_port is None else "Bot 服务 · 未运行"))
        )
        self.bot_status.configure(
            text=bot_label,
            style="StatusOnline.TLabel" if bot_running else "StatusOffline.TLabel",
        )
        self.bridge_status.configure(
            text="Bridge · 运行中" if bridge_running else ("Bridge · 端口无效" if bridge_port is None else "Bridge · 未运行"),
            style="StatusOnline.TLabel" if bridge_running else "StatusOffline.TLabel",
        )
        vision_text, vision_ready = vision_status(vision_mode, vision_model)
        self.vision_status.configure(
            text=vision_text,
            style="StatusOnline.TLabel" if vision_ready else "StatusInfo.TLabel",
        )
        if not napcat_url:
            self.napcat_status.configure(text="NapCat · 未配置", style="StatusInfo.TLabel")
        elif napcat_port is None:
            self.napcat_status.configure(text="NapCat · 远程地址", style="StatusInfo.TLabel")
        else:
            self.napcat_status.configure(
                text=(
                    "NapCat · API 可用"
                    if napcat_api_ready
                    else ("NapCat · 端口已开但 API 不可用" if napcat_running else "NapCat · 未运行")
                ),
                style="StatusOnline.TLabel" if napcat_api_ready else "StatusOffline.TLabel",
            )

    def _drain_logs(self) -> None:
        messages: list[str] = []
        for _ in range(60):
            try:
                messages.append(self.log_queue.get_nowait())
            except queue.Empty:
                break
        if messages:
            self.log.configure(state="normal")
            self.log.insert("end", "\n".join(messages) + "\n")
            self.log.see("end")
            self.log.configure(state="disabled")
        self.after(200, self._drain_logs)

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_default_splitter_position(self) -> None:
        """Give the settings area more room on first launch while keeping logs visible."""
        try:
            height = self.main_splitter.winfo_height()
            if height <= 0:
                self.after(100, self._set_default_splitter_position)
                return
            position = int(height * 0.68)
            self.main_splitter.sashpos(0, max(360, min(position, height - 250)))
        except tk.TclError:
            return

    def _set_update_controls(self, busy: bool, checking: bool = False) -> None:
        self._update_busy = busy
        self.update_check_button.configure(text="检查中..." if checking else "检查更新")
        state = ["disabled"] if busy else ["!disabled"]
        self.update_check_button.state(state)

    def _git_update_snapshot(self) -> bool:
        status = run_git_command("status", "--porcelain", "--untracked-files=all")
        if status.returncode != 0:
            detail = git_output_tail(status.stdout) or f"Git 返回码 {status.returncode}"
            raise RuntimeError(detail)
        local_changes = bool(status.stdout.strip())
        head = run_git_command("rev-parse", "HEAD")
        if head.returncode != 0:
            detail = git_output_tail(head.stdout) or f"Git 返回码 {head.returncode}"
            raise RuntimeError(detail)
        return local_changes

    def _update_manifest_url(self) -> str:
        configured = self._value("UPDATE_MANIFEST_URL", "").strip()
        return configured or DEFAULT_UPDATE_MANIFEST_URL

    def _fetch_update_manifest(self) -> dict[str, str]:
        request = Request(
            self._update_manifest_url(),
            headers={"Accept": "application/json", "User-Agent": "OneBotLLMBridge-ControlPanel"},
            method="GET",
        )
        with urlopen(request, timeout=12) as response:
            return parse_update_manifest(json.loads(response.read().decode("utf-8")))

    def _release_state(self, manifest: dict[str, str]) -> tuple[bool, str]:
        local_version = parse_release_version(__version__)
        remote_version = parse_release_version(manifest["version"])
        minimum_version = parse_release_version(manifest["min_version"])
        force_required = manifest["update_type"] == "force" and local_version < minimum_version
        if local_version >= remote_version and not force_required:
            return False, f"当前版本 v{__version__}，已是稳定版 v{manifest['version']}"
        update_type = manifest["update_type"]
        labels = {"hot": "热更新", "normal": "普通更新", "force": "强制更新"}
        message = f"发现 v{manifest['version']}（{labels[update_type]}）：{manifest['message']}"
        if update_type == "force":
            message += f"；低于最低版本 v{manifest['min_version']} 的客户端必须更新"
        return True, message

    def _fetch_release_target(self, target_ref: str) -> None:
        fetch = run_git_command("fetch", "origin", "--tags", timeout=90)
        if fetch.returncode != 0:
            detail = git_output_tail(fetch.stdout) or f"Git 返回码 {fetch.returncode}"
            raise RuntimeError(f"获取稳定版本失败：{detail}")
        target = run_git_command("rev-parse", "--verify", f"refs/tags/{target_ref}")
        if target.returncode != 0:
            target = run_git_command("rev-parse", "--verify", target_ref)
        if target.returncode != 0:
            detail = git_output_tail(target.stdout) or f"找不到 {target_ref}"
            raise RuntimeError(f"稳定版本目标不存在：{detail}")

    def check_updates(self) -> None:
        if self._update_busy:
            return
        if not (ROOT / ".git").is_dir():
            message = "当前目录不是 Git 克隆目录，控制台无法在线更新；请首次用 git clone 下载项目。"
            self._append_log(message)
            messagebox.showwarning("无法检查更新", message, parent=self)
            return
        self._set_update_controls(True, checking=True)
        self._append_log("正在检查项目更新：不会修改本地文件，也不会重启服务")
        threading.Thread(target=self._update_worker, args=(False,), daemon=True).start()

    def _start_project_update(self) -> None:
        if self._update_busy:
            return
        if not (ROOT / ".git").is_dir():
            message = "当前目录不是 Git 克隆目录，不能通过控制台更新；请首次用 git clone 下载项目。"
            self._append_log(message)
            messagebox.showwarning("无法更新项目", message, parent=self)
            return
        self._set_update_controls(True)
        self._append_log("正在更新项目：先检查本地改动，再获取远程代码")
        threading.Thread(target=self._update_worker, args=(True,), daemon=True).start()

    def _update_worker(self, apply_update: bool) -> None:
        try:
            manifest = self._fetch_update_manifest()
            available, summary = self._release_state(manifest)
            local_changes = self._git_update_snapshot()
            if not available:
                self.after(0, lambda summary=summary: self._update_finished(summary, False, None))
                return
            if not apply_update:
                if local_changes:
                    summary += "；当前目录有本地代码改动，更新前需要先处理"
                self.after(0, lambda summary=summary, manifest=manifest: self._update_finished(summary, False, manifest))
                return
            if local_changes:
                raise RuntimeError(
                    "检测到本地代码改动，已停止更新。请先提交/暂存这些改动，或确认它们不需要保留后再更新；控制台不会替你删除或覆盖。"
                )
            backup = create_backup(ROOT)
            self._fetch_release_target(manifest["target_ref"])
            target_ref = f"refs/tags/{manifest['target_ref']}"
            target = run_git_command("rev-parse", "--verify", target_ref)
            if target.returncode != 0:
                target_ref = manifest["target_ref"]
            merge = run_git_command("merge", "--ff-only", target_ref, timeout=60)
            if merge.returncode != 0:
                detail = git_output_tail(merge.stdout) or f"Git 返回码 {merge.returncode}"
                raise RuntimeError(f"稳定版本更新失败（备份已保存到 {backup}）：{detail}")
            message = f"已更新到 v{manifest['version']}（{manifest['update_type']}），更新前备份已保存到 {backup}。"
            self.after(0, lambda message=message, manifest=manifest: self._update_finished(message, False, manifest))
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                detail = "Git 操作超时，请检查网络或代理后重试。"
            else:
                detail = str(exc)
            self.after(0, lambda detail=detail: self._update_finished(f"项目更新失败：{detail}", True, None))

    def _update_finished(self, message: str, failed: bool, manifest: dict[str, str] | None) -> None:
        self._set_update_controls(False)
        self._append_log(message)
        if failed:
            messagebox.showwarning("项目更新失败", message, parent=self)
            return
        if not manifest:
            return
        update_type = manifest["update_type"]
        if message.startswith("发现"):
            if update_type == "force":
                self._required_update = True
                self._append_log("这是强制更新：启动服务前必须先完成更新")
            prompt = f"{message}\n\n现在更新项目吗？"
            if update_type == "force":
                prompt += "\n这是强制更新，不更新将无法启动服务。"
            if messagebox.askyesno("发现项目更新", prompt, parent=self):
                self._start_project_update()
            elif update_type == "force":
                messagebox.showwarning("需要更新", "当前版本不再兼容，请在“检查更新”后选择立即更新。", parent=self)
            return
        if update_type == "force" and message.startswith("已更新到"):
            self._required_update = False
            self._append_log("强制更新已完成：正在重启 Bot 和 Bridge，NapCat/QQ 保持运行")
            self.restart_all()
            return
        if update_type == "hot":
            self._append_log("这是热更新：正在只重启 Bot 和 Bridge，NapCat/QQ 保持运行")
            self.restart_all()
        elif update_type == "normal" and message.startswith("已更新到"):
            if messagebox.askyesno("更新完成", "普通更新已完成。现在重启 Bot 和 Bridge 吗？NapCat/QQ 不会被关闭。", parent=self):
                self.restart_all()

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def backup_config(self) -> None:
        try:
            path = create_backup(ROOT)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            self._append_log(format_panel_error("备份配置", exc))
            return
        self._append_log(f"配置备份已创建：{path}")
        messagebox.showinfo("备份完成", f"已备份到：\n{path}", parent=self)

    def restore_config(self) -> None:
        selected = filedialog.askopenfilename(
            parent=self,
            title="选择配置备份",
            filetypes=[("ZIP 备份", "*.zip"), ("所有文件", "*.*")],
        )
        if not selected:
            return
        if not messagebox.askyesno(
            "恢复配置",
            "恢复会覆盖当前本地配置和 Persona/词典文件，运行中的服务不会自动重启。确定继续吗？",
            parent=self,
        ):
            return
        try:
            restored = restore_backup(ROOT, Path(selected))
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            self._append_log(format_panel_error("恢复配置", exc))
            return
        self._append_log(f"已恢复 {len(restored)} 个文件；请重新打开控制台或重新选择配置")
        messagebox.showinfo("恢复完成", "配置已恢复。请检查界面内容，确认后手动重启相关服务。", parent=self)

    def _close(self) -> None:
        if messagebox.askyesno("退出", "是否同时停止本控制台启动的服务？", parent=self):
            self.stop_all()
        self.destroy()


if __name__ == "__main__":
    ControlPanel().mainloop()
