"""
AnalysisArticle / ArticleCompanyLink / ArticleTagLink 嘅 CRUD + 常用查詢。

呢張表同 News 用 1:1 shared-PK 設計（`analysis_article.news_id` 本身就係 FK
去 `news.news_id`，一篇分析文章冇獨立嘅 id 序列，佢個身份就係嗰行 News 自己）。
所以呢層方法入面雖然叫個參數做 `article_id`，實際傳嘅值同你操作 News 嗰個
`news_id` 係同一個 number——呢個係刻意嘅設計，唔係打錯字。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.manager.generic import count_obj, create_obj, delete_obj, get_obj, list_obj, update_obj
from app.models import AnalysisArticle, ArticleCompanyLink, ArticleTagLink, News, Tag


class ArticleManagerMixin:
    # ------------------------------------------------------------ AnalysisArticle
    def create_article(self, **kwargs: Any) -> AnalysisArticle:
        """
        直接開一行 analysis_article。kwargs 入面嘅 news_id 一定要係已經存在
        嘅 news 行(FK 約束)——如果想連底下嗰行 News 都一齊開，用 `add_article()`。
        """
        with self.session_scope() as s:
            return create_obj(s, AnalysisArticle, **kwargs)

    def get_article(self, article_id: int) -> Optional[AnalysisArticle]:
        with self.session_scope() as s:
            return get_obj(s, AnalysisArticle, article_id)

    def update_article(self, article_id: int, **kwargs: Any) -> Optional[AnalysisArticle]:
        with self.session_scope() as s:
            return update_obj(s, AnalysisArticle, article_id, **kwargs)

    def delete_article(self, article_id: int) -> bool:
        """
        淨係刪走呢篇分析文章本身(連埋佢自己嘅 company/tag link，見 model 嗰個
        cascade)，唔會累事刪走底下嗰行 News。反過嚟先會：刪 News 先至會連埋
        分析文章一齊刪(見 `News.analysis_article` 嗰個 cascade)。
        """
        with self.session_scope() as s:
            return delete_obj(s, AnalysisArticle, article_id)

    def list_articles(
        self, *, limit: int = 100, offset: int = 0, **filters: Any
    ) -> list[AnalysisArticle]:
        with self.session_scope() as s:
            return list_obj(s, AnalysisArticle, limit=limit, offset=offset, **filters)

    def count_articles(self, **filters: Any) -> int:
        with self.session_scope() as s:
            return count_obj(s, AnalysisArticle, **filters)

    def get_article_full(self, article_id: int) -> Optional[AnalysisArticle]:
        """
        一次過將底下嗰行 News、關聯緊嘅公司(company_links.company)同
        tag(tag_links.tag)全部 eager load 埋——俾
        `app/etl/extract_concepts.process_article_for_concepts()` 用嚟
        組裝 LLM prompt，唔使擔心 session 關咗之後讀 relationship 會報錯。
        """
        with self.session_scope() as s:
            stmt = (
                select(AnalysisArticle)
                .options(
                    selectinload(AnalysisArticle.news),
                    selectinload(AnalysisArticle.company_links).selectinload(
                        ArticleCompanyLink.company
                    ),
                    selectinload(AnalysisArticle.tag_links).selectinload(ArticleTagLink.tag),
                )
                .where(AnalysisArticle.news_id == article_id)
            )
            return s.scalars(stmt).first()

    def add_article(
        self,
        title: str,
        published_at: datetime,
        thesis: Optional[str] = None,
        conclusion: Optional[str] = None,
        description: Optional[str] = None,
        sentiment: Optional[str] = None,
        tickers: Optional[str] = None,
        tags: Optional[str] = None,
        source: Optional[str] = None,
        url: Optional[str] = None,
        news_type: str = "analysis",
        company_ids: Optional[Sequence[int]] = None,
        primary_company_id: Optional[int] = None,
        tag_names: Optional[Sequence[str]] = None,
        tag_type: str = "theme",
    ) -> AnalysisArticle:
        """
        一步過新增分析文章：連底下嗰行 News 都一齊開(shared-PK)，再連埋
        公司/tag(如果有提供)，全部包喺同一個 transaction——同 `add_news()`
        嗰套一致，要嘛全部成功，一有錯就晒晒 rollback。

        `primary_company_id`：`company_ids` 入面邊一間先係呢篇文章真正
        focus 緊嗰間(對應 `ArticleCompanyLink.is_primary`)，冇填就全部
        `is_primary=False`。
        """
        with self.session_scope() as s:
            news = News(
                title=title,
                source=source,
                url=url,
                published_at=published_at,
                news_type=news_type,
                sentiment=sentiment,
            )
            s.add(news)
            s.flush()  # 攞返 news.news_id，俾下面 AnalysisArticle 做返自己嘅 PK

            article = AnalysisArticle(
                news_id=news.news_id,
                title=title,
                description=description,
                sentiment=sentiment,
                thesis=thesis,
                conclusion=conclusion,
                tickers=tickers,
                tags=tags,
            )
            s.add(article)
            s.flush()

            for company_id in company_ids or []:
                s.add(
                    ArticleCompanyLink(
                        article_id=article.news_id,
                        company_id=company_id,
                        is_primary=(company_id == primary_company_id),
                    )
                )

            for tag_name in tag_names or []:
                tag = s.scalars(select(Tag).where(Tag.tag_name == tag_name)).first()
                if tag is None:
                    tag = Tag(tag_name=tag_name, tag_type=tag_type)
                    s.add(tag)
                    s.flush()
                s.add(ArticleTagLink(article_id=article.news_id, tag_id=tag.tag_id))

            s.flush()
            return article

    def search_articles(
        self,
        keyword: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AnalysisArticle]:
        """喺 title/description/thesis/conclusion 度做關鍵字模糊搜尋，仲可以夾埋日期區間。"""
        with self.session_scope() as s:
            stmt = select(AnalysisArticle).join(News, News.news_id == AnalysisArticle.news_id)
            if keyword:
                like_pattern = f"%{keyword}%"
                stmt = stmt.where(
                    or_(
                        AnalysisArticle.title.ilike(like_pattern),
                        AnalysisArticle.description.ilike(like_pattern),
                        AnalysisArticle.thesis.ilike(like_pattern),
                        AnalysisArticle.conclusion.ilike(like_pattern),
                    )
                )
            if start_date:
                stmt = stmt.where(News.published_at >= start_date)
            if end_date:
                stmt = stmt.where(News.published_at <= end_date)
            stmt = stmt.order_by(News.published_at.desc()).limit(limit)
            return list(s.scalars(stmt).all())

    def get_articles_for_company(
        self,
        company_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AnalysisArticle]:
        with self.session_scope() as s:
            stmt = (
                select(AnalysisArticle)
                .join(ArticleCompanyLink, ArticleCompanyLink.article_id == AnalysisArticle.news_id)
                .join(News, News.news_id == AnalysisArticle.news_id)
                .where(ArticleCompanyLink.company_id == company_id)
            )
            if start_date:
                stmt = stmt.where(News.published_at >= start_date)
            if end_date:
                stmt = stmt.where(News.published_at <= end_date)
            stmt = stmt.order_by(News.published_at.desc()).limit(limit)
            return list(s.scalars(stmt).all())

    def get_articles_by_tag(self, tag_name: str, limit: int = 100) -> list[AnalysisArticle]:
        with self.session_scope() as s:
            stmt = (
                select(AnalysisArticle)
                .join(ArticleTagLink, ArticleTagLink.article_id == AnalysisArticle.news_id)
                .join(Tag, Tag.tag_id == ArticleTagLink.tag_id)
                .join(News, News.news_id == AnalysisArticle.news_id)
                .where(Tag.tag_name == tag_name)
                .order_by(News.published_at.desc())
                .limit(limit)
            )
            return list(s.scalars(stmt).all())

    # ------------------------------- ArticleCompanyLink (複合主鍵: article_id + company_id)
    def link_article_company(
        self, article_id: int, company_id: int, is_primary: Optional[bool] = None
    ) -> ArticleCompanyLink:
        """已經連咗就更新返 is_primary；未連過就新增(upsert 行為，唔會撞 primary key)。"""
        with self.session_scope() as s:
            link = s.get(ArticleCompanyLink, (article_id, company_id))
            if link is not None:
                if is_primary is not None:
                    link.is_primary = is_primary
                s.flush()
                return link
            link = ArticleCompanyLink(
                article_id=article_id, company_id=company_id, is_primary=bool(is_primary)
            )
            s.add(link)
            s.flush()
            return link

    def unlink_article_company(self, article_id: int, company_id: int) -> bool:
        with self.session_scope() as s:
            link = s.get(ArticleCompanyLink, (article_id, company_id))
            if link is None:
                return False
            s.delete(link)
            return True

    # ------------------------------------- ArticleTagLink (複合主鍵: article_id + tag_id)
    def link_article_tag(self, article_id: int, tag_id: int) -> ArticleTagLink:
        with self.session_scope() as s:
            link = s.get(ArticleTagLink, (article_id, tag_id))
            if link is not None:
                return link
            link = ArticleTagLink(article_id=article_id, tag_id=tag_id)
            s.add(link)
            s.flush()
            return link

    def unlink_article_tag(self, article_id: int, tag_id: int) -> bool:
        with self.session_scope() as s:
            link = s.get(ArticleTagLink, (article_id, tag_id))
            if link is None:
                return False
            s.delete(link)
            return True
