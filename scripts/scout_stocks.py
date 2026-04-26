#!/usr/bin/env python3
"""
scout_stocks.py

週次で東証全上場銘柄をスキャンし、12基準を満たす有望高配当銘柄を発掘してメール送信。
既に stocks.json に登録済みの銘柄は除外する。
"""

import json
import math
import os
import smtplib
import sys
import time
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from io import BytesIO
from pathlib import Path

import pandas as pd
import pytz
import requests
import yfinance as yf

sys.path.insert(0, str(Path(__file__).parent))
try:
    from translate_names import NAME_JA
except ImportError:
    NAME_JA = {}

JST = pytz.timezone('Asia/Tokyo')
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; StockScout/1.0)'}
MIN_YIELD = 3.7   # 配当利回り下限(%)
MIN_SCORE = 10    # メール通知するスコア下限（最大19点）


# ─────────────────────────────────────────────
# ヘルパー
# ─────────────────────────────────────────────

def safe_float(v):
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def analyze_trend(values):
    """線形回帰でトレンド判定: 'up' / 'flat' / 'down'"""
    vals = [safe_float(v) for v in values]
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None
    n = len(vals)
    mx = (n - 1) / 2
    my = sum(vals) / n
    if my == 0:
        return 'flat'
    num = sum((i - mx) * (vals[i] - my) for i in range(n))
    den = sum((i - mx) ** 2 for i in range(n))
    if den == 0:
        return 'flat'
    rel_slope = (num / den) / abs(my)
    if rel_slope > 0.03:
        return 'up'
    elif rel_slope < -0.03:
        return 'down'
    return 'flat'


def get_series(df, *names):
    """DataFrameから指定行を古い順で返す"""
    for name in names:
        if name in df.index:
            raw = df.loc[name].tolist()
            return [safe_float(v) for v in reversed(raw)]
    return []


# ─────────────────────────────────────────────
# Step 1: 東証全銘柄リスト取得
# ─────────────────────────────────────────────

