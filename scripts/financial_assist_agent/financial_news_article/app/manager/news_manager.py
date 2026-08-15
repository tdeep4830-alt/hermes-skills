"""
News / Tag / NewsCompanyLink / NewsTagLink 嘅 CRUD + 常用查詢。
"""
from __future__ import annotations

from datetime import datetime, timezone, time
from typing import Any, Optional, Sequence

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.manager.generic import count_obj, create_obj, delete_obj, get_obj, list_obj, update_obj
from app.models import Company, News, NewsCompanyLink, NewsTagLink, Tag, TagCategoryRule
from app.models.tag import tag_rule_target_fields


class NewsManagerMixin:
    # ----------------------------------------------------------------- News
    def create_news(self, **kwargs: Any) -> News:
        with self.session_scope() as s:
            return create_obj(s, News, **kwargs)

    def get_news(self, news_id: int) -> Optional[News]:
        with self.session_scope() as s:
            return get_obj(s, News, news_id)

    def update_news(self, news_id: int, **kwargs: Any) -> Optional[News]:
        with self.session_scope() as s:
            return update_obj(s, News, news_id, **kwargs)

    def delete_news(self, news_id: int) -> bool:
        """刪咗一則新聞時，連埋佢啲 company/tag link 都會一齊刪走 (見 delete-orphan cascade)。"""
        with self.session_scope() as s:
            return delete_obj(s, News, news_id)

    def list_news(
        self, *, limit: int = 100, offset: int = 0, order_by: Any = None, **filters: Any
    ) -> list[News]:
        with self.session_scope() as s:
            effective_order = order_by if order_by is not None else News.published_at.desc()
            return list_obj(s, News, limit=limit, offset=offset, order_by=effective_order, **filters)

    def count_news(self, **filters: Any) -> int:
        with self.session_scope() as s:
            return count_obj(s, News, **filters)

    def get_news_full(self, news_id: int) -> Optional[News]:
        """一次過將關聯緊嘅公司 (company_links.company) 同 tag (tag_links.tag) 一齊攞埋。"""
        with self.session_scope() as s:
            stmt = (
                select(News)
                .options(
                    selectinload(News.company_links).selectinload(NewsCompanyLink.company),
                    selectinload(News.tag_links).selectinload(NewsTagLink.tag),
                )
                .where(News.news_id == news_id)
            )
            return s.scalars(stmt).first()

    def add_news(
        self,
        title: str,
        published_at: datetime,
        description: Optional[str] = None,
        content: Optional[str] = None,
        source: Optional[str] = None,
        url: Optional[str] = None,
        news_type: str = "company",
        sentiment: Optional[str] = None,
        company_ids: Optional[Sequence[int]] = None,
        tag_names: Optional[Sequence[str]] = None,
        tag_type: str = "theme",
    ) -> News:
        """
        一步過新增新聞，並喺同一個 transaction 度連埋公司 / tag（如果有提供）。
        要嘛全部成功，一有錯就晒晒 rollback，唔會出現「新聞有咗但連結漏咗」嘅情況。
        """
        with self.session_scope() as s:
            news = News(
                title=title,
                description=description,
                content=content,
                source=source,
                url=url,
                published_at=published_at,
                news_type=news_type,
                sentiment=sentiment,
            )
            s.add(news)
            s.flush()

            for company_id in company_ids or []:
                s.add(NewsCompanyLink(news_id=news.news_id, company_id=company_id, relevance=1.0))

            for tag_name in tag_names or []:
                tag = s.scalars(select(Tag).where(Tag.tag_name == tag_name)).first()
                if tag is None:
                    tag = Tag(tag_name=tag_name, tag_type=tag_type)
                    s.add(tag)
                    s.flush()
                s.add(NewsTagLink(news_id=news.news_id, tag_id=tag.tag_id))

            s.flush()
            return news

    def search_news(
        self,
        keyword: Optional[str] = None,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        news_type: Optional[str] = None,
        limit: int = 100,
    ) -> list[News]:
        """喺 title / content 度做關鍵字模糊搜尋，仲可以夾埋日期區間 / news_type 篩選。
        start_date/end_date 可以係 date 或 datetime，會自動轉換。"""
        from datetime import date
        
        with self.session_scope() as s:
            stmt = select(News)
            if keyword:
                like_pattern = f"%{keyword}%"
                stmt = stmt.where(or_(News.title.ilike(like_pattern), News.content.ilike(like_pattern)))
            if start_date:
                # 如果係 date 對象，轉成 datetime（當日 00:00:00）
                if isinstance(start_date, date) and not isinstance(start_date, datetime):
                    start_date = datetime.combine(start_date, time.min).replace(tzinfo=timezone.utc)
                stmt = stmt.where(News.published_at >= start_date)
            if end_date:
                # 如果係 date 對象，轉成 datetime（當日 23:59:59.999999）
                if isinstance(end_date, date) and not isinstance(end_date, datetime):
                    end_date = datetime.combine(end_date, time.max).replace(tzinfo=timezone.utc)
                stmt = stmt.where(News.published_at <= end_date)
            if news_type:
                stmt = stmt.where(News.news_type == news_type)
            stmt = stmt.order_by(News.published_at.desc()).limit(limit)
            return list(s.scalars(stmt).all())

    def get_news_for_company(
        self,
        company_id: int,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[News]:
        with self.session_scope() as s:
            stmt = (
                select(News)
                .join(NewsCompanyLink, NewsCompanyLink.news_id == News.news_id)
                .where(NewsCompanyLink.company_id == company_id)
            )
            if start_date:
                stmt = stmt.where(News.published_at >= start_date)
            if end_date:
                stmt = stmt.where(News.published_at <= end_date)
            stmt = stmt.order_by(News.published_at.desc()).limit(limit)
            return list(s.scalars(stmt).all())

    def get_favorable_news_by_company(
        self,
        *,
        start_date: Optional[datetime] = None,
        end_date: Optional[datetime] = None,
        min_relevance: float = 0.0,
        limit_per_company: int = 5,
    ) -> dict[str, list[tuple[News, float]]]:
        """
        攞返 sentiment='positive' 嘅新聞，按公司 ticker 分組（最新排先）。
        要先跑咗 run_matching()（match_news_to_companies），先會包埋 tag_rule/embedding
        兩層搵到嘅公司，唔淨係得 ingest 嗰陣 LLM 直接提及嘅 ticker。
        回傳 {ticker: [(News, relevance), ...]}。
        """
        with self.session_scope() as s:
            stmt = (
                select(Company.ticker, News, NewsCompanyLink.relevance)
                .join(NewsCompanyLink, NewsCompanyLink.company_id == Company.company_id)
                .join(News, News.news_id == NewsCompanyLink.news_id)
                .where(News.sentiment == "positive", NewsCompanyLink.relevance >= min_relevance)
            )
            if start_date:
                stmt = stmt.where(News.published_at >= start_date)
            if end_date:
                stmt = stmt.where(News.published_at <= end_date)
            stmt = stmt.order_by(Company.ticker, News.published_at.desc())

            grouped: dict[str, list[tuple[News, float]]] = {}
            for ticker, news, relevance in s.execute(stmt).all():
                bucket = grouped.setdefault(ticker, [])
                if len(bucket) < limit_per_company:
                    bucket.append((news, relevance))
            return grouped

    def get_news_by_tag(self, tag_name: str, limit: int = 100) -> list[News]:
        with self.session_scope() as s:
            stmt = (
                select(News)
                .join(NewsTagLink, NewsTagLink.news_id == News.news_id)
                .join(Tag, Tag.tag_id == NewsTagLink.tag_id)
                .where(Tag.tag_name == tag_name)
                .order_by(News.published_at.desc())
                .limit(limit)
            )
            return list(s.scalars(stmt).all())

    # ------------------------------------------------------------------ Tag
    def create_tag(self, **kwargs: Any) -> Tag:
        with self.session_scope() as s:
            return create_obj(s, Tag, **kwargs)

    def get_tag(self, tag_id: int) -> Optional[Tag]:
        with self.session_scope() as s:
            return get_obj(s, Tag, tag_id)

    def get_or_create_tag(self, tag_name: str, tag_type: str = "theme") -> Tag:
        with self.session_scope() as s:
            tag = s.scalars(select(Tag).where(Tag.tag_name == tag_name)).first()
            if tag is None:
                tag = Tag(tag_name=tag_name, tag_type=tag_type)
                s.add(tag)
                s.flush()
            return tag

    def update_tag(self, tag_id: int, **kwargs: Any) -> Optional[Tag]:
        with self.session_scope() as s:
            return update_obj(s, Tag, tag_id, **kwargs)

    def delete_tag(self, tag_id: int) -> bool:
        with self.session_scope() as s:
            return delete_obj(s, Tag, tag_id)

    def list_tags(self, *, limit: int = 100, offset: int = 0, **filters: Any) -> list[Tag]:
        with self.session_scope() as s:
            return list_obj(s, Tag, limit=limit, offset=offset, **filters)

    # ------------------------------------------------------- TagCategoryRule
    def add_tag_category_rule(
        self, tag_name: str, target_field: str, target_value: str, tag_type: str = "theme"
    ) -> TagCategoryRule:
        """
        News-Company Matching Layer 2 用嘅 crosswalk：tag 冇就自動起一個。
        target_field: "product_category" | "industry" | "sector"
        """
        if target_field not in tag_rule_target_fields:
            raise ValueError(f"未知嘅 target_field: {target_field!r}，要 {tag_rule_target_fields}")
        with self.session_scope() as s:
            tag = s.scalars(select(Tag).where(Tag.tag_name == tag_name)).first()
            if tag is None:
                tag = Tag(tag_name=tag_name, tag_type=tag_type)
                s.add(tag)
                s.flush()

            rule = s.scalars(
                select(TagCategoryRule).where(
                    TagCategoryRule.tag_id == tag.tag_id,
                    TagCategoryRule.target_field == target_field,
                    TagCategoryRule.target_value == target_value,
                )
            ).first()
            if rule is not None:
                return rule

            rule = TagCategoryRule(tag_id=tag.tag_id, target_field=target_field, target_value=target_value)
            s.add(rule)
            s.flush()
            return rule

    def list_tag_category_rules(self, *, limit: int = 100, offset: int = 0) -> list[TagCategoryRule]:
        with self.session_scope() as s:
            return list_obj(s, TagCategoryRule, limit=limit, offset=offset)

    # ----------------------------------- NewsCompanyLink (複合主鍵: news_id + company_id)
    def link_news_company(
        self, news_id: int, company_id: int, relevance: Optional[float] = None
    ) -> NewsCompanyLink:
        """如果已經連咗，就更新返 relevance；未連過就新增 (upsert 行為，唔會撞 primary key)。"""
        with self.session_scope() as s:
            link = s.get(NewsCompanyLink, (news_id, company_id))
            if link is not None:
                if relevance is not None:
                    link.relevance = relevance
                s.flush()
                return link
            link = NewsCompanyLink(news_id=news_id, company_id=company_id, relevance=relevance)
            s.add(link)
            s.flush()
            return link

    def unlink_news_company(self, news_id: int, company_id: int) -> bool:
        with self.session_scope() as s:
            link = s.get(NewsCompanyLink, (news_id, company_id))
            if link is None:
                return False
            s.delete(link)
            return True

    # ----------------------------------------- NewsTagLink (複合主鍵: news_id + tag_id)
    def link_news_tag(self, news_id: int, tag_id: int) -> NewsTagLink:
        with self.session_scope() as s:
            link = s.get(NewsTagLink, (news_id, tag_id))
            if link is not None:
                return link
            link = NewsTagLink(news_id=news_id, tag_id=tag_id)
            s.add(link)
            s.flush()
            return link

    def unlink_news_tag(self, news_id: int, tag_id: int) -> bool:
        with self.session_scope() as s:
            link = s.get(NewsTagLink, (news_id, tag_id))
            if link is None:
                return False
            s.delete(link)
            return True
