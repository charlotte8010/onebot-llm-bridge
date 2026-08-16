# 腾讯云部署说明

这份说明专门解决一个问题：本地电脑关机后，QQ Bot 还能不能继续工作。

结论先说：

- 只把 `bot_service.py` 放到云服务器：模型服务在云上，但本地 QQ/NapCat 关机后不会收发消息。
- 要让电脑关机后 QQ Bot 仍能收发消息：需要在腾讯云 **Windows 云服务器** 上同时运行 QQNT、NapCat、Bridge 和 Bot service。
- Linux 云服务器适合只运行 Bot service；NapCat/QQNT 按当前项目目标仍建议放在 Windows。

## 一、推荐架构：完整运行在 Windows 云服务器

```text
腾讯云 Windows CVM
  QQNT + NapCat
       -> Bridge:8766
       -> Bot service:8765
       -> 模型 API
```

本地电脑只需要远程桌面登录服务器进行安装和维护。四个服务都在同一台服务器时，建议保持：

```dotenv
NAPCAT_API_URL=http://127.0.0.1:3000
BRIDGE_HOST=127.0.0.1
BRIDGE_PORT=8766
BOT_SERVICE_HOST=127.0.0.1
BOT_SERVICE_PORT=8765
```

NapCat 的 OneBot11 配置也都填本机地址：

```text
HTTP Server：127.0.0.1:3000
HTTP Client：http://127.0.0.1:8766/onebot
```

这样腾讯云安全组不需要开放 3000、8765、8766。只在安全组开放远程桌面端口，并尽量限制来源 IP。QQ 登录、手机授权和 NapCat 版本兼容问题，和本地安装时完全一样。

### 安装步骤

1. 创建 Windows 云服务器，建议选择有足够内存运行 QQNT 和模型桥接的配置。
2. 通过远程桌面登录，安装 QQNT、NapCat、Python 3.11+ 和 Git。
3. 克隆项目：

   ```powershell
   git clone https://github.com/charlotte8010/onebot-llm-bridge.git C:\onebot-llm-bridge
   Set-Location C:\onebot-llm-bridge
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

4. 运行 `control_panel.py`，填写模型、NapCat Token、Persona 和 Bot 服务 Token。
5. 选择 NapCat launcher，确认 QQNT 版本受当前 NapCat release 支持。
6. 在 NapCat WebUI 配置 HTTP Server 和 HTTP Client，地址按上面的本机地址填写。
7. 点击控制台“保存配置”，再点击“启动全部”和“启动 NapCat”。保存不会自动重启服务。
8. 先私聊自己的 QQ 测试，再配置群聊、Supabase、主动消息和 reaction。

## 二、过渡方案：Linux 云服务器只运行 Bot service

这个方案适合先把耗时的模型服务搬到云端。它要求本地电脑继续运行 QQ/NapCat/Bridge。

### 1. 云服务器安装

以下命令适用于常见的 Ubuntu/Debian CVM。不同腾讯云镜像的 Python 包名可能略有区别：

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
sudo useradd --system --home /opt/onebot-llm-bridge --shell /usr/sbin/nologin onebot || true
sudo git clone https://github.com/charlotte8010/onebot-llm-bridge.git /opt/onebot-llm-bridge
sudo python3 -m venv /opt/onebot-llm-bridge/.venv
sudo chown -R onebot:onebot /opt/onebot-llm-bridge
sudo mkdir -p /etc/onebot-llm-bridge
sudo cp /opt/onebot-llm-bridge/deploy/tencent-cloud/bot.env.example /etc/onebot-llm-bridge/bot.env
sudo chmod 600 /etc/onebot-llm-bridge/bot.env
sudo nano /etc/onebot-llm-bridge/bot.env
```

把 `bot.env` 里的 API Key、Persona 路径和 Token 改成真实值。不要把这个文件提交到 GitHub。

### 2. 安全连接

优先选择下面任意一种：

