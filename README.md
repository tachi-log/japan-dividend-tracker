# 📈 日本株 高配当トラッカー

257銘柄の日本株を対象に、配当利回りを自動取得・表示するWebアプリです。

## 🚀 セットアップ手順

### 1. GitHubリポジトリを作成

```bash
git init
git add .
git commit -m "initial commit"
git remote add origin https://github.com/<あなたのユーザー名>/<リポジトリ名>.git
git push -u origin main
```

### 2. GitHub Pages を有効化

リポジトリの **Settings → Pages → Source** を
`Deploy from a branch` → `main` `/` `(root)` に設定。

これで `https://<ユーザー名>.github.io/<リポジトリ名>/` でWebアプリが公開されます。

### 3. GitHub Actions の確認

`.github/workflows/fetch_stocks.yml` が自動的に以下のスケジュールで動作します：

| 実行タイミング | JST | UTC (cron) |
|---|---|---|
| 前場終了後 | 11:35 | 02:35 |
| 後場終了後 | 15:35 | 06:35 |
| 夕方 | 18:00 | 09:00 |

- 実行は **平日（月〜金）** のみ
- GitHub Actions 画面の **Run workflow** ボタンから手動実行も可能

### 4. ローカルでテスト実行

```bash
pip install -r requirements.txt
python scripts/fetch_stocks.py
```

`data/stocks.json` にデータが保存されます。

---

## 📱 表示仕様

| 配当利回り | 表示 |
|---|---|
| **4.0%以上** | 🔴 赤ボーダー + 「高配当」バナー |
| **3.7〜4.0%** | 🟠 オレンジボーダー + 「注目」バナー |
| 3.7%未満 | 通常表示 |

### 表示項目
- 銘柄コード / 銘柄名
- 現在株価 / 前日比（金額・％）
- 配当利回り（リアルタイム計算）
- セクター / 時価総額

---

## 📁 ファイル構成

```
├── index.html                    # Webアプリ本体
├── data/
│   └── stocks.json               # 株価データ（自動更新）
├── scripts/
│   └── fetch_stocks.py           # データ取得スクリプト
├── .github/
│   └── workflows/
│       └── fetch_stocks.yml      # GitHub Actionsワークフロー
└── requirements.txt
```

---

## ⚠ 注意事項

- yfinance はYahoo Financeの非公式ライブラリです。レート制限により一部銘柄の取得に失敗する場合があります。
- 株価データは投資判断の参考情報です。実際の取引はご自身の判断でお願いします。
