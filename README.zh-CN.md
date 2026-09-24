# GPU Watch Dashboard v2.6

一款轻量、无需代理的仪表盘，专为实验室共享的 GPU 服务器设计。它能显示哪些 GPU 当前空闲、忙碌的 GPU 由谁在用，以及磁盘还剩多少空间。所有数据仅通过普通 SSH 采集。

[English](README.md) | **简体中文** | [繁體中文](README.zh-HK.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

![GPU Watch 仪表盘：实验室概览、会议截稿倒计时和各服务器的 GPU 卡片](docs/images/dashboard.png)

<sub>截图使用的是虚构的演示数据。界面语言为韩语，技术术语使用英语。</sub>

## 为什么选择 GPU Watch

在共享服务器上开始任务之前，通常需要先弄清三件事：哪块 GPU 空闲，忙碌的 GPU 由谁在用，磁盘空间是否够用。GPU Watch 在一个页面上回答这三个问题。

- **无需代理。** 每台 GPU 服务器只需提供 SSH 访问、`nvidia-smi` 和 `python3`，无需在服务器上安装或常驻任何程序。
- **轻量。** 后端只使用 Python 标准库和 SQLite。前端是无需构建步骤的原生 HTML、CSS 和 JavaScript。
- **严谨。** 不猜测进程归属，不显示完整命令行，也不会把过时的读数当作实时数据展示。

## 功能

### GPU 与服务器

- 每块 GPU 的实时状态：free（空闲）或 busy（忙碌）、利用率、显存（VRAM）、温度，以及已忙碌的时长或最近一次使用时间。
- 每块 GPU 上的进程及其用户、显存占用和简短的命令摘要。详情中还会显示 PID、启动时间和容器，但绝不显示完整命令行。
- 实验室切换、实验室整体概览（在线服务器、空闲与忙碌的 GPU、显存），以及“使用中”“全部空闲”“连接失败”“磁盘警告”等筛选。
- 活动徽章：🔥 高负载（近 7 天忙碌指数不低于 50%）、❄️ 长期闲置（7 天内没有持续 60 秒以上的使用）、⛔ 连接失败。

### 磁盘

- 每个文件系统的空闲、可用、已用和保留空间，使用率达到 90% 时发出警告。
- 按用户统计的用量，包括可读取的主目录、配置的路径以及 Docker 可写层。结果不完整时，下限值标记为 `≥`，估计值标记为 `≈`。

### 历史与趋势

- Recent Activity（最近活动）记录 busy/free 的切换，可按日期、服务器和用户筛选。连接 DOWN/UP 事件默认隐藏，也可以选择一并显示。
- 每台服务器近 7 天的忙碌指数和各用户的使用占比。
- LAB DAILY INDEX：最近 24 小时逐小时的显存 K 线，以及 30 天日均显存的变化趋势。

### 实验室工具

- 最多 6 个会议截稿倒计时（KST），支持 TBA 条目。计时器标题中的国旗表情由仓库自带的 SVG 文件绘制。
- 可选设置过期时间的公告板。作者用自己设定的密码编辑公告，管理员可用管理员 PIN 管理所有公告。
- 快捷链接到会议截稿网站、AI 服务状态页面和 AI 资讯。页面还会显示 Artificial Analysis Intelligence Index（前 29 个模型）。数据由服务器获取，因此 API 密钥不会到达浏览器。

### 日常使用

- 默认每 10 秒自动刷新。刷新时会保留所选标签页、滚动位置和键盘焦点，页面在后台时会放慢刷新频率。数据停止更新时会显示提示横幅。
- 支持宽度低至 320px 的屏幕，可完全使用键盘操作，并遵循系统的“减少动态效果”设置。配色符合 WCAG 2.2 AA 的对比度要求。

## 工作原理

```text
Browser ──HTTP──▶ Caddy  (IP allowlist, compression)
                    │
                    ▼
              server.py   (HTTP API · collector · maintenance)
               │      │
          SSH  │      └──▶ SQLite  (data/)
               ▼
          GPU servers  (nvidia-smi · /proc · df · du · docker)
```

1. 采集器每隔 `poll_interval_seconds`（10 秒）并行连接 `hosts.json` 中的所有主机，并在每台主机上执行 `nvidia-smi` 查询和一个小型内联 Python 探针。
2. 进程归属根据 `/proc` 中的 UID 和 NSS 用户名确定，并用 PID 启动时间和 GPU UUID 再次核对。无法确认时显示为未知，而不是猜测。
3. 每隔 `disk_poll_interval_seconds`（30 分钟），使用 `df`、带时间限制的 `du` 和 `docker ps --size` 测量磁盘用量。
4. 观测结果以时间区间的形式存入 SQLite。重叠的 GPU、进程和用户会合并计算，因此忙碌时间不会被重复统计。
5. 页面读取 `/api/snapshot`、`/api/events`、`/api/insights` 和 `/api/intelligence-index`。

### 状态判定规则

- 如果 GPU 上有计算进程、显存占用至少 500 MiB，或利用率至少 10%，则该 GPU 为 **busy**。两个阈值都可以配置。
- 持续占用显存的进程即使利用率为 0% 也算作忙碌，因此已被占用的 GPU 不会显示为空闲。
- 只要未能完整观测 GPU（无论是 SSH 失败还是 GPU 探测失败），服务器即为 **DOWN**。它的所有 GPU 都不计为可用，之前的读数也不会作为当前数据显示。

## 安全模型

- **访问控制。** Caddy 执行 IP 白名单。应用本身也会再次独立校验客户端 IP、`Host` 和 `Origin`。
- **写操作。** 修改公告和计时器需要同源请求以及密码或 PIN。哈希采用迭代 600,000 次的 PBKDF2-SHA256。失败的尝试会按子网和全局两个维度进行速率限制。
- **绝不发送到浏览器的内容。** 远程命令、stderr、完整命令行、环境变量、SSH 用户名、端口和密码。服务器卡片只显示经过校验的 IP 地址。
- **浏览器防护。** 严格的内容安全策略（CSP）会阻止内联脚本。图标和国旗均由本地提供，不从第三方主机加载。
- **容器。** 以非 root 用户运行，根文件系统只读，移除全部 capabilities，并启用 `no-new-privileges` 以及 PID、内存和日志限制。
- **机密信息。** SSH 密码、API 密钥和 PIN 哈希只存放在被 git 忽略的运行时目录（`secrets/`、`data/`、`operator-secrets/`）中，从不进入仓库。SSH 密码通过 `SSH_ASKPASS` 交给 OpenSSH，而不是放在命令行上。
- **传输。** 明文 HTTP 仅适用于可信的局域网。若要更大范围地开放，请先添加 TLS 和身份验证。

## 环境要求

| 位置 | 需要 |
|---|---|
| 仪表盘主机 | Python 3.12（仅标准库）和 OpenSSH 客户端，或 Docker |
| 每台 GPU 服务器 | 监控账号的 SSH 访问、带 `nvidia-smi` 的 NVIDIA 驱动、`python3`、`df` 和 `du`。Docker 为可选项，安装后可额外显示各容器的用量。 |
| 访问者 | 较新的网页浏览器 |

## 快速开始

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch

# 1. 填写你的服务器（参见下方“配置”）。
$EDITOR hosts.json

# 2. 创建管理员 PIN。脚本会输出一次性明文 PIN 的保存位置。
python3 scripts/admin-passphrase.py ensure

# 3. 启动仪表盘。
python3 server.py --host 127.0.0.1 --port 8787
```

打开 <http://127.0.0.1:8787/>。默认只允许本机回环地址访问。如需让局域网内的其他机器访问，请明确列出允许的地址。下面的地址仅为示例。

```sh
GPU_WATCH_ALLOWED_NETWORKS="127.0.0.0/8,::1/128,192.0.2.0/24" \
GPU_WATCH_ALLOWED_HOSTS="192.0.2.10:8787,127.0.0.1:8787,localhost:8787" \
python3 server.py --host 0.0.0.0 --port 8787
```

## 配置

### `hosts.json`

```json
{
  "poll_interval_seconds": 10,
  "disk_poll_interval_seconds": 1800,
  "busy_memory_threshold_mib": 500,
  "busy_utilization_threshold_percent": 10,
  "labs": [{ "id": "vision", "label": "VISION LAB" }],
  "hosts": [
    {
      "name": "atlas",
      "label": "atlas",
      "lab": "vision",
      "ssh_host": "192.0.2.11",
      "ssh_port": 22,
      "ssh_user": "gpuwatch",
      "ssh_identity_file": "~/.ssh/id_ed25519",
      "expected_gpu_count": 4,
      "note": "NVIDIA GeForce RTX 4090 x4",
      "owner": "Vision",
      "owner_type": "assigned",
      "location": "Room 301"
    }
  ]
}
```

| 主机字段 | 含义 |
|---|---|
| `name` | 唯一 ID（字母、数字、`.`、`_`、`-`）。省略 `ssh_host` 时，它也会被用作 SSH 别名。 |
| `label`、`lab` | 显示名称和所属实验室 |
| `ssh_host`、`ssh_port`、`ssh_user` | 连接目标 |
| `ssh_identity_file` | 密钥登录使用的私钥 |
| `ssh_password_file`、`ssh_options` | 密码登录。密码文件放在 `secrets/` 下，运行时读取。 |
| `display_ip` | 通过别名连接的主机在卡片上显示的 IP |
| `expected_gpu_count` | 服务器处于 DOWN 状态时仍然显示的 GPU 格数 |
| `note`、`owner`、`owner_type`、`location` | 卡片上的文字和徽章样式（`assigned` 或 `shared`） |
| `disk_user_paths` | 额外测量的按用户路径，格式为 `{ "user": …, "path": … }` |
| `collect_docker_usage` | 同时用 `docker ps --size` 测量 Docker 可写层。默认仅对 `nll` 实验室的主机开启。 |

其他顶层设置还包括探测超时、`collector_workers`、决定 🔥 和 ❄️ 徽章阈值的 `activity_policy`，以及保留期限。默认情况下，事件保留 180 天，公告保留 90 天，每日备份保留 14 天。

### 环境变量

| 变量 | 用途 | 默认值 |
|---|---|---|
| `GPU_WATCH_ALLOWED_NETWORKS` | 客户端 IP 白名单（以逗号分隔的 CIDR） | 本机回环 |
| `GPU_WATCH_ALLOWED_HOSTS` | 接受的 `Host` 请求头 | 本机回环 |
| `GPU_WATCH_TRUSTED_PROXY_NETWORKS` | 信任其 `X-Forwarded-For` 请求头的反向代理 | 本机回环 |
| `GPU_WATCH_SSH_CONFIG_FILE` | 要使用的 OpenSSH 配置文件的绝对路径 | 无 |
| `GPU_WATCH_SSH_IDENTITY_FILE` | 替代各主机设置的密钥文件 | 无 |
| `GPU_WATCH_ADMIN_PIN_HASH` | 管理员 PIN 哈希，用来替代 `data/admin_pin.hash` | 无 |
| `GPU_WATCH_RUNTIME_MODE` | `standalone`、`production` 或 `emergency` | `standalone` |
| `GPU_WATCH_BUILD_VERSION` | 页脚显示的版本号 | `VERSION` |

如需使用 Artificial Analysis 面板，请把 API 密钥放在 `secrets/artificial_analysis_api_key` 中。该文件必须是普通文件（不能是符号链接），权限为 `0600`。服务器每 6 小时更新一次指数，失败后每小时重试一次。超过 48 小时的数据会标记为过时。

## 部署

参考的生产环境由两个经过加固的容器组成。对外只发布 Caddy 的端口，应用只在内部 Docker 网络中运行。

- `Dockerfile` 基于以摘要固定的 `python:3.12-alpine` 镜像构建应用。
- `Dockerfile.caddy` 使用固定的提交和固定的依赖版本构建 Caddy。
- `deploy.sh` 是实验室生产主机的部署脚本。它先检查路径和权限，然后创建带校验和的 SQLite 在线备份。接着构建两个镜像，并验证候选容器（health、snapshot、collector）。在保留旧容器的前提下切换生产环境，检查白名单和运行状态，失败时自动回滚。若要在其他环境使用，请先修改与主机相关的路径和地址。

### Windows 应急备用

`emergency-local-fallback.ps1 -Action Status|Start|Stop` 仅在生产环境宕机时运行本地副本。只要能连上生产环境，不加 `-Force` 时 `Start` 就会拒绝执行。此外还要求 `VERSION` 和发布指纹一致，并确认有一次新的采集周期。防火墙只对实验室局域网开放。`Stop` 会移除监听端口、防火墙规则和临时密码文件。

## 运维

```sh
# 检查运行中容器的健康状态
docker exec gpu-watch-dashboard python3 /app/scripts/check_local.py --health-only

# 检查 SQLite 完整性和聚合不变量
python3 scripts/audit-data.py data/gpu_watch.sqlite3

# 从本地备份恢复（会校验目标路径和校验和）
sh scripts/restore-backup.sh /absolute/path/to/backup.sqlite3
```

维护任务每天创建 SQLite 备份，`deploy.sh` 每次部署前还会额外备份一次。没有自动异地备份；`scripts/offsite-backup.sh` 是手动工具。

## 开发与测试

```sh
python3 -m unittest discover -s tests -v   # Python 回归测试与契约测试
node tests/test_frontend.js                # 前端逻辑契约测试
```

测试还会锁定产品行为，例如状态判定规则、区间计算、安全响应头和界面文案。契约测试失败通常意味着用户可见的变化，需要有意识地做出决定。

## 项目结构

| 路径 | 作用 |
|---|---|
| `server.py` | HTTP API、采集器、存储和维护 |
| `gpu_watch/` | 身份验证、进程归属、安全辅助、历史记录和 Artificial Analysis 客户端 |
| `static/` | 前端（`index.html`、`app.js`、`styles.css`）以及自带的图标和国旗 |
| `hosts.json` | 服务器、实验室和采集设置 |
| `Caddyfile`、`Dockerfile`、`Dockerfile.caddy` | 边缘代理和容器镜像 |
| `deploy.sh` | 带备份和回滚的生产部署 |
| `run-dashboard.ps1`、`emergency-local-fallback.ps1` | Windows 应急采集器 |
| `scripts/` | 健康检查、数据审计、备份与恢复、管理员 PIN、SSH 辅助工具 |
| `tests/` | Python 和 Node 测试 |

## 致谢

国旗图标来自采用 CC-BY 4.0 许可的 [Twemoji](https://github.com/jdecked/twemoji)，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。服务图标归各自所有者所有，仅用于标示指向其状态页面的链接。

## 许可证

GPU Watch 采用 [MIT 许可证](LICENSE) 发布。随附的国旗图标和服务图标不在此许可证范围内，详见上方的致谢部分。
