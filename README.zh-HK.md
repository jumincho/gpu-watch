# GPU Watch Dashboard v3

一個輕量、無需代理程式的儀表板，專為實驗室共用的 GPU 伺服器而設。它會顯示哪些 GPU 現時空閒、忙碌的 GPU 由誰使用，以及磁碟尚餘多少空間。所有數據只透過普通 SSH 收集。

[English](README.md) | [简体中文](README.zh-CN.md) | **繁體中文** | [日本語](README.ja.md) | [한국어](README.ko.md)

![GPU Watch 儀表板：實驗室概覽、會議截稿倒數及各伺服器的 GPU 卡片](docs/images/dashboard.png)

<sub>截圖使用虛構的示範數據。介面語言為韓文，技術用語則以英文標示。</sub>

## 為何選用 GPU Watch

在共用伺服器上開始工作前，通常要先弄清三件事：哪張 GPU 空閒，忙碌的 GPU 由誰使用，磁碟空間是否足夠。GPU Watch 在同一頁面內解答這三個問題。

- **無需代理程式。** 每部 GPU 伺服器只需提供 SSH 存取、`nvidia-smi` 及 `python3`，伺服器上沒有任何常駐程式。
- **輕量。** 後端只使用 Python 標準函式庫及 SQLite。前端是毋須建置步驟的原生 HTML、CSS 及 JavaScript。
- **審慎。** 不會猜測進程的擁有者，不會顯示完整指令行，亦不會把過時的讀數當作即時數據顯示。

## v3 的改動

