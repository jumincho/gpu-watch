# GPU Watch Dashboard v3

[English](README.md) · [한국어](README.ko.md) · [日本語](README.ja.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-HK.md)

共有 NVIDIA GPU サーバー向けの軽量ダッシュボードです。Python 標準ライブラリと SQLite、ビルド不要の HTML/CSS/JavaScript を使用します。サーバー一覧とスクリーンショットは架空の例で、実運用の認証情報やデータは含みません。

GPU、VRAM、温度、プロセス、ユーザー、使用履歴、ディスク容量、LAB DAILY INDEX を表示します。最大6個の学会タイマーは TBA と固定色に対応し、掲示は期限なしでも登録できます。DOWN/UP は任意表示で、内部診断イベントは一覧から除外します。

標準の収集間隔は GPU が10秒、Disk が30分です。Util が0%でもメモリを占有するプロセスは busy です。短い利用も記録しますが、60秒未満のセッションは長期アイドルの解除には使いません。未観測時間や未確認の所有者を推測で補いません。

v3 は effective UID の確認、GPU 障害から独立した Disk 更新、任意の固定権限 Disk helper、API 上限に応じた AA 更新を改善しました。キーはサーバー側だけに保存します。100リクエスト/日・4ページの場合は約1時間間隔を目標とし、ブラウザーは5分ごとにキャッシュを確認します。

設定、導入、セキュリティ、復旧の正式な手順は [English README](README.md) を参照してください。例のアドレスと SSH キーは自分の環境に合わせて変更します。Windows は通常停止した緊急用コピーで、運用と同じソース指紋を維持します。HTTP に通信暗号化はなく、自動オフサイトバックアップもありません。

```sh
git clone https://github.com/jumincho/gpu-watch.git
cd gpu-watch
# Configure hosts.json and SSH before starting.
python3 scripts/admin-passphrase.py ensure
python3 server.py --host 127.0.0.1 --port 8787
```

[MIT License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)

2026-09-25 · GPT-6 Astra Max / Claude Opus 5.5 Max
