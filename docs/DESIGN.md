# OneBot LLM Bridge 设计文档

## 1. 项目定位

OneBot LLM Bridge 是一个“消息编排层”，位于 QQ 客户端和大模型之间：

```text
QQ / NapCat
    │ OneBot 11 HTTP Client 事件上报
    ▼
OneBot Adapter
    │ 标准化消息
    ▼
Conversation Orchestrator
    │ 防抖、合并、上下文、回复决策
    ▼
LLM Provider
    │ OpenAI 兼容请求
    ▼
Reply Formatter
    │ 气泡、引用、表情和发送节奏
    ▼
NapCat HTTP Server
    │ OneBot 动作
    ▼
QQ
```

它不是训练框架，也不负责微调模型。它更像一个可配置的聊天运行时：模型负责语言生成，Bridge 负责什么时候说、对谁说、以什么形式说。

## 2. 非目标

第一版不做以下事情：

- 不直接破解 QQ 协议，不替代 NapCat。
- 不保存或分发 QQ 登录凭证。
- 不默认上传聊天记录。
- 不把某个具体人物作为内置身份。
- 不强制用户使用某一家模型或中转站。
- 不把“回复像真人”当成绕过平台规则的承诺。

## 3. 术语

| 术语 | 含义 |
| --- | --- |
| OneBot | 机器人和 QQ 客户端之间的统一协议。 |
| NapCat API | NapCat 的 HTTP Server，用来执行 `send_msg` 等动作。 |
| Event webhook | NapCat 的 HTTP Client，把收到的消息 POST 给 bridge。 |
| Bridge | 接收事件、编排对话并调用 NapCat 动作的服务。 |
| LLM service | 调用模型 API 的服务。 |
| Bubble | 一次 QQ 气泡，也就是一条实际发送的消息。 |
| Conversation key | 会话标识，例如 `private:123456` 或 `group:987654`。 |
| Debounce | 等待短时间，把同一会话连续到达的消息合并后再处理。 |

## 4. 三个服务和三个 Token

### 4.1 NapCat HTTP Server

NapCat HTTP Server 监听本机端口，例如：

```text
http://127.0.0.1:3000
```

Bridge 通过它调用 `send_private_msg`、`send_group_msg`、`set_input_status` 和其他 OneBot 动作。控制台中这个地址叫 **NapCat API**，它使用 NapCat HTTP Server 自己的 Token，建议在配置中称为 `NAPCAT_ACCESS_TOKEN`。

### 4.2 NapCat HTTP Client

NapCat HTTP Client 把事件上报到 bridge，例如：

```text
http://127.0.0.1:8766/onebot
```

它使用事件上报 Token，建议称为 `NAPCAT_EVENT_TOKEN`。这个 Token 可以和 HTTP Server Token 相同，但没有必要相同；重要的是两边分别匹配。

### 4.3 LLM service

LLM service 监听本机端口，例如 `http://127.0.0.1:8765`。Bridge 连接它的 `/reply` 接口生成回复。两者共享 `BOT_SERVICE_TOKEN`。

### 4.4 对照表

| 配置项 | 谁使用 | 对应 NapCat 位置 | 不能混成什么 |
| --- | --- | --- | --- |
| `NAPCAT_API_URL` | bridge | HTTP Server 地址 | 不是事件上报地址 |
| `NAPCAT_ACCESS_TOKEN` | bridge -> NapCat | HTTP Server Token | 不是 HTTP Client Token |
| `NAPCAT_EVENT_TOKEN` | NapCat -> bridge | HTTP Client Token | 不是 Bot service Token |
| `BOT_SERVICE_TOKEN` | bridge <-> LLM service | 不在 NapCat 里 | 不是 NapCat Token |

## 5. 推荐的代码结构

```text
onebot-llm-bridge/
├─ app.py                       # 程序入口和生命周期
├─ config.py                    # 环境变量、默认值和校验
├─ adapters/
│  ├─ onebot11_http.py          # HTTP Client 接收事件
│  └─ onebot11_actions.py       # HTTP Server 动作
├─ core/
│  ├─ events.py                 # OneBot -> 内部事件
│  ├─ conversation.py           # 会话与上下文
│  ├─ debounce.py               # 消息合并和等待
│  ├─ policy.py                 # 是否回复、回复类型
│  └─ formatting.py             # 气泡拆分和输出校验
├─ providers/
│  ├─ base.py                   # 模型提供商接口
│  └─ openai_compatible.py      # OpenAI Chat Completions
├─ memory/
│  ├─ base.py                   # 记忆接口
│  ├─ sqlite.py                 # 默认本地记忆
│  └─ supabase.py               # 可选共享记忆
├─ ui/
│  └─ control_panel.py          # 可选控制台
├─ examples/
│  ├─ .env.example
│  └─ persona_prompt.example.txt
├─ tests/
└─ docs/
```

