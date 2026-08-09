"""
負責由外部來源攞返「原始」AI/Tech 相關新聞，唔做 clean/dedup/公司配對
(嗰啲交俾 clean_news.py 做)。

刻意收窄用三類全部免費、支援 RSS 或 API(唔使爬蟲)嘅來源，覆蓋面對準
AI/Tech(唔想成個 pipeline 咩新聞都收，先污染晒個 Mind Map)：

1. RSS —— 幾個大型科技媒體嘅 AI 分類 feed(TechCrunch/The Verge/
   Ars Technica/VentureBeat/MIT Technology Review)。全部公開、免費、
   唔使 API key，本身已經係編輯精選過嘅 AI 分類內容。
2. Finnhub —— 免費 API key(https://finnhub.io/register)，用嚟攞：
   - 大盤 technology 分類新聞(/news?category=technology)
   - 你 DB 已經有嘅公司(Company.ticker)嘅公司專屬新聞(/company-news)——
     呢類新聞由 Finnhub 已經知道係邊間公司，唔使靠關鍵字估，準過RSS。
   免費層：大約 60 calls/min，但條款寫明淨係俾「個人、非商業」用途，
   自己用嚟做研究冇問題，日後想商業化呢個 project 就要留意升級。
3. Hacker News —— 用 hnrss.org(免費、冇 API key)攞 front page，
   反映緊科技社群熱度，幫手發現主流媒體未必即刻報導、但科技圈已經開始
   討論緊嘅嘢(例如新 model/新 chip 發布)。留意 HN front page 唔淨係
   AI/Tech，一定要經 clean_news.py 嘅關鍵字 filter 先可以入 DB。

每個 fetch 函數都盡量做到「單一個來源攞唔到唔會累事成個 pipeline 死」——
攞失敗就 log 個 warning/exception，回傳空 list，等其他來源照跑。

每個 fetch 函數回傳嘅每個 item 用統一格式：
    {
        "title": str,
        "url": str,
        "summary": str,
        "source": str,
        "published_at": datetime,
        "raw_source_type": "rss" | "hn" | "finnhub",
        "known_tickers": list[str],   # 淨係 Finnhub company news 先會填，其他來源係 []
    }
"""
from __future__ import annotations
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- RSS 來源
# 全部係公開、免費、唔使 API key 嘅 AI 分類 feed。
AI_TECH_RSS_FEEDS: dict[str, str] = {
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "Ars Technica AI": "https://arstechnica.com/ai/feed/",
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
    "MIT Technology Review AI": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
}

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
HN_RSS_URL = "https://hnrss.org/frontpage"


def _strip_html(text: str) -> str:
    """RSS entry 嘅 summary 成日夾埋 HTML tag，攞個大概文字版就夠。"""
    return re.sub(r"<[^<]+?>", " ", text or "").strip()


def _parse_entry_datetime(entry: Any) -> datetime:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        return datetime.fromtimestamp(time.mktime(struct), tz=timezone.utc)
    return datetime.now(timezone.utc)


def fetch_rss_feeds(feeds: Optional[dict[str, str]] = None) -> list[dict[str, Any]]:
    """
    用 feedparser 逐個攞返晒 RSS feed 嘅 entry。單一個 feed 攞唔到
    (網絡問題/feed 轉咗 URL)唔會累事成個 pipeline 死，淨係 log 個
    warning 繼續攞第個。
    """
    import feedparser

    feeds = feeds if feeds is not None else AI_TECH_RSS_FEEDS
    items: list[dict[str, Any]] = []
    for source_name, feed_url in feeds.items():
        try:
            parsed = feedparser.parse(feed_url)
            if parsed.get("bozo") and not parsed.entries:
                logger.warning("RSS feed 攞唔到/parse失敗: %s (%s)", source_name, feed_url)
                continue
            for entry in parsed.entries:
                items.append(
                    {
                        "title": (entry.get("title") or "").strip(),
                        "url": (entry.get("link") or "").strip(),
                        "summary": _strip_html(entry.get("summary", "")),
                        "source": source_name,
                        "published_at": _parse_entry_datetime(entry),
                        "raw_source_type": "rss",
                        "known_tickers": [],
                    }
                )
        except Exception:
            logger.exception("Fetch RSS feed 失敗: %s (%s)", source_name, feed_url)
    return items


