# OneBot LLM Bridge 新手教程

这篇教程面向第一次搭 QQ Bot 的用户。目标不是让你理解所有代码，而是让你知道每一步在做什么、哪里出错应该看哪一项。

## 1. 先理解整体关系

你需要运行三个东西：

```text
NapCat       负责登录 QQ、收发 QQ 消息
Bridge       负责接收消息、判断和组织回复
Bot service  负责调用大模型
```

推荐端口：

| 服务 | 默认端口 | 谁连接谁 |
| --- | ---: | --- |
| NapCat HTTP Server | 3000 | Bridge 连接它发送消息 |
| Bot service | 8765 | Bridge 连接它调用模型 |
| Bridge HTTP Client | 8766 | NapCat 把收到的消息推给它 |

消息实际走向是：

```text
你在 QQ 发消息
  -> NapCat
  -> Bridge:8766/onebot
  -> Bot service:8765/reply
  -> Bridge
  -> NapCat:3000/send_private_msg 或 send_group_msg
  -> QQ 回复
```

端口不同不是问题，反而是正常的。它们是三个不同服务。

## 2. 准备软件

### 必需

- Windows 10/11 或 Linux。
- Python 3.11 或更高版本。
- 已安装并登录 QQ 的 NapCat。
- 一个支持聊天接口的模型 API。

### 模型 API 要准备什么

通常需要三样东西：

1. API Key：类似密码，不能发给别人。
2. Base URL：例如 `https://api.example.com/v1`。
3. Model：例如 `some-chat-model`。

如果服务商说“兼容 OpenAI API”，一般就能使用本项目的 OpenAI-compatible provider。Base URL 通常要以 `/v1` 结尾，但以服务商文档为准。

## 3. 下载和运行项目

```powershell
git clone https://github.com/你的用户名/onebot-llm-bridge.git
Set-Location .\onebot-llm-bridge
```

创建虚拟环境：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

第一版尽量只使用 Python 标准库，避免新手先被依赖安装卡住。以后如果加入第三方依赖，再按仓库提供的 `requirements.txt` 安装。

复制示例配置：

```powershell
Copy-Item .\examples\.env.example .\.env.local
New-Item -ItemType Directory -Force .\.local | Out-Null
```

## 4. 配置 NapCat

打开 NapCat WebUI，进入：

```text
网络配置 -> OneBot11
```

### 4.1 HTTP Server：让 Bridge 能发送消息

新增或启用 HTTP Server：

```text
监听地址：127.0.0.1
端口：3000
Token：自己生成一串随机字符串
```

把它理解成“Bridge 调 NapCat 的电话线路”。控制台的：

```dotenv
NAPCAT_API_URL=http://127.0.0.1:3000
NAPCAT_ACCESS_TOKEN=这里填 HTTP Server 的 Token
```

必须和 NapCat HTTP Server 的配置一致。

### 4.2 HTTP Client：让 NapCat 上报收到的消息

新增或启用 HTTP Client：

```text
目标地址：http://127.0.0.1:8766/onebot
Token：可以和 Server 相同，也可以另生成一串
```

控制台对应：

```dotenv
NAPCAT_EVENT_TOKEN=这里填 HTTP Client 的 Token
```

不要把目标地址写成 `http://127.0.0.1:8765`。8765 是 Bot service，不接收 NapCat 事件。

### 4.3 三个 Token 怎么区分

这是最容易出错的地方：

| 看到的名称 | 实际用途 | 填到哪里 |
| --- | --- | --- |
| HTTP Server Token / Access Token | Bridge 调 NapCat | `NAPCAT_ACCESS_TOKEN` |
| HTTP Client Token / 上报 Token | NapCat 推消息给 Bridge | `NAPCAT_EVENT_TOKEN` |
| Bot service Token | Bridge 调模型服务 | `BOT_SERVICE_TOKEN`，两边相同 |

如果日志出现：

```text
403 token verify failed
```

先检查你是不是把 HTTP Client Token 填到了 `NAPCAT_ACCESS_TOKEN`，或者反过来了。

## 5. 配置项目

打开 `.env.local`，填写最小配置：

