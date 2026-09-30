# GPU Watch Dashboard v2

共有 NVIDIA GPU サーバーを SSH で確認する軽量ダッシュボードです。Python 標準ライブラリと SQLite、ビルド不要の HTML/CSS/JavaScript を使用し、GPU サーバーに常駐エージェントは不要です。

**正式リリース: v2 · 2026-09-30 · GPT-6.1 Sol (max)。画面にリリース情報のフッターは表示しません。**

[English](README.md) | [简体中文](README.zh-CN.md) | [繁體中文](README.zh-HK.md) | [日本語](README.ja.md) | [한국어](README.ko.md)

公開版のサーバー名、IP アドレス、SSH パスは架空の例です。導入前に設定を置き換え、鍵と known_hosts を検証してください。パスワード、API キー、DB、内部運用文書は含みません。

## 機能と集計の規則

- GPU は既定で 10 秒、Disk は 30 分ごとに収集し、遅い Disk 処理は独立したワーカーと時間予算で実行します。複数プロセス・複数利用者、研究室の概要、状態フィルターに対応します。
- 計算プロセスが存在する、VRAM ≥500 MiB、Util ≥10% のいずれかで busy になります。Util 0% の常駐プロセスも GPU を占有します。指標は占有率であり計算効率ではありません。
- effective UID、NSS/数値 UID、PID 起動識別、GPU UUID を再検証します。root 所有の /proc ディレクトリだけで所有者を推測せず、利用者が一時的に不明でも検証済みプロセスは保持します。全所有者が確認できた区間だけ異なる利用者で時間を均等分割し、一部未確認の区間は未帰属にします。完全な引数列、環境、オプション値は公開しません。
- 60 秒未満のセッションも履歴と占有統計に残し、長期未使用の解除だけから除外します。長期未使用は 100% の観測率を要求しません。7 日指数は観測された GPU 時間を分母とし、欠測をゼロで埋めません。LAB DAILY INDEX は KST の日単位で各 GPU の観測時間加重 VRAM 平均を合算します。
- Recent Activity は日付・サーバー・利用者・ページ移動に対応し、DOWN/UP は既定で非表示、チェックで追加できます。観測の空白と利用者変更は内部データに保持します。
- Disk Used と警告は同じ合算 used/(used+available) を使用します。重複マウントは一度だけ計上し、予約領域を分母から除外します。不完全な帰属は ≥/≈ で表示します。任意の root helper は固定の引数なしコマンドだけを許可し、低優先度、ロック、5 分キャッシュを使用します。
- 会議タイマー 6 個、固定色プール、締切順、同時刻の登録順、TBA、ローカル国旗 SVG に対応します。公告は期限なしで登録・変更でき、フォーム外をクリックしても閉じません。
- 自動更新はタブ・スクロール・フォーカスを保持します。AA 上位 29 モデルをサーバーでキャッシュし、100 回/日・4 ページなら約 1 時間ごと、最低 15 分、最大 8 回分を残します。閲覧者がいなくても scheduler が実行し、API の版と短いモデル名を表示します。

## 導入と安全性

Python 3.12+ と OpenSSH、または Docker を使用します。GPU サーバーには SSH、python3、nvidia-smi、df、GNU du が必要で、永続 home ディレクトリには依存しません。hosts.json、IP/Host 許可リスト、SSH ホスト鍵を先に設定します。AA キーは app 所有・0600 の secrets/artificial_analysis_api_key に保存し、ブラウザーには順位キャッシュだけを送ります。

edge と app が IP/Host/Origin を検証します。書き込みは公告パスワードまたは管理者 PIN が必要で、PBKDF2-SHA256 600,000 回、同時処理上限、失敗回数制限を使用します。非 root、読み取り専用、cap-drop、no-new-privileges、リソース上限を維持します。HTTP は信頼する LAN が前提で、IP 制限は通信暗号化を代替しません。パスワード保存の提案はブラウザーの方針にも依存します。

deploy.sh の例示アドレスとパスを変更してから導入します。検証済み SQLite バックアップを作り、失敗時はコンテナ・DB・SSH を復元します。Windows 緊急コピーは通常停止し、VERSION・コード指紋・全ファイル一覧を本番と一致させます。Force でも不一致を無視できません。LAN ファイアウォールだけ UAC が必要で app は一般ユーザーで動き、Stop はプロセス・規則・一時パスワードを削除します。

既定の保持期間は日次バックアップ 14 日、導入前バックアップ 30 日、イベント/日指数 180 日、生区間 8 日です。自動 offsite バックアップは設定しません。詳細な設定・構造・運用コマンドは [English README](README.md) を参照してください。

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
