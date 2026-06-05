# 本地常驻运行（macOS 登录自启 + 保活）

把 DSA Web 服务作为**常驻服务**跑在本机：登录即自动启动、崩溃自动拉起、一直运行。访问地址 `http://127.0.0.1:8000`。

> 适用：个人本机长期使用。需要关机/不登录也运行，请用云服务器（见 [部署指南](DEPLOY.md)）。stockscreener 的 Streamlit(Render)/前端(Vercel)已在云端 24/7 运行，无需本地常驻。

## 前置：一次性环境准备

```bash
cd /Users/iz/dev/daily_stock_analysis
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # 按需填入 LLM key、STOCK_LIST 等
```

## 方式一：手动前台运行（临时用）

```bash
./run-local.sh
# 或： .venv/bin/python main.py --serve-only --port 8000
```
终端 `Ctrl+C` 停止。关掉终端即退出 —— 不是常驻。

## 方式二：macOS LaunchAgent（常驻，推荐）

登录即启动、崩溃自动重启、一直运行。

### 1. 创建 LaunchAgent

新建 `~/Library/LaunchAgents/com.dsa.webserver.plist`：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.dsa.webserver</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/iz/dev/daily_stock_analysis/.venv/bin/python</string>
        <string>main.py</string>
        <string>--serve-only</string>
        <string>--port</string>
        <string>8000</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/iz/dev/daily_stock_analysis</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>10</integer>
    <key>StandardOutPath</key>
    <string>/Users/iz/dev/daily_stock_analysis/logs/launchd_dsa.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/iz/dev/daily_stock_analysis/logs/launchd_dsa.err.log</string>
</dict>
</plist>
```

> 路径按实际项目位置调整。`RunAtLoad`=登录自启，`KeepAlive`=退出/崩溃自动拉起。

### 2. 启用

```bash
mkdir -p /Users/iz/dev/daily_stock_analysis/logs
launchctl load -w ~/Library/LaunchAgents/com.dsa.webserver.plist
```

访问 `http://127.0.0.1:8000`（首次进入会要求设置登录密码，因 `ADMIN_AUTH_ENABLED=true`）。

## 管理命令

```bash
# 状态（有 PID 即在运行）
launchctl list | grep dsa.webserver

# 临时停止（并阻止自动重启）
launchctl unload ~/Library/LaunchAgents/com.dsa.webserver.plist

# 启动 / 启用
launchctl load -w ~/Library/LaunchAgents/com.dsa.webserver.plist

# 改了代码后重启（让新代码生效）
launchctl kickstart -k gui/$(id -u)/com.dsa.webserver

# 看日志
tail -f /Users/iz/dev/daily_stock_analysis/logs/launchd_dsa.out.log

# 彻底移除
launchctl unload ~/Library/LaunchAgents/com.dsa.webserver.plist
rm ~/Library/LaunchAgents/com.dsa.webserver.plist
```

## 说明与限制

- **用户级自启**：仅在本机开机且该用户登录时运行；睡眠时进程挂起、唤醒继续。
- 想在关机/未登录也运行，需 LaunchDaemon（系统级）或云服务器部署。
- 全市场选股较重，本机内存建议 ≥4GB；详见 [美股 / 新加坡选股](us-screening.md)。
- 端口默认 8000，可在 plist 的 `--port` 参数修改。
