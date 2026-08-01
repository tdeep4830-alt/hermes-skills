"""
負責將清理好嘅新聞寫入 PostgreSQL：
  1. insert 落 news table
  2. 寫返 news_company_link / news_tag_link
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import News, NewsCompanyLink


def load_article(
    session: Session,
    article: dict,
    news_type: str,
    matched_companies: list[tuple[int, float]] | None = None,
) -> News:
    news = News(
        title=article["title"],
        content=article.get("content"),
        source=article.get("source"),
        url=article.get("url"),
        published_at=article["published_at"],
        news_type=news_type,
    )
    session.add(news)
    session.flush()  # 攞返 news.news_id，等下面 link 用得到

    for company_id, relevance in matched_companies or []:
        session.add(
            NewsCompanyLink(
                news_id=news.news_id,
                company_id=company_id,
                relevance=relevance,
            )
        )

    return news