```dotenv
LLM_API_KEY=你的模型Key
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=你的模型名

NAPCAT_API_URL=http://127.0.0.1:3000
NAPCAT_ACCESS_TOKEN=NapCat_HTTP_Server_Token
NAPCAT_EVENT_TOKEN=NapCat_HTTP_Client_Token

BOT_SERVICE_TOKEN=Bridge和Bot_service之间的Token

# random 表示从 3、4、5、6 秒中随机等待，也可以写成固定数字
DEBOUNCE_SECONDS=random
FOLLOWUP_SECONDS=120
TYPING_STATUS=true
REACTION_MODE=off
ACTIVE_INTERVAL_MINUTES=60
ACTIVE_PRIVATE_ENABLED=false
ACTIVE_PRIVATE_TARGET_ID=
ACTIVE_PRIVATE_PROMPT=
ACTIVE_GROUP_ENABLED=false
ACTIVE_GROUP_TARGET_ID=
ACTIVE_GROUP_PROMPT=
TOOLS_ENABLED=false
TOOL_ALLOWLIST=get_time
```

群聊需要识别机器人时，再填写机器人的 QQ 号和称呼：

```dotenv
BOT_QQ=你的机器人QQ号
BOT_NAMES=bot,助手,你的机器人昵称
```

`FOLLOWUP_SECONDS` 只影响 `GROUP_MODE=smart`：机器人回复后，在这个时间内可以继续当前话题而不必再次 @。`TYPING_STATUS=false` 会关闭私聊中的“正在输入”状态。

`DECISION_MODE=heuristic` 是默认模式，只用本地规则判断群消息；设为 `model` 后，`GROUP_MODE=smart` 的白名单群会把未明确 @ 的消息交给模型做路由判断。模型只能返回 `reply`、`quote_reply`、`emoji_react` 或 `ignore`，不能直接修改配置，也不能凭空生成事实。模型决策失败时会忽略该条消息。

如果配置了 `MEMORY_DB`，可以在私聊中发送 `记住：内容` 保存明确事实，发送
`忘记：内容` 删除完全相同的事实。普通聊天不会自动写入事实，避免把玩笑误记成偏好。

`REACTION_MODE=like` 允许模型使用 `[[REACTION:emoji_id]]` 对收到的消息发送 QQ 表情回应；默认关闭。
推荐同时设置 `EMOJI_CATALOG=./examples/emoji_catalog.example.json`。词典中为每个表情填写名称、含义、使用场景和 NapCat 数字 ID，模型只选择名称，程序负责转换成 ID，避免模型瞎猜数字。
主动消息也默认关闭。私聊和群聊可以分别开启，二者共用主动消息间隔。例如：

```dotenv
ACTIVE_INTERVAL_MINUTES=120
ACTIVE_PRIVATE_ENABLED=true
ACTIVE_PRIVATE_TARGET_ID=你的QQ号
ACTIVE_PRIVATE_PROMPT=想一句自然的近况，发一条简短私聊消息
ACTIVE_GROUP_ENABLED=true
ACTIVE_GROUP_TARGET_ID=你的群号
ACTIVE_GROUP_PROMPT=想一句适合群里的自然近况
```

主动消息由 Bridge 定时调用模型并通过 NapCat 发送，关闭控制台或 Bridge 后定时器会停止。

工具调用默认关闭。开启后仍然只允许 `TOOL_ALLOWLIST` 中的工具；当前内置
`get_time`，模型通过 `[[TOOL:get_time]]` 请求，Bridge 把结果交回模型后再发送最终消息。
不要把任意命令执行器加入白名单。

生成随机 Token 可以使用：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

建议分别生成三串，不要在聊天窗口、GitHub issue 或截图中发出来。

## 6. 第一次启动

建议第一次不要同时双击很多脚本，按顺序打开三个终端窗口。

### 窗口一：先启动 Bot service

```powershell
python .\bot_service.py
```

看到类似下面的内容才算启动成功：

```text
Loaded configuration
Listening on http://127.0.0.1:8765
```

### 窗口二：启动 Bridge

```powershell
python .\app.py
```

成功时应该看到：

```text
Listening for OneBot events on http://127.0.0.1:8766/onebot
Bot service: http://127.0.0.1:8765
NapCat API: http://127.0.0.1:3000
```

### 窗口三：启动 NapCat

先确保 QQ 已经登录，再启动 NapCat。NapCat 的启动方式取决于安装包；常见路径类似：

```text
E:\Napcat\NapCat.Shell\NapCatWinBootMain.exe
```

