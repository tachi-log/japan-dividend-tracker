#!/usr/bin/env python3
import json, time, requests

SECTOR_JA = {
    'Technology': 'テクノロジー',
    'Financial Services': '金融',
    'Financials': '金融',
    'Industrials': '産業',
    'Consumer Cyclical': '一般消費財',
    'Consumer Defensive': '生活必需品',
    'Healthcare': 'ヘルスケア',
    'Basic Materials': '素材',
    'Materials': '素材',
    'Energy': 'エネルギー',
    'Utilities': '公益事業',
    'Real Estate': '不動産',
    'Communication Services': '通信',
    'Telecommunication Services': '通信',
    'Consumer Staples': '生活必需品',
    'Information Technology': 'テクノロジー',
}
HEADERS = {'User-Agent': 'Mozilla/5.0'}

with open('data/stocks.json', encoding='utf-8') as f:
    data = json.load(f)

codes = [s['code'] for s in data['stocks']]
name_map = {}

for i in range(0, len(codes), 50):
    batch = codes[i:i+50]
    symbols = ','.join(f"{c}.T" for c in batch)
    try:
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}&lang=ja&region=JP"
        r = requests.get(url, headers=HEADERS, timeout=15)
        for item in r.json().get('quoteResponse', {}).get('result', []):
            sym = item.get('symbol', '').replace('.T', '')
            name_map[sym] = item.get('longName') or item.get('shortName') or sym
        print(f"取得済み: {len(name_map)}件")
    except Exception as e:
        print(f"[WARN] {e}")
    time.sleep(0.5)

for s in data['stocks']:
    if s['code'] in name_map:
        s['name'] = name_map[s['code']]
    en = s.get('sector', '')
    if en in SECTOR_JA:
        s['sector'] = SECTOR_JA[en]

with open('data/stocks.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"✓ 日本語化完了（{len(name_map)}銘柄）")
