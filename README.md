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

`separate` is the recommended mode for a text-only chat model such as a
DeepSeek text endpoint. It makes two requests only when a message contains an
image. If an image cannot be downloaded or decoded, the bridge logs a short
failure and continues with the text part of the message. It never sends the
NapCat access token to the model provider.

To check whether a new provider URL and key expose an OpenAI-compatible model
list, run:

```powershell
python .\check_models.py
```

This prints the configured chat/vision model names and the result of each
`/models` request, but never prints API keys.

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

当前仓库已经包含 Milestone 0 和 Milestone 1 的最小骨架：

- `app.py`：监听 OneBot 11 HTTP Client 事件的 Bridge。
- `bot_service.py`：调用 OpenAI 兼容模型的本地服务。
- `onebot_llm_bridge/`：配置、事件标准化、回复策略、气泡格式化和 NapCat 动作客户端。
- `tests/`：23 个不依赖真实 QQ 和 API Key 的单元测试。

当前版本已经可以跑通“私聊文本 -> 防抖合并 -> 模型 -> NapCat 发回”的基本链路，并支持 smart 群聊短时续聊、引用回复和私聊输入状态；图片下载、长期记忆和控制台仍按下方路线逐步加入。

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

## 预计支持的功能

### 第一阶段：能稳定聊天

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

### 第二阶段：更像一个真正的聊天程序

- 结构化决策：是否回复、回复哪个话题、是否引用、是否回应表情。
- 记忆摘要和用户事实。
- 图片输入和 Base64 转换。
- 模型预设与 `/models` 检测。
- 可选的 Supabase 共享记忆。
- 运行状态、日志和错误提示。

### 第三阶段：扩展生态

- 定时主动消息。
- 工具调用和插件。
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
