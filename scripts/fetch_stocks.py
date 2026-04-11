#!/usr/bin/env python3
import yfinance as yf
import json, os, time, requests, math, sys
from datetime import datetime
from pathlib import Path
import pytz

sys.path.insert(0, str(Path(__file__).parent))
from translate_names import NAME_JA

STOCK_CODES = [
    9986,3076,8130,2659,3333,4008,4042,4097,8309,8725,8593,8584,6785,7723,3231,
    3003,2169,9757,9769,4641,3817,3901,4674,2003,1414,1928,6345,9364,9381,5388,
    7989,7820,7994,4540,1343,9432,2181,2353,8572,7593,5401,4820,8016,1822,9990,
    4318,4246,6091,7246,2296,4631,9107,5938,8125,7239,6905,7177,7313,4521,4114,
    1878,2914,4613,8252,8750,9076,6481,7261,7267,9101,7240,9434,4202,9104,3291,
    1719,6417,3861,4927,7164,4205,5411,8174,4502,8410,6141,4523,8424,8766,9143,
    6724,9506,5076,4503,5201,5021,8425,7202,5929,5406,7762,7272,8439,7751,4528,
    4118,5444,7270,3401,8035,1925,4021,5108,4183,9882,9989,4732,7476,7504,8111,8012,
    8418,4901,4041,3946,4272,6272,8037,2168,7607,6349,7128,8570,5945,2664,6140,
    7925,4627,6317,1808,8316,7199,8919,1911,1950,6480,8804,2907,7296,5902,6249,
    3434,5451,6501,6954,7203,9022,6702,8233,8267,9766,4063,4661,7011,7974,9202,
    9861,9023,4373,6754,6857,4413,4461,6798,6086,6432,5333,6999,9509,7908,6516,
    6952,3826,7823,8141,4229,4973,6701,6146,6814,7721,2326,3915,7970,1605,2502,
    2503,4204,5334,6301,6326,8001,8031,8058,8306,8473,8591,8630,9433,1951,2124,
    5105,9069,2163,3834,7921,7995,9233,1723,2185,2768,3433,4248,5186,5911,7438,
    8002,8098,5970,7466,8015,8566,9436,3837,3844,4345,4507,5393,6367,8117,8697,
    1828,3191,5184,7613,7811,9956,9997,8967,3283,3471,8952,3226,3269,3282,3462,
    8953,3249
]

JST = pytz.timezone('Asia/Tokyo')
HEADERS = {'User-Agent': 'Mozilla/5.0'}

SECTOR_JA = {
    'Technology':'テクノロジー','Financial Services':'金融','Financials':'金融',
    'Industrials':'産業','Consumer Cyclical':'一般消費財','Consumer Defensive':'生活必需品',
    'Healthcare':'ヘルスケア','Basic Materials':'素材','Materials':'素材',
    'Energy':'エネルギー','Utilities':'公益事業','Real Estate':'不動産',
    'Communication Services':'通信','Telecommunication Services':'通信',
    'Consumer Staples':'生活必需品','Information Technology':'テクノロジー',
}

# ============================================================
# トレンド分析ヘルパー
# ============================================================

def safe_float(v):
    """値をfloatに変換。変換不能またはNaNはNoneを返す。"""
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def analyze_trend(values):
    """
    values: 古い順→新しい順のリスト
    戻り値: 'up' / 'flat' / 'down' / None
    線形回帰の傾きを平均値で正規化して判定する。
    """
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
    """
    DataFrameから指定した行名のいずれかを探して値リストを返す。
    yfinanceは新しい順で返すので逆順（古い順）にして返す。
    """
    for name in names:
        if name in df.index:
            raw = df.loc[name].tolist()
            result = []
            for v in reversed(raw):
                result.append(safe_float(v))
            return result
    return []


# ============================================================
# 財務諸表データ取得
# ============================================================

