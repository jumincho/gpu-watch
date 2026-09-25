# GPU Watch Dashboard v3

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-HK.md)

面向共享 NVIDIA GPU 服务器的轻量状态面板。后端使用 Python 标准库和 SQLite，前端使用无需构建的 HTML/CSS/JavaScript。服务器清单和截图均为示例，不包含实际部署的凭据或数据库。

显示 GPU、VRAM、温度、进程、用户、使用记录、磁盘空间和 LAB DAILY INDEX。最多支持六个会议计时器，包括 TBA 和固定配色；公告可以不设到期时间。DOWN/UP 可选显示，内部观测诊断事件不出现在活动列表中。

默认每10秒采集 GPU，每30分钟采集 Disk。即使 Util 为0%，占用显存的驻留进程仍可判定为 busy。短暂使用也会记录，但不足60秒的会话不会取消长期空闲状态。不会把未观测时间填为零，也不会猜测未确认的用户。

v3 改进了 effective UID 确认、独立于 GPU 故障的 Disk 更新、可选的固定权限 Disk helper，以及按 API 配额调整的 AA 更新。密钥仅保存在服务端。每日100次请求、四页数据时目标约为每小时更新，浏览器每五分钟查询缓存。

完整安装、配置、安全与恢复步骤以 [英文 README](README.md) 为准。请替换示例地址和 SSH 密钥。Windows 副本仅用于应急，平时关闭，并保持与生产相同的源码指纹。HTTP 不提供传输加密，项目不配置自动异地备份。

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch
# Configure hosts.json and SSH before starting.
python3 scripts/admin-passphrase.py ensure
python3 server.py --host 127.0.0.1 --port 8787
```

[MIT License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

2026-09-25 · GPT-6 Astra Max / Claude Opus 5.5 Max