- Tailscale：两台电脑加入同一个 tailnet，本地控制台填云服务器的 `100.x.x.x` 地址。
- ZeroTier：两台电脑加入同一个虚拟局域网，填分配的私网地址。
- SSH 隧道：把云端 8765 映射到本地回环地址，再让本地控制台填 `127.0.0.1` 和映射端口。

不建议直接在腾讯云安全组开放 TCP 8765。若确实必须公网访问，应在 HTTPS 反向代理后使用，并把安全组限制为固定来源 IP；项目当前的 `BOT_SERVICE_HOST` 字段只接受主机名/IP，不接受 `https://` URL。

### 3. 启动为 systemd 服务

```bash
sudo cp /opt/onebot-llm-bridge/deploy/tencent-cloud/onebot-llm-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now onebot-llm-bot
sudo systemctl status onebot-llm-bot
journalctl -u onebot-llm-bot -f
```

云端 `bot.env` 最重要的几项：

```dotenv
BOT_SERVICE_HOST=0.0.0.0
BOT_SERVICE_PORT=8765
BOT_SERVICE_TOKEN=和本地控制台相同的随机字符串
```

`0.0.0.0` 是云端服务的监听地址，不要把它填到本地控制台。**本地控制台**要填 Tailscale/ZeroTier 私网 IP；如果用了 SSH 隧道，则填 `127.0.0.1` 和隧道本地端口。

### 4. 本地控制台设置

本地仍保留 NapCat API：

```text
NapCat API：http://127.0.0.1:3000
Bridge 端口：8766
```

只把下面两项改成云端对应值：

```text
Bot 服务地址：云服务器私网/Tailscale IP
Bot 端口：8765
Bot 服务 Token：和云端 bot.env 完全相同
```

也可以在控制台“连接与服务”里点击“云端设置”，一次填写地址、端口和 Token，再点击“测试连接”。点击“一键诊断”，如果 Bot 显示“云端可用”，说明 Bridge 已经能访问云端。点击“启动全部”时，控制台会启动本地 Bridge，但会明确跳过本地 Bot。控制台不会替你创建腾讯云实例或安装 QQ/NapCat。

## 三、云端常见问题

### 云服务器 Bot 显示启动但本地诊断不可用

先检查：

```bash
sudo systemctl status onebot-llm-bot
ss -lntp | grep 8765
```

再检查 Tailscale/ZeroTier 是否在线、云端防火墙和腾讯云安全组是否允许虚拟网卡访问 8765。不要只检查公网 IP。

### 本地电脑关机后仍然没有 QQ 消息

说明你使用的是“只搬 Bot service”的方案。QQ 登录和 NapCat 还在本地，必须改成 Windows 云服务器完整部署，或让另一台一直开机的 Windows 机器运行 QQ/NapCat。

### 需要保存 Persona、记忆和图片吗

完整 Windows 部署时，Persona、SQLite 和图片都在云服务器项目目录。只运行云端 Bot 时，Persona 也要复制到云端路径；本地 Bridge 的 Persona 不会自动上传。Supabase 可以用来共享记忆，但不会替代 QQ/NapCat。

### 更新项目

Windows 云服务器可以使用项目控制台的稳定版更新；Linux 云服务器先备份配置、Persona 和 SQLite，再更新到版本清单指定的 Tag：

```bash
sudo cp -a /etc/onebot-llm-bridge/bot.env /etc/onebot-llm-bridge/bot.env.bak
sudo -u onebot git -C /opt/onebot-llm-bridge fetch origin --tags
sudo -u onebot git -C /opt/onebot-llm-bridge merge --ff-only v0.2.1
sudo systemctl restart onebot-llm-bot
sudo journalctl -u onebot-llm-bot -n 80 --no-pager
```

更新前先备份 `/etc/onebot-llm-bridge/bot.env`、Persona 和 SQLite 文件。不要直接跟随 `main`，普通开发提交不会被当成稳定更新。