实现时允许先把文件放在根目录，但模块之间仍然要遵循这些边界。不要让 `onebot11_http.py` 直接读取某个人的兴趣爱好，也不要让模型 provider 直接调用 NapCat。

## 6. 内部事件模型

所有 OneBot 事件进入系统后，先转成内部结构，后续模块不再依赖 NapCat 的原始字段：

```json
{
  "event_id": "onebot-message-id-or-generated-id",
  "time": 1760000000,
  "conversation": {
    "type": "private",
    "id": "123456"
  },
  "sender": {
    "id": "123456",
    "name": "示例用户"
  },
  "message_id": "9988",
  "text": "你好",
  "segments": [
    {"type": "text", "data": {"text": "你好"}}
  ],
  "reply_to": null,
  "images": [],
  "raw": {}
}
```

要求：

- `conversation.id` 一律使用字符串，避免 QQ 号超过 JavaScript 安全整数范围。
- `text` 只放可供模型阅读的文本，不把原始 CQ 码直接塞进 Prompt。
- 图片等媒体放在独立字段，下载失败不能让整条文本消息丢失。
- 保留 `raw` 只用于调试，日志中必须脱敏。

## 7. 一条消息的处理流程

### 7.1 接收阶段

1. HTTP Client POST `/onebot`。
2. 校验 Content-Type、Content-Length、请求体大小和事件 Token。
3. 解析 JSON，拒绝无效事件并返回明确的 4xx。
4. 标准化 sender、conversation、文本和图片。
5. 写入本地事件日志或 SQLite。
6. 返回 HTTP 200，避免 NapCat 因等待模型而重复上报。

### 7.2 合并阶段

以 `conversation_key` 分组。收到第一条消息后启动等待窗口，例如 3 秒；窗口内同一会话的新消息加入批次并重置或不重置窗口，具体由策略决定。

推荐默认值：

- 私聊：每次新消息重置 3–6 秒随机窗口，合并对方连续发言。
- 群聊：以首条消息启动 3–6 秒窗口，后续消息加入但不无限延后。
- 同一会话同一时间最多一个处理任务。
- 模型处理期间到达的新消息进入下一批，或者按可配置规则追加到当前话题。

### 7.3 回复决策

决策层输出结构化对象，不直接输出自然语言：

```json
{
  "decision": "reply",
  "reason": "私聊且消息是直接问题",
  "target_message_id": "9988",
  "reply_mode": "quote_reply",
  "confidence": 0.86
}
```

允许的基础决策：`reply`、`quote_reply`、`emoji_react` 和 `ignore`。决策层不能因为不确定就编造用户事实。个人偏好应该来自显式配置、可靠记忆或当前对话，而不是模型猜测。

### 7.4 模型调用

模型请求至少包含：

- system prompt：通用身份和行为约束。
- persona prompt：用户自己的可选人设。
- conversation context：最近消息和必要摘要。
- current batch：本次合并后的新消息。
- images：可选的 Base64 图片。

模型输出建议使用明确格式：

```text
[[BUBBLE]]第一句
[[BUBBLE]]第二句
```

解析器要做最后一道校验：去除空气泡、限制最大条数、限制总长度，并保证发送接口只收到允许的消息段。

### 7.5 发送阶段

1. 先关闭“正在输入”状态。
2. 按气泡顺序发送。
3. 气泡之间使用 0.3–1.5 秒可配置间隔，不要固定刷屏。
4. 记录每个动作的返回值和 message_id。
5. 失败时区分“未发送”和“部分发送”，不要重复发送已经成功的气泡。

## 8. Prompt 与记忆边界

通用项目只负责提供 Prompt 插槽，不提供任何真实个人资料：

```text
你是一个运行在 QQ 中的聊天助手。
你会根据当前对话判断是否需要回复。
没有证据时不要编造用户的经历、爱好、身份和观点。
回复要简短、自然，并适合 QQ 气泡。

下面是用户主动提供的可选人设：
{{PERSONA_PROMPT}}

下面是当前对话：
{{CONTEXT}}
```

记忆分三层：

1. **短期上下文**：最近若干条消息，默认本地内存或 SQLite。
2. **摘要记忆**：较长对话压缩后的中性摘要。
3. **事实记忆**：用户明确说过、且允许保存的稳定信息。

