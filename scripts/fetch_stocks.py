#!/usr/bin/env python3
"""
日本株高配当株トラッカー - データ取得スクリプト
Yahoo Finance APIで日本語銘柄名・日本語セクターを取得
"""

import yfinance as yf
import json
import os
import time
import requests
from datetime import datetime
import pytz

STOCK_CODES = [
    9986, 3076, 8130, 2659, 3333, 4008, 4042, 4097, 8309, 8725,
    8593, 8584, 6785, 7723, 3231, 3003, 2169, 9757, 9769, 4641,
    3817, 3901, 4674, 2003, 1414, 1928, 6345, 9364, 9381, 5388,
    7989, 7820, 7994, 4540, 1343, 9432, 2181, 2353, 8572, 7593,
    5401, 4820, 8016, 1822, 9990, 4318, 4246, 6091, 7246, 2296,
    4631, 9107, 5938, 8125, 7239, 6905, 7177, 7313, 4521, 4114,
    1878, 2914, 4613, 8252, 8750, 9076, 6481, 7261, 7267, 9101,
    7240, 9434, 4202, 9104, 3291, 1719, 6417, 3861, 4927, 7164,
    4205, 5411, 8174, 4502, 8410, 6141, 4523, 8424, 8766, 9143,
    6724, 9506, 5076, 4503, 5201, 5021, 8425, 7202, 5929, 5406,
    7762, 7272, 8439, 7751, 4528, 4118, 5444, 7270, 8539, 1925,
    4021, 5108, 4183, 9882, 9989, 4732, 7476, 7504, 8111, 8012,
    8418, 4901, 4041, 3946, 4272, 6272, 8037, 2168, 7607, 6349,
    7128, 8570, 5945, 2664, 6140, 7925, 4627, 6317, 1808, 8316,
    7199, 8919, 1911, 1950, 6480, 8804, 2907, 7296, 5902, 6249,
    3434, 5451, 6501, 6954, 7203, 9022, 6702, 8233, 8267, 9766,
    4063, 4661, 7011, 7974, 9202, 9861, 9023, 4373, 6754, 6857,
    4413, 4461, 6798, 6086, 6432, 5333, 6999, 9509, 7908, 6516,
    6952, 3826, 7823, 8141, 4229, 4973, 6701, 6146, 6814, 7721,
    2326, 3915, 7970, 1605, 2502, 2503, 4204, 5334, 6301, 6326,
    8001, 8031, 8058, 8306, 8473, 8591, 8630, 9433, 1951, 2124,
    5105, 9069, 2163, 3834, 7921, 7995, 9233, 1723, 2185, 2768,
    3433, 4248, 5186, 5911, 7438, 8002, 8098, 5970, 7466, 8015,
    8566, 9436, 3837, 3844, 4345, 4507, 5393, 6367, 8117, 8697,
    1828, 3191, 5184, 7613, 7811, 9956, 9997, 8967, 3283, 3471,
    8952, 3226, 3269, 3282, 3462, 8953, 3249
]

JST = pytz.timezone('Asia/Tokyo')
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; StockTracker/1.0)'}

# 英語セクター → 日本語変換
SECTOR_JA = {
    'Technology': 'テクノロジー',
    'Financial Services': '金融',
    'Financials': '金融',
    'Industrials': '産業',
    'Consumer Cyclical': '一般消費財',
    'Consumer Defensive': '生活必需品',
    'Healthcare': 'ヘルスケア',
    'Basic Materials': '素材',
    'Energy': 'エネルギー',
    'Utilities': '公益事業',
    'Real Estate': '不動産',
    'Communication Services': '通信',
    'Consumer Staples': '生活必需品',
    'Information Technology': 'テクノロジー',
    'Materials': '素材',
    'Telecommunication Services': '通信',
}


def translate_sector(sector_en: str) -> str:
    if not sector_en or sector_en == '不明':
        return '不明'
    return SECTOR_JA.get(sector_en, sector_en)


def get_japanese_names_bulk(codes: list) -> dict:
    """Yahoo Finance APIで日本語銘柄名を一括取得（50件ずつ）"""
    name_map = {}
    batch_size = 50
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        symbols = ','.join(f"{c}.T" for c in batch)
        try:
            url = (
                "https://query1.finance.yahoo.com/v7/finance/quote"
                f"?symbols={symbols}&lang=ja&region=JP"
            )
            resp = requests.get(url, headers=HEADERS, timeout=15)
            data = resp.json()
            results = data.get('quoteResponse', {}).get('result', [])
            for r in results:
                sym = r.get('symbol', '').replace('.T', '')
                name = r.get('longName') or r.get('shortName') or sym
                name_map[sym] = name
        except Exception as e:
            print(f"  [WARN] 日本語名取得失敗 (batch {i}): {e}")
        time.sleep(0.5)
    return name_map


