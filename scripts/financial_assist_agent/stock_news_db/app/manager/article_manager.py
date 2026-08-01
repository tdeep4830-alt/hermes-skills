"""
AnalysisArticle / ArticleCompanyLink / ArticleTagLink 嘅 CRUD。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import select

from app.models import AnalysisArticle, ArticleCompanyLink, ArticleTagLink, Company, News, Tag


class ArticleManagerMixin:
    def add_analysis_article(
        self,
        title: str,
        published_at: datetime,
        description: Optional[str] = None,
        content: Optional[str] = None,
        source: Optional[str] = None,
        url: Optional[str] = None,
        thesis: Optional[str] = None,
        conclusion: Optional[str] = None,
        sentiment: Optional[str] = None,
        company_ids: Optional[Sequence[int]] = None,
        tag_names: Optional[Sequence[str]] = None,
    ) -> AnalysisArticle:
        """
        一步過建立分析文章：底層存做一則 News（content/source/url/published_at），
        再存一個對應嘅 AnalysisArticle（title/description/thesis/conclusion 等分析結果），
        並連埋 company / tag（如果有提供），同 add_news 一樣包喺同一個 transaction。
        """
        with self.session_scope() as s:
            news = News(
                title=title,
                description=description,
                content=content,
                source=source,
                url=url,
                published_at=published_at,
                news_type="company" if company_ids else "macro",
                sentiment=sentiment,
            )
            s.add(news)
            s.flush()

            # AnalysisArticle.news_id 係 analysis_article table 嘅 PK,
            # 而 ArticleCompanyLink/ArticleTagLink 嘅 FK 指返 analysis_article.news_id
            # （唔係 news.news_id）,所以要先起好 AnalysisArticle 並 flush，
            # 啲 link row 先搵到啱嘅 parent row，唔會撞 FK constraint。
            tickers = []
            valid_company_ids = []
            for company_id in company_ids or []:
                company = s.get(Company, company_id)
                if company is not None:
                    tickers.append(company.ticker)
                    valid_company_ids.append(company_id)

            article = AnalysisArticle(
                news_id=news.news_id,
                title=title,
                description=description,
                sentiment=sentiment,
                thesis=thesis,
                conclusion=conclusion,
                tickers=",".join(tickers) or None,
                tags=",".join(tag_names) if tag_names else None,
            )
            s.add(article)
            s.flush()

            for company_id in valid_company_ids:
                s.add(ArticleCompanyLink(article_id=news.news_id, company_id=company_id))

            for tag_name in tag_names or []:
                tag = s.scalars(select(Tag).where(Tag.tag_name == tag_name)).first()
                if tag is None:
                    tag = Tag(tag_name=tag_name, tag_type="theme")
                    s.add(tag)
                    s.flush()
                s.add(ArticleTagLink(article_id=news.news_id, tag_id=tag.tag_id))

            return article
