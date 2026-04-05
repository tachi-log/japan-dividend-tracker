#!/usr/bin/env python3
import yfinance as yf
import json, os, time, requests
from datetime import datetime
import pytz

STOCK_CODES = [
    9986,3076,8130,2659,3333,4008,4042,4097,8309,8725,8593,8584,6785,7723,3231,
    3003,2169,9757,9769,4641,3817,3901,4674,2003,1414,1928,6345,9364,9381,5388,
    7989,7820,7994,4540,1343,9432,2181,2353,8572,7593,5401,4820,8016,1822,9990,
    4318,4246,6091,7246,2296,4631,9107,5938,8125,7239,6905,7177,7313,4521,4114,
    1878,2914,4613,8252,8750,9076,6481,7261,7267,9101,7240,9434,4202,9104,3291,
    1719,6417,3861,4927,7164,4205,5411,8174,4502,8410,6141,4523,8424,8766,9143,
    6724,9506,5076,4503,5201,5021,8425,7202,5929,5406,7762,7272,8439,7751,4528,
    4118,5444,7270,8539,1925,4021,5108,4183,9882,9989,4732,7476,7504,8111,8012,
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

def get_japanese_names(codes):
    name_map = {}
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]
        symbols = ','.join(f"{c}.T" for c in batch)
        try:
            url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}&lang=ja&region=JP"
            r = requests.get(url, headers=HEADERS, timeout=15)
            for item in r.json().get('quoteResponse',{}).get('result',[]):
                sym = item.get('symbol','').replace('.T','')
                name_map[sym] = item.get('longName') or item.get('shortName') or sym
        except Exception as e:
            print(f"[WARN] {e}")
        time.sleep(0.5)
    return name_map

def fetch_stock(code, name_map):
    sym = f"{code}.T"
    s = str(code)
    rec = {'code':s,'name':name_map.get(s,s),'current_price':None,'previous_close':None,
           'price_change':None,'price_change_pct':None,'dividend_yield':None,
           'sector':'不明','market_cap':None,'last_updated':datetime.now(JST).isoformat(),'error':None}
    try:
        info = yf.Ticker(sym).info
        price = info.get('currentPrice') or info.get('regularMarketPrice')
        prev  = info.get('previousClose') or info.get('regularMarketPreviousClose')
        chg   = round(price-prev,2) if price and prev else None
        chgp  = round(chg/prev*100,2) if chg and prev else None
        raw   = info.get('dividendYield') or info.get('trailingAnnualDividendYield')
        dy    = round(raw*100 if raw and raw<1 else raw,2) if raw else None
        ann   = info.get('dividendRate') or info.get('trailingAnnualDividendRate')
        if dy is None and ann and price:
            dy = round(ann/price*100,2)
        sector_en = info.get('sector','')
        rec.update({'name':name_map.get(s) or info.get('longName') or info.get('shortName') or s,
                    'current_price':price,'previous_close':prev,'price_change':chg,
                    'price_change_pct':chgp,'dividend_yield':dy,
                    'sector':SECTOR_JA.get(sector_en, sector_en or '不明'),
                    'market_cap':info.get('marketCap')})
    except Exception as e:
        rec['error']=str(e)
    return rec

def main():
    print(f"開始: {datetime.now(JST).strftime('%Y-%m-%d %H:%M:%S JST')} / {len(STOCK_CODES)}銘柄")
    name_map = get_japanese_names(STOCK_CODES)
    print(f"日本語名取得: {len(name_map)}件")
    results = []
    for i,code in enumerate(STOCK_CODES,1):
        s = fetch_stock(code, name_map)
        results.append(s)
        dy = f"{s['dividend_yield']:.2f}%" if s['dividend_yield'] else "N/A"
        print(f"[{i:3d}] {s['code']} {s['name'][:15]:15s} {dy}")
        if i % 30 == 0: time.sleep(1)
    valid = [s for s in results if s['dividend_yield']]
    os.makedirs('data', exist_ok=True)
    with open('data/stocks.json','w',encoding='utf-8') as f:
        json.dump({'last_updated':datetime.now(JST).isoformat(),
                   'fetch_count':len(results),'valid_count':len(valid),
                   'alert_40_count':len([s for s in valid if s['dividend_yield']>=4.0]),
                   'alert_37_count':len([s for s in valid if 3.7<=s['dividend_yield']<4.0]),
                   'stocks':results},f,ensure_ascii=False,indent=2)
    print("✓ data/stocks.json 保存完了")

if __name__=='__main__':
    main()
