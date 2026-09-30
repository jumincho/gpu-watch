# GPU Watch Dashboard v2

一款轻量、无需代理的仪表盘，专为实验室共享的 GPU 服务器设计。它能显示哪些 GPU 当前空闲、忙碌的 GPU 由谁在用，以及磁盘还剩多少空间。所有数据仅通过普通 SSH 采集。

**v2** · 2026-09-30 发布 · GPT-6.1 Sol (max) · [发布验证](RELEASE_VALIDATION.md)

[English](README.md) | **简体中文** | [繁體中文](README.zh-HK.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

![GPU Watch 仪表盘：实验室概览、会议截稿倒计时和各服务器的 GPU 卡片](docs/images/dashboard.png)

<sub>截图使用的是虚构的演示数据。界面语言为韩语，技术术语使用英语。</sub>

> [!NOTE]
> 公开版附带的是虚构的服务器清单：虚构的服务器名称、文档专用范围的 IP 地址和示例 SSH 路径。部署前请换成你自己的配置。凭据、数据库和内部运维文档都不包含在仓库中。

## 为什么选择 GPU Watch

在共享服务器上开始任务之前，通常需要先弄清三件事：哪块 GPU 空闲，忙碌的 GPU 由谁在用，磁盘空间是否够用。GPU Watch 在一个页面上回答这三个问题。

- **无需代理。** 每台 GPU 服务器只需提供 SSH 访问、`nvidia-smi` 和 `python3`，服务器上没有任何常驻程序。
- **轻量。** 后端只使用 Python 标准库和 SQLite。前端是无需构建步骤的原生 HTML、CSS 和 JavaScript。
- **严谨。** 不猜测进程归属，不显示完整命令行，也不会把过时的读数当作实时数据展示。

## v2 的变化

- **Intelligence Index 按计划刷新。** 一个小型调度线程会按配额安排的时间执行刷新，即使没有人打开页面也不例外。
- **更严格的归属判定。** 无法读取 `/proc/<pid>/status` 时，不再因为 `/proc` 目录归 root 所有就认定进程属于 root，而是交由经过校验的 `ps` 查询判断。该查询可读取最长 64 个字符的用户名，并把 `?` 或被截断的名称（以 `+` 结尾）视为未知。只有所有者是 root 时，才用容器名代替用户名显示。
- **所有者未知的进程仍会显示，但不归属。** 所有者未知的已验证进程仍会列出。只有当该 GPU 上所有进程的所有者都已知时，GPU 时间才会计入用户；否则该区间保持未归属。
- **磁盘警告改用服务器合计。** 警告徽章现在与 Disk 标签页的 Used 使用同一个合计值，不再取最满的那个文件系统。徽章的提示文字会说明该值是全部文件系统的合计。
- **密码管理器提示。** 密码和 PIN 输入框带有让常见密码管理器忽略的提示。
- **不再显示发布页脚。** 发布信息只保留在 `VERSION`、包元数据、文档和 GitHub Releases 中。
- **界面细节调整。** 即使在 320px 宽的屏幕上，最长的主机名和 IP 也能显示在一行内。返回顶部按钮不再遮挡页面底部的链接；在中等宽度下，状态图例和筛选按钮分成两行显示。

## 功能

### GPU 与服务器

- 每块 GPU 的实时状态：free（空闲）或 busy（忙碌）、利用率、显存（VRAM）、温度，以及已忙碌的时长或最近一次使用时间。
- 每块 GPU 上的进程及其用户、显存占用和简短的命令摘要。详情中还会显示 PID、启动时间和容器，但绝不显示完整命令行。
- 实验室切换、实验室整体概览（在线服务器、空闲与忙碌的 GPU、显存），以及“使用中”“全部空闲”“连接失败”“磁盘警告”等筛选。
- 活动徽章：🔥 高负载（近 7 天忙碌指数不低于 50%）、❄️ 长期闲置（7 天内没有持续 60 秒以上的使用）、⛔ 连接失败。

### 磁盘

- 每个文件系统的空闲、可用、已用和保留空间。服务器所有文件系统的合计使用率达到 90% 时显示 Disk 警告。
- 按用户统计的用量，包括可读取的主目录、配置的路径以及 Docker 可写层。结果不完整时，下限值标记为 `≥`，估计值标记为 `≈`。

### 历史与趋势

- Recent Activity（最近活动）记录 busy/free 的切换，可按日期、服务器和用户筛选。连接 DOWN/UP 事件默认隐藏，也可以选择一并显示。
- 每台服务器近 7 天的忙碌指数和各用户的使用占比。
- LAB DAILY INDEX：最近 24 小时逐小时的显存 K 线，以及 30 天日均显存的变化趋势。

### 实验室工具

- 最多 6 个会议截稿倒计时（KST），支持 TBA 条目。每个计时器的颜色保持不变，标题中的国旗表情由仓库自带的 SVG 文件绘制。
- 可选设置过期时间的公告板。作者用自己设定的密码编辑公告，管理员可用管理员 PIN 管理所有公告。
- 快捷链接到会议截稿网站、AI 服务状态页面和 AI 资讯。页面还会显示 Artificial Analysis Intelligence Index（前 29 个模型）。数据由服务器获取，因此 API 密钥不会到达浏览器。

### 日常使用

- 默认每 10 秒自动刷新。刷新时会保留所选标签页、滚动位置和键盘焦点，页面在后台时会放慢刷新频率。数据停止更新时会显示提示横幅。
- 点击表单外部不会关闭表单，关闭表单时会清除其中的密码。
- 支持宽度低至 320px 的屏幕，可完全使用键盘操作，并遵循系统的“减少动态效果”设置。文字对比度达到 WCAG AA 标准。

## 工作原理

```text
Browser ──HTTP──▶ Caddy  (IP allowlist, compression)
                    │
                    ▼
              server.py   (HTTP API · collector · maintenance · index scheduler)
               │      │
          SSH  │      └──▶ SQLite  (data/)
               ▼
          GPU servers  (nvidia-smi · /proc · df · du · docker)
```

1. 采集器每隔 `poll_interval_seconds`（10 秒）并行连接 `hosts.json` 中的所有主机，并在每台主机上执行 `nvidia-smi` 查询和一个小型内联 Python 探针。
2. 进程所有者根据 `/proc/<pid>/status` 中的有效 UID 和 NSS 用户名确定；没有用户名时使用 `uid:<数字>`。结果还会用 PID 启动时间和 GPU UUID 再次核对。无法确认时显示为所有者未知，而不是猜测。
3. 每隔 `disk_poll_interval_seconds`（30 分钟），由独立的工作线程使用 `df`、带时间限制的 `du` 和 `docker ps --size` 测量磁盘用量，也可以改用可选的特权助手。
4. 观测结果以时间区间的形式存入 SQLite。重叠的 GPU、进程和用户会合并计算，因此忙碌时间不会被重复统计。
5. 页面读取 `/api/snapshot`、`/api/events`、`/api/insights` 和 `/api/intelligence-index`。

### 状态判定规则

- 如果 GPU 上有计算进程、显存占用至少 500 MiB，或利用率至少 10%，则该 GPU 为 **busy**。两个阈值都可以配置。
- 持续占用显存的进程即使利用率为 0% 也算作忙碌，因此已被占用的 GPU 不会显示为空闲。这些数字衡量的是占用情况，而不是计算效率。
- 未能完整观测 GPU 时，服务器即为 **DOWN**。诊断信息会区分超时、SSH 连接或认证失败，以及驱动或设备故障。它的所有 GPU 都不计为可用，之前的读数也不会作为当前数据显示；磁盘探测成功也不会让它恢复为 UP。
- 短暂的连接错误会在同一时间预算内重试一次。失败期间的时间绝不会被补记为使用时间。
- 针对 NVIDIA 驱动与库版本不匹配，提供一个带防护的备用方案，只能为明确批准的主机启用。正常情况下始终使用原生驱动。

### 时间与指数

- 多个用户共用一块 GPU 时，忙碌时间在不同的已确认所有者之间平均分配。只要该 GPU 上有任何进程的所有者未知，该区间就保持未归属，而不会计到已知用户头上。
- 不足 60 秒的会话仍会计入历史和合计，但不会解除 ❄️ 徽章。判定长期闲置需要 7 天的追踪期，但不要求 100% 的观测覆盖率。
- 近 7 天忙碌指数是忙碌时间除以已观测的 GPU 槽位时间。未观测的时间不算作空闲；四舍五入后覆盖率为 100% 时显示 `관측 7일 / 7일`。
- LAB DAILY INDEX 按 KST 自然日，把每块 GPU 以观测时间加权的平均显存相加。原始显存以 16 MiB 为单位存储。

## 安全模型

- **访问控制。** Caddy 执行 IP 白名单。应用本身也会再次独立校验客户端 IP、`Host` 和 `Origin`。
- **写操作。** 修改公告和计时器需要同源请求以及密码或 PIN。哈希采用迭代 600,000 次的 PBKDF2-SHA256。哈希校验最多同时进行 2 个，失败的尝试会按子网和全局两个维度进行速率限制。
- **密码输入框。** 输入内容会被遮盖，关闭自动填充，带有密码管理器忽略提示，并在对话框关闭时清除。浏览器仍可能提示保存密码，网页无法完全阻止这一点。
- **绝不发送到浏览器的内容。** 远程命令、stderr、完整命令行、环境变量、SSH 用户名、端口和密码。服务器卡片只显示经过校验的 IP 地址。
- **浏览器防护。** 严格的内容安全策略（CSP）会阻止内联脚本和第三方脚本。静态文件只按明确的允许列表提供，并拒绝路径穿越和符号链接。图标和国旗均由本地提供。
- **容器。** 以非 root 用户运行，根文件系统只读，移除全部 capabilities，并启用 `no-new-privileges` 以及 PID、内存和日志限制。
- **机密信息。** SSH 密码、API 密钥和 PIN 哈希只存放在被 git 忽略的运行时目录（`secrets/`、`data/`、`operator-secrets/`）中，从不进入仓库。SSH 密码通过 `SSH_ASKPASS` 交给 OpenSSH，而不是放在命令行上。
- **传输。** 明文 HTTP 仅适用于可信的局域网。IP 过滤不等于加密；若要更大范围地开放，请先添加 TLS 和身份验证。

## 环境要求

| 位置 | 需要 |
|---|---|
| 仪表盘主机 | Python 3.12（仅标准库）和 OpenSSH 客户端，或 Docker |
| 每台 GPU 服务器 | 监控账号的 SSH 访问、带 `nvidia-smi` 的 NVIDIA 驱动、`python3`（探针兼容 Python 3.6）、`df` 和 GNU `du`。不需要主目录。Docker 为可选项，安装后可额外显示各容器的用量。使用特权磁盘助手时还需要 `sudo`。 |
| 访问者 | 较新的网页浏览器 |

## 快速开始

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch

# 1. 填写你的服务器（参见下方“配置”），并确认 SSH 密钥和 known_hosts。
$EDITOR hosts.json

# 2. 创建管理员 PIN。脚本会输出一次性明文 PIN 的保存位置。
python3 scripts/admin-passphrase.py ensure

# 3. 启动仪表盘。
python3 server.py --host 127.0.0.1 --port 8787
```

打开 <http://127.0.0.1:8787/>。请把管理员 PIN 保存在安全的地方，然后删除明文文件。

默认只允许本机回环地址访问。如需让局域网内的其他机器访问，请明确列出允许的地址。下面的地址仅为示例。

```sh
GPU_WATCH_ALLOWED_NETWORKS="127.0.0.0/8,::1/128,192.0.2.0/24" \
GPU_WATCH_ALLOWED_HOSTS="192.0.2.10:8787,127.0.0.1:8787,localhost:8787" \
python3 server.py --host 0.0.0.0 --port 8787
```

## 配置

### `hosts.json`

仓库自带的 `hosts.json` 描述的是一个虚构的实验室，请换成你自己的服务器。

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
| `display_ip` | 通过别名连接的主机在卡片上显示的 IP。必须是有效的 IP 地址。 |
| `expected_gpu_count` | 服务器处于 DOWN 状态时仍然显示的 GPU 格数 |
| `note`、`owner`、`owner_type`、`location` | 卡片上的文字和徽章样式（`assigned` 或 `shared`） |
| `disk_user_paths` | 额外测量的按用户路径，格式为 `{ "user": …, "path": … }` |
| `collect_docker_usage` | 同时用 `docker ps --size` 测量 Docker 可写层。默认仅对 `nll` 实验室的主机开启。 |
| `privileged_disk_helper` | 通过已安装的 root 助手测量磁盘用量。不能与 `disk_user_paths` 同时使用。 |

其他顶层设置包括探测超时、`collector_workers`、决定 🔥 和 ❄️ 徽章阈值的 `activity_policy`，以及保留期限：

| 数据 | 默认保留期限 |
|---|---|
| 事件和每日指数 | 180 天 |
| 原始区间 | 8 天 |
| 已删除或已过期的公告 | 删除或过期后 90 天。未设过期时间的公告会一直保留到被删除。 |
| 每日 SQLite 备份 | 14 天 |
| 部署前备份 | 30 天 |

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
| `GPU_WATCH_BUILD_VERSION` | 健康检查 API 报告的版本号。`deploy.sh` 要求它与 `VERSION` 一致。 | 包版本 |

### Artificial Analysis

如需显示 Intelligence Index，请把 API 密钥放在 `secrets/artificial_analysis_api_key` 中。该文件必须是归应用用户所有的普通文件（不能是符号链接），权限为 `0600`，并放在受限的目录中。每次尝试都会重新读取密钥，因此无需重启即可轮换。服务器拒绝重定向，因此密钥不会被发送到其他源。浏览器只会收到缓存的排名。

| 情况 | 行为 |
|---|---|
| 平时 | 根据响应中的 `X-RateLimit-*` 头和页数，把完整刷新均匀分布在配额周期内，并预留最多 8 次请求 |
| 每天 100 次、共 4 页 | 大约每小时一次；间隔不短于 15 分钟 |
| 配额不足或收到 `Retry-After` | 等到配额重置或指定的时间 |
| 没有速率限制头 | 每 6 小时一次 |
| 刷新失败 | 1 小时后重试；如果 `Retry-After` 要求更久，则以其为准 |
| 数据超过 48 小时 | 标记为过时 |

- 每个请求在发出前就先计数，因此重启或网络错误不会让服务器误以为还有剩余次数。只有所有页面都通过校验后，才会发布新的排名。
- 配额状态（上限、剩余次数、重置时间、页数、下次尝试时间）与密钥分开，保存在 `data/` 中缓存的旁边。
- 无论有没有人在看，一个小型调度线程都会按这些时间执行刷新。页面本身每 5 分钟检查一次服务器缓存。
- 指数版本按 API 返回的值原样显示（目前为 `4.3`），不会凭空加上补丁版本号。较长的模型变体名称会被简写，例如 `(max)`。

## 特权磁盘助手

在监控账号无法读取其他用户主目录的服务器上，按用户统计的合计只能部分计入。对于这类服务器，可以安装一个小型 root 助手，它只运行 GPU Watch 自己的磁盘采集程序。

```sh
# 生成可供审阅的安装脚本（此步骤不会安装任何东西）。
python3 scripts/provision-disk-helper.py --user gpuwatch --output disk-installer.py

# 审阅 disk-installer.py 后复制到 GPU 服务器，并以管理员身份运行。
sudo python3 -I disk-installer.py
```

安装脚本会创建 `/usr/local/libexec/gpu-watch-disk`、一条只允许监控账号不带参数运行该助手的 sudoers 规则，以及 `/var/cache/gpu-watch/` 缓存目录。助手不接受任何路径、命令或环境变量输入。它使用固定的 `PATH`，阻止并发扫描，将结果缓存 5 分钟，并以较低的 CPU 优先级运行。不会安装守护进程，也不会保存管理员密码。

安装完成后，为该主机设置 `"privileged_disk_helper": true`。如果助手不存在，GPU Watch 会在同一时间预算内改用普通权限扫描，并把按用户的用量标记为部分统计。修改磁盘探针后，请重新安装助手。

## 部署

参考的生产环境由两个经过加固的容器组成。对外只发布 Caddy 的端口，应用只在内部 Docker 网络中运行。

- `Dockerfile` 基于以摘要固定的 `python:3.12-alpine` 镜像构建应用。
- `Dockerfile.caddy` 使用固定的提交和固定的依赖版本构建 Caddy。
- `deploy.sh` 是实验室生产主机的部署脚本。它先检查路径和权限，然后创建带校验和的 SQLite 在线备份。接着构建两个镜像，并验证候选容器（health、snapshot、collector）。在保留旧容器的前提下切换生产环境，检查白名单和运行状态，失败时回滚容器、SSH 运行时文件和数据库。它只清理本项目自己未使用的构建镜像。若要在其他环境使用，请先修改与主机相关的路径和地址。

| 容器 | 限制 |
|---|---|
| 应用 | 非 root 用户、只读根文件系统、移除 capabilities、`no-new-privileges`、512 MiB 内存、128 个 PID |
| Caddy | 只读根文件系统、192 MiB 内存、64 个 PID |

> [!IMPORTANT]
> 发布指纹是对 `VERSION`、`server.py`、`hosts.json`、`gpu_watch/` 和 `static/` 计算的哈希。生产环境和应急副本的指纹必须一致。脚本、文档和测试不在指纹范围内，因此还要比较完整的文件清单。

### Windows 应急备用

`emergency-local-fallback.ps1 -Action Status|Start|Stop` 仅在生产环境宕机时运行本地副本。使用前请先修改其中的路径、地址和 SSH 配置。

- 只要能连上生产环境，`Start` 就会拒绝执行；只有为有意接管而加上 `-Force` 时才会继续。即便如此，它仍要求 `VERSION` 和发布指纹一致，并且必须完成一次新的采集周期才会报告成功。
- 应用以普通用户身份运行。只有为实验室局域网添加防火墙规则时才需要 Windows UAC。
- `Stop` 会先核对进程的确切身份，再移除监听端口、对应的防火墙规则和临时密码文件。同一端口上的无关进程不受影响。
- 本地数据库是独立的。在切回生产环境之前，请核对宕机期间新建的公告和计时器。

## 运维

```sh
# 检查运行中容器的健康状态
docker exec gpu-watch-dashboard python3 /app/scripts/check_local.py --health-only --expected-build-version 2

# 检查 SQLite 完整性和聚合不变量
python3 scripts/audit-data.py data/gpu_watch.sqlite3

# 从本地备份恢复（会校验目标路径和校验和）
sh scripts/restore-backup.sh /absolute/path/to/backup.sqlite3
```

维护任务每天创建 SQLite 备份，`deploy.sh` 每次部署前还会额外备份一次。没有自动异地备份；`scripts/offsite-backup.sh` 是手动工具。

## 开发与测试

```sh
python3 -m unittest discover -s tests -v   # 270 个 Python 回归测试与契约测试
node tests/test_frontend.js                # 21 项前端检查
```

少数测试只适用于 Windows 或 POSIX，在其他平台上会被跳过。请在 Windows 或装有 GNU coreutils 的 Linux 上运行完整测试；精简的 Alpine 应用镜像使用 BusyBox，无法运行 GNU `du` 相关的测试夹具。

测试还会锁定产品行为，例如状态判定规则、区间计算、安全响应头和界面文案。契约测试失败通常意味着用户可见的变化，需要有意识地做出决定。请保留迁移、有意义的回归测试、已验证的备份和计时器颜色。修改界面时，优先使用职责单一的模块，不要重写庞大的存储或探针代码，并保持计算规则清晰明确。

## 项目结构

| 路径 | 作用 |
|---|---|
| `server.py` | HTTP API、采集器、存储和维护 |
| `gpu_watch/` | 身份验证、进程归属、安全辅助、历史记录，以及带配额追踪和调度器的 Artificial Analysis 客户端 |
| `static/` | 前端（`index.html`、`app.js`、`styles.css`）以及自带的图标和国旗 |
| `hosts.json` | 服务器、实验室和采集设置（虚构示例） |
| `Caddyfile`、`Dockerfile`、`Dockerfile.caddy` | 边缘代理和容器镜像 |
| `deploy.sh` | 带备份和回滚的生产部署 |
| `run-dashboard.ps1`、`emergency-local-fallback.ps1` | Windows 应急采集器 |
| `scripts/` | 健康检查、数据审计、备份与恢复、管理员 PIN、SSH 辅助工具，以及磁盘助手安装脚本生成器 |
| `tests/` | Python 和 Node 测试 |

## 发布信息

**v2** 为正式发布版本，于 2026-09-30 由 GPT-6.1 Sol (max) 发布。仪表盘不显示发布页脚；发布信息保留在 `VERSION`、`gpu_watch/__init__.py`、本 README 和 GitHub Releases 中。

[RELEASE_VALIDATION.md](RELEASE_VALIDATION.md) 列出了本次发布通过的检查。它记录的是该源码版本经过测试的行为，并不保证在今后的所有环境或故障下都成立。

## 致谢

国旗图标来自采用 CC-BY 4.0 许可的 [Twemoji](https://github.com/jdecked/twemoji)，详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。服务图标归各自所有者所有，仅用于标示指向其状态页面的链接。

## 许可证

GPU Watch 采用 [MIT 许可证](LICENSE) 发布。随附的国旗图标和服务图标不在此许可证范围内，详见上方的致谢部分。