def fetch_all_tse_codes():
    """JPXから東証全上場銘柄コードを取得"""
    url = 'https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls'
    print(f"JPX銘柄リスト取得中...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        df = pd.read_excel(BytesIO(resp.content), header=0)
        print(f"  カラム: {df.columns.tolist()[:8]}")

        # コード列を探す
        code_col = None
        for col in df.columns:
            if 'コード' in str(col):
                code_col = col
                break
        if code_col is None:
            for col in df.columns:
                if df[col].dtype in ['int64', 'float64']:
                    code_col = col
                    break
        if code_col is None:
            print("[ERROR] コード列が見つかりません")
            return []

        codes_raw = df[code_col].dropna().astype(str).str.strip().tolist()
        # 4文字で先頭が数字のコードのみ（130A等の新形式も含む）
        codes = [c for c in codes_raw if len(c) == 4 and c[0].isdigit()]
        print(f"  東証全銘柄数: {len(codes)}")
        return codes
    except Exception as e:
        print(f"[ERROR] JPX取得失敗: {e}")
        return []


# ─────────────────────────────────────────────
# Step 2: 既存登録銘柄コード取得
# ─────────────────────────────────────────────

def load_existing_codes():
    """stocks.json から既登録銘柄コードを取得"""
    path = Path(__file__).parent.parent / 'data' / 'stocks.json'
    if not path.exists():
        return set()
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return {str(s['code']) for s in data.get('stocks', [])}


# ─────────────────────────────────────────────
# Step 3: 一括配当利回りスキャン
# ─────────────────────────────────────────────

def bulk_fetch_yields(codes, batch_size=50):
    """Yahoo Finance v7 APIで配当利回りを一括取得"""
    results = {}
    batches = [codes[i:i + batch_size] for i in range(0, len(codes), batch_size)]
    total = len(batches)

    for i, batch in enumerate(batches):
        symbols = ','.join(f"{c}.T" for c in batch)
        url = (
            f"https://query1.finance.yahoo.com/v7/finance/quote"
            f"?symbols={symbols}&lang=ja&region=JP"
        )
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            for item in r.json().get('quoteResponse', {}).get('result', []):
                sym = item.get('symbol', '').replace('.T', '')
                dy = item.get('dividendYield') or item.get('trailingAnnualDividendYield')
                price = item.get('regularMarketPrice')
                ann_div = item.get('dividendRate') or item.get('trailingAnnualDividendRate')

                if dy is not None:
                    yield_pct = dy * 100 if dy < 1 else dy
                elif ann_div and price:
                    yield_pct = ann_div / price * 100
                else:
                    yield_pct = None

                if yield_pct is not None:
                    results[sym] = round(yield_pct, 2)
        except Exception as e:
            print(f"[WARN] batch {i + 1}/{total}: {e}")

        if (i + 1) % 20 == 0:
            print(f"  利回りスキャン進捗: {i + 1}/{total} バッチ ({len(results)} 銘柄取得済み)")
        time.sleep(0.5)

    return results


# ─────────────────────────────────────────────
# Step 4: 候補銘柄の詳細分析
# ─────────────────────────────────────────────

def fetch_full_data(code):
    """候補銘柄の12基準データをすべて取得"""
    code = str(code)
    sym = f"{code}.T"
    data = {
        'code': code,
        'name': str(code),
        'dividend_yield': None,
        'current_price': None,
        'annual_dividend': None,
        'sector': '不明',
        'market_cap': None,
        'operating_margin': None,
        'payout_ratio': None,
        'revenue_trend': None,
        'eps_trend': None,
        'ocf_status': None,
        'equity_ratio': None,
        'cash_trend': None,
        'dividend_hist': None,
    }

    try:
        ticker = yf.Ticker(sym)
        info = ticker.info

        price = info.get('currentPrice') or info.get('regularMarketPrice')
        raw_dy = info.get('dividendYield') or info.get('trailingAnnualDividendYield')
        dy = round(raw_dy * 100 if raw_dy and raw_dy < 1 else raw_dy, 2) if raw_dy else None
        ann = info.get('dividendRate') or info.get('trailingAnnualDividendRate')
        if dy is None and ann and price:
            dy = round(ann / price * 100, 2)

        op_m = safe_float(info.get('operatingMargins'))
        pay = safe_float(info.get('payoutRatio'))

        data.update({
            'name': NAME_JA.get(str(code)) or info.get('longName') or info.get('shortName') or str(code),
            'current_price': price,
            'dividend_yield': dy,
            'annual_dividend': ann,
            'sector': info.get('sector', '不明'),
            'market_cap': info.get('marketCap'),
            'operating_margin': round(op_m * 100, 1) if op_m is not None else None,
            'payout_ratio': round(pay * 100, 1) if pay is not None else None,
        })

        # 損益計算書
        try:
            fin = ticker.financials
            if fin is not None and not fin.empty:
                rev = get_series(fin, 'Total Revenue', 'TotalRevenue')
                if rev:
                    data['revenue_trend'] = analyze_trend(rev)
                eps = get_series(fin, 'Basic EPS', 'Diluted EPS', 'BasicEPS', 'DilutedEPS')
                if eps:
                    data['eps_trend'] = analyze_trend(eps)
        except Exception as e:
            print(f"    [WARN] {code} financials: {e}")

        # キャッシュフロー計算書
        try:
            cf = ticker.cashflow
            if cf is not None and not cf.empty:
                ocf = get_series(
                    cf,
                    'Operating Cash Flow', 'OperatingCashFlow',
                    'Total Cash From Operating Activities',
                )
                if ocf:
                    valid = [v for v in ocf if v is not None]
                    if valid:
                        all_pos = all(v > 0 for v in valid)
                        trend = analyze_trend(ocf)
                        if all_pos and trend == 'up':
                            data['ocf_status'] = 'good'
                        elif all_pos:
                            data['ocf_status'] = 'ok'
                        else:
                            data['ocf_status'] = 'bad'
        except Exception as e:
            print(f"    [WARN] {code} cashflow: {e}")

        # 貸借対照表
        try:
            bs = ticker.balance_sheet
            if bs is not None and not bs.empty:
                eq = get_series(
                    bs,
                    'Stockholders Equity', 'Total Stockholder Equity',
                    'StockholdersEquity', 'Common Stock Equity', 'CommonStockEquity',
                )
                ta = get_series(bs, 'Total Assets', 'TotalAssets')
                if eq and ta:
                    eq_v = next((v for v in reversed(eq) if v is not None), None)
                    ta_v = next((v for v in reversed(ta) if v is not None), None)
                    if eq_v and ta_v and ta_v > 0:
                        data['equity_ratio'] = round(eq_v / ta_v * 100, 1)

                cash = get_series(
                    bs,
                    'Cash And Cash Equivalents', 'CashAndCashEquivalents',
                    'Cash', 'Cash Cash Equivalents And Short Term Investments',
                )
                if cash:
                    data['cash_trend'] = analyze_trend(cash)
        except Exception as e:
            print(f"    [WARN] {code} balance_sheet: {e}")

        # 配当履歴
        try:
            divs = ticker.dividends
            if divs is not None and len(divs) >= 4:
                annual = divs.groupby(divs.index.year).sum()
                if len(annual) >= 2:
                    amounts = [float(annual[y]) for y in sorted(annual.index)]
                    cut = any(b < a * 0.9 for a, b in zip(amounts, amounts[1:]))
                    incr = all(b >= a for a, b in zip(amounts, amounts[1:]))
                    if cut:
                        data['dividend_hist'] = 'cut'
                    elif incr:
                        data['dividend_hist'] = 'increase'
                    else:
                        data['dividend_hist'] = 'stable'
        except Exception as e:
            print(f"    [WARN] {code} dividends: {e}")

    except Exception as e:
        data['error'] = str(e)

    return data


# ─────────────────────────────────────────────
# Step 5: 12基準スコアリング（最大19点）
# ─────────────────────────────────────────────

def score_stock(s):
    """
    12基準でスコアリング。
    減配があれば即失格(-99)。最大19点。
    """
    # 失格: 減配あり
    if s.get('dividend_hist') == 'cut':
        return -99, ['❌ 減配あり（失格）']

    score = 0
    details = []

    # ① 売上推移（最大2点）
    rt = s.get('revenue_trend')
    if rt == 'up':
        score += 2
        details.append('① 売上推移：右肩上がり ✓ (+2)')
    elif rt == 'flat':
        details.append('① 売上推移：横ばい (0)')
    elif rt == 'down':
        details.append('① 売上推移：減少 (-)')
    else:
        details.append('① 売上推移：データなし')

    # ② 営業利益率（最大2点）
    om = s.get('operating_margin')
    if om is not None:
        if om >= 10:
            score += 2
            details.append(f'② 営業利益率：{om}% ✓ (+2)')
        elif om >= 5:
            score += 1
            details.append(f'② 営業利益率：{om}% (+1)')
        else:
            details.append(f'② 営業利益率：{om}% (-)')
    else:
        details.append('② 営業利益率：データなし')

    # ③ EPS推移（最大2点）
    et = s.get('eps_trend')
    if et == 'up':
        score += 2
        details.append('③ EPS推移：右肩上がり ✓ (+2)')
    elif et == 'flat':
        details.append('③ EPS推移：横ばい (0)')
    elif et == 'down':
        details.append('③ EPS推移：減少 (-)')
    else:
        details.append('③ EPS推移：データなし')

    # ④ 営業キャッシュフロー（最大2点）
    ocf = s.get('ocf_status')
    if ocf == 'good':
        score += 2
        details.append('④ 営業CF：プラス＆増加 ✓ (+2)')
    elif ocf == 'ok':
        score += 1
        details.append('④ 営業CF：プラス維持 (+1)')
    elif ocf == 'bad':
        details.append('④ 営業CF：マイナスあり (-)')
    else:
        details.append('④ 営業CF：データなし')

    # ⑤ 配当履歴（最大2点）
    dh = s.get('dividend_hist')
    if dh == 'increase':
        score += 2
        details.append('⑤ 配当履歴：増配継続 ✓ (+2)')
    elif dh == 'stable':
        score += 1
        details.append('⑤ 配当履歴：横ばい維持 (+1)')
    else:
        details.append('⑤ 配当履歴：データなし')

    # ⑥ 配当性向（最大2点）
    pr = s.get('payout_ratio')
    if pr is not None:
        if 30 <= pr <= 50:
            score += 2
            details.append(f'⑥ 配当性向：{pr}%（適正） ✓ (+2)')
        elif 50 < pr <= 70:
            score += 1
            details.append(f'⑥ 配当性向：{pr}%（やや高め） (+1)')
        else:
            details.append(f'⑥ 配当性向：{pr}%（範囲外） (-)')
    else:
        details.append('⑥ 配当性向：データなし')

    # ⑦ 自己資本比率（最大2点）
    er = s.get('equity_ratio')
    if er is not None:
        if er >= 40:
            score += 2
            details.append(f'⑦ 自己資本比率：{er}% ✓ (+2)')
        elif er >= 30:
            score += 1
            details.append(f'⑦ 自己資本比率：{er}% (+1)')
        else:
            details.append(f'⑦ 自己資本比率：{er}% (-)')
    else:
        details.append('⑦ 自己資本比率：データなし')

    # ⑧ 現金推移（最大1点）
    ct = s.get('cash_trend')
    if ct == 'up':
        score += 1
        details.append('⑧ 現金推移：増加 ✓ (+1)')
    elif ct:
        details.append(f'⑧ 現金推移：{ct} (0)')
    else:
        details.append('⑧ 現金推移：データなし')

    # ⑨ 配当利回り（最大2点）
    dy = s.get('dividend_yield')
    if dy is not None:
        if dy >= 4.0:
            score += 2
            details.append(f'⑨ 配当利回り：{dy}% ✓ (+2)')
        elif dy >= 3.7:
            score += 1
            details.append(f'⑨ 配当利回り：{dy}% (+1)')
        else:
            details.append(f'⑨ 配当利回り：{dy}% (-)')
    else:
        details.append('⑨ 配当利回り：データなし')

    # ⑩ 連続増配（最大1点、⑤と連動）
    if dh == 'increase':
        score += 1
        details.append('⑩ 連続増配：確認済み ✓ (+1)')
    else:
        details.append('⑩ 連続増配：確認できず (0)')

    return score, details


# ─────────────────────────────────────────────
# Step 6: メール送信
# ─────────────────────────────────────────────

def send_email(candidates):
    """有望候補をGmailで通知"""
    gmail_address = os.environ.get('GMAIL_ADDRESS')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD')
    notify_email = os.environ.get('NOTIFY_EMAIL', gmail_address)

    if not gmail_address or not gmail_password:
        print("[SKIP] メール設定なし（GMAIL_ADDRESS / GMAIL_APP_PASSWORD が未設定）")
        return

    now_str = datetime.now(JST).strftime('%Y年%m月%d日')
    subject = f"【クロコちゃん】高配当有望銘柄レポート {now_str}（{len(candidates)}銘柄）"

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "  クロコちゃんの高配当銘柄スキャンレポート",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"実行日時 : {datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST",
        f"有望候補 : {len(candidates)} 銘柄（スコア{MIN_SCORE}点以上／19点満点）",
        "",
    ]

    for i, (stock, score, details) in enumerate(candidates, 1):
        dy_str = f"{stock['dividend_yield']:.2f}%" if stock.get('dividend_yield') else "N/A"
        price_str = f"¥{stock['current_price']:,.0f}" if stock.get('current_price') else "N/A"
        mcap = stock.get('market_cap')
        mcap_str = f"{mcap / 1e8:.0f}億円" if mcap else "N/A"
        sector = stock.get('sector') or '不明'

        lines += [
            f"【第{i}位】 {stock['code']}  {stock.get('name', '')}",
            f"  スコア    : {score} / 19点",
            f"  配当利回り: {dy_str}",
            f"  株価      : {price_str}",
            f"  時価総額  : {mcap_str}",
            f"  セクター  : {sector}",
            "",
            "  ■ 採点詳細",
        ]
        for d in details:
            lines.append(f"    {d}")
        lines += ["", "─" * 50, ""]

    lines += [
        "※ このメールはクロコちゃん（Claude）が自動生成しました。",
        "※ 追加したい銘柄はClaudeに「〇〇（銘柄コード）を追加して」と伝えてください。",
        "※ 投資判断はご自身の責任でお願いします。",
    ]

    body = "\n".join(lines)

    msg = MIMEMultipart()
    msg['From'] = gmail_address
    msg['To'] = notify_email
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain', 'utf-8'))

    try:
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(gmail_address, gmail_password)
            smtp.send_message(msg)
        print(f"✓ メール送信完了 → {notify_email}")
    except Exception as e:
        print(f"[ERROR] メール送信失敗: {e}")
        raise


