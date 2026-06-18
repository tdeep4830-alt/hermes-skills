#!/usr/bin/env python3
"""
BTC 每日新聞抓取腳本
=====================
功能：從多個主流加密貨幣新聞 RSS 來源抓取最新 BTC 相關新聞，
      整理成摘要並可透過 Telegram 發送通知。

用法：
  python3 fetch_btc_news.py              # 顯示新聞摘要
  python3 fetch_btc_news.py --telegram   # 顯示 + 發送 Telegram
  python3 fetch_btc_news.py --brief      # 只顯示標題
  python3 fetch_btc_news.py --limit 10   # 自訂顯示數量（預設 5）

依賴：
  pip3 install feedparser requests python-dotenv --break-system-packages
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── 第三方套件 ──────────────────────────────────────────────────────────────
try:
    import feedparser
    import requests
    from dotenv import load_dotenv
except ImportError:
    print("❌ 缺少依賴套件，請執行：")
    print("   pip3 install feedparser requests python-dotenv --break-system-packages")
    sys.exit(1)


# ── 設定 ────────────────────────────────────────────────────────────────────

# 載入環境變數
ENV_PATH = Path.home() / ".hermes" / "config" / "btc_news.env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

# HTTP 請求 Header（模擬瀏覽器，避免被阻擋）
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# 新聞來源 RSS（按優先度排列）
NEWS_SOURCES = [
    # 專業加密貨幣媒體
    {"name": "CoinDesk",        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",     "emoji": "📰"},
    {"name": "CoinTelegraph",   "url": "https://cointelegraph.com/rss",                        "emoji": "📡"},
    {"name": "Bitcoin Magazine","url": "https://bitcoinmagazine.com/.rss/full",                "emoji": "₿"},
    {"name": "The Block",       "url": "https://www.theblock.co/rss.xml",                      "emoji": "🔷"},
    {"name": "Decrypt",         "url": "https://decrypt.co/feed",                              "emoji": "🔐"},
    {"name": "Blockworks",      "url": "https://blockworks.co/feed/",                          "emoji": "⛏️"},
    # 備用：Google News（較寬鬆，通常不會封鎖爬蟲）
    {"name": "Google News",     "url": "https://news.google.com/rss/search?q=Bitcoin+BTC+cryptocurrency&hl=en-US&gl=US&ceid=US:en", "emoji": "🌐"},
    {"name": "Google News ZH",  "url": "https://news.google.com/rss/search?q=比特幣+BTC&hl=zh-TW&gl=TW&ceid=TW:zh-Hant",           "emoji": "🇹🇼"},
]

# BTC 關鍵字過濾
BTC_KEYWORDS = [
    "bitcoin", "btc", "比特幣", "satoshi", "lightning network",
    "lightning", "halving", "hash rate", "hashrate", "ordinals",
    "taproot", "bip", "proof of work", "runes", "nakamoto",
    "crypto", "cryptocurrency", "加密貨幣", "數位資產",
]

# CoinGecko 免費 API（BTC 現價）
COINGECKO_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin&vs_currencies=usd&include_24hr_change=true"
)

# Binance 公開 API（備用價格來源）
BINANCE_PRICE_URL = "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"


# ── 核心函數 ─────────────────────────────────────────────────────────────────

def is_btc_related(entry: dict) -> bool:
    """判斷文章是否與 BTC 相關。"""
    text = (
        entry.get("title", "") + " " +
        entry.get("summary", "") + " " +
        " ".join(tag.get("term", "") for tag in entry.get("tags", []))
    ).lower()
    return any(kw in text for kw in BTC_KEYWORDS)


def parse_published_time(entry) -> datetime:
    """解析文章發布時間，回傳 UTC datetime。"""
    try:
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    except Exception:
        pass
    return datetime.now(timezone.utc)


def time_ago(dt: datetime) -> str:
    """把 datetime 轉為「X 小時前」格式。"""
    diff = datetime.now(timezone.utc) - dt
    seconds = int(diff.total_seconds())
    if seconds < 0:
        return "剛剛"
    elif seconds < 60:
        return f"{seconds} 秒前"
    elif seconds < 3600:
        return f"{seconds // 60} 分鐘前"
    elif seconds < 86400:
        return f"{seconds // 3600} 小時前"
    else:
        return f"{seconds // 86400} 天前"


def _clean_summary(raw: str) -> str:
    """移除 HTML tag，截取前 150 字元作為摘要。"""
    text = re.sub(r"<[^>]+>", "", raw).strip()
    text = re.sub(r"\s+", " ", text)
    return (text[:150] + "…") if len(text) > 150 else text


def fetch_feed(source: dict) -> list[dict]:
    """抓取單一 RSS 來源，回傳 BTC 相關文章清單。"""
    articles = []
    try:
        # feedparser 支援傳入 request_headers
        feed = feedparser.parse(
            source["url"],
            request_headers=HEADERS,
            agent=HEADERS["User-Agent"],
        )
        # bozo=True 表示 feed 格式有問題，但仍可嘗試解析
        status = getattr(feed, "status", 200)
        if status == 403:
            print(f"   ⚠️  {source['name']}: 拒絕存取 (403)，略過", file=sys.stderr)
            return []

        for entry in feed.entries:
            if not is_btc_related(entry):
                continue
            articles.append({
                "title":     entry.get("title", "（無標題）").strip(),
                "summary":   _clean_summary(entry.get("summary", "")),
                "link":      entry.get("link", ""),
                "source":    source["name"],
                "emoji":     source["emoji"],
                "published": parse_published_time(entry),
            })
        if articles:
            print(f"   ✅ {source['name']}: 找到 {len(articles)} 篇 BTC 文章")
        else:
            print(f"   ℹ️  {source['name']}: 無 BTC 相關文章")
    except Exception as e:
        print(f"   ❌ {source['name']}: {e}", file=sys.stderr)
    return articles


def fetch_news(limit: int = 10) -> list[dict]:
    """
    從所有 RSS 來源抓取，過濾 BTC 相關文章，
    依時間排序後回傳最新 `limit` 篇。
    """
    all_articles = []
    for source in NEWS_SOURCES:
        all_articles.extend(fetch_feed(source))

    # 去重（相同標題只留一篇）
    seen_titles = set()
    unique = []
    for art in all_articles:
        key = art["title"].lower()[:60]
        if key not in seen_titles:
            seen_titles.add(key)
            unique.append(art)

    unique.sort(key=lambda x: x["published"], reverse=True)
    return unique[:limit]


def fetch_btc_price() -> dict | None:
    """抓取 BTC 現價，優先 CoinGecko，備用 Binance。"""
    # 方案 1：CoinGecko
    try:
        r = requests.get(COINGECKO_PRICE_URL, headers=HEADERS, timeout=10)
        if r.ok:
            data = r.json().get("bitcoin", {})
            return {
                "price":      data.get("usd", 0),
                "change_24h": data.get("usd_24h_change", 0),
                "source":     "CoinGecko",
            }
    except Exception:
        pass

    # 方案 2：Binance 公開 API（不需 API key）
    try:
        r = requests.get(BINANCE_PRICE_URL, timeout=10)
        if r.ok:
            data = r.json()
            price  = float(data.get("lastPrice", 0))
            change = float(data.get("priceChangePercent", 0))
            return {"price": price, "change_24h": change, "source": "Binance"}
    except Exception:
        pass

    return None


# ── 輸出格式化 ────────────────────────────────────────────────────────────────

def format_full(articles: list[dict], price: dict | None) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        f"📰 BTC 每日新聞摘要 — {now}",
        "",
        f"🔥 重點新聞（最新 Top {len(articles)}）",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]

    for i, art in enumerate(articles, 1):
        ago = time_ago(art["published"])
        lines += [
            f"\n{i}. {art['title']}",
            f"   {art['emoji']} 來源: {art['source']} | ⏰ {ago}",
        ]
        if art["summary"]:
            lines.append(f"   📝 {art['summary']}")
        if art["link"]:
            lines.append(f"   🔗 {art['link']}")

    if price:
        arrow = "📈" if price["change_24h"] >= 0 else "📉"
        sign  = "+" if price["change_24h"] >= 0 else ""
        lines += [
            "",
            "📊 BTC 價格快照",
            "━━━━━━━━━━━━━━━━━━━━━━",
            f"   💰 現價:    ${price['price']:,.0f} USD",
            f"   {arrow} 24h:    {sign}{price['change_24h']:.2f}%",
            f"   📡 資料源:  {price['source']}",
        ]

    lines.append("\n─────────────────────────")
    return "\n".join(lines)


def format_brief(articles: list[dict]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [f"₿ BTC 新聞速覽 — {now}", ""]
    for i, art in enumerate(articles, 1):
        lines.append(f"{i}. [{art['source']}] {art['title']}")
    return "\n".join(lines)


# ── Telegram 發送 ─────────────────────────────────────────────────────────────

def send_telegram(message: str) -> bool:
    token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    if not token or not chat_id:
        print(f"❌ 請在 {ENV_PATH} 設定 TELEGRAM_BOT_TOKEN 與 TELEGRAM_CHAT_ID", file=sys.stderr)
        return False

    url    = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [message[i:i + 4000] for i in range(0, len(message), 4000)]

    for chunk in chunks:
        r = requests.post(url, json={
            "chat_id":    chat_id,
            "text":       chunk,
            "parse_mode": "HTML",
        }, timeout=15)
        if not r.ok:
            print(f"❌ Telegram 錯誤: {r.text}", file=sys.stderr)
            return False
        time.sleep(0.3)

    print("✅ Telegram 通知已發送")
    return True


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="BTC 每日新聞抓取工具")
    parser.add_argument("--telegram", action="store_true", help="發送 Telegram 通知")
    parser.add_argument("--brief",    action="store_true", help="只顯示標題（簡潔模式）")
    parser.add_argument("--limit",    type=int, default=5,  help="顯示新聞數量（預設 5）")
    args = parser.parse_args()

    print(f"🔍 正在從 {len(NEWS_SOURCES)} 個來源抓取 BTC 新聞…\n")
    articles = fetch_news(limit=args.limit)

    if not articles:
        print("\n⚠️  未找到 BTC 相關新聞，可能是網路問題或所有 RSS 源都被封鎖")
        print("   💡 提示：請確認 VPS 可正常存取外部網路")
        sys.exit(1)

    price = None
    if not args.brief:
        print("\n💹 正在取得 BTC 現價…")
        price = fetch_btc_price()
        if not price:
            print("   ⚠️  無法取得價格，略過")

    output = format_brief(articles) if args.brief else format_full(articles, price)
    print("\n" + output)

    if args.telegram:
        print("\n📤 正在發送 Telegram 通知…")
        send_telegram(output)


if __name__ == "__main__":
    main()
