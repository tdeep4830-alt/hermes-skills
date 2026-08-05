"""
負責由外部來源（新聞 API / RSS / 爬蟲）攞返原始新聞資料。
呢個 module 淨係負責「攞資料」，唔應該喺度做清理或者寫入 DB。

TODO: 換成你實際用緊嘅新聞來源，例如：
    - NewsAPI (https://newsapi.org)
    - Finnhub / Alpha Vantage 嘅 news endpoint
    - 直接 RSS feed (用 feedparser)
"""
from __future__ import annotations

import requests

from app.config import settings


def fetch_raw_news(query: str | None = None) -> list[dict]:
    """
    回傳一個 list，每個元素係一則原始新聞（未清理），格式例如：
        {
            "title": "...",
            "content": "...",
            "source": "...",
            "url": "...",
            "published_at": "2026-07-22T09:00:00Z",
        }
    """
    if not settings.NEWS_API_KEY:
        raise RuntimeError("未設定 NEWS_API_KEY，請喺 .env 度填返新聞 API 嘅 key")

    # ---- 示範：用 NewsAPI 做例子，實際請按你揀嘅來源調整 ----
    resp = requests.get(
        "https://newsapi.org/v2/everything",
        params={
            "q": query or "stock market",
            "apiKey": settings.NEWS_API_KEY,
            "language": "en",
            "sortBy": "publishedAt",
        },
        timeout=15,
    )
    resp.raise_for_status()
    articles = resp.json().get("articles", [])

    return [
        {
            "title": a.get("title"),
            "content": a.get("content") or a.get("description"),
            "source": (a.get("source") or {}).get("name"),
            "url": a.get("url"),
            "published_at": a.get("publishedAt"),
        }
        for a in articles
    ]
