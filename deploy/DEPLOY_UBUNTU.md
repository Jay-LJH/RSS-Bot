# Ubuntu 部署与守护进程（systemd）

## 1) 服务器准备
- Ubuntu 22.04+
- 已安装 `python3`, `python3-venv`, `rsync`

## 2) 环境变量
项目使用 `.env` 读取 `TELEGRAM_BOT_TOKEN`。建议在 `/opt/yachiyo/.env` 配置：

```env
TELEGRAM_BOT_TOKEN=xxx
GEMINI_API_KEY=xxx
GITHUB_KEY=xxx
```

## 3) 一键安装服务
在项目根目录执行：

```bash
bash deploy/install_service.sh
```

## 4) 常用命令

```bash
sudo systemctl status yachiyo-bot.service
sudo systemctl restart yachiyo-bot.service
sudo journalctl -u yachiyo-bot.service -f
```

## 5) 开机自启
安装脚本已自动执行：

```bash
sudo systemctl enable yachiyo-bot.service
```

## 6) 修改代码后发布
再次执行安装脚本即可（会同步代码并重启服务）：

```bash
bash deploy/install_service.sh
```
