# GPU Watch Dashboard v2.5

一個輕量、無需代理程式的儀表板，專為實驗室共用的 GPU 伺服器而設。它會顯示哪些 GPU 現時空閒、忙碌的 GPU 由誰使用，以及磁碟尚餘多少空間。所有數據只透過普通 SSH 收集。

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE) ![Python 3.12](https://img.shields.io/badge/python-3.12-3776ab.svg) ![Dependencies: standard library only](https://img.shields.io/badge/dependencies-stdlib%20only-success.svg) ![Frontend: no build step](https://img.shields.io/badge/frontend-no%20build%20step-informational.svg)

**v2.5** · 2026-10-03 發佈 · GPT-6 Astra Ultra · [發佈驗證](RELEASE_VALIDATION.md)

[English](README.md) | [简体中文](README.zh-CN.md) | **繁體中文** | [日本語](README.ja.md) | [한국어](README.ko.md)

![GPU Watch 儀表板：實驗室概覽、會議截稿倒數及各伺服器的 GPU 卡片](docs/images/dashboard.png)

<sub>截圖使用虛構的示範數據。介面語言為韓文，技術用語則以英文標示。</sub>

> [!NOTE]
> 公開版附帶的是虛構的伺服器清單：虛構的伺服器名稱、文件專用範圍的 IP 地址及示例 SSH 路徑。部署前請換成你自己的設定。憑證、資料庫及內部營運文件都不包括在儲存庫內。

## 為何選用 GPU Watch

在共用伺服器上開始工作前，通常要先弄清三件事：哪張 GPU 空閒，忙碌的 GPU 由誰使用，磁碟空間是否足夠。GPU Watch 在同一頁面內解答這三個問題。

- **無需代理程式。** 每部 GPU 伺服器只需提供 SSH 存取、`nvidia-smi` 及 `python3`，伺服器上沒有任何常駐程式。
- **輕量。** 後端只使用 Python 標準函式庫及 SQLite。前端是毋須建置步驟的原生 HTML、CSS 及 JavaScript。
- **審慎。** 不會猜測進程的擁有者，不會顯示完整指令行，亦不會把過時的讀數當作即時數據顯示。

## v2.5 的改動

- **發佈前核對 GPU 觀測值。** 必需的 VRAM 數據缺失或無效時，不會把 GPU 顯示為空閒，亦不會計入觀測時間。使用率及溫度屬選用項目，缺失時仍可保留有效的 VRAM 觀測。
- **每小時記錄以目前的 GPU 清單為準。** 已停用 GPU 的過往 VRAM 數據不會再計入每小時的 Lab Pulse 圖表。
- **加強 HTTP 及診斷資料的邊界。** 拒絕重複的 `Host` 標頭及不完整的 JSON 請求本文。維護 health 回應只顯示例外類型，不會顯示可能包含敏感資料的例外文字。
- **舊請求不會干擾新草稿。** 延遲到達的告示或計時器儲存/刪除回應會更新已儲存的數據，同時保留新編輯視窗的草稿、焦點及處理中的按鈕狀態。計時器 PIN 亦會在對話框關閉前清除。
- **明確限制 Windows 緊急副本的檔案權限。** 啟動時，會把執行期檔案上繼承或明確保留的廣泛 ACL 權限，替換為目前用戶、SYSTEM 及 Administrators 的權限，並使用實際通過核對的 SSH 金鑰。
- **保留現有管理員 PIN。** `ensure` 會保留有效的舊雜湊，並在驗證成功後升級。已儲存的雜湊無效時，需要明確修復；啟動過程不會自行更換 PIN。

現有介面、用語、收集範圍及輕量架構維持不變。[發佈驗證](RELEASE_VALIDATION.md) 記錄了測試範圍及其限制，並不代表對日後所有環境或故障的保證。

## 功能

### GPU 與伺服器

- 每張 GPU 的即時狀態：free（空閒）或 busy（忙碌）、使用率、顯示記憶體（VRAM）、溫度，以及已忙碌的時間或最近一次使用時間。
- 每張 GPU 上的進程及其用戶、記憶體用量和簡短的指令摘要。詳細資料會加上 PID、啟動時間及容器，但絕不顯示完整指令行。
- 實驗室切換、實驗室整體概覽（在線伺服器、空閒與忙碌的 GPU、VRAM），以及「使用中」「全部空閒」「連線失敗」「磁碟警告」等篩選。
- 活動徽章：🔥 高使用率（近 7 日忙碌指數達 50% 或以上）、❄️ 長期閒置（7 日內沒有持續 60 秒或以上的使用）、⛔ 連線失敗。

### 磁碟

- 每個檔案系統的空閒、可用、已用及保留空間。伺服器所有檔案系統的總計使用率達 90% 時，會顯示 Disk 警告。
- 按用戶統計的用量，涵蓋可讀取的主目錄、已設定的路徑及 Docker 可寫層。結果不完整時，下限值以 `≥` 標示，估計值以 `≈` 標示。

### 記錄與趨勢

- Recent Activity（最近活動）記錄 busy/free 的轉換，可按日期、伺服器及用戶篩選。連線 DOWN/UP 事件預設隱藏，亦可選擇一併顯示。
- 每部伺服器近 7 日的忙碌指數及各用戶的使用比例。
- LAB DAILY INDEX：最近 24 小時每小時的 VRAM 陰陽燭圖，以及 30 日每日平均 VRAM 的趨勢。

### 實驗室工具

- 最多 6 個會議截稿倒數（KST），支援 TBA 項目。每個計時器的顏色保持不變，標題中的國旗表情符號以儲存庫內附的 SVG 檔案繪製。
- 可選擇設定到期時間的告示板。作者以自己設定的密碼編輯告示，管理員則可用管理員 PIN 管理所有告示。
- 快速連結至會議截稿網站、AI 服務狀態頁面及 AI 資訊。頁面亦會顯示 Artificial Analysis Intelligence Index（首 29 個模型）。數據由伺服器取得，因此 API 金鑰不會傳到瀏覽器。

### 日常使用

- 預設每 10 秒自動更新。更新時會保留已選的分頁、滾動位置及鍵盤焦點，頁面在背景時會放慢更新頻率。數據停止更新時會顯示提示橫幅。
- 在表單以外的地方點擊不會關閉表單，關閉表單時會清除其中的密碼。
- 已檢查的版面支援闊度低至 320px 的屏幕，並依從系統的「減少動態效果」設定。鍵盤導覽及文字對比度樣本已按相關 WCAG AA 標準檢查，但這並非完整的無障礙認證。

## 運作原理

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

1. 收集器每隔 `poll_interval_seconds`（10 秒）並行連接 `hosts.json` 內的所有主機，並在每部主機上執行 `nvidia-smi` 查詢及一個小型內嵌 Python 探測程式。
2. 進程擁有者根據 `/proc/<pid>/status` 內的有效 UID 及 NSS 名稱判定；沒有名稱時則使用 `uid:<數字>`。結果會再以 PID 啟動時間及 GPU UUID 核對。無法確認時會顯示為擁有者不明，而不會猜測。
3. 每隔 `disk_poll_interval_seconds`（30 分鐘），由獨立的工作執行緒以 `df`、設有時限的 `du` 及 `docker ps --size` 量度磁碟用量，亦可改用可選的特權輔助程式。
4. 觀測結果以時間區間的形式儲存於 SQLite。重疊的 GPU、進程及用戶會合併計算，因此忙碌時間不會重複計算。
5. 頁面讀取 `/api/snapshot`、`/api/events`、`/api/insights` 及 `/api/intelligence-index`。

### 狀態判定規則

- 如 GPU 上有運算進程、VRAM 用量至少 500 MiB，或使用率至少 10%，該 GPU 即為 **busy**。兩個門檻均可設定。
- 持續佔用 VRAM 的進程即使使用率為 0%，亦視為忙碌，因此已被預留的 GPU 不會顯示為空閒。這些數字反映的是佔用情況，而非運算效率。
- 未能完整觀測 GPU 時，伺服器即為 **DOWN**。診斷資料會區分逾時、SSH 連線或驗證失敗，以及驅動程式或裝置故障。它的所有 GPU 都不計作可用，之前的讀數亦不會當作現時數據顯示；磁碟探測成功亦不會令它恢復為 UP。
- 短暫的連線錯誤會在同一時間預算內重試一次。失敗期間的時間絕不會補記為使用時間。
- 針對 NVIDIA 驅動程式與程式庫版本不符的情況，設有附防護措施的後備方案，只可為明確批准的主機啟用。正常情況下一律使用原生驅動程式。

### 時間與指數

- 多名用戶共用一張 GPU 時，忙碌時間會在不同的已確認擁有者之間平均分配。只要該 GPU 上有任何進程的擁有者不明，該區間便保持未歸屬，不會計到已知用戶身上。
- 不足 60 秒的工作階段仍會計入記錄及總計，但不會解除 ❄️ 徽章。判定長期閒置需要 7 日的追蹤期，但不要求 100% 的觀測覆蓋率。
- 近 7 日忙碌指數是忙碌時間除以已觀測的 GPU 槽位時間。未觀測的時間不當作閒置；四捨五入後覆蓋率為 100% 時會顯示 `관측 7일 / 7일`。
- LAB DAILY INDEX 按 KST 曆日，把每張 GPU 以觀測時間加權的平均 VRAM 相加。原始 VRAM 以 16 MiB 為單位儲存。

## 安全模型

- **存取控制。** Caddy 執行 IP 白名單。應用程式本身亦會再次獨立核對用戶端 IP、`Host` 及 `Origin`。
- **寫入操作。** 修改告示及計時器需要同源請求，以及密碼或 PIN。雜湊採用迭代 600,000 次的 PBKDF2-SHA256。雜湊核對最多同時進行 2 個，失敗的嘗試會按子網絡及整體兩方面進行速率限制。
- **密碼輸入欄。** 輸入內容會被遮蓋，並關閉自動完成，附有密碼管理工具略過提示，且會在對話框關閉時清除。瀏覽器仍可能提示儲存密碼，網頁無法完全阻止。
- **絕不傳送到瀏覽器的內容。** 遠端指令、stderr、完整指令行、環境變數、SSH 用戶名稱、連接埠及密碼。伺服器卡片只顯示經核實的 IP 地址。
- **瀏覽器防護。** 嚴格的內容安全政策（CSP）會封鎖內嵌及第三方指令碼。靜態檔案只按明確的允許清單提供，並拒絕路徑穿越及符號連結。圖示及國旗均由本機提供。
- **容器。** 以非 root 用戶執行，根檔案系統唯讀，移除所有 capabilities，並啟用 `no-new-privileges` 以及 PID、記憶體及日誌限制。
- **機密資料。** SSH 密碼、API 金鑰及 PIN 雜湊只存放於被 git 忽略的執行期資料夾（`secrets/`、`data/`、`operator-secrets/`），從不放入儲存庫。SSH 密碼透過 `SSH_ASKPASS` 交給 OpenSSH，而不會放在指令行上。
- **傳輸。** 純文字 HTTP 只適用於可信任的區域網絡。IP 過濾並不等於加密；如要向更大範圍開放，請先加入 TLS 及身份驗證。

## 系統要求

| 位置 | 需要 |
|---|---|
| 儀表板主機 | Python 3.12（只用標準函式庫）及 OpenSSH 用戶端，或 Docker |
| 每部 GPU 伺服器 | 監察帳戶的 SSH 存取、附 `nvidia-smi` 的 NVIDIA 驅動程式、`python3`（探測程式兼容 Python 3.6）、`df` 及 GNU `du`。不需要主目錄。Docker 為選用，安裝後可額外顯示各容器的用量。使用特權磁碟輔助程式時另需 `sudo`。 |
| 瀏覽者 | 較新的網頁瀏覽器 |

## 快速開始

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch

# 1. 填寫你的伺服器（見下方「設定」），並核對 SSH 金鑰及 known_hosts。
$EDITOR hosts.json

# 2. 建立管理員 PIN。腳本會顯示一次性純文字 PIN 的儲存位置。
python3 scripts/admin-passphrase.py ensure

# 3. 啟動儀表板。
python3 server.py --host 127.0.0.1 --port 8787
```

開啟 <http://127.0.0.1:8787/>。請把管理員 PIN 存放在安全的地方，然後刪除純文字檔案。日後如需設定新的 4 位數 PIN，請執行 `python3 scripts/admin-passphrase.py rotate`。

預設只容許本機回送（loopback）用戶端連線。如要讓區域網絡內的其他電腦瀏覽，請明確列出容許的對象。以下地址只作示例。

```sh
GPU_WATCH_ALLOWED_NETWORKS="127.0.0.0/8,::1/128,192.0.2.0/24" \
GPU_WATCH_ALLOWED_HOSTS="192.0.2.10:8787,127.0.0.1:8787,localhost:8787" \
python3 server.py --host 0.0.0.0 --port 8787
```

## 設定

### `hosts.json`

儲存庫內附的 `hosts.json` 描述的是一個虛構的實驗室，請換成你自己的伺服器。

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

| 主機欄位 | 意思 |
|---|---|
| `name` | 唯一 ID（英文字母、數字、`.`、`_`、`-`）。省略 `ssh_host` 時，它亦會用作 SSH 別名。 |
| `label`、`lab` | 顯示名稱及所屬實驗室 |
| `ssh_host`、`ssh_port`、`ssh_user` | 連線目標 |
| `ssh_identity_file` | 金鑰登入所用的私密金鑰 |
| `ssh_password_file`、`ssh_options` | 密碼登入。密碼檔案放在 `secrets/` 之下，於執行時讀取。 |
| `display_ip` | 以別名連線的主機在卡片上顯示的 IP。必須是有效的 IP 地址。 |
| `expected_gpu_count` | 伺服器處於 DOWN 狀態時仍然顯示的 GPU 格數 |
| `note`、`owner`、`owner_type`、`location` | 卡片上的文字及徽章樣式：`assigned` 或 `shared`（`owner` 為 `공용` 時亦視作 shared），另有實驗室專用的 `physical_ai_2` 及 `app_serving` 樣式 |
| `disk_user_paths` | 額外量度的按用戶路徑，格式為 `{ "user": …, "path": … }` |
| `collect_docker_usage` | 一般探針同時以 `docker ps --size` 量度 Docker 可寫層。預設只為 `lab` 是 `nll` 的主機啟用；其他實驗室請明確設定。`privileged_disk_helper` 按固定政策一律收集 Docker 用量，不受此選項影響。 |
| `privileged_disk_helper` | 透過已安裝的 root 輔助程式量度磁碟用量。不可與 `disk_user_paths` 同時使用。 |

其他頂層設定包括探測逾時、`collector_workers`、決定 🔥 及 ❄️ 徽章門檻的 `activity_policy`，以及保留期限：

| 數據 | 預設保留期限 |
|---|---|
| 事件及每日指數 | 180 日 |
| 原始區間 | 8 日 |
| 已刪除或已到期的告示 | 刪除或到期後 90 日。沒有設定到期時間的告示會一直保留，直至被刪除。 |
| 每日 SQLite 備份 | 14 日 |
| 部署前備份 | 30 日 |

### 環境變數

| 變數 | 用途 | 預設值 |
|---|---|---|
| `GPU_WATCH_ALLOWED_NETWORKS` | 用戶端 IP 白名單（以逗號分隔的 CIDR） | 本機回送 |
| `GPU_WATCH_ALLOWED_HOSTS` | 接受的 `Host` 標頭 | 本機回送 |
| `GPU_WATCH_TRUSTED_PROXY_NETWORKS` | 信任其 `X-Forwarded-For` 標頭的反向代理 | 本機回送 |
| `GPU_WATCH_SSH_CONFIG_FILE` | 要使用的 OpenSSH 設定檔的絕對路徑 | 無 |
| `GPU_WATCH_SSH_IDENTITY_FILE` | 取代各主機設定的金鑰檔案 | 無 |
| `GPU_WATCH_ADMIN_PIN_HASH` | 管理員 PIN 雜湊，用來取代 `data/admin_pin.hash` | 無 |
| `GPU_WATCH_RUNTIME_MODE` | `standalone`、`production` 或 `emergency` | `standalone` |
| `GPU_WATCH_BUILD_VERSION` | 健康檢查 API 回報的版本號碼。`deploy.sh` 要求它與 `VERSION` 一致。 | 套件版本 |

### Artificial Analysis

如要顯示 Intelligence Index，請把 API 金鑰放在 `secrets/artificial_analysis_api_key`。該檔案必須是屬應用程式用戶所有的一般檔案（不可以是符號連結），權限為 `0600`，並放在受限制的資料夾內。每次嘗試都會重新讀取金鑰，因此毋須重新啟動便可更換。伺服器會拒絕重新導向，因此金鑰不會傳送到其他來源。瀏覽器只會收到快取的排名。

| 情況 | 行為 |
|---|---|
| 平時 | 根據回應中的 `X-RateLimit-*` 標頭及頁數，把完整更新平均分佈在配額週期內，並預留最多 8 次請求 |
| 每日 100 次、共 4 頁 | 約每小時一次；間隔不短於 15 分鐘 |
| 配額不足或收到 `Retry-After` | 等到配額重設或指定的時間 |
| 沒有速率限制標頭 | 每 6 小時一次 |
| 更新失敗 | 1 小時後重試；如 `Retry-After` 要求更長時間，則以其為準 |
| 數據超過 48 小時 | 標示為過時 |

- 每個請求在發出前便先計數，因此重新啟動或網絡錯誤不會令伺服器誤以為仍有剩餘次數。只有所有頁面都通過核對後，才會發佈新的排名。
- 配額狀態（上限、剩餘次數、重設時間、頁數、下次嘗試時間）與金鑰分開，儲存在 `data/` 內快取的旁邊。
- 無論有沒有人瀏覽，一個小型排程執行緒都會按這些時間執行更新。頁面本身每 5 分鐘檢查一次伺服器快取。
- 指數版本按 API 回傳的數值原樣顯示（現時為 `4.3`），不會自行加上修補版本號碼。較長的模型變體名稱會被簡化，例如 `(max)`。

## 特權磁碟輔助程式

在監察帳戶無法讀取其他用戶主目錄的伺服器上，按用戶統計的總數只能部分計入。對於這類伺服器，可以安裝一個小型 root 輔助程式，它只會執行 GPU Watch 本身的磁碟收集程式。

```sh
# 產生可供審閱的安裝腳本（此步驟不會安裝任何東西）。
python3 scripts/provision-disk-helper.py --user gpuwatch --output disk-installer.py

# 審閱 disk-installer.py 後複製到 GPU 伺服器，並以管理員身份執行。
sudo python3 -I disk-installer.py
```

安裝腳本會建立 `/usr/local/libexec/gpu-watch-disk`、一條只容許監察帳戶不帶參數執行該輔助程式的 sudoers 規則，以及 `/var/cache/gpu-watch/` 快取資料夾。輔助程式不接受任何路徑、指令或環境變數輸入。它使用固定的 `PATH`，會防止並行掃描，把結果快取 5 分鐘，並以較低的 CPU 優先次序運行。不會安裝常駐服務，亦不會儲存管理員密碼。

安裝後，為該主機設定 `"privileged_disk_helper": true`。如輔助程式不存在，GPU Watch 會在同一時間預算內改用一般權限掃描，並把按用戶的用量標示為部分統計。修改磁碟探測程式後，請重新安裝輔助程式。

## 部署

參考的正式環境由兩個經強化的容器組成。對外只開放 Caddy 的連接埠，應用程式只在內部 Docker 網絡中運行。

- `Dockerfile` 以按摘要固定的 `python:3.12-alpine` 映像建置應用程式。
- `Dockerfile.caddy` 以固定的 commit 及固定的依賴版本建置 Caddy。
- `deploy.sh` 是實驗室正式主機的部署腳本。它先檢查路徑及權限，建置兩個映像，並驗證候選容器（health、snapshot、collector）。然後停止並保留舊應用容器，在更改資料庫或 SSH 執行期檔案之前建立經核實、附校驗碼的 SQLite 備份。切換正式環境後檢查白名單及運作狀態，失敗時會復原容器、SSH 執行期檔案及資料庫。它只會清理本專案自己未使用的建置映像。如要在其他環境使用，請先修改與主機相關的路徑及地址。

| 容器 | 限制 |
|---|---|
| 應用程式 | 非 root 用戶、根檔案系統唯讀、移除 capabilities、`no-new-privileges`、512 MiB 記憶體、128 個 PID |
| Caddy | 根檔案系統唯讀、192 MiB 記憶體、64 個 PID |

> [!IMPORTANT]
> 發佈指紋是對 `VERSION`、`server.py`、`hosts.json`、`gpu_watch/` 及 `static/` 計算的雜湊。正式環境與緊急副本的指紋必須一致。腳本、文件及測試不在指紋範圍內，因此亦要比較完整的檔案清單。

### Windows 緊急後備

`emergency-local-fallback.ps1 -Action Status|Start|Stop` 只會在正式環境停止運作時執行本機副本。使用前請先修改其中的路徑、地址及 SSH 設定。

- 只要能連上正式環境，`Start` 便會拒絕執行；只有為刻意接管而加上 `-Force` 時才會繼續。即使如此，`VERSION` 與發佈指紋仍必須一致，並要完成一次新的收集週期才會回報成功。
- 應用程式以一般用戶身份執行。只有為實驗室區域網絡加入防火牆規則時才需要 Windows UAC。
- `Stop` 會先核對進程的確切身份，再移除監聽程式、相應的防火牆規則及臨時密碼檔案。同一連接埠上的無關進程不受影響。
- 本機資料庫是獨立的。在切換回正式環境前，請核對停機期間新增的告示及計時器。

## 營運

```sh
# 檢查運行中容器的健康狀態
docker exec gpu-watch-dashboard python3 /app/scripts/check_local.py --health-only --expected-build-version 2.5

# 檢查 SQLite 完整性及彙總不變量
python3 scripts/audit-data.py data/gpu_watch.sqlite3

# 從本機備份還原（會核對目標路徑及校驗碼）
sh scripts/restore-backup.sh /absolute/path/to/backup.sqlite3
```

維護工作每日建立 SQLite 備份，`deploy.sh` 每次部署前亦會額外備份一次。系統沒有自動異地備份；`scripts/offsite-backup.sh` 是手動工具。

## 開發與測試

```sh
python3 -m unittest discover -s tests -v   # 284 項 Python 回歸測試及契約測試
node tests/test_frontend.js                # 25 項前端檢查
```

少數測試只適用於 Windows 或 POSIX，在其他平台上會略過。請在 Windows 或裝有 GNU coreutils 的 Linux 上執行完整測試；精簡的 Alpine 應用程式映像使用 BusyBox，無法執行 GNU `du` 相關的測試夾具。

測試亦會鎖定產品行為，例如狀態判定規則、區間計算、安全標頭及介面文字。契約測試失敗通常代表用戶看得見的改動，需要經過深思熟慮才作決定。請保留資料庫遷移、有意義的回歸測試、已核實的備份及計時器顏色。修改介面時，應優先使用職責單一的模組，避免重寫龐大的儲存或探測程式碼，並保持計算規則清晰明確。

## 專案結構

| 路徑 | 用途 |
|---|---|
| `server.py` | HTTP API、收集器、儲存及維護 |
| `gpu_watch/` | 身份驗證、進程歸屬、安全輔助、記錄，以及附配額追蹤及排程器的 Artificial Analysis 用戶端 |
| `static/` | 前端（`index.html`、`app.js`、`styles.css`）以及內附的圖示及國旗 |
| `hosts.json` | 伺服器、實驗室及收集設定（虛構示例） |
| `Caddyfile`、`Dockerfile`、`Dockerfile.caddy` | 邊緣代理及容器映像 |
| `deploy.sh` | 附備份及復原功能的正式部署 |
| `run-dashboard.ps1`、`emergency-local-fallback.ps1` | Windows 緊急收集器 |
| `scripts/` | 健康檢查、數據審核、備份及還原、管理員 PIN、SSH 輔助工具，以及磁碟輔助程式安裝腳本產生器 |
| `tests/` | Python 及 Node 測試 |

## 發佈資訊

**v2.5** 為正式推出版本，於 2026-10-03 由 GPT-6 Astra Ultra 發佈。儀表板不顯示發佈頁尾；發佈資訊保留在 `VERSION`、`gpu_watch/__init__.py`、本 README 及 GitHub Releases。

[RELEASE_VALIDATION.md](RELEASE_VALIDATION.md) 列出本次發佈通過的檢查。它記錄的是此原始碼版本經測試的行為，並不保證在日後所有環境或故障下都成立。

## 鳴謝

國旗圖示來自採用 CC-BY 4.0 授權的 [Twemoji](https://github.com/jdecked/twemoji)，詳見 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。服務圖示屬各自擁有者所有，只用作標示前往其狀態頁面的連結。

## 授權

GPU Watch 以 [MIT 授權條款](LICENSE) 發佈。內附的國旗圖示及服務圖示不在此授權範圍內，詳見上方的鳴謝部分。