def fetch_financial_data(ticker):
    """
    ticker: yf.Ticker オブジェクト
    財務諸表から8項目スクリーニングに必要なデータを取得して返す。
    すべてtry/exceptで保護 — 失敗しても空dictを返す。
    """
    result = {}

    # ── 損益計算書 ──
    try:
        fin = ticker.financials
        if fin is not None and not fin.empty:
            # 売上推移
            rev = get_series(fin, 'Total Revenue', 'TotalRevenue')
            if rev:
                result['revenue_trend'] = analyze_trend(rev)

            # EPS推移
            eps = get_series(fin, 'Basic EPS', 'Diluted EPS', 'BasicEPS', 'DilutedEPS')
            if eps:
                result['eps_trend'] = analyze_trend(eps)
    except Exception as e:
        print(f"  [WARN] financials: {e}")

    # ── キャッシュフロー計算書 ──
    try:
        cf = ticker.cashflow
        if cf is not None and not cf.empty:
            ocf = get_series(
                cf,
                'Operating Cash Flow', 'OperatingCashFlow',
                'Total Cash From Operating Activities',
                'Cash From Operations',
            )
            if ocf:
                valid = [v for v in ocf if v is not None]
                if valid:
                    all_pos = all(v > 0 for v in valid)
                    trend   = analyze_trend(ocf)
                    if all_pos and trend == 'up':
                        result['ocf_status'] = 'good'
                    elif all_pos:
                        result['ocf_status'] = 'ok'
                    else:
                        result['ocf_status'] = 'bad'
    except Exception as e:
        print(f"  [WARN] cashflow: {e}")

    # ── 貸借対照表 ──
    try:
        bs = ticker.balance_sheet
        if bs is not None and not bs.empty:
            # 自己資本比率 = 自己資本 / 総資産
            eq = get_series(
                bs,
                'Stockholders Equity', 'Total Stockholder Equity',
                'StockholdersEquity', 'Common Stock Equity', 'CommonStockEquity',
            )
            ta = get_series(bs, 'Total Assets', 'TotalAssets')
            if eq and ta:
                eq_v = next((v for v in reversed(eq) if v is not None), None)
                ta_v = next((v for v in reversed(ta) if v is not None), None)
                if eq_v is not None and ta_v and ta_v > 0:
                    result['equity_ratio'] = round(eq_v / ta_v * 100, 1)

            # 現金推移
            cash = get_series(
                bs,
                'Cash And Cash Equivalents', 'CashAndCashEquivalents',
                'Cash', 'Cash Cash Equivalents And Short Term Investments',
                'Cash And Short Term Investments',
            )
            if cash:
                result['cash_trend'] = analyze_trend(cash)
    except Exception as e:
        print(f"  [WARN] balance_sheet: {e}")

    # ── 配当履歴 ──
    try:
        divs = ticker.dividends
        if divs is not None and len(divs) >= 4:
            annual = divs.groupby(divs.index.year).sum()
            if len(annual) >= 2:
                amounts = [float(annual[y]) for y in sorted(annual.index)]
                cut   = any(b < a * 0.9 for a, b in zip(amounts, amounts[1:]))
                incr  = all(b >= a        for a, b in zip(amounts, amounts[1:]))
                if cut:
                    result['dividend_hist'] = 'cut'
                elif incr:
                    result['dividend_hist'] = 'increase'
                else:
                    result['dividend_hist'] = 'stable'
    except Exception as e:
        print(f"  [WARN] dividends: {e}")

    return result


# ============================================================
# 銘柄名取得
# ============================================================

def get_japanese_names(codes):
    # 辞書に登録済みのコードは直接使用
    name_map = {str(c): NAME_JA[str(c)] for c in codes if str(c) in NAME_JA}
    missing = [c for c in codes if str(c) not in NAME_JA]
    if missing:
        # 辞書にないコードのみAPIで取得試行
        for i in range(0, len(missing), 50):
            batch = missing[i:i+50]
            symbols = ','.join(f"{c}.T" for c in batch)
            try:
                url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}&lang=ja&region=JP"
                r = requests.get(url, headers=HEADERS, timeout=15)
                for item in r.json().get('quoteResponse', {}).get('result', []):
                    sym = item.get('symbol', '').replace('.T', '')
                    name_map[sym] = item.get('longName') or item.get('shortName') or sym
            except Exception as e:
                print(f"[WARN] name fetch: {e}")
            time.sleep(0.5)
    return name_map


# ============================================================
# 1銘柄のデータ取得
# ============================================================

