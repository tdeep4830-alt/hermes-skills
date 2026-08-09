"""
測試新加嘅 `AnalysisArticle` / `ArticleCompanyLink` / `ArticleTagLink` 三張表。

呢層依家未有專屬嘅 manager mixin(呢個project嘅ArticleManagerMixin,同News/Company
嗰套一樣嘅CRUD入口,係下一步先加),所以呢度直接用 `DatabaseManager.session_scope()`
操作 SQLAlchemy session 嚟驗證：

1. Schema 本身岩唔岩(1:1 shared-PK: `analysis_article.news_id` 就係
   FK 去 `news.news_id`)。
2. Cascade delete config 啱唔啱——呢個位歷史上出過 bug(News/Company/Tag
   嗰陣冇設cascade,delete嗰陣會撞FK violation),所以新表一樣要用真實
   delete 操作驗證,唔淨係睇個 model 定義。

執行: pytest tests/test_article_models.py -v
（前提同 test_connection.py 一樣：DATABASE_URL 指嘅 PostgreSQL 已經開緊機，
  並且已經 `alembic upgrade head` 到 fe93b4bda46e 呢個 revision 或之後。）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.manager.db_manager import DatabaseManager
from app.models import AnalysisArticle, ArticleCompanyLink, ArticleTagLink, Tag


@pytest.fixture
def db():
    manager = DatabaseManager()
    try:
        yield manager
    finally:
        manager.dispose()


@pytest.fixture
def seeded_company(db):
    ticker = f"TST{uuid.uuid4().hex[:8].upper()}"
    company = db.create_company(ticker=ticker, name_en=f"Test Co {ticker}")
    yield company
    # 唔喺呢度 delete_company —— 有幾條 test 想自己驗證「刪 company 會點」，
    # 交返俾各自嘅 test body 決定幾時刪，呢度淨係做保底清理(如果 test 冇自己刪走)。
    with db.session_scope() as s:
        existing = s.get(type(company), company.company_id)
        if existing is not None:
            s.delete(existing)


@pytest.fixture
def seeded_tag(db):
    tag_name = f"test-tag-{uuid.uuid4().hex[:8]}"
    with db.session_scope() as s:
        tag = Tag(tag_name=tag_name, tag_type="theme")
        s.add(tag)
        s.flush()
        tag_id = tag.tag_id
    yield tag_id
    with db.session_scope() as s:
        existing = s.get(Tag, tag_id)
        if existing is not None:
            s.delete(existing)


def _create_news(db, title: str):
    return db.add_news(title=title, published_at=datetime.now(timezone.utc), news_type="company")


def test_analysis_article_shares_pk_with_news(db):
    """`analysis_article.news_id` 就係 `news.news_id`(1:1 shared-PK)。"""
    news = _create_news(db, "分析文章測試新聞")
    try:
        with db.session_scope() as s:
            article = AnalysisArticle(
                news_id=news.news_id,
                title="測試分析文章",
                thesis="如果AI持續發展，相關電力公司將受惠",
                conclusion="睇好",
                sentiment="positive",
            )
            s.add(article)
            s.flush()
            assert article.news_id == news.news_id

        with db.session_scope() as s:
            fetched = s.get(AnalysisArticle, news.news_id)
            assert fetched is not None
            assert fetched.thesis == "如果AI持續發展，相關電力公司將受惠"
    finally:
        db.delete_news(news.news_id)


def test_article_company_link_and_tag_link(db, seeded_company, seeded_tag):
    news = _create_news(db, "分析文章連結公司/tag測試")
    try:
        with db.session_scope() as s:
            article = AnalysisArticle(news_id=news.news_id, title="測試")
            s.add(article)
            s.flush()
            s.add(
                ArticleCompanyLink(
                    article_id=article.news_id,
                    company_id=seeded_company.company_id,
                    is_primary=True,
                )
            )
            s.add(ArticleTagLink(article_id=article.news_id, tag_id=seeded_tag))
            s.flush()

        with db.session_scope() as s:
            company_links = list(
                s.scalars(
                    select(ArticleCompanyLink).where(ArticleCompanyLink.article_id == news.news_id)
                ).all()
            )
            tag_links = list(
                s.scalars(select(ArticleTagLink).where(ArticleTagLink.article_id == news.news_id)).all()
            )
            assert len(company_links) == 1
            assert company_links[0].is_primary is True
            assert len(tag_links) == 1
    finally:
        db.delete_news(news.news_id)


def test_deleting_company_only_removes_link_not_article(db, seeded_company):
    """刪一間公司,淨係應該刪走 `analysis_article_company_link` 嗰行,唔會累事連篇文都無咗。"""
    news = _create_news(db, "刪公司唔應該累事刪文章")
    with db.session_scope() as s:
        article = AnalysisArticle(news_id=news.news_id, title="測試")
        s.add(article)
        s.flush()
        s.add(ArticleCompanyLink(article_id=article.news_id, company_id=seeded_company.company_id))

    db.delete_company(seeded_company.company_id)

    with db.session_scope() as s:
        # 文章本身仲喺度
        assert s.get(AnalysisArticle, news.news_id) is not None
        # 但連結公司嗰行已經因為 cascade 冇埋
        remaining_links = list(
            s.scalars(
                select(ArticleCompanyLink).where(ArticleCompanyLink.article_id == news.news_id)
            ).all()
        )
        assert remaining_links == []

    db.delete_news(news.news_id)


def test_deleting_news_cascades_to_article_and_its_links(db, seeded_company, seeded_tag):
    """
    刪一則 News,如果佢有掛住一篇分析文章,要連埋篇文、同篇文自己嘅
    company_links/tag_links 一齊刪晒——否則會撞 FK violation
    (analysis_article.news_id 撐住唔畀刪 news)。
    """
    news = _create_news(db, "刪News要連埋分析文章一齊清")
    with db.session_scope() as s:
        article = AnalysisArticle(news_id=news.news_id, title="測試")
        s.add(article)
        s.flush()
        s.add(ArticleCompanyLink(article_id=article.news_id, company_id=seeded_company.company_id))
        s.add(ArticleTagLink(article_id=article.news_id, tag_id=seeded_tag))

    # 呢一步如果 cascade 冇設好,會直接拋 IntegrityError(FK violation)
    deleted = db.delete_news(news.news_id)
    assert deleted is True

    with db.session_scope() as s:
        assert s.get(AnalysisArticle, news.news_id) is None
        remaining_company_links = list(
            s.scalars(
                select(ArticleCompanyLink).where(ArticleCompanyLink.article_id == news.news_id)
            ).all()
        )
        remaining_tag_links = list(
            s.scalars(select(ArticleTagLink).where(ArticleTagLink.article_id == news.news_id)).all()
        )
        assert remaining_company_links == []
        assert remaining_tag_links == []