def fetch_single_stock(code: int, name_map: dict) -> dict:
    ticker_symbol = f"{code}.T"
    code_str = str(code)
    base = {
        'code': code_str,
        'ticker': ticker_symbol,
        'name': name_map.get(code_str, code_str),
        'current_price': None,
        'previous_close': None,
        'price_change': None,
        'price_change_pct': None,
        'dividend_yield': None,
        'annual_dividend': None,
        'sector': '不明',
        'industry': '不明',
        'market_cap': None,
        'last_updated': datetime.now(JST).isoformat(),
        'error': None
    }

    try:
        ticker = yf.Ticker(ticker_symbol)
        info = ticker.info

        current_price = (
            info.get('currentPrice')
            or info.get('regularMarketPrice')
            or info.get('navPrice')
        )
        previous_close = (
            info.get('previousClose')
            or info.get('regularMarketPreviousClose')
        )

        price_change = None
        price_change_pct = None
        if current_price and previous_close:
            price_change = round(current_price - previous_close, 2)
            price_change_pct = round(price_change / previous_close * 100, 2)

        raw_yield = (
            info.get('dividendYield')
            or info.get('trailingAnnualDividendYield')
        )
        dividend_yield = None
        if raw_yield is not None:
            dividend_yield = round(
                raw_yield * 100 if raw_yield < 1 else raw_yield, 2
            )

        annual_dividend = (
            info.get('dividendRate')
            or info.get('trailingAnnualDividendRate')
        )
        if dividend_yield is None and annual_dividend and current_price:
            dividend_yield = round(annual_dividend / current_price * 100, 2)

        sector_en = info.get('sector') or ''
        sector_ja = translate_sector(sector_en) if sector_en else '不明'

        industry_en = info.get('industry') or ''
        industry_ja = translate_sector(industry_en) if industry_en else '不明'

        final_name = name_map.get(code_str) or info.get('longName') or info.get('shortName') or code_str

        base.update({
            'name': final_name,
            'current_price': current_price,
            'previous_close': previous_close,
            'price_change': price_change,
            'price_change_pct': price_change_pct,
            'dividend_yield': dividend_yield,
            'annual_dividend': annual_dividend,
            'sector': sector_ja,
            'industry': industry_ja,
            'market_cap': info.get('marketCap'),
        })
    except Exception as e:
        base['error'] = str(e)
        print(f"  [ERROR] {code}: {e}")

    return base


def main():
    print("=" * 60)
    print(f"日本株高配当株トラッカー - データ取得開始")
    print(f"取得時刻: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S JST')}")
    print(f"対象銘柄数: {len(STOCK_CODES)}")
    print("=" * 60)

    print("\n📝 日本語銘柄名を取得中...")
    name_map = get_japanese_names_bulk(STOCK_CODES)
    print(f"✓ {len(name_map)} 銘柄の日本語名を取得")

    print("\n📈 株価データを取得中...")
    results = []
    total = len(STOCK_CODES)

    for i, code in enumerate(STOCK_CODES, 1):
        print(f"[{i:3d}/{total}] {code}.T ...", end=' ', flush=True)
        stock = fetch_single_stock(code, name_map)
        results.append(stock)

        dy = stock['dividend_yield']
        price = stock['current_price']
        label = f"¥{price:,.0f}" if price else "N/A"
        yield_label = f"{dy:.2f}%" if dy else "N/A"
        print(f"{stock['name'][:18]:18s}  {label:>10s}  利回り:{yield_label}")

        if i % 30 == 0:
            time.sleep(1)

    valid = [s for s in results if s['dividend_yield'] is not None]
    alert_40 = [s for s in valid if s['dividend_yield'] >= 4.0]
    alert_37 = [s for s in valid if 3.7 <= s['dividend_yield'] < 4.0]

    print("\n" + "=" * 60)
    print(f"取得完了: {len(results)} 銘柄 / エラー: {sum(1 for s in results if s['error'])}")
    print(f"4.0%以上: {len(alert_40)} 銘柄 / 3.7〜4.0%: {len(alert_37)} 銘柄")
    print("=" * 60)

    os.makedirs('data', exist_ok=True)
    output = {
        'last_updated': datetime.now(JST).isoformat(),
        'fetch_count': len(results),
        'valid_count': len(valid),
        'alert_40_count': len(alert_40),
        'alert_37_count': len(alert_37),
        'stocks': results
    }

    with open('data/stocks.json', 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"✓ data/stocks.json に保存しました")


if __name__ == '__main__':
    main()
