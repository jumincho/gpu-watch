# GPU Watch Dashboard v2

透過 SSH 查看共用 NVIDIA GPU 的輕量儀表板。後端只使用 Python 標準函式庫和 SQLite，前端是毋須建置的 HTML/CSS/JavaScript；GPU 伺服器毋須常駐代理程式。

**v2 正式發布：2026-09-30 · GPT-6.1 Sol (max)。頁面不顯示發布資訊頁尾。**

[English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-HK.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

公開版包含虛構伺服器名稱、文件範例 IP 及 SSH 路徑。部署前請替換設定並驗證金鑰和 known_hosts；密碼、API 金鑰、資料庫及內部維運文件均不納入儲存庫。

## 功能與資料規則

- 預設每 10 秒採集 GPU、每 30 分鐘採集磁碟；磁碟有獨立工作者及逾時預算。支援多程序、多使用者、實驗室概覽及狀態篩選。
- 有計算程序、顯存 ≥500 MiB 或使用率 ≥10% 時算 busy。0% 使用率的常駐程序仍占用 GPU；指標量度占用而非計算效率。
- 重新驗證 effective UID、NSS/數字 UID、PID 起始識別及 GPU UUID。不能僅憑 root 擁有的 /proc 目錄猜測擁有人，也不能因暫時查不到使用者而隱藏已驗證的占用。全部使用者已知時按不同使用者均分時間；部分未知區間保留未歸屬。完整命令列、環境及選項值不公開。
- 少於 60 秒的工作仍保留歷史及占用統計，只不解除長期閒置標記。長期閒置毋須 100% 觀測覆盖；七日指標以觀測 GPU 時間為分母，缺失不填零。LAB DAILY INDEX 以 KST 日界累加各 GPU 的觀測時間加權顯存平均。
- Recent Activity 支援日期、伺服器、使用者及分頁；DOWN/UP 預設隱藏，可勾選顯示。觀測缺口及使用者變更只保留於資料層。
- 磁碟警告及 Disk Used 使用一致的總 used/(used+available)。重複掛載只計一次，保留區塊不進分母；部分歸屬用 ≥/≈ 表示。可選 root helper 只接受固定無參數指令，使用低優先級、鎖及五分鐘快取。
- 六個會議計時器維持固定色彩、截止時間排序及相同時間的註冊順序，支援 TBA 及本地旗幟 SVG。公告可不設到期日，既有公告也可改為永久。點擊視窗外不會關閉表單。
- 自動更新保留分頁、捲動及焦點。AA 前 29 名由伺服器快取；100 次/日、四頁時約每小時更新，最低 15 分鐘，預留最多八次。無瀏覽者時也按計劃執行，版本直接使用 API 提供值，模型名稱簡寫。

## 部署與安全

使用 Python 3.12+ 和 OpenSSH，或 Docker。GPU 伺服器需要 SSH、python3、nvidia-smi、df 和 GNU du；採集不依賴持久 home 目錄。先設定 hosts.json、IP/Host 白名單及 SSH 主機金鑰。AA 金鑰保存在 app 擁有、0600 權限的 secrets/artificial_analysis_api_key 檔案，瀏覽器只接收排名快取。

edge 與 app 驗證 IP/Host/Origin；寫入需要公告密碼或管理員 PIN，使用 PBKDF2-SHA256 600,000 次、並行上限及失敗次數限制。容器維持非 root、唯讀、cap-drop、no-new-privileges 和資源上限。HTTP 以可信 LAN 為前提，IP 限制不等於傳輸加密。瀏覽器自行決定密碼儲存提示。

部署前修改 deploy.sh 的範例位址和路徑。先驗證 SQLite 備份，失敗則還原容器、DB 及 SSH 檔案。Windows 緊急副本平時關閉，VERSION、程式指紋及完整檔案清單須與生產一致；Force 不能跳過不一致檢查。只有 LAN 防火牆需要 UAC，app 使用一般帳號；Stop 清理程序、規則及臨時密碼。

預設每日備份 14 天、部署前備份 30 天、事件及日指標 180 天、原始區間八天，不設自動異地備份。詳細設定、結構及指令見 [English README](README.md)。

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