- **進程擁有者**改為讀取 `/proc/<pid>/status` 內的有效 UID。即使進程的 `/proc` 目錄看似屬 root 所有（non-dumpable 進程），亦能歸到正確的用戶。
- **GPU 探測失敗時磁碟仍會繼續更新**，例如 SSH 正常、但因 NVIDIA 驅動程式版本不符而只有 GPU 探測失敗的情況。
- **可選用的特權磁碟輔助程式**：在監察帳戶無法讀取其他用戶主目錄的伺服器上，亦能按用戶統計用量。請參閱[特權磁碟輔助程式](#特權磁碟輔助程式)。
- **Artificial Analysis 的更新依從 API 配額**：更新間隔由回應中的速率限制標頭決定，不再固定為 6 小時。
- **安全強化**：建立告示亦與密碼雜湊共用同一個並行上限；Caddy 建置固定使用已修補的 OpenTelemetry 模組。
- **所有檔案均以 LF 換行取出**，因此 Linux 正式環境與 Windows 副本的發佈指紋一致。
- **介面細節調整**：計時器的剩餘時間位於卡片的垂直中央。較長的標題優先顯示，必要時將剩餘時間分成兩行；仍放不下的標題使用省略號，滑鼠移上時顯示全文。手機上的 Intelligence Index 更緊湊，韓文按詞換行。

## 功能

### GPU 與伺服器

- 每張 GPU 的即時狀態：free（空閒）或 busy（忙碌）、使用率、顯示記憶體（VRAM）、溫度，以及已忙碌的時間或最近一次使用時間。
- 每張 GPU 上的進程及其用戶、記憶體用量和簡短的指令摘要。詳細資料會加上 PID、啟動時間及容器，但絕不顯示完整指令行。
- 實驗室切換、實驗室整體概覽（在線伺服器、空閒與忙碌的 GPU、VRAM），以及「使用中」「全部空閒」「連線失敗」「磁碟警告」等篩選。
- 活動徽章：🔥 高使用率（近 7 日忙碌指數達 50% 或以上）、❄️ 長期閒置（7 日內沒有持續 60 秒或以上的使用）、⛔ 連線失敗。

### 磁碟

- 空閒、可用、已用及保留空間。警告徽章與 Disk 分頁的 `Used` 使用相同的伺服器整體匯總值：已用總量 /（已用總量 + 可用空間總量），顯示使用率達到 90% 時警告。同一檔案系統的重複掛載只計一次。
- 按用戶統計的用量，涵蓋可讀取的主目錄、已設定的路徑及 Docker 可寫層。結果不完整時，下限值以 `≥` 標示，估計值以 `≈` 標示。

### 記錄與趨勢

- Recent Activity（最近活動）記錄 busy/free 的轉換，可按日期、伺服器及用戶篩選。連線 DOWN/UP 事件預設隱藏，亦可選擇一併顯示。
- 每部伺服器近 7 日的忙碌指數及各用戶的使用比例。
- LAB DAILY INDEX：最近 24 小時每小時的 VRAM 陰陽燭圖，以及 30 日每日平均 VRAM 的趨勢。

### 實驗室工具

- 最多 6 個會議截稿倒數（KST），支援 TBA 項目。計時器標題中的國旗表情符號，以儲存庫內附的 SVG 檔案繪製。
- 可選擇設定到期時間的告示板。作者以自己設定的密碼編輯告示，管理員則可用管理員 PIN 管理所有告示。
- 快速連結至會議截稿網站、AI 服務狀態頁面及 AI 資訊。頁面亦會顯示 Artificial Analysis Intelligence Index（首 29 個模型）。數據由伺服器取得，因此 API 金鑰不會傳到瀏覽器。

### 日常使用

- 預設每 10 秒自動更新。更新時會保留已選的分頁、滾動位置及鍵盤焦點，頁面在背景時會放慢更新頻率。數據停止更新時會顯示提示橫幅。
- 支援闊度低至 320px 的屏幕、鍵盤導覽及系統的「減少動態效果」設定。發佈檢查涵蓋文字對比度、焦點可見性及不同屏幕闊度的版面。

## 運作原理

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

1. 收集器每隔 `poll_interval_seconds`（10 秒）並行連接 `hosts.json` 內的所有主機，並在每部主機上執行 `nvidia-smi` 查詢及一個小型內嵌 Python 探測程式。
2. 進程擁有者根據 `/proc/<pid>/status` 內的有效 UID 及 NSS 名稱判定，再以 PID 啟動時間及 GPU UUID 核對。無法確認時會顯示為不明，而不會猜測。
3. 每隔 `disk_poll_interval_seconds`（30 分鐘），以 `df`、設有時限的 `du` 及 `docker ps --size` 量度磁碟用量，亦可改用可選的特權輔助程式。
4. 觀測結果以時間區間的形式儲存於 SQLite。重疊的 GPU、進程及用戶會合併計算，因此忙碌時間不會重複計算。
5. 頁面讀取 `/api/snapshot`、`/api/events`、`/api/insights` 及 `/api/intelligence-index`。

### 狀態判定規則

- 如 GPU 上有運算進程、VRAM 用量至少 500 MiB，或使用率至少 10%，該 GPU 即為 **busy**。兩個門檻均可設定。
- 持續佔用 VRAM 的進程即使使用率為 0%，亦視為忙碌，因此已被預留的 GPU 不會顯示為空閒。
- 只要未能完整觀測 GPU（無論是 SSH 失敗還是 GPU 探測失敗），伺服器即為 **DOWN**。它的所有 GPU 都不計作可用，之前的讀數亦不會當作現時數據顯示。磁碟探測成功亦不會令 DOWN 的伺服器恢復為 UP。

## 安全模型

- **存取控制。** Caddy 執行 IP 白名單。應用程式本身亦會再次獨立核對用戶端 IP、`Host` 及 `Origin`。
- **寫入操作。** 修改告示及計時器需要同源請求，以及密碼或 PIN。雜湊採用迭代 600,000 次的 PBKDF2-SHA256。雜湊核對最多同時進行 2 個，失敗的嘗試會按子網絡及整體兩方面進行速率限制。
- **有限的程序資訊。** 不向瀏覽器傳送完整指令行、環境變數或憑證，只提供經清理的簡短摘要。運作錯誤會限制長度並遮蔽敏感值，但可能包含主機地址或連接埠，因此不能用來隱藏網絡結構。伺服器卡片會按設計顯示經核實的 IP 地址。
- **瀏覽器防護。** 嚴格的內容安全政策（CSP）會封鎖內嵌指令碼。圖示及國旗均由本機提供，不會從第三方主機載入。
- **容器。** 以非 root 用戶執行，根檔案系統唯讀，移除所有 capabilities，並啟用 `no-new-privileges` 以及 PID、記憶體及日誌限制。
- **機密資料。** SSH 密碼、API 金鑰及 PIN 雜湊只存放於被 git 忽略的執行期資料夾（`secrets/`、`data/`、`operator-secrets/`），從不放入儲存庫。SSH 密碼透過 `SSH_ASKPASS` 交給 OpenSSH，而不會放在指令行上。
- **傳輸。** 純文字 HTTP 只適用於可信任的區域網絡。如要向更大範圍開放，請先加入 TLS 及身份驗證。

## 系統要求

| 位置 | 需要 |
|---|---|
| 儀表板主機 | Python 3.12（只用標準函式庫）及 OpenSSH 用戶端，或 Docker |
| 每部 GPU 伺服器 | 監察帳戶的 SSH 存取、附 `nvidia-smi` 的 NVIDIA 驅動程式、`python3`、`df` 及 GNU `du`。Docker 為選用，安裝後可額外顯示各容器的用量。使用特權磁碟輔助程式時另需 `sudo`。 |
| 瀏覽者 | 較新的網頁瀏覽器 |

## 快速開始

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch

# 1. 填寫你的伺服器（見下方「設定」）。
$EDITOR hosts.json

# 2. 建立管理員 PIN。腳本會顯示一次性純文字 PIN 的儲存位置。
python3 scripts/admin-passphrase.py ensure

# 3. 啟動儀表板。
python3 server.py --host 127.0.0.1 --port 8787
```

開啟 <http://127.0.0.1:8787/>。預設只容許本機回送（loopback）用戶端連線。如要讓區域網絡內的其他電腦瀏覽，請明確列出容許的對象。以下地址只作示例。

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
| `display_ip` | 以別名連線的主機在卡片上顯示的 IP |
| `expected_gpu_count` | 伺服器處於 DOWN 狀態時仍然顯示的 GPU 格數 |
| `note`、`owner`、`owner_type`、`location` | 卡片上的文字及徽章樣式（`assigned` 或 `shared`） |
| `disk_user_paths` | 額外量度的按用戶路徑，格式為 `{ "user": …, "path": … }` |
| `collect_docker_usage` | 同時以 `docker ps --size` 量度 Docker 可寫層。預設只為 `nll` 實驗室的主機啟用。 |
| `privileged_disk_helper` | 透過已安裝的 root 輔助程式量度磁碟用量。不可與 `disk_user_paths` 同時使用。 |

其他頂層設定還包括探測逾時、`collector_workers`、決定 🔥 及 ❄️ 徽章門檻的 `activity_policy`，以及保留期限。預設情況下，事件保留 180 日，每日備份保留 14 日。告示在刪除或到期 90 日後清理；沒有到期日期的告示會一直保留，直到被刪除。

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
| `GPU_WATCH_BUILD_VERSION` | 頁尾顯示的版本號碼 | `VERSION` |

### Artificial Analysis

如要顯示 Intelligence Index，請把 API 金鑰放在 `secrets/artificial_analysis_api_key`。該檔案必須是一般檔案（不可以是符號連結），權限為 `0600`。瀏覽器只會收到快取的排名。

- 伺服器根據回應中的 `X-RateLimit-*` 標頭及頁數，把完整更新平均分佈在配額週期內。它會預留少量請求（最多 8 次），更新間隔不短於 15 分鐘。在每日 100 次配額、共 4 頁的情況下，約每小時更新一次。
- 配額不足時，會等到配額重設後才更新。每個請求在發出前便先計數，因此重新啟動或網絡錯誤不會令它誤以為仍有剩餘次數。
- 回應中沒有速率限制標頭時，每 6 小時更新一次。失敗後 1 小時重試；如 API 回傳更長的 `Retry-After`，則以其為準。超過 48 小時的數據會標示為過時。
- 配額狀態（上限、剩餘次數、重設時間、頁數、下次嘗試時間）與金鑰分開，儲存在 `data/` 內快取的旁邊。
- 頁面每 5 分鐘檢查一次伺服器快取。如沒有人開啟頁面，更新可能要延至下一個維護週期（預設每小時一次）。

## 特權磁碟輔助程式

在監察帳戶無法讀取其他用戶主目錄的伺服器上，按用戶統計的總數只能部分計入。對於這類伺服器，可以安裝一個小型 root 輔助程式，它只會執行 GPU Watch 本身的磁碟收集程式。

```sh
# 產生可供審閱的安裝腳本（此步驟不會安裝任何東西）。
python3 scripts/provision-disk-helper.py --user gpuwatch --output disk-installer.py

# 審閱 disk-installer.py 後複製到 GPU 伺服器，並以管理員身份執行。
sudo python3 -I disk-installer.py
```

安裝腳本會建立 `/usr/local/libexec/gpu-watch-disk`、一條只容許監察帳戶不帶參數執行該輔助程式的 sudoers 規則，以及 `/var/cache/gpu-watch/` 快取資料夾。輔助程式不接受任何路徑、指令或環境變數輸入。它會防止並行掃描，把結果快取 5 分鐘，並以較低的 CPU 優先次序運行。不會安裝常駐服務，亦不會儲存管理員密碼。

公開範例預設不啟用 helper：省略 `privileged_disk_helper` 或設為 false 時不會使用 sudo。安裝後，只為該主機設定 `"privileged_disk_helper": true`。如找不到 helper，GPU Watch 會在相同的時間預算內退回一般權限統計，並將用戶用量標為部分統計。

## 部署

參考的正式環境由兩個經強化的容器組成。對外只開放 Caddy 的連接埠，應用程式只在內部 Docker 網絡中運行。

- `Dockerfile` 以按摘要固定的 `python:3.12-alpine` 映像建置應用程式。
- `Dockerfile.caddy` 以固定的 commit 及固定的依賴版本建置 Caddy。
- `deploy.sh` 是實驗室正式主機的部署腳本。它檢查路徑及權限、建置兩個映像，並驗證 SSH 和 Caddy 設定。切換前會停止應用程式，建立及驗證 SQLite 線上備份。它保留舊容器，檢查新部署的 health 和存取控制，失敗時復原。如要在其他環境使用，請先修改與主機相關的路徑及地址。

發佈指紋是對 `VERSION`、`server.py`、`hosts.json`、`gpu_watch/` 及 `static/` 計算的雜湊。正式環境與緊急副本的指紋必須一致。

### Windows 緊急後備

`emergency-local-fallback.ps1 -Action Status|Start|Stop` 管理 Windows 緊急副本。請先按自己的環境調整參考路徑、正式地址、SSH 設定及 LAN 範圍。除非傳入 `-Force`，否則正式服務能回應時 `Start` 會拒絕啟動。它檢查 `VERSION`、release fingerprint 及新一輪收集結果，並將防火牆規則限定於設定的 LAN 範圍。`Stop` 會移除它建立的監聽程序、防火牆規則及臨時密碼檔。本機資料庫獨立存在，因此切回前應核對故障期間建立的告示及計時器。

## 營運

```sh
# 檢查運行中容器的健康狀態
docker exec gpu-watch-dashboard python3 /app/scripts/check_local.py --health-only --expected-build-version 3

# 檢查 SQLite 完整性及彙總不變量
python3 scripts/audit-data.py data/gpu_watch.sqlite3

# 還原 data/backups 內的備份（核對路徑、SQLite 完整性、外鍵及結構）
sh scripts/restore-backup.sh "$PWD/data/backups/backup.sqlite3"
```

維護工作每日建立 SQLite 備份，`deploy.sh` 每次部署前亦會額外備份一次。系統沒有自動異地備份；`scripts/offsite-backup.sh` 是手動工具。

## 開發與測試

```sh
python3 -m unittest discover -s tests -v   # Python 回歸測試及契約測試
node tests/test_frontend.js                # 前端邏輯契約測試
```

測試亦會鎖定產品行為，例如狀態判定規則、區間計算、安全標頭及介面文字。契約測試失敗通常代表用戶看得見的改動，需要經過深思熟慮才作決定。

## 專案結構

| 路徑 | 用途 |
|---|---|
| `server.py` | HTTP API、收集器、儲存及維護 |
| `gpu_watch/` | 身份驗證、進程歸屬、安全輔助、記錄，以及可追蹤配額的 Artificial Analysis 用戶端 |
| `static/` | 前端（`index.html`、`app.js`、`styles.css`）以及內附的圖示及國旗 |
| `hosts.json` | 伺服器、實驗室及收集設定（虛構示例） |
| `Caddyfile`、`Dockerfile`、`Dockerfile.caddy` | 邊緣代理及容器映像 |
| `deploy.sh` | 附備份及復原功能的正式部署 |
| `run-dashboard.ps1`、`emergency-local-fallback.ps1` | Windows 緊急收集器 |
| `scripts/` | 健康檢查、數據審核、備份及還原、管理員 PIN、SSH 輔助工具，以及磁碟輔助程式安裝腳本產生器 |
| `tests/` | Python 及 Node 測試 |

## 發佈資訊

v3，於 2026-09-25 發佈。介面頁尾顯示 `GPT-6 Astra Max (Implementation) · Claude Opus 5.5 Max (Frontend)`。

## 鳴謝

國旗圖示來自採用 CC-BY 4.0 授權的 [Twemoji](https://github.com/jdecked/twemoji)，詳情請參閱 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。服務圖示屬各擁有者所有，只用於標示通往其狀態頁面的連結。

## 授權

GPU Watch 以 [MIT 授權](LICENSE) 發佈。隨附的國旗圖示及服務圖示不在此授權範圍內，詳情請參閱上方的鳴謝部分。
