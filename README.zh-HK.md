# GPU Watch Dashboard v3

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-HK.md)

適用於共用 NVIDIA GPU 伺服器的輕量狀態面板。後端使用 Python 標準程式庫和 SQLite，前端使用毋須建置的 HTML/CSS/JavaScript。伺服器清單和截圖均為示例，不包含實際部署的憑證或資料庫。

顯示 GPU、VRAM、溫度、程序、使用者、使用紀錄、磁碟空間及 LAB DAILY INDEX。最多支援六個學術會議計時器，包括 TBA 及固定配色；公告可不設到期時間。DOWN/UP 可選擇顯示，內部觀測診斷事件不會出現在活動清單。

預設每10秒收集 GPU 資料，每30分鐘收集 Disk 資料。即使 Util 為0%，佔用顯示記憶體的常駐程序仍可判定為 busy。短暫使用也會記錄，但不足60秒的工作階段不會取消長期閒置狀態。未觀測時間不會當作零使用，也不會猜測未確認的使用者。

v3 改善了 effective UID 確認、獨立於 GPU 故障的 Disk 更新、可選的固定權限 Disk helper，以及依 API 配額調整的 AA 更新。金鑰只儲存在伺服器端。每日100次請求、四頁資料時以約每小時更新為目標，瀏覽器每五分鐘查詢快取。

完整安裝、設定、安全及復原步驟以 [英文 README](README.md) 為準。請替換示例地址和 SSH 金鑰。Windows 副本只供緊急使用，平時關閉，並保持與正式環境相同的原始碼指紋。HTTP 不提供傳輸加密，專案沒有自動異地備份。

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch
# Configure hosts.json and SSH before starting.
python3 scripts/admin-passphrase.py ensure
python3 server.py --host 127.0.0.1 --port 8787
```

[MIT License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

2026-09-25 · GPT-6 Astra Max / Claude Opus 5.5 Max
