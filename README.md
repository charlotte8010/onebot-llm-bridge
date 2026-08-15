# OneBot LLM Bridge

一个面向 QQ 的通用 AI Bot 桥接程序。它通过 NapCat 接收和发送 QQ 消息，再调用 OpenAI 兼容的大模型接口生成回复。

项目本身不绑定任何人的人设、QQ 号、群号或聊天记录。你可以通过控制台配置模型、回复策略、Persona、记忆、图片识别和 Supabase 共享记忆。

## 能做什么

- 连接 NapCat 和 OneBot 11 HTTP 接口
- 支持私聊、群聊白名单和智能群聊跟随
- 等待并合并对方连续发送的多条消息
- 配置固定或随机的回复延迟
- 控制是否需要 @、是否继续当前话题、是否忽略无关消息
- 将一条回复拆成多个 QQ 气泡
- 可选的图片识别：主模型识图或单独使用视觉模型
- 可选的本地 SQLite 记忆
- 可选的 Supabase 跨设备共享记忆和自动摘要
- 可选的 reaction、定时主动消息和白名单工具
- Windows 控制台：检测模型、编辑 Persona、编辑表情词典、启动和诊断服务

## 消息流程

```text
QQ 消息
  -> NapCat
  -> Bridge : 8766/onebot
  -> Bot service : 8765/reply
  -> OpenAI 兼容模型
  -> Bridge
  -> NapCat : 3000/send_private_msg 或 send_group_msg
  -> QQ 回复
```

默认端口和用途如下：

| 服务 | 默认端口 | 用途 |
| --- | ---: | --- |
| NapCat HTTP Server | 3000 | Bridge 通过它发送消息、reaction 和输入状态 |
| Bot service | 8765 | 调用模型并返回回复 |
| Bridge HTTP 上报入口 | 8766 | NapCat 把收到的 QQ 消息上报到这里 |

## 快速开始

### 1. 准备环境

- Windows 10/11 或 Linux
- Python 3.11 或更高版本
- 已安装并登录 NapCat 使用的 QQNT
- 一个兼容 OpenAI Chat Completions 的模型接口

克隆项目并进入目录：

```powershell
git clone https://github.com/charlotte8010/onebot-llm-bridge.git
Set-Location .\onebot-llm-bridge
```

项目主要使用 Python 标准库，不需要先安装一大堆第三方依赖。建议使用虚拟环境：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

### 2. 打开控制台

Windows 可以双击：

```text
start_control_panel.bat
```

也可以运行：

```powershell
python .\control_panel.py
```

建议优先使用控制台完成配置，不需要手动编辑 `.env.local`。

首次配置顺序：

1. 在“模型与识图”填写 API Key、Base URL。
2. 点击“检测模型”，选择要使用的模型。
3. 在“连接与服务”确认 NapCat API 地址和端口。
4. 配置事件上报 Token、Bot 服务 Token。
5. 需要人设时，在“回复与记忆”选择 Persona 文件。
6. 点击“保存配置”。保存只写入配置，不会自动重启服务。
7. 点击“启动全部”，或分别启动 NapCat、Bot 和 Bridge。

控制台右上角的“操作说明”里还提供了分章节的配置教程和故障排查说明。

## 模型配置

模型服务通常需要三个值：

```dotenv
LLM_API_KEY=你的模型密钥
LLM_BASE_URL=https://api.example.com/v1
LLM_MODEL=你的模型名称
```

Base URL 是否需要 `/v1` 以服务商文档为准。控制台的“检测模型”会请求当前填写地址的 `/models`，不会固定显示某几个 DeepSeek 模型。

### 图片识别

`VISION_MODE` 有三种值：

```dotenv
# 不处理图片
VISION_MODE=off

# 图片直接交给主模型，主模型必须支持视觉输入
VISION_MODE=direct

# 使用单独的视觉模型描述图片，再交给普通聊天模型
VISION_MODE=separate
VISION_API_KEY=视觉模型密钥
VISION_BASE_URL=https://vision-provider.example/v1
VISION_MODEL=视觉模型名称
```

