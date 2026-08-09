"""
將 clean_news.py 處理好嘅新聞清單寫入 DB。負責：

1. 對照 `News.url` 做「已經存在就跳過」嘅去重——每日排程都會攞返
   RSS/Finnhub/HN 最近一段時間嘅新聞，成日會同前一日 fetch 到嘅有重疊，
   靠 url 判斷唔會插重複行。
2. call `db.add_news()` 寫入，連埋 clean_news.py 配對到嘅 company_ids，
   一律打埋個 "AI" tag(呢個 pipeline 專門focus AI/Tech，方便你日後
   `db.get_news_by_tag("AI")` 反查晒全部)。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.models import News

logger = logging.getLogger(__name__)


def _news_url_exists(db, url: str) -> bool:
    if not url:
        return False
    with db.session_scope() as s:
        return s.scalars(select(News.news_id).where(News.url == url)).first() is not None


def load_news_items(
    db, items: list[dict[str, Any]], *, ai_tag_name: str = "AI", tag_type: str = "theme"
) -> dict[str, int]:
    """
    回傳統計 dict：{"inserted": n, "skipped_existing": n, "skipped_invalid": n}。
    `skipped_invalid`：冇 title 或者冇 url 嘅殘缺 item(理論上唔應該出現，
    但外部來源格式隨時變，做多層保護好過成個 pipeline 中途死)。
    """
    inserted = 0
    skipped_existing = 0
    skipped_invalid = 0

    for item in items:
        title = (item.get("title") or "").strip()
        url = (item.get("url") or "").strip()
        if not title or not url:
            logger.warning("跳過缺 title/url 嘅新聞: %r", item)
            skipped_invalid += 1
            continue

        if _news_url_exists(db, url):
            skipped_existing += 1
            continue

        company_ids = item.get("company_ids") or []
        news_type = "company" if company_ids else "industry"

        db.add_news(
            title=title,
            published_at=item["published_at"],
            content=item.get("summary"),
            source=item.get("source"),
            url=url,
            news_type=news_type,
            company_ids=company_ids or None,
            tag_names=[ai_tag_name],
            tag_type=tag_type,
        )
        inserted += 1

    return {"inserted": inserted, "skipped_existing": skipped_existing, "skipped_invalid": skipped_invalid}
