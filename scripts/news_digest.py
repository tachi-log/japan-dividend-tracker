#!/usr/bin/env python3
"""
news_digest.py

平日19時に関心キーワードのニュースをRSSから取得してメール送信。
"""

import os
import smtplib
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytz
import requests
import xml.etree.ElementTree as ET

JST = pytz.timezone('Asia/Tokyo')

# ─────────────────────────────────────────────
# 設定
# ─────────────────────────────────────────────

KEYWORDS = [
    # 経済・投資
    '経済', '株', '株価', '日経', '投資', 'NISA', 'ニーサ', '配当', 'FIRE',
    '金利', '円安', '円高', '為替', 'インフレ', '物価', 'GDP',
    # 国内・世界ニュース
    '政治', '政府', '首相', '国会', '選挙', '外交', 'アメリカ', '中国',
    '戦争', 'ウクライナ', '中東', '国際', '世界',
    # 健康・ライフスタイル
    '健康', '医療', '病気', 'がん', '食事', '運動', 'ウォーキング', '長寿',
    '睡眠', 'メンタル', 'ストレス',
    # その他関心事
    'AI', '人工知能', 'テクノロジー', '楽天', '節約', '副業',
]

RSS_FEEDS = [
    {
        'name': 'NHKニュース（主要）',
        'url': 'https://www3.nhk.or.jp/rss/news/cat0.xml',
    },
    {
        'name': 'NHK（経済）',
        'url': 'https://www3.nhk.or.jp/rss/news/cat4.xml',
    },
    {
        'name': 'NHK（政治）',
        'url': 'https://www3.nhk.or.jp/rss/news/cat6.xml',
    },
    {
        'name': 'NHK（国際）',
        'url': 'https://www3.nhk.or.jp/rss/news/cat5.xml',
    },
    {
        'name': 'NHK（科学・医療）',
        'url': 'https://www3.nhk.or.jp/rss/news/cat3.xml',
    },
    {
        'name': '東洋経済オンライン',
        'url': 'https://toyokeizai.net/list/feed/rss',
    },
    {
        'name': 'ダイヤモンドオンライン',
        'url': 'https://diamond.jp/category/feed',
    },
]

HEADERS = {'User-Agent': 'Mozilla/5.0'}


# ─────────────────────────────────────────────
# RSSフィード取得
# ─────────────────────────────────────────────

def fetch_rss(feed):
    """RSSフィードから記事リストを取得"""
    articles = []
    try:
        r = requests.get(feed['url'], headers=HEADERS, timeout=15)
        r.raise_for_status()
        r.encoding = 'utf-8'
        root = ET.fromstring(r.content)

        # RSS 2.0 形式
        for item in root.findall('.//item'):
            title = item.findtext('title') or ''
            link  = item.findtext('link') or ''
            desc  = item.findtext('description') or ''
            pub   = item.findtext('pubDate') or ''
            articles.append({
                'source': feed['name'],
                'title':  title.strip(),
                'link':   link.strip(),
                'desc':   desc.strip()[:100],
                'pub':    pub.strip(),
            })
    except Exception as e:
        print(f"[WARN] {feed['name']}: {e}")
    return articles


def is_relevant(article):
    """キーワードにマッチするか判定"""
    text = (article['title'] + article['desc']).lower()
    return any(kw.lower() in text for kw in KEYWORDS)


def is_today(pub_str):
    """今日または昨日の記事かどうか（古い記事を除外）"""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(pub_str)
        dt_jst = dt.astimezone(JST)
        now = datetime.now(JST)
        return (now - dt_jst) < timedelta(hours=30)
    except Exception:
        return True  # 日付不明は含める


# ─────────────────────────────────────────────
# メール送信
# ─────────────────────────────────────────────

def send_email(articles):
    gmail_address  = os.environ.get('GMAIL_ADDRESS')
    gmail_password = os.environ.get('GMAIL_APP_PASSWORD')
    notify_email   = os.environ.get('NOTIFY_EMAIL', gmail_address)

    if not gmail_address or not gmail_password:
        print("[SKIP] メール設定なし")
        return

    now     = datetime.now(JST)
    weekday = ['月', '火', '水', '木', '金', '土', '日'][now.weekday()]
    subject = f"【クロコちゃん】{now.strftime('%m月%d日')}({weekday}) 今日のニュースまとめ"

    # ソース別にグループ化
    from collections import defaultdict
    grouped = defaultdict(list)
    for a in articles:
        grouped[a['source']].append(a)

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  今日のニュースまとめ　{now.strftime('%Y年%m月%d日')}({weekday})",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"お疲れ様です、たっちん！",
        f"今日も1日お疲れ様でした。",
        f"クロコちゃんが気になるニュースを {len(articles)} 件ピックアップしました。",
        "",
    ]

    for source, items in grouped.items():
        lines.append(f"■ {source}（{len(items)}件）")
        lines.append("─" * 40)
        for a in items:
            lines.append(f"・{a['title']}")
            if a['link']:
                lines.append(f"  {a['link']}")
        lines.append("")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "明日もいい1日を！ クロコちゃんより",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    body = "\n".join(lines)

    msg = MIMEMultipart()
    msg['From']    = gmail_address
    msg['To']      = notify_email
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
    print(f"=== ニュースダイジェスト開始 {datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST ===\n")

    all_articles = []
    seen_titles  = set()

    for feed in RSS_FEEDS:
        print(f"取得中: {feed['name']}")
        articles = fetch_rss(feed)
        time.sleep(0.5)

        for a in articles:
            # 重複タイトル除外・キーワードフィルタ・日付フィルタ
            if a['title'] in seen_titles:
                continue
            if not is_relevant(a):
                continue
            if not is_today(a['pub']):
                continue
            seen_titles.add(a['title'])
            all_articles.append(a)

    print(f"\n該当記事: {len(all_articles)} 件")

    if not all_articles:
        print("記事なし。メール送信をスキップします。")
        return

    send_email(all_articles)
    print(f"\n=== 完了 {datetime.now(JST).strftime('%Y-%m-%d %H:%M')} JST ===")


if __name__ == '__main__':
    main()