如果聊天模型是文本模型，例如某些 DeepSeek 文本接口，建议使用 `separate`。当视觉 API 和聊天 API 是同一家服务商时，视觉 Key 和 Base URL 可以留空，程序会复用 `LLM_*` 配置，但仍建议明确填写 `VISION_MODEL`。

图片无法下载、解析失败或模型不支持视觉输入时，程序会记录简短错误并继续处理文字，不会把 NapCat Token 发给模型服务商。

## NapCat 配置

在 NapCat WebUI 中打开 OneBot11 网络配置。

### HTTP Server

HTTP Server 负责让 Bridge 调用 NapCat 发送消息：

```text
监听地址：127.0.0.1
端口：3000
```

控制台中的对应配置是：

```dotenv
NAPCAT_API_URL=http://127.0.0.1:3000
NAPCAT_ACCESS_TOKEN=HTTP Server 的 Token
```

### HTTP 上报

HTTP 上报负责让 NapCat 把 QQ 收到的消息发送给 Bridge：

```text
上报地址：http://127.0.0.1:8766/onebot
```

上报配置里的 Token 必须和控制台的“事件上报 Token”一致：

```dotenv
NAPCAT_EVENT_TOKEN=HTTP 上报配置里的 Token
```

这几个 Token 不要混用：

- NapCat Access Token：Bridge 调用 NapCat 的 HTTP Server 时使用
- 事件上报 Token：NapCat 上报消息给 Bridge 时使用
- Bot 服务 Token：Bridge 调用本地 8765 服务时使用

## 回复策略

控制台的“回复与记忆”可以配置：

| 配置 | 含义 |
| --- | --- |
| 叫到才回 | 群里需要 @ 或叫到 Bot 名称 |
| 智能跟随 | 被叫到后，在继续话题时间内可以接着聊天 |
| 群聊都回 | 白名单群里的消息都进入回复判断 |
| 关闭群聊 | 不处理群聊 |

`DECISION_MODE` 可以选择：

- `heuristic`：本地规则，速度快、成本低
- `model`：交给模型判断回复、引用、reaction 或忽略

`DEBOUNCE_SECONDS=random` 会在 3、4、5、6 秒中随机等待，并把这段时间内对方连续发送的消息合并成一次输入。也可以填写固定秒数。

## Persona 和本地记忆

Persona 是稳定的人设、兴趣、说话方式和明确禁忌；建议通过控制台选择或编辑，不要把 API Key 写进 Persona。

例如：

```dotenv
PERSONA_FILE=./.local/persona_prompt.txt
```

本地 SQLite 记忆是一个电脑上的数据库文件，不需要单独安装数据库服务：

```dotenv
CONTEXT_MESSAGES=20
MEMORY_DB=./.local/context.sqlite3
```

它只保存恢复上下文所需的标准化消息和明确事实，不保存完整原始 OneBot 事件、Token 或 Cookie。`.local/` 和 SQLite 文件会被 Git 忽略，不会上传 GitHub。

不想启用跨重启记忆时，把 `MEMORY_DB` 留空即可。

启用本地记忆后，可以在私聊中使用：

```text
记住：我喜欢某个作品
忘记：我喜欢某个作品
```

程序不会从普通聊天中自动编造或提取事实。

## Supabase 跨设备记忆

Supabase 是可选功能，用于多台机器共享标准化消息、明确事实、智能群设置和模型生成的摘要。

1. 创建一个私有 Supabase 项目。
2. 打开 SQL Editor。
3. 执行 `supabase/migrations/202608150001_bridge_memory.sql`。
4. 将项目 URL 填入 `SUPABASE_URL`。
5. 将后台 Secret Key 填入 `SUPABASE_SECRET_KEY`。
6. 填写 `BOT_QQ`，再选择本地优先或协调模式。
7. 保存配置并手动重启 Bot 和 Bridge。

```dotenv
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SECRET_KEY=sb_secret_...
SUPABASE_TIMEOUT_SECONDS=10
REMOTE_MEMORY_MODE=local_first
SUMMARY_ENABLED=false
SUMMARY_MIN_MESSAGES=40
SUMMARY_DELAY_SECONDS=10
```