def fetch_stock(code, name_map):
    sym = f"{code}.T"
    s   = str(code)
    rec = {
        'code': s,
        'name': name_map.get(s, s),
        'current_price': None,
        'previous_close': None,
        'price_change': None,
        'price_change_pct': None,
        'dividend_yield': None,
        'annual_dividend': None,
        'sector': '不明',
        'market_cap': None,
        'last_updated': datetime.now(JST).isoformat(),
        'error': None,
        # ── スクリーニング項目（自動取得） ──
        'operating_margin': None,   # 営業利益率 (%)
        'payout_ratio': None,       # 配当性向 (%)
        'per': None,                # PER (株価収益率)
        'pbr': None,                # PBR (株価純資産倍率)
        'week52_high': None,        # 52週高値
        'week52_low': None,         # 52週安値
        'revenue_trend': None,      # 売上推移: 'up'/'flat'/'down'
        'eps_trend': None,          # EPS推移: 'up'/'flat'/'down'
        'ocf_status': None,         # 営業CF: 'good'/'ok'/'bad'
        'equity_ratio': None,       # 自己資本比率 (%)
        'cash_trend': None,         # 現金推移: 'up'/'flat'/'down'
        'dividend_hist': None,      # 配当履歴: 'increase'/'stable'/'cut'
    }

    try:
        ticker = yf.Ticker(sym)
        info   = ticker.info

        # ── 基本情報 ──
        price = info.get('currentPrice') or info.get('regularMarketPrice')
        prev  = info.get('previousClose') or info.get('regularMarketPreviousClose')
        chg   = round(price - prev, 2) if price and prev else None
        chgp  = round(chg / prev * 100, 2) if chg and prev else None
        raw   = info.get('dividendYield') or info.get('trailingAnnualDividendYield')
        dy    = round(raw * 100 if raw and raw < 1 else raw, 2) if raw else None
        ann   = info.get('dividendRate') or info.get('trailingAnnualDividendRate')
        if dy is None and ann and price:
            dy = round(ann / price * 100, 2)

        sector_en = info.get('sector', '')

        # ── info から直接取得できるスクリーニング項目 ──
        op_m = safe_float(info.get('operatingMargins'))
        pay  = safe_float(info.get('payoutRatio'))

        rec.update({
            'name': name_map.get(s) or info.get('longName') or info.get('shortName') or s,
            'current_price': price,
            'previous_close': prev,
            'price_change': chg,
            'price_change_pct': chgp,
            'dividend_yield': dy,
            'annual_dividend': ann,
            'sector': SECTOR_JA.get(sector_en, sector_en or '不明'),
            'market_cap': info.get('marketCap'),
            'operating_margin': round(op_m * 100, 1) if op_m is not None else None,
            'payout_ratio':     round(pay * 100, 1) if pay is not None else None,
            'per': safe_float(info.get('trailingPE')),
            'pbr': safe_float(info.get('priceToBook')),
            'week52_high': safe_float(info.get('fiftyTwoWeekHigh')),
            'week52_low':  safe_float(info.get('fiftyTwoWeekLow')),
        })

        # ── 財務諸表からトレンドデータを取得 ──
        fin_data = fetch_financial_data(ticker)
        rec.update(fin_data)

    except Exception as e:
        rec['error'] = str(e)

    return rec


# ============================================================
# メイン
# ============================================================

def main():
    print(f"開始: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S JST')} / {len(STOCK_CODES)}銘柄")
    name_map = get_japanese_names(STOCK_CODES)
    print(f"日本語名取得: {len(name_map)}件")

    results = []
    for i, code in enumerate(STOCK_CODES, 1):
        s = fetch_stock(code, name_map)
        results.append(s)
        dy = f"{s['dividend_yield']:.2f}%" if s['dividend_yield'] else "N/A"
        auto_fields = sum(1 for k in ['operating_margin', 'revenue_trend', 'equity_ratio',
                                       'ocf_status', 'dividend_hist'] if s.get(k) is not None)
        print(f"[{i:3d}] {s['code']} {s['name'][:15]:15s} {dy} (自動取得: {auto_fields}/5項目)")
        # レート制限対策
        if i % 10 == 0:
            time.sleep(2)
        else:
            time.sleep(0.3)

    valid = [s for s in results if s['dividend_yield']]
    os.makedirs('data', exist_ok=True)
    with open('data/stocks.json', 'w', encoding='utf-8') as f:
        json.dump({
            'last_updated': datetime.now(JST).isoformat(),
            'fetch_count': len(results),
            'valid_count': len(valid),
            'alert_40_count': len([s for s in valid if s['dividend_yield'] >= 4.0]),
            'alert_37_count': len([s for s in valid if 3.7 <= s['dividend_yield'] < 4.0]),
            'stocks': results,
        }, f, ensure_ascii=False, indent=2)
    print("✓ data/stocks.json 保存完了")


if __name__ == '__main__':
    main()
