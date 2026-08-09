from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.tag import Tag  # noqa: E402




class News(Base):
    """
    新聞主表 —— 每日 Input 嘅高頻資料。
    唔喺呢度直接放 company_id，因為一則新聞可能關聯多間公司，
    亦可能完全同公司無關（大環境 / 其他資產），交俾底下嘅 junction table 處理。
    """

    __tablename__ = "news"

    news_id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)

    # 新聞實際刊登時間 —— 查詢/排序主要靠呢個欄位，記得落 index
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # 新聞被 Input 入 DB 嘅時間，同 published_at 分開嚟
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    news_type: Mapped[str] = mapped_column(String(30), nullable=False, default="company")
    # news_type 例子: "company" / "industry" / "macro" / "other_asset"

    sentiment: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    # sentiment 例子: "positive" / "negative" / "neutral"（可由 NLP 後補）

    company_links: Mapped[list["NewsCompanyLink"]] = relationship(
        back_populates="news", cascade="all, delete-orphan"
    )
    tag_links: Mapped[list["NewsTagLink"]] = relationship(
        back_populates="news", cascade="all, delete-orphan"
    )
    analysis_article: Mapped[Optional["AnalysisArticle"]] = relationship(
        back_populates="news", uselist=False, cascade="all, delete-orphan"
    )


class NewsCompanyLink(Base):
    """新聞 <-> 公司 嘅多對多關聯表"""

    __tablename__ = "news_company_link"

    news_id: Mapped[int] = mapped_column(ForeignKey("news.news_id"), primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.company_id"), primary_key=True
    )
    relevance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # relevance: 0-1 分，代表呢則新聞對呢間公司嘅相關程度（可由人手或 NLP model 打分）
    is_primary: Mapped[bool] = mapped_column(default=False, nullable=False)
    # 一則新聞可以連多間公司，is_primary 用嚟標記邊間先係「主角」，
    # 方便快速攞返「呢則新聞主要係關於邊間公司」，唔使靠 relevance 排序估。
    match_source: Mapped[str] = mapped_column(String(20), nullable=False, default="direct_mention")
    # match_source 例子: "direct_mention"（LLM/keyword 直接提及 ticker）
    # / "tag_rule"（Layer 2 tag↔category 規則配對）/ "embedding"（Layer 3 語義比對）

    news: Mapped["News"] = relationship(back_populates="company_links")
    company: Mapped["Company"] = relationship(back_populates="news_links")


class NewsTagLink(Base):
    """新聞 <-> 標籤 嘅多對多關聯表（處理宏觀/其他資產類新聞）"""

    __tablename__ = "news_tag_link"

    news_id: Mapped[int] = mapped_column(ForeignKey("news.news_id"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.tag_id"), primary_key=True)

    news: Mapped["News"] = relationship(back_populates="tag_links")
    tag: Mapped["Tag"] = relationship()


# 補一個 import 俾 NewsCompanyLink 用嘅 Company type hint（避免 circular import 報錯）
from app.models.company import Company  # noqa: E402



class AnalysisArticle(Base):
    """
    用嚟提供分析文章畀 LLM 作總結，記錄文章內嘅 Theme 及推論。
    例如：title/description/sentiment/thesis/conclusion/tickers/tags。
    """

    __tablename__ = "analysis_article"

    news_id: Mapped[int] = mapped_column(
        ForeignKey("news.news_id"), primary_key=True
    )
    title: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sentiment: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    thesis: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    conclusion: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tickers: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    news: Mapped["News"] = relationship(back_populates="analysis_article")
    # cascade="all, delete-orphan"：刪一篇分析文章嗰陣，佢嘅公司/tag 關聯記錄
    # 會一齊刪走(但唔會影響到 Company / Tag 本身)——同 News 嗰套 cascade 設計一致。
    company_links: Mapped[list["ArticleCompanyLink"]] = relationship(
        back_populates="analysis_article", cascade="all, delete-orphan"
    )
    tag_links: Mapped[list["ArticleTagLink"]] = relationship(
        back_populates="analysis_article", cascade="all, delete-orphan"
    )


class ArticleCompanyLink(Base):
    """分析文章 <-> 公司 嘅多對多關聯表"""

    __tablename__ = "analysis_article_company_link"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_article.news_id"), primary_key=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("companies.company_id"), primary_key=True
    )
    is_primary: Mapped[bool] = mapped_column(default=False, nullable=False)
    # 標記邊間公司先係呢篇分析文章嘅主角(一篇文可以順帶提到幾間公司，
    # 但通常淨係一間先係分析員真正focus緊嗰間)。

    analysis_article: Mapped["AnalysisArticle"] = relationship(back_populates="company_links")
    company: Mapped["Company"] = relationship(back_populates="article_links")


class ArticleTagLink(Base):
    """分析文章 <-> 標籤 嘅多對多關聯表（處理宏觀/其他資產類文章）"""

    __tablename__ = "analysis_article_tag_link"

    article_id: Mapped[int] = mapped_column(
        ForeignKey("analysis_article.news_id"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.tag_id"), primary_key=True)

    analysis_article: Mapped["AnalysisArticle"] = relationship(back_populates="tag_links")
    tag: Mapped["Tag"] = relationship(back_populates="article_links")


class NewsArticleMatch(Base):
    """
    News <-> AnalysisArticle Matching 結果（三層：shared_company / shared_tag / embedding）。
    AnalysisArticle 本身其實係一行 News 加咗 thesis 等分析欄位，
    所以 article_id 都係指返 analysis_article.news_id（即係嗰篇文章底層嗰行 news）。
    """

    __tablename__ = "news_article_matches"
    __table_args__ = (CheckConstraint("news_id <> article_id", name="ck_news_article_matches_not_self"),)

    news_id: Mapped[int] = mapped_column(ForeignKey("news.news_id"), primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("analysis_article.news_id"), primary_key=True)
    relevance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    match_source: Mapped[str] = mapped_column(String(20), nullable=False)
    # match_source 例子: "shared_company" / "shared_tag" / "embedding"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    news: Mapped["News"] = relationship()
    article: Mapped["AnalysisArticle"] = relationship()