`local_first`：远端不可用时仍优先使用本地记忆，适合单机运行。

`coordinated`：使用短租约协调多个 Bridge，减少两台机器重复回复同一个话题。

不要使用 anon key 代替 Secret Key，也不要把 Secret Key 提交到 GitHub。远端记忆默认关闭，启用前请确认自己了解数据存储位置。

## Reaction、主动消息和工具

这些功能默认关闭。

### Reaction

```dotenv
REACTION_MODE=like
EMOJI_CATALOG=./examples/emoji_catalog.example.json
```

Reaction 是对已有消息添加回应，不是额外发送一条表情消息。表情词典可以在控制台中编辑，让模型使用语义名称而不是直接记数字 ID。

### 定时主动消息

```dotenv
ACTIVE_ENABLED=true
ACTIVE_INTERVAL_MINUTES=60
ACTIVE_TARGET_TYPE=private
ACTIVE_TARGET_ID=你的 QQ 号
ACTIVE_PROMPT=生成一条自然的主动消息
```

建议先用私聊测试，并设置较长间隔。

### 工具白名单

工具需要同时满足两个条件：启用“白名单工具”，并在工具选择中勾选具体工具。

当前内置示例工具是 `get_time`，只用于查询当前时间，不会执行任意 Shell 命令：

```dotenv
TOOLS_ENABLED=true
TOOL_ALLOWLIST=get_time
```

工具实现位于 `onebot_llm_bridge/tools.py`。

## 直接运行服务

通常建议使用控制台。需要手动运行时，在项目根目录分别打开两个 PowerShell：

```powershell
python .\bot_service.py
python .\app.py
```

也可以指定其他 dotenv 文件：

```powershell
python .\bot_service.py --env-file .\.env.local
python .\app.py --env-file .\.env.local
```

测试模型接口：

```powershell
python .\check_models.py
```

运行测试：

```powershell
python -m unittest discover -s tests -q
```

## 项目结构

```text
app.py                              Bridge 入口
bot_service.py                      Bot 服务入口
control_panel.py                    Windows 控制台
onebot_llm_bridge/config.py         配置读取和校验
onebot_llm_bridge/services.py        回复、记忆和工具流程
onebot_llm_bridge/napcat.py         NapCat HTTP 客户端
onebot_llm_bridge/memory.py          本地 SQLite 记忆
onebot_llm_bridge/remote_memory.py   Supabase REST 记忆
onebot_llm_bridge/images.py          图片解析和视觉输入
onebot_llm_bridge/tools.py           白名单工具
examples/                            示例配置、Persona 和表情词典
supabase/migrations/                 Supabase 数据库迁移
tests/                               不依赖真实 QQ 和 API Key 的测试
```

## 常见问题

### Bot 不回复

1. 查看 Bot 服务是否监听 `127.0.0.1:8765`。
2. 查看 Bridge 是否监听 `127.0.0.1:8766`。
3. 检查 NapCat 上报地址是否是 `http://127.0.0.1:8766/onebot`。
4. 检查三个 Token 是否分别填到了正确位置。

### NapCat 报 401

通常是事件上报 Token 不一致。对比 NapCat OneBot11 HTTP 上报配置和控制台的“事件上报 Token”，不要填 Bot 服务 Token。

### NapCat 报 403

通常是 NapCat HTTP Server 的 Access Token 不正确，或者控制台的 NapCat API 地址和实际监听地址不同。

### 图片没有被识别

确认主模型是否支持视觉输入。文本模型请使用 `VISION_MODE=separate`，并填写视觉模型配置。

### 模型检测失败

检查当前填写的 Base URL、API Key，以及服务商是否开放 `/models` 接口。有些中转站可以聊天，但不提供模型列表，这时可以手动填写模型名称。

## 安全与隐私

请不要提交以下内容：

- API Key、Secret Key、Token、Cookie 和登录缓存
- QQ 聊天导出文件、真实图片和二维码
- 私人 Persona、SQLite 数据库和 Supabase 凭据

项目默认只在本机监听服务。需要让其他设备访问时，必须额外配置网络和鉴权，并重新评估安全风险。
