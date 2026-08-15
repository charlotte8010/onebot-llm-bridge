# OneBot LLM Bridge

## Image understanding

Image messages are resolved from common OneBot/NapCat forms (data URL, URL,
local path, or NapCat `get_image`) and size-limited before being sent to a
model. The feature is optional:

```dotenv
# off      ignore images
# direct   send images to the main LLM; it must support vision
# separate use a second vision-capable LLM, then give its description to the
#         normal chat model; useful when the main model is text-only
VISION_MODE=separate
VISION_API_KEY=vision-provider-key
VISION_BASE_URL=https://vision-provider.example/v1
VISION_MODEL=vision-model-name
VISION_MAX_TOKENS=512
VISION_TIMEOUT_SECONDS=30
```

## Cross-machine memory

The bridge can optionally share normalized messages, explicit facts, smart
group settings, and model-written summaries through a private Supabase
project. Run `supabase/migrations/202608150001_bridge_memory.sql` first, then
configure both values:

```dotenv
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=sb_secret_...
SUPABASE_TIMEOUT_SECONDS=10
REMOTE_MEMORY_MODE=local_first
SUMMARY_ENABLED=false
SUMMARY_MIN_MESSAGES=40
SUMMARY_DELAY_SECONDS=10
```

`local_first` keeps the local SQLite path responsive and synchronizes remote
memory best-effort. `coordinated` adds a short per-conversation lease so two
machines do not answer the same conversation at once; if the remote store is
unavailable, that batch fails closed instead of risking duplicate delivery.
The secret key is never sent to the model or written to logs. The remote store
is opt-in and no personal data is bundled.

When the chat and vision models use the same provider, `VISION_API_KEY` and
`VISION_BASE_URL` may be left empty; they inherit `LLM_API_KEY` and
`LLM_BASE_URL`. Keep `VISION_MODEL` explicit so a text-only chat model is not
accidentally used for image input.

`separate` is the recommended mode for a text-only chat model such as a
DeepSeek text endpoint. It makes two requests only when a message contains an
image. If an image cannot be downloaded or decoded, the bridge logs a short
failure and continues with the text part of the message. It never sends the
NapCat access token to the model provider.

Persistent context is opt-in. To keep recent normalized messages across a
bridge restart, set:

```dotenv
CONTEXT_MESSAGES=20
MEMORY_DB=./.local/context.sqlite3
```

The database contains only recent normalized chat fields needed for context;
the raw OneBot event and access tokens are not stored. Leave `MEMORY_DB` empty
to keep the current in-memory-only behavior.

When a memory database is enabled, a private message such as `记住：我喜欢某个作品`
stores an explicit user fact for that QQ account. `忘记：...` removes an exact
fact. Facts are injected into the model as verified data; the bridge never
creates facts from ordinary conversation automatically.

Optional reactions and scheduled messages are disabled by default:

```dotenv
REACTION_MODE=off
ACTIVE_ENABLED=false
ACTIVE_INTERVAL_MINUTES=60
ACTIVE_TARGET_TYPE=private
ACTIVE_TARGET_ID=
ACTIVE_PROMPT=
TOOLS_ENABLED=false
TOOL_ALLOWLIST=get_time
```

Set `REACTION_MODE=like` to allow the model marker
`[[REACTION:emoji_id]]` to call NapCat's `set_msg_emoji_like`. Scheduled
messages require a target and prompt; they send the model's generated bubbles
at the configured interval.

Set `EMOJI_CATALOG=./examples/emoji_catalog.example.json` to teach reaction
meanings explicitly. The model selects a name such as `赞` or `笑哭`; the
bridge resolves it to the numeric NapCat ID.

Tools are also opt-in and allowlisted. The current built-in example is
`get_time`; a model can request it with `[[TOOL:get_time]]`, after which the
tool result is fed back into the model. Tool code is registered in
`onebot_llm_bridge/tools.py`; arbitrary shell commands are never executed.

To check whether a new provider URL and key expose an OpenAI-compatible model
list, run:

```powershell
python .\check_models.py
```

This prints the configured chat/vision model names and the result of each
`/models` request, but never prints API keys.

## Control panel

For users who do not want to edit `.env.local` by hand:

```powershell
python .\control_panel.py
```

On Windows, you can also double-click `start_control_panel.bat`.

The panel manages chat/vision models, model detection, group policy, debounce,
context, explicit facts, reactions, scheduled messages, service tokens, and optional NapCat launch paths. **Save
configuration** only writes `.env.local`; it does not restart anything. Use
**Start all**, **Start NapCat**, or **Restart all** explicitly when you are
ready to apply the saved values. **One-click diagnostics** probes each local
service, NapCat's `get_status`, and the configured model `/models` endpoint
without saving or restarting.

一个面向普通用户和开发者的通用 QQ AI Bot 框架。