也可以由项目控制台提供“启动 NapCat”按钮，但控制台不能替代 QQ 登录和二维码授权。

## 7. 第一次测试不要直接去群里

先私聊机器人 QQ，发送：

```text
你好
```

按顺序检查三个日志窗口：

1. NapCat 是否出现“接收 <- 私聊”。
2. Bridge 是否出现收到事件并向 `8765/reply` 请求。
3. Bot service 是否出现 `POST /reply 200`。
4. NapCat 是否出现“发送 -> 私聊”。

只要其中一段没有日志，就从那一段开始排查，不要先改 Prompt。

## 8. 常见错误排查

### 8.1 Bridge 收不到消息

检查 NapCat HTTP Client：

- 地址是不是 `http://127.0.0.1:8766/onebot`。
- Bridge 是否真的在监听 8766。
- HTTP Client Token 是否等于 `NAPCAT_EVENT_TOKEN`。
- NapCat WebUI 日志是否出现 HTTP 上报 401 或 400。

### 8.2 `ExplicitHttpRejection` 或 403

这通常意味着 NapCat 拒绝了 Bridge 的动作请求。检查：

- `NAPCAT_API_URL` 是否指向 HTTP Server，例如 `http://127.0.0.1:3000`。
- `NAPCAT_ACCESS_TOKEN` 是否是 HTTP Server Token。
- 是否把 `8766/onebot` 错填成 NapCat API。
- 改完配置后是否重启了 Bridge。

### 8.3 Bot service 返回 401

检查 Bridge 和 Bot service 是否使用同一串：

```dotenv
BOT_SERVICE_TOKEN=同一个值
```

这串 Token 不需要填进 NapCat。

### 8.4 端口被占用

Windows PowerShell：

```powershell
Get-NetTCPConnection -LocalPort 8765,8766,3000 -State Listen
```

找到旧进程后，优先回到旧窗口按 `Ctrl+C` 正常退出。只有确认是自己的旧进程时，才结束它：

```powershell
Stop-Process -Id 进程号
```

不要为了释放端口直接结束所有 Python 或 QQ 进程。

### 8.5 模型请求超时

先用最短 Prompt 测试，不要一开始塞入几十条上下文。然后检查：

- Base URL 是否包含正确的 `/v1`。
- 模型名是否是服务商实际支持的名称。
- API Key 是否有效。
- 中转站是否需要关闭思考模式。
- 超时可以从 60 秒开始，但不建议无限增大。

### 8.6 回复为空

检查模型原始响应和解析日志：

- 是否读取了正确的 `choices[0].message.content`。
- 是否把 reasoning 字段误当成最终回复。
- Prompt 是否要求了模型只返回空 JSON。
- 气泡解析器是否把所有内容都过滤掉了。

空回复应该安全地不发送，而不是发送一个空消息或重复请求很多次。

## 9. 如何配置人设

人设文件只写稳定、明确、允许被机器人使用的信息。例如：

```text
你是一个聊天助手。
说话简短自然，像普通 QQ 聊天，不要每次都总结或解释。
没有证据时不要编造用户的经历、爱好和观点。
如果用户没有要求长文，优先用一到两条短消息回复。
```

不建议把所有聊天记录直接塞进 Prompt。更好的顺序是：

1. 用人设文件描述说话原则。
2. 用短期上下文保持当前话题。
3. 用事实记忆保存用户明确说过的长期信息。
4. 用少量真实示例帮助模型理解消息形态。

个人数据应放在 `.local/` 或仓库外部，不要放进公共 GitHub 仓库。

## 10. 群聊配置建议

第一次只允许私聊，确认稳定后再加一个测试群：

```dotenv
GROUP_MODE=mention
GROUP_ALLOWLIST=你的测试群号
```

推荐模式：

- `mention`：只有 @ 机器人或明确叫到名字才回复。
- `smart`：在白名单群中结合上下文判断是否插话。
- `all`：白名单群里的有效消息都交给决策层，容易变吵，不建议初始使用。
- `off`：完全不回复群聊。

群聊必须有白名单。不要让一个刚启动的 Bot 自动监听所有群。

## 11. 如何更新项目

更新前：

1. 记录当前 `.env.local` 和本地数据位置。
2. 停止 Bridge 和 Bot service。
3. 保留 NapCat 是否运行取决于更新内容；只改 Python 时通常不必退出 QQ。