模型不能把一次玩笑自动升级成永久事实。事实写入最好有来源消息、时间和置信度。

## 9. 配置设计

第一版建议只使用 `.env`，不要为了“看起来高级”引入复杂配置系统：

```dotenv
LLM_API_KEY=replace-me
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=your-model
LLM_MAX_TOKENS=1024
LLM_TIMEOUT_SECONDS=60

NAPCAT_API_URL=http://127.0.0.1:3000
NAPCAT_ACCESS_TOKEN=replace-me
NAPCAT_EVENT_TOKEN=replace-me
BRIDGE_HOST=127.0.0.1
BRIDGE_PORT=8766

BOT_SERVICE_URL=http://127.0.0.1:8765
BOT_SERVICE_TOKEN=replace-me

GROUP_MODE=mention
GROUP_ALLOWLIST=
REPLY_DELAY=random
DEBOUNCE_SECONDS=random
CONTEXT_MESSAGES=20
PERSONA_FILE=./.local/persona_prompt.txt
MEMORY_BACKEND=sqlite
MEMORY_DB=./.local/memory.sqlite3
```

配置加载顺序要固定并写入文档：命令行参数 > `.env.local` > `.env` > 默认值。启动时打印配置摘要，但永远不打印完整 Key、Token、Cookie 或 Secret。

`DEBOUNCE_SECONDS=random` 时从 3、4、5、6 秒中随机选择；填写数字时使用固定秒数。私聊新消息会重新选择并重置窗口，群聊只使用首条消息创建的窗口。

## 10. 安全设计

- 默认只监听 `127.0.0.1`。
- 非本机监听时强制要求服务 Token。
- 校验所有请求体大小，默认不超过 1 MiB。
- 使用常量时间比较校验 Bearer Token。
- HTTP 错误日志只记录状态码和动作名，不记录完整请求头。
- 图片下载限制协议、大小、超时和本地路径范围，防止 SSRF 和任意文件读取。
- 不允许通过 Prompt 让模型执行任意系统命令。
- 工具调用必须有白名单、参数校验和超时。
- `.env*`、SQLite、下载图片和聊天导出文件全部 Git 忽略。

## 11. 测试策略

最低测试集合：

1. OneBot 事件标准化：私聊、群聊、@、回复、图片和异常 JSON。
2. Token 校验：缺失、错误、正确和非本机监听。
3. 防抖：连续消息合并、私聊重置、群聊不无限延后。
4. 决策：mention、白名单、ignore、续聊和话题切换。
5. 模型 provider：超时、4xx、5xx、空回复和多气泡解析。
6. 动作发送：成功、部分失败和重复保护。
7. 配置：默认值、环境变量覆盖和敏感信息脱敏。
8. 控制台：保存、启动、停止、状态显示和错误提示。

第一版不要求模型输出完全稳定，但应该用固定假响应覆盖核心编排逻辑。不要让单元测试依赖真实 API Key 或真实 QQ。

## 12. 开发路线

### Milestone 0：文档和脚手架

- 建立项目结构、`.env.example`、README 和设计文档。
- 定义内部事件、决策和回复结构。
- 建立最小测试运行方式。

### Milestone 1：最小可用版本

- HTTP Client 接收 OneBot 事件。
- OpenAI 兼容 provider。
- NapCat HTTP Server 发送文本。
- 私聊回复和简单群聊 mention 模式。

### Milestone 2：聊天体验

- 防抖和连续消息合并。
- 上下文窗口。
- 气泡拆分、引用回复和输入状态。
- 控制台与日志。

### Milestone 3：可选能力

- SQLite 记忆。
- 图片 Base64 输入。
- Supabase 共享记忆。
- 表情回应和主动消息。

### Milestone 4：插件和多平台

- Provider 插件。
- OneBot WebSocket。
- 定时任务和工具调用。
- Linux/Docker 部署。

## 13. 关键取舍

### 为什么先做 bridge，而不是训练模型

大多数“像不像本人”的问题来自 Prompt、上下文、回复时机和消息形态，而不是必须微调。把这些能力做成运行时配置，用户换模型也能继续使用。

### 为什么默认本地 SQLite

本地数据库部署简单、隐私边界清楚、断网时仍能工作。共享数据库作为可选后端，不应该成为第一次启动的前置条件。

### 为什么把决策和语言生成分开

“要不要回复”与“具体怎么说”是两个不同问题。分开以后，可以用便宜快速的模型做决策，用更强的模型生成回复，也能在模型异常时安全地选择不回复。