它通过 [OneBot 11](https://github.com/botuniverse/onebot-11) 接入 NapCat、LLOneBot 等 QQ 客户端，再把消息交给 OpenAI 兼容接口或其他模型服务。项目不绑定任何人的人设、QQ 号、群号和聊天记录，复制配置后即可开始使用。

## 这个项目解决什么问题

直接把 QQ 消息转发给大模型，通常会遇到这些问题：

- 对方连续发三句话，机器人回复三次。
- 群里每句话都回复，像刷屏。
- 模型输出一大段，和 QQ 的聊天习惯不一样。
- NapCat 的事件上报、HTTP Server、Bot 服务各有一个 Token，容易填混。
- 模型 API 换了以后，必须手动改很多文件。
- 回复过程中服务重启，消息没有记录，也不知道是否已经发出去。

OneBot LLM Bridge 把这些问题拆成可配置模块，默认提供一套稳妥的聊天流程，同时允许开发者替换其中任何一层。

## 设计目标

1. **普通用户能看懂**：尽量通过控制台和 `.env` 配置，不要求修改 Python 代码。
2. **不绑定个人资料**：人设、兴趣、语料、QQ 号和群白名单都属于用户自己的配置。
3. **兼容 OpenAI 风格 API**：大多数中转站、本地网关和云端服务只需要填写 Base URL、Key 和模型名。
4. **消息先编排再回复**：支持防抖、合并、是否回复、上下文和多气泡。
5. **默认本地运行**：消息和 Token 不会因为使用本项目自动上传到第三方；需要云端记忆时由用户主动启用。
6. **可替换**：OneBot、模型提供商、记忆存储和控制台都通过边界接口连接。

## 文档导航

- [设计文档](docs/DESIGN.md)：项目边界、模块划分、事件流、数据结构和安全设计。
- [普通用户教程](docs/TUTORIAL.md)：从安装 NapCat 到发出第一条回复，按步骤操作。

## 当前实现状态

当前仓库已经包含可运行的基础聊天链路和可选扩展：

- `app.py`：监听 OneBot 11 HTTP Client 事件的 Bridge。
- `bot_service.py`：调用 OpenAI 兼容模型的本地服务。
- `onebot_llm_bridge/`：配置、事件标准化、回复策略、气泡格式化和 NapCat 动作客户端。
- `control_panel.py`：可选的 Windows 控制台，管理配置和本地服务启动。
- `tests/`：不依赖真实 QQ 和 API Key 的单元测试。

当前版本已经可以跑通“私聊文本 -> 防抖合并 -> 模型 -> NapCat 发回”的基本链路，并支持 smart 群聊的话题相关性判断、模型决策路由（回复/引用/表情/忽略）、私聊输入状态、可选图片识别、显式事实记忆、定时主动消息、跨电脑记忆和 Windows 控制台。

在填写 `examples/.env.example` 的副本后，可以分别启动：

```powershell
python .\bot_service.py
python .\app.py
```

`DEBOUNCE_SECONDS=random` 会从 3、4、5、6 秒中随机等待；也可以填写一个固定数字。

运行测试：

```powershell
python -m unittest discover -s tests -q
```

## 后续路线

### 已完成：能稳定聊天

- OneBot 11 HTTP Client 事件接收。
- OneBot 11 HTTP Server 动作调用。
- OpenAI Chat Completions 兼容接口。
- 私聊和群聊白名单。
- 连续消息合并、防抖和可选随机回复延迟。
- 简单的上下文窗口。
- `ignore`、`reply`、`quote_reply` 三种基础决策。
- 文本回复拆成多个 QQ 气泡。
- 本地 SQLite 运行记录。
- Windows 控制台和命令行启动方式。

### 已完成：聊天运行时增强

- 基础结构化决策：是否回复、是否续聊、是否引用、是否回应表情。
- 显式用户事实记忆；自动摘要仍在后续路线中。
- 图片输入和 Base64 转换。
- 模型预设与 `/models` 检测。
- 可选的 Supabase 共享记忆、远端智能群白名单和自动摘要已经实现；默认关闭，启用前先执行迁移。
- 可选表情回应、定时主动消息和受白名单约束的 `get_time` 工具调用。
- 运行状态、日志和错误提示。

### 未完成：扩展生态

- 多账号、多群独立配置。
- Docker/Linux 部署。
- WebSocket OneBot 适配器。

## 许可证和隐私原则

仓库只保存通用代码、示例配置和假数据。以下内容禁止提交：

- QQ 登录缓存、二维码和 Cookie。
- API Key、Secret Key、Token。
- 聊天导出文件和真实图片。
- 个人 Prompt、私人记忆和群成员信息。

项目具体许可证在实现第一版时确定；在许可证确定前，文档和代码不应被默认当作可自由商用的软件包。
