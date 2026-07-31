#!/usr/bin/env python3
"""
BTC 每日新聞抓取腳本（修復版）
修復：
  1. fetch_feed()：改用 requests 先抓 content，再交 feedparser 解析
     → 解決 feedparser 直連被 CDN block 的問題
  2. is_btc_related()：改為「title 有關鍵字」OR「來源係 BTC 專門媒體」
     → 避免 keyword 過嚴漏掉文章
  3. 新增 retry 機制（最多 2 次）
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import feedparser
    import requests
    from dotenv import load_dotenv
except ImportError:
    print("❌ pip3 install feedparser requests python-dotenv --break-system-packages")
    sys.exit(1)

ENV_PATH = Path.home() / ".hermes" / "config" / "btc_news.env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

# ── 修復一：更真實的瀏覽器 Headers ──────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Cache-Control":   "no-cache",
}

NEWS_SOURCES = [
    {"name": "CoinDesk",         "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",      "emoji": "📰", "btc_focused": False, "content_keywords": []},
    {"name": "CoinTelegraph",    "url": "https://cointelegraph.com/rss",                         "emoji": "📡", "btc_focused": False, "content_keywords": []},
    {"name": "Bitcoin Magazine", "url": "https://bitcoinmagazine.com/.rss/full",                 "emoji": "₿",  "btc_focused": True, "content_keywords": []},
    {"name": "The Block",        "url": "https://www.theblock.co/rss.xml",                       "emoji": "🔷", "btc_focused": False, "content_keywords": []},
    {"name": "Decrypt",          "url": "https://decrypt.co/feed",                               "emoji": "🔐", "btc_focused": False, "content_keywords": []},
    {"name": "Blockworks",       "url": "https://blockworks.co/feed/",                           "emoji": "⛏️",  "btc_focused": False, "content_keywords": []},
    {"name": "Google News",      "url": "https://news.google.com/rss/search?q=Bitcoin+BTC+price&hl=en-US&gl=US&ceid=US:en", "emoji": "🌐", "btc_focused": True, "content_keywords": []},
    {"name": "Google News ZH",   "url": "https://news.google.com/rss/search?q=比特幣+BTC+價格&hl=zh-TW&gl=TW&ceid=TW:zh-Hant", "emoji": "🇹🇼", "btc_focused": True, "content_keywords": []},
]

cache_title_set = set()  # 用於去重，避免同一篇文章多次出現 

# ── 修復二：更寬鬆的 BTC 過濾邏輯 ──────────────────────────────────────────
BTC_KEYWORDS_TITLE = [
    # 直接提及 BTC
    "bitcoin", "btc", "比特幣",
    # 相關機構/產品
    "microstrategy", "blackrock bitcoin", "spot bitcoin", "bitcoin etf",
    "saylor", "coinbase", "binance",
    # 技術/協議
    "satoshi", "lightning", "halving", "ordinals", "taproot", "runes",
    "proof of work", "hashrate", "hash rate",
    # 常見新聞標題模式
    "crypto", "cryptocurrency", "digital asset",
]

BTC_KEYWORDS_BODY = [
    "bitcoin", "btc", "比特幣", "crypto", "cryptocurrency",
    "blockchain", "digital asset", "satoshi",
]

def is_btc_related(entry, source: dict) -> bool:
    """
    修復版過濾邏輯：
    - btc_focused 來源（Bitcoin Magazine、Google News BTC query）：全部收錄
    - 其他來源：title 含關鍵字即收錄（唔依賴 summary/body）
    """
    # 規則 1：BTC 專門來源，直接收錄所有文章
    if source.get("btc_focused"):
        return True

    # 規則 2：Title 含 BTC 關鍵字（最可靠）
    title = entry.get("title", "").lower()
    if any(kw in title for kw in BTC_KEYWORDS_TITLE):
        return True

    # 規則 3：Summary/Content 含關鍵字（備用）
    summary = entry.get("summary", "")
    # feedparser Atom feed 的內容可能在 content[0].value
    if not summary and entry.get("content"):
        try:
            summary = entry.content[0].get("value", "")
        except Exception:
            pass
    summary_clean = re.sub(r"<[^>]+>", "", summary).lower()
    if any(kw in summary_clean for kw in BTC_KEYWORDS_BODY):
        return True

    return False


def parse_published_time(entry) -> datetime:
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    except Exception:
        pass
    return datetime.now(timezone.utc)


def time_ago(dt: datetime) -> str:
    diff   = datetime.now(timezone.utc) - dt
    secs   = int(diff.total_seconds())
    if secs < 0:      return "剛剛"
    if secs < 60:     return f"{secs} 秒前"
    if secs < 3600:   return f"{secs // 60} 分鐘前"
    if secs < 86400:  return f"{secs // 3600} 小時前"
    return f"{secs // 86400} 天前"


def _clean_summary(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw).strip()
    text = re.sub(r"\s+", " ", text)
    return (text[:150] + "…") if len(text) > 150 else text


# ── 修復三：requests 先抓，feedparser 後解析 ────────────────────────────────
def fetch_feed(source: dict, retries: int = 2) -> list[dict]:
    """
    修復版：用 requests 抓取 RSS content，再交 feedparser 解析。
    比直接 feedparser.parse(url) 更能控制 headers，避免 CDN block。
    """
    for attempt in range(retries):
        try:
            r = requests.get(source["url"], headers=HEADERS, timeout=15)

            if r.status_code == 403:
                print(f"   ⚠️  {source['name']}: 403 被拒，略過", file=sys.stderr)
                return []
            if r.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"   ⏳ {source['name']}: Rate limit，等 {wait}s 後重試", file=sys.stderr)
                time.sleep(wait)
                continue
            if not r.ok:
                print(f"   ⚠️  {source['name']}: HTTP {r.status_code}", file=sys.stderr)
                return []
            if len(r.content) < 200:
                print(f"   ⚠️  {source['name']}: 回應內容太短（{len(r.content)} bytes）", file=sys.stderr)
                return []

            # ✅ 用 content bytes 解析（唔係 URL）
            feed     = feedparser.parse(r.content)
            articles = []

            for entry in feed.entries:
                if not is_btc_related(entry, source):
                    continue
                # 去重：同一篇文章唔好出現多次
                title_key = entry.get("title", "").lower()[:60]
                if title_key in cache_title_set:
                    continue
                cache_title_set.add(title_key)
                articles.append({
                    "title":     entry.get("title", "（無標題）").strip(),
                    "summary":   _clean_summary(entry.get("summary", "")),
                    "link":      entry.get("link", ""),
                    "source":    source["name"],
                    "emoji":     source["emoji"],
                    "published": parse_published_time(entry),
                })

            if articles:
                print(f"   ✅ {source['name']}: {len(articles)} 篇")
            else:
                total = len(feed.entries)
                print(f"   ℹ️  {source['name']}: 共 {total} 篇文章，無符合 BTC 條件")
            return articles

        except requests.exceptions.Timeout:
            print(f"   ⏱️  {source['name']}: 連線逾時", file=sys.stderr)
        except Exception as e:
            print(f"   ❌ {source['name']}: {e}", file=sys.stderr)

        if attempt < retries - 1:
            time.sleep(2)

    return []


def fetch_news(limit: int = 10) -> list[dict]:
    all_articles = []
    for source in NEWS_SOURCES:
        all_articles.extend(fetch_feed(source))

    seen, unique = set(), []
    for art in all_articles:
        key = art["title"].lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(art)

    unique.sort(key=lambda x: x["published"], reverse=True)
    return unique[:limit]


def fetch_btc_price() -> dict | None:
    for url, source_name in [
        ("https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd&include_24hr_change=true", "CoinGecko"),
        ("https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT", "Binance"),
    ]:
        try:
            r = requests.get(url, timeout=10)
            if not r.ok:
                continue
            data = r.json()
            if source_name == "CoinGecko":
                btc = data.get("bitcoin", {})
                return {"price": btc.get("usd", 0), "change_24h": btc.get("usd_24h_change", 0), "source": "CoinGecko"}
            else:
                return {"price": float(data["lastPrice"]), "change_24h": float(data["priceChangePercent"]), "source": "Binance"}
        except Exception:
            continue
    return None


def format_full(articles, price):
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"📰 BTC 每日新聞摘要 — {now}", "",
             f"🔥 重點新聞（最新 Top {len(articles)}）",
             "━━━━━━━━━━━━━━━━━━━━━━"]
    for i, art in enumerate(articles, 1):
        lines += [f"\n{i}. {art['title']}",
                  f"   {art['emoji']} {art['source']} | ⏰ {time_ago(art['published'])}"]
        if art["summary"]:
            lines.append(f"   📝 {art['summary']}")
        if art["link"]:
            lines.append(f"   🔗 {art['link']}")
    if price:
        arrow = "📈" if price["change_24h"] >= 0 else "📉"
        sign  = "+" if price["change_24h"] >= 0 else ""
        lines += ["", "📊 BTC 價格快照", "━━━━━━━━━━━━━━━━━━━━━━",
                  f"   💰 現價:   ${price['price']:,.0f} USD",
                  f"   {arrow} 24h:   {sign}{price['change_24h']:.2f}%",
                  f"   📡 來源:   {price['source']}"]
    lines.append("\n─────────────────────────")
    return "\n".join(lines)


def format_brief(articles):
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"₿ BTC 新聞速覽 — {now}", ""]
    for i, art in enumerate(articles, 1):
        lines.append(f"{i}. [{art['source']}] {art['title']}")
    return "\n".join(lines)


def send_telegram(message: str) -> bool:
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        print(f"❌ 請設定 {ENV_PATH}", file=sys.stderr)
        return False
    url    = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [message[i:i+4000] for i in range(0, len(message), 4000)]
    for chunk in chunks:
        r = requests.post(url, json={"chat_id": chat_id, "text": chunk}, timeout=15)
        if not r.ok:
            print(f"❌ Telegram: {r.text}", file=sys.stderr)
            return False
        time.sleep(0.3)
    print("✅ Telegram 已發送")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--telegram", action="store_true")
    parser.add_argument("--brief",    action="store_true")
    parser.add_argument("--limit",    type=int, default=10)
    args = parser.parse_args()

    print(f"🔍 正在從 {len(NEWS_SOURCES)} 個來源抓取 BTC 新聞…\n")
    articles = fetch_news(limit=args.limit)

    if not articles:
        print("\n⚠️  未找到 BTC 相關新聞")
        sys.exit(1)

    price = None
    if not args.brief:
        print("\n💹 正在取得 BTC 現價…")
        price = fetch_btc_price()

    output = format_brief(articles) if args.brief else format_full(articles, price)
    print("\n" + output)

    if args.telegram:
        send_telegram(output)


if __name__ == "__main__":
    main()