更新后：

```powershell
git pull --ff-only origin main
python -m unittest discover -q
```

如果端口已经被旧进程占用，先确认旧进程是否还在，再启动新版本。不要同时运行两个 Bridge，否则同一条消息可能被回复两次。

## 12. 最小验收清单

提交或发布一个版本前，至少手动确认：

- 私聊一句话能回复。
- 私聊连续三句话只产生一批处理。
- NapCat 重试同一事件时只产生一次回复，机器人自己发出的消息不会触发自回复。
- 群聊未 @ 时按配置保持安静。
- 群聊 @ 后能回复正确群。
- NapCat Server Token 错误时提示清楚。
- HTTP Client Token 错误时日志能定位到事件上报。
- Bot service Token 错误时不会把 Token 打进日志。
- 模型空回复不会发空消息。
- 重启后不会启动两个 Bridge。
- `.env.local`、图片、SQLite 和聊天记录不会进入 Git。
# Optional image understanding

If QQ images arrive as `[图片]` but the reply acts as if no image was sent,
there are two common causes: the OneBot adapter only supplied an image file
identifier, or the selected chat model is text-only. The bridge now resolves
the identifier through NapCat and lets you choose a vision strategy.

Add this to `.env.local`:

```dotenv
VISION_MODE=separate
VISION_API_KEY=the-key-for-your-vision-provider
VISION_BASE_URL=https://vision-provider.example/v1
VISION_MODEL=the-vision-model-name
VISION_MAX_TOKENS=512
VISION_TIMEOUT_SECONDS=30
```

If both models are hosted by the same service, leave `VISION_API_KEY` and
`VISION_BASE_URL` empty. They inherit the corresponding `LLM_*` values. Keep
`VISION_MODEL` set to the actual vision-capable model name.

Use `direct` only when the main `LLM_MODEL` explicitly supports image input.
Use `off` to disable image requests completely. In `separate` mode the vision
model describes visible content first, and the main model receives that
description as ordinary text. The actual image is not forwarded to the text
model. Restart the bot service and bridge after changing `.env.local`.

The resolver accepts data URLs, HTTP(S) URLs, local paths, and NapCat's
`get_image` response. It limits downloads to 10 MiB. A failed image lookup is
logged and the message continues as text instead of becoming an empty reply.

Before restarting the services, you can check whether both providers are
reachable and list the model IDs they expose:

```powershell
python .\check_models.py
```

Some relay services do not implement `/models`; in that case the diagnostic
reports the limitation, and you can still keep using the explicitly configured
model name.

## Control panel

Start the optional local Tkinter panel with:

```powershell
python .\control_panel.py
```

On Windows, double-click `start_control_panel.bat` for the same result.

It includes the chat model, separate vision model, model detection, group
policy, debounce, context, memory, token fields, and optional NapCat launcher
paths. The save button only writes `.env.local` and deliberately leaves
running services alone. Use **Start NapCat** once the three Windows paths are
filled, then click **Start all** for the bridge and bot service. Click restart
manually after saving when you want new values to take effect. **One-click
diagnostics** checks Bot service, Bridge, NapCat `get_status`, and the model
`/models` endpoint in the background; it does not save or restart anything.

## Optional persistent context

By default the bridge forgets its context when it restarts. To keep the recent
conversation window, add:

```dotenv
CONTEXT_MESSAGES=20
MEMORY_DB=./.local/context.sqlite3
DECISION_MODE=heuristic
```

This is intentionally opt-in. The store keeps normalized message fields only,
not complete raw OneBot events, tokens, or cookies. Remove `MEMORY_DB` when
you want memory to stay process-local.

## Cross-machine memory and summaries

Run `supabase/migrations/202608150001_bridge_memory.sql` in a private
Supabase project, then configure both computers with:

```dotenv
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=sb_secret_...
SUPABASE_TIMEOUT_SECONDS=10
REMOTE_MEMORY_MODE=local_first
SUMMARY_ENABLED=false
SUMMARY_MIN_MESSAGES=40
SUMMARY_DELAY_SECONDS=10
```

The bridge shares normalized messages, explicit facts, summaries, and remote
smart-group settings. It writes messages idempotently. If the remote service
is unavailable, local SQLite remains usable. Enable summaries only after the
normal reply path works; they consume the configured model and are bounded to
one summary plus forty short facts per run.
