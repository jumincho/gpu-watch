# GPU Watch Dashboard v2

通过 SSH 查看共享 NVIDIA GPU 的轻量仪表盘。后端只使用 Python 标准库和 SQLite，前端是无需构建的 HTML/CSS/JavaScript，GPU 服务器不需要常驻代理。

**v2 正式发布：2026-09-30 · GPT-6.1 Sol (max)。页面不显示发布信息页脚。**

[English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-HK.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

公开版包含虚构服务器名、文档示例 IP 和 SSH 路径。部署前请替换配置并验证 SSH 密钥和 known_hosts；密码、API 密钥、数据库和内部运营文档不包含在仓库内。

## 功能与数据规则

- 默认每 10 秒采集 GPU、每 30 分钟采集磁盘；磁盘有独立工作线程和超时预算。支持多进程、多用户、实验室概览和状态筛选。
- 有计算进程、显存 ≥500 MiB 或利用率 ≥10% 时算 busy。利用率为 0% 的驻留进程仍占用 GPU；这些指标衡量占用，而不是计算效率。
- 通过 effective UID、NSS/数字 UID、PID 启动标识和 GPU UUID 重新验证所有者。不会仅凭 root 拥有的 /proc 目录猜测用户，也不会因用户暂时未知而隐藏已验证的占用进程。全部用户已知时在不同用户之间均分时间；部分未知区间保持未归属。完整命令行、环境和参数值不公开。
- 不到 60 秒的任务仍进入历史和占用统计，只不解除长期空闲标记。长期空闲不要求 100% 观测覆盖；七日指标使用已观测 GPU 时间，缺失时间不填零。LAB DAILY INDEX 按 KST 日界累加各 GPU 的观测时间加权显存均值。
- Recent Activity 支持日期、服务器、用户和分页；DOWN/UP 默认隐藏，可选包含，观测缺口和用户变化只保留在数据层。
- 磁盘警告与 Disk Used 使用相同的总 used/(used+available)。重复挂载只算一次，保留块不进入分母；不完整归属用 ≥/≈ 表示。可选 root helper 只接受固定无参数命令，采用低优先级、锁和五分钟缓存。
- 六个会议计时器保留固定颜色、截止日期排序及同时间注册顺序，支持 TBA 和本地国旗 SVG。公告可无到期日期，已有公告也可改为长期。点击对话框外部不会关闭表单。
- 自动刷新保留标签、滚动和焦点。AA 前 29 名缓存在服务器；按 API 配额约每小时刷新（100 次/日、四页），最低 15 分钟，预留最多八次，没人打开页面也会按计划更新。版本来自 API，模型名称简写。

## 部署与安全

使用 Python 3.12+ 和 OpenSSH，或 Docker。GPU 服务器需 SSH、python3、nvidia-smi、df 和 GNU du；采集不依赖持久 home 目录。先配置 hosts.json、IP/Host 白名单和 SSH 主机密钥。AA 密钥保存在 app 所有、0600 权限的 secrets/artificial_analysis_api_key 文件，浏览器只能读取排名缓存。

edge 和 app 校验 IP/Host/Origin；写操作需要公告密码或管理员 PIN，采用 PBKDF2-SHA256 600,000 次及并发/失败次数限制。容器使用非 root、只读文件系统、cap-drop、no-new-privileges 和资源限制。HTTP 只适合可信 LAN；IP 限制不等于传输加密。密码保存提示由浏览器最终决定。

deploy.sh 中的示例地址和路径必须修改。部署前备份并验证 SQLite，失败时回滚容器、数据库和 SSH 运行文件。Windows 应急副本平时关闭，VERSION、代码指纹及全文件清单必须和生产一致；Force 也不能跳过不一致检查。仅 LAN 防火墙操作需要 UAC，app 以普通用户运行；Stop 清理进程、规则和临时密码。

默认日备份保留 14 天、部署前备份 30 天、事件和日指标 180 天、原始区间八天。不配置自动异地备份。详细字段、结构和运营命令请见 [English README](README.md)。

## Commands

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch
# Configure hosts.json and SSH before starting.
python3 scripts/admin-passphrase.py ensure
python3 server.py --host 127.0.0.1 --port 8787
python3 scripts/audit-data.py data/gpu_watch.sqlite3
python3 -m unittest discover -s tests -v
node tests/test_frontend.js
```

[MIT License](LICENSE) · [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) · [Twemoji](https://github.com/jdecked/twemoji) (CC-BY 4.0).