# ─────────────────────────────────────────────
# メイン
# ─────────────────────────────────────────────

def main():
    print(f"=== クロコちゃん銘柄スキャン開始 {datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST ===\n")

    # 既存登録銘柄
    existing = load_existing_codes()
    print(f"既存登録銘柄: {len(existing)} 銘柄")

    # 全銘柄コード取得
    all_codes = fetch_all_tse_codes()
    if not all_codes:
        print("[ERROR] 銘柄リスト取得失敗。終了します。")
        sys.exit(1)

    # 未登録銘柄のみ対象
    new_codes = [c for c in all_codes if str(c) not in existing]
    print(f"スキャン対象: {len(new_codes)} 銘柄（既存 {len(existing)} 銘柄を除外）\n")

    # ─── Phase 1: 一括利回りスキャン ───
    print("--- Phase 1: 一括利回りスキャン ---")
    yield_map = bulk_fetch_yields(new_codes)

    candidates_codes = [
        code for code, dy in yield_map.items()
        if dy is not None and dy >= MIN_YIELD
    ]
    print(f"\n利回り{MIN_YIELD}%以上: {len(candidates_codes)} 銘柄\n")

    if not candidates_codes:
        print("候補銘柄なし。メール送信をスキップします。")
        return

    # ─── Phase 2: 詳細分析 ───
    print(f"--- Phase 2: 詳細分析（{len(candidates_codes)} 銘柄） ---")
    results = []
    for i, code in enumerate(candidates_codes, 1):
        print(f"[{i:3d}/{len(candidates_codes)}] {code} 分析中...")
        s = fetch_full_data(code)
        score, details = score_stock(s)
        dy = s.get('dividend_yield')
        dy_str = f"{dy:.2f}%" if dy else "N/A"
        print(f"  → スコア: {score:2d}/19  利回り: {dy_str}  {s.get('name', '')}")
        if score >= 0:  # 失格(-99)以外を保持
            results.append((s, score, details))
        time.sleep(0.5)

    # スコア降順にソート
    results.sort(key=lambda x: x[1], reverse=True)

    # MIN_SCORE 以上を候補に
    good_candidates = [(s, sc, d) for s, sc, d in results if sc >= MIN_SCORE]

    print(f"\n★ スコア{MIN_SCORE}点以上の有望銘柄: {len(good_candidates)} 銘柄")
    for s, sc, _ in good_candidates:
        dy = s.get('dividend_yield')
        print(f"  {s['code']}  {s.get('name', ''):<20}  {sc:2d}/19点  利回り{dy:.2f}%" if dy else
              f"  {s['code']}  {s.get('name', ''):<20}  {sc:2d}/19点")

    if not good_candidates:
        print("\n今週は有望候補なし。メール送信をスキップします。")
        return

    # ─── Phase 3: メール送信 ───
    print("\n--- Phase 3: メール送信 ---")
    send_email(good_candidates)

    print(f"\n=== スキャン完了 {datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST ===")


if __name__ == '__main__':
    main()