# ------------------------------------------------------------- Hacker News
def fetch_hacker_news(min_points: int = 50, limit: int = 50) -> list[dict[str, Any]]:
    """
    用 hnrss.org(免費、冇 API key)攞返 HN front page，用 min_points
    篩走啲討論度未算熱嘅 submission。留意 HN 淨係反映科技社群興趣，
    唔淨係 AI/Tech，所以呢度攞到嘅嘢一定要經 clean_news.py 嘅關鍵字
    filter 先可以入 DB。
    """
    import feedparser

    url = f"{HN_RSS_URL}?points={min_points}"
    items: list[dict[str, Any]] = []
    try:
        parsed = feedparser.parse(url)
        for entry in parsed.entries[:limit]:
            items.append(
                {
                    "title": (entry.get("title") or "").strip(),
                    "url": (entry.get("link") or "").strip(),
                    "summary": _strip_html(entry.get("summary", "")),
                    "source": "Hacker News",
                    "published_at": _parse_entry_datetime(entry),
                    "raw_source_type": "hn",
                    "known_tickers": [],
                }
            )
    except Exception:
        logger.exception("Fetch Hacker News 失敗")
    return items


# --------------------------------------------------------------- Finnhub
def _finnhub_item_to_dict(item: dict, *, source: str, tickers: list[str]) -> dict[str, Any]:
    unix_ts = item.get("datetime")
    published_at = (
        datetime.fromtimestamp(unix_ts, tz=timezone.utc) if unix_ts else datetime.now(timezone.utc)
    )
    return {
        "title": (item.get("headline") or "").strip(),
        "url": (item.get("url") or "").strip(),
        "summary": (item.get("summary") or "").strip(),
        "source": source,
        "published_at": published_at,
        "raw_source_type": "finnhub",
        "known_tickers": tickers,
    }


def fetch_finnhub_general_news(category: str = "technology") -> list[dict[str, Any]]:
    """大盤分類新聞(免費層有，但要有 FINNHUB_API_KEY)。"""
    import requests

    if not settings.FINNHUB_API_KEY:
        logger.info("FINNHUB_API_KEY 未設定，跳過 Finnhub general news")
        return []

    try:
        resp = requests.get(
            f"{FINNHUB_BASE_URL}/news",
            params={"category": category, "token": settings.FINNHUB_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        raw_items = resp.json()
    except Exception:
        logger.exception("Fetch Finnhub general news 失敗")
        return []

    return [_finnhub_item_to_dict(item, source="Finnhub", tickers=[]) for item in raw_items]


def fetch_finnhub_company_news(tickers: list[str], *, days_back: int = 1) -> list[dict[str, Any]]:
    """
    逐個 ticker 攞返公司專屬新聞(免費層有)。因為已知邊個 ticker，
    呢啲新聞可以直接同對應公司連結，唔使靠關鍵字/公司名做 matching。
    """
    import requests

    if not settings.FINNHUB_API_KEY:
        logger.info("FINNHUB_API_KEY 未設定，跳過 Finnhub company news")
        return []

    today = datetime.now(timezone.utc).date()
    from_date = today - timedelta(days=days_back)

    items: list[dict[str, Any]] = []
    for ticker in tickers:
        try:
            resp = requests.get(
                f"{FINNHUB_BASE_URL}/company-news",
                params={
                    "symbol": ticker,
                    "from": from_date.isoformat(),
                    "to": today.isoformat(),
                    "token": settings.FINNHUB_API_KEY,
                },
                timeout=10,
            )
            resp.raise_for_status()
            raw_items = resp.json()
        except Exception:
            logger.exception("Fetch Finnhub company news 失敗: %s", ticker)
            continue
        items.extend(_finnhub_item_to_dict(item, source="Finnhub", tickers=[ticker]) for item in raw_items)
    return items


def fetch_all(
    *,
    tracked_tickers: Optional[list[str]] = None,
    rss_feeds: Optional[dict[str, str]] = None,
    hn_min_points: int = 50,
    finnhub_days_back: int = 1,
) -> list[dict[str, Any]]:
    """一步過攞晒三類來源，回傳未去重/未 filter 嘅原始清單。"""
    items: list[dict[str, Any]] = []
    items.extend(fetch_rss_feeds(rss_feeds))
    items.extend(fetch_hacker_news(min_points=hn_min_points))
    items.extend(fetch_finnhub_general_news())
    if tracked_tickers:
        items.extend(fetch_finnhub_company_news(tracked_tickers, days_back=finnhub_days_back))
    return items

if __name__ == "__main__":
    # 測試用，攞返最近 1 日嘅新聞，印出統計。
    tracked_tickers = ["AAPL", "MSFT", "GOOGL"]
    items = fetch_all(tracked_tickers=tracked_tickers, hn_min_points=50, finnhub_days_back=1)
    for item in items:
        print(item)
        
    print(f"Fetch 完成: {len(items)} items")


