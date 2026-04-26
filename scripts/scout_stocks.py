#!/usr/bin/env python3
"""
scout_stocks.py

平日毎日、東証全上場銘柄を一括スキャンし、12基準を満たす有望高配当銘柄を発掘してメール送信。
・ETF/REIT/ファンド/インデックスは除外
・利回り4%以上のみ対象
・同月内にメール済みの銘柄は除外
・スコア上位30銘柄のみメール
・銘柄名は日本語表記
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

JST          = pytz.timezone('Asia/Tokyo')
HEADERS      = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'}
MIN_YIELD    = 4.0   # 配当利回り下限(%)
MIN_SCORE    = 17    # メール通知するスコア下限（最大23点）
MAX_MAIL     = 9999  # 上限なし（該当全銘柄をメール）

# ETF・ファンド系を除外するキーワード
ETF_KEYWORDS = [
    'ETF', 'REIT', 'リート', 'ファンド', 'インデックス', '上場投信',
    'Fund', 'Index', 'Trust', 'J-REIT',
    'iFree', 'iシェアーズ', 'ｉシェアーズ', 'One ETF', 'NEXT FUNDS',
    'eMAXIS', 'たわら', 'ニッセイ', '野村', 'シェアーズ',
]

DATA_DIR     = Path(__file__).parent.parent / 'data'
SCOUTED_FILE = DATA_DIR / 'scouted.json'


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
    vals = [safe_float(v) for v in values]
    vals = [v for v in vals if v is not None]
    if len(vals) < 2:
        return None
    n   = len(vals)
    mx  = (n - 1) / 2
    my  = sum(vals) / n
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
    for name in names:
        if name in df.index:
            return [safe_float(v) for v in reversed(df.loc[name].tolist())]
    return []


def is_etf(name_ja):
    """ETF・REIT・ファンド系かどうか判定"""
    if not name_ja:
        return False
    return any(kw.lower() in name_ja.lower() for kw in ETF_KEYWORDS)


# ─────────────────────────────────────────────
# Step 1: 既存登録銘柄コード取得
# ─────────────────────────────────────────────

def load_existing_codes():
    path = DATA_DIR / 'stocks.json'
    if not path.exists():
        return set()
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    return {str(s['code']) for s in data.get('stocks', [])}


# ─────────────────────────────────────────────
# Step 2: 今月メール済み銘柄の管理
# ─────────────────────────────────────────────

def load_scouted_this_month():
    """今月すでにメールした銘柄コードを取得"""
    month_key = datetime.now(JST).strftime('%Y-%m')
    if not SCOUTED_FILE.exists():
        return set()
    with open(SCOUTED_FILE, encoding='utf-8') as f:
        data = json.load(f)
    return set(data.get(month_key, []))


def save_scouted_codes(codes):
    """メールした銘柄コードを今月分に追記して保存"""
    month_key = datetime.now(JST).strftime('%Y-%m')
    data = {}
    if SCOUTED_FILE.exists():
        with open(SCOUTED_FILE, encoding='utf-8') as f:
            data = json.load(f)

    existing = set(data.get(month_key, []))
    existing.update(str(c) for c in codes)
    data[month_key] = sorted(existing)

    # 古い月のデータは3か月分だけ保持
    all_months = sorted(data.keys(), reverse=True)
    data = {m: data[m] for m in all_months[:3]}

    DATA_DIR.mkdir(exist_ok=True)
    with open(SCOUTED_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ scouted.json 更新（今月: {len(data[month_key])} 銘柄）")


# ─────────────────────────────────────────────
# Step 3: JPX全銘柄リスト取得（日本語名つき）
# ─────────────────────────────────────────────

def fetch_all_tse_stocks():
    """JPXから全上場銘柄を取得。{code: name_ja} を返す。ETF系は除外済み。"""
    url = 'https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls'
    print("JPX銘柄リスト取得中...")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        df = pd.read_excel(BytesIO(resp.content), header=0)

        # コード列・銘柄名列を特定
        code_col = next((c for c in df.columns if 'コード' in str(c)), None)
        name_col = next((c for c in df.columns if '銘柄名' in str(c) or '名称' in str(c)), None)

        if code_col is None:
            code_col = next((c for c in df.columns if df[c].dtype in ['int64', 'float64']), None)

        if code_col is None:
            print("[ERROR] コード列が見つかりません")
            return {}

        codes = df[code_col].dropna().astype(str).str.strip()
        names = df[name_col].astype(str).str.strip() if name_col is not None else codes

        stocks = {}
        for code, name in zip(codes, names):
            if len(code) == 4 and code[0].isdigit():
                if not is_etf(name):        # ETF系を除外
                    stocks[code] = name

        total_raw = sum(1 for c in codes if len(c) == 4 and c[0].isdigit())
        print(f"  東証全銘柄: {total_raw} → ETF等除外後: {len(stocks)}")
        return stocks
    except Exception as e:
        print(f"[ERROR] JPX取得失敗: {e}")
        return {}


# ─────────────────────────────────────────────
# Step 4: 一括利回りスキャン（yf.download 方式）
# ─────────────────────────────────────────────

def bulk_fetch_yields(codes, batch_size=100):
    """yf.download() で価格＋配当を一括取得して利回りを計算"""
    yield_map = {}
    batches = [codes[i:i + batch_size] for i in range(0, len(codes), batch_size)]
    total   = len(batches)

    for i, batch in enumerate(batches):
        symbols = [f"{c}.T" for c in batch]
        try:
            data = yf.download(
                symbols,
                period='1y',
                actions=True,
                group_by='ticker',
                progress=False,
                auto_adjust=False,
            )
            if data.empty:
                continue

            for sym in symbols:
                code = sym.replace('.T', '')
                try:
                    if len(symbols) == 1:
                        close = data['Close']
                        divs  = data['Dividends']
                    else:
                        close = data[sym]['Close']
                        divs  = data[sym]['Dividends']

                    price      = safe_float(close.dropna().iloc[-1]) if not close.dropna().empty else None
                    annual_div = float(divs.sum()) if divs is not None else 0.0

                    if price and price > 0 and annual_div > 0:
                        yield_pct = annual_div / price * 100
                        if yield_pct >= MIN_YIELD:
                            yield_map[code] = round(yield_pct, 2)
                except Exception:
                    pass

        except Exception as e:
            print(f"[WARN] batch {i + 1}/{total}: {e}")

        if (i + 1) % 10 == 0:
            print(f"  一括スキャン: {i + 1}/{total} バッチ完了 (候補: {len(yield_map)} 銘柄)")
        time.sleep(1)

    return yield_map


# ─────────────────────────────────────────────
# Step 5: 候補銘柄の詳細分析
# ─────────────────────────────────────────────

def fetch_full_data(code, name_ja=None):
    code = str(code)
    sym  = f"{code}.T"
    data = {
        'code': code,
        'name': name_ja or NAME_JA.get(code) or code,
        'dividend_yield': None,
        'current_price': None,
        'annual_dividend': None,
        'sector': '不明',
        'market_cap': None,
        'operating_margin': None,
        'payout_ratio': None,
        'pbr': None,
        'per': None,
        'revenue_trend': None,
        'eps_trend': None,
        'ocf_status': None,
        'equity_ratio': None,
        'cash_trend': None,
        'dividend_hist': None,
    }

    try:
        ticker = yf.Ticker(sym)
        info   = ticker.info

        price  = info.get('currentPrice') or info.get('regularMarketPrice')
        raw_dy = info.get('dividendYield') or info.get('trailingAnnualDividendYield')
        dy     = round(raw_dy * 100 if raw_dy and raw_dy < 1 else raw_dy, 2) if raw_dy else None
        ann    = info.get('dividendRate') or info.get('trailingAnnualDividendRate')
        if dy is None and ann and price:
            dy = round(ann / price * 100, 2)

        op_m = safe_float(info.get('operatingMargins'))
        pay  = safe_float(info.get('payoutRatio'))

        # 日本語名：JPXの名前を優先、なければNAME_JA辞書、最後にyfinanceの英語名
        ja_name = name_ja or NAME_JA.get(code) or info.get('longName') or info.get('shortName') or code

        data.update({
            'name':             ja_name,
            'current_price':    price,
            'dividend_yield':   dy,
            'annual_dividend':  ann,
            'sector':           info.get('sector', '不明'),
            'market_cap':       info.get('marketCap'),
            'operating_margin': round(op_m * 100, 1) if op_m is not None else None,
            'payout_ratio':     round(pay * 100, 1)  if pay is not None else None,
            'pbr':              safe_float(info.get('priceToBook')),
            'per':              safe_float(info.get('trailingPE')),
        })

        try:
            fin = ticker.financials
            if fin is not None and not fin.empty:
                rev = get_series(fin, 'Total Revenue', 'TotalRevenue')
                if rev:
                    data['revenue_trend'] = analyze_trend(rev)
                eps = get_series(fin, 'Basic EPS', 'Diluted EPS', 'BasicEPS', 'DilutedEPS')
                if eps:
                    data['eps_trend'] = analyze_trend(eps)
        except Exception:
            pass

        try:
            cf = ticker.cashflow
            if cf is not None and not cf.empty:
                ocf = get_series(cf, 'Operating Cash Flow', 'OperatingCashFlow',
                                 'Total Cash From Operating Activities')
                if ocf:
                    valid   = [v for v in ocf if v is not None]
                    all_pos = all(v > 0 for v in valid) if valid else False
                    trend   = analyze_trend(ocf)
                    data['ocf_status'] = ('good' if all_pos and trend == 'up'
                                          else 'ok' if all_pos else 'bad')
        except Exception:
            pass

        try:
            bs = ticker.balance_sheet
            if bs is not None and not bs.empty:
                eq = get_series(bs, 'Stockholders Equity', 'Total Stockholder Equity',
                                'StockholdersEquity', 'Common Stock Equity', 'CommonStockEquity')
                ta = get_series(bs, 'Total Assets', 'TotalAssets')
                if eq and ta:
                    eq_v = next((v for v in reversed(eq) if v is not None), None)
                    ta_v = next((v for v in reversed(ta) if v is not None), None)
                    if eq_v and ta_v and ta_v > 0:
                        data['equity_ratio'] = round(eq_v / ta_v * 100, 1)
                cash = get_series(bs, 'Cash And Cash Equivalents', 'CashAndCashEquivalents',
                                  'Cash', 'Cash Cash Equivalents And Short Term Investments')
                if cash:
                    data['cash_trend'] = analyze_trend(cash)
        except Exception:
            pass

        try:
            divs = ticker.dividends
            if divs is not None and len(divs) >= 4:
                annual = divs.groupby(divs.index.year).sum()
                if len(annual) >= 2:
                    amounts = [float(annual[y]) for y in sorted(annual.index)]
                    cut  = any(b < a * 0.9 for a, b in zip(amounts, amounts[1:]))
                    incr = all(b >= a        for a, b in zip(amounts, amounts[1:]))
                    data['dividend_hist'] = 'cut' if cut else 'increase' if incr else 'stable'
        except Exception:
            pass

    except Exception as e:
        data['error'] = str(e)

    return data


# ─────────────────────────────────────────────
# Step 6: 12基準スコアリング（最大19点）
# ─────────────────────────────────────────────

def score_stock(s):
    if s.get('dividend_hist') == 'cut':
        return -99, ['❌ 減配あり（失格）']

    score   = 0
    details = []

    rt = s.get('revenue_trend')
    if rt == 'up':
        score += 2; details.append('① 売上推移：右肩上がり ✓ (+2)')
    elif rt == 'flat':
        details.append('① 売上推移：横ばい (0)')
    elif rt == 'down':
        details.append('① 売上推移：減少 (-)')
    else:
        details.append('① 売上推移：データなし')

    om = s.get('operating_margin')
    if om is not None:
        if om >= 10:
            score += 2; details.append(f'② 営業利益率：{om}% ✓ (+2)')
        elif om >= 5:
            score += 1; details.append(f'② 営業利益率：{om}% (+1)')
        else:
            details.append(f'② 営業利益率：{om}% (-)')
    else:
        details.append('② 営業利益率：データなし')

    et = s.get('eps_trend')
    if et == 'up':
        score += 2; details.append('③ EPS推移：右肩上がり ✓ (+2)')
    elif et == 'flat':
        details.append('③ EPS推移：横ばい (0)')
    elif et == 'down':
        details.append('③ EPS推移：減少 (-)')
    else:
        details.append('③ EPS推移：データなし')

    ocf = s.get('ocf_status')
    if ocf == 'good':
        score += 2; details.append('④ 営業CF：プラス＆増加 ✓ (+2)')
    elif ocf == 'ok':
        score += 1; details.append('④ 営業CF：プラス維持 (+1)')
    elif ocf == 'bad':
        details.append('④ 営業CF：マイナスあり (-)')
    else:
        details.append('④ 営業CF：データなし')

    dh = s.get('dividend_hist')
    if dh == 'increase':
        score += 2; details.append('⑤ 配当履歴：増配継続 ✓ (+2)')
    elif dh == 'stable':
        score += 1; details.append('⑤ 配当履歴：横ばい維持 (+1)')
    else:
        details.append('⑤ 配当履歴：データなし')

    pr = s.get('payout_ratio')
    if pr is not None:
        if 30 <= pr <= 50:
            score += 2; details.append(f'⑥ 配当性向：{pr}%（適正） ✓ (+2)')
        elif 50 < pr <= 70:
            score += 1; details.append(f'⑥ 配当性向：{pr}%（やや高め） (+1)')
        else:
            details.append(f'⑥ 配当性向：{pr}%（範囲外） (-)')
    else:
        details.append('⑥ 配当性向：データなし')

    er = s.get('equity_ratio')
    if er is not None:
        if er >= 40:
            score += 2; details.append(f'⑦ 自己資本比率：{er}% ✓ (+2)')
        elif er >= 30:
            score += 1; details.append(f'⑦ 自己資本比率：{er}% (+1)')
        else:
            details.append(f'⑦ 自己資本比率：{er}% (-)')
    else:
        details.append('⑦ 自己資本比率：データなし')

    ct = s.get('cash_trend')
    if ct == 'up':
        score += 1; details.append('⑧ 現金推移：増加 ✓ (+1)')
    elif ct:
        details.append(f'⑧ 現金推移：{ct} (0)')
    else:
        details.append('⑧ 現金推移：データなし')

    dy = s.get('dividend_yield')
    if dy is not None:
        if dy >= 4.0:
            score += 2; details.append(f'⑨ 配当利回り：{dy}% ✓ (+2)')
        elif dy >= 3.7:
            score += 1; details.append(f'⑨ 配当利回り：{dy}% (+1)')
        else:
            details.append(f'⑨ 配当利回り：{dy}% (-)')
    else:
        details.append('⑨ 配当利回り：データなし')

    if dh == 'increase':
        score += 1; details.append('⑩ 連続増配：確認済み ✓ (+1)')
    else:
        details.append('⑩ 連続増配：確認できず (0)')

    # ⑪ PBR（最大2点）
    pbr = s.get('pbr')
    if pbr is not None:
        if pbr < 1.0:
            score += 2; details.append(f'⑪ PBR：{pbr:.2f}倍（1倍未満） ✓ (+2)')
        elif pbr < 1.5:
            score += 1; details.append(f'⑪ PBR：{pbr:.2f}倍 (+1)')
        else:
            details.append(f'⑪ PBR：{pbr:.2f}倍 (-)')
    else:
        details.append('⑪ PBR：データなし')

    # ⑫ PER（最大2点）
    per = s.get('per')
    if per is not None and per > 0:
        if per < 10:
            score += 2; details.append(f'⑫ PER：{per:.1f}倍（10倍未満） ✓ (+2)')
        elif per < 15:
            score += 1; details.append(f'⑫ PER：{per:.1f}倍 (+1)')
        else:
            details.append(f'⑫ PER：{per:.1f}倍 (-)')
    else:
        details.append('⑫ PER：データなし')

    return score, details


# ─────────────────────────────────────────────
# Step 7: メール送信
# ─────────────────────────────────────────────

def send_email(candidates):
    gmail_address  = os.environ.get('GMAIL_ADDRESS')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD')
    notify_email   = os.environ.get('NOTIFY_EMAIL', gmail_address)

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
        dy_str    = f"{stock['dividend_yield']:.2f}%" if stock.get('dividend_yield') else "N/A"
        price_str = f"¥{stock['current_price']:,.0f}"  if stock.get('current_price') else "N/A"
        mcap      = stock.get('market_cap')
        mcap_str  = f"{mcap / 1e8:.0f}億円" if mcap else "N/A"

        lines += [
            f"【第{i}位】 {stock['code']}  {stock.get('name', '')}",
            f"  スコア    : {score} / 19点",
            f"  配当利回り: {dy_str}",
            f"  株価      : {price_str}",
            f"  時価総額  : {mcap_str}",
            f"  セクター  : {stock.get('sector') or '不明'}",
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

    msg = MIMEMultipart()
    msg['From']    = gmail_address
    msg['To']      = notify_email
    msg['Subject'] = subject
    msg.attach(MIMEText("\n".join(lines), 'plain', 'utf-8'))

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

    existing       = load_existing_codes()
    scouted        = load_scouted_this_month()
    skip_codes     = existing | scouted
    print(f"既存登録銘柄: {len(existing)} 銘柄（除外）")
    print(f"今月メール済み: {len(scouted)} 銘柄")

    # ─── Phase 1: JPXリスト取得（ETF除外・日本語名つき） ───
    all_stocks = fetch_all_tse_stocks()   # {code: name_ja}
    new_stocks = {c: n for c, n in all_stocks.items() if c not in skip_codes}
    print(f"スキャン対象: {len(new_stocks)} 銘柄\n")

    if not new_stocks:
        print("スキャン対象なし。終了します。")
        return

    # ─── Phase 2: 一括利回りスキャン ───
    print("--- Phase 2: 一括利回りスキャン ---")
    yield_map       = bulk_fetch_yields(list(new_stocks.keys()))
    candidates_codes = list(yield_map.keys())
    print(f"\n利回り{MIN_YIELD}%以上の候補: {len(candidates_codes)} 銘柄\n")

    if not candidates_codes:
        print("候補銘柄なし。メール送信をスキップします。")
        return

    # ─── Phase 3: 詳細分析 ───
    print(f"--- Phase 3: 詳細分析（{len(candidates_codes)} 銘柄） ---")
    results = []
    for i, code in enumerate(candidates_codes, 1):
        name_ja = new_stocks.get(code)
        s              = fetch_full_data(code, name_ja)
        score, details = score_stock(s)

        # 失格（減配）または1点未満はスキップ（ログにも出さない）
        if score <= 0:
            time.sleep(0.5)
            continue

        dy     = s.get('dividend_yield')
        dy_str = f"{dy:.2f}%" if dy else "N/A"
        print(f"[{i:3d}/{len(candidates_codes)}] {code} {name_ja or ''} → {score:2d}/19点  {dy_str}")
        results.append((s, score, details))
        time.sleep(0.5)

    results.sort(key=lambda x: x[1], reverse=True)

    # スコア MIN_SCORE 以上、上位 MAX_MAIL 件まで
    good_candidates = [(s, sc, d) for s, sc, d in results if sc >= MIN_SCORE][:MAX_MAIL]

    print(f"\n★ 有望銘柄（スコア{MIN_SCORE}点以上、上位{MAX_MAIL}件）: {len(good_candidates)} 銘柄")
    for s, sc, _ in good_candidates:
        dy = s.get('dividend_yield')
        print(f"  {s['code']}  {s.get('name', ''):<20}  {sc:2d}/19点  {dy:.2f}%" if dy else
              f"  {s['code']}  {s.get('name', ''):<20}  {sc:2d}/19点")

    if not good_candidates:
        print("\n今日は有望候補なし。メール送信をスキップします。")
        return

    # ─── Phase 4: メール送信 ───
    print("\n--- Phase 4: メール送信 ---")
    send_email(good_candidates)

    # ─── 送信済み銘柄を記録 ───
    emailed_codes = [s['code'] for s, _, _ in good_candidates]
    save_scouted_codes(emailed_codes)

    print(f"\n=== スキャン完了 {datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST ===")


if __name__ == '__main__':
    main()
