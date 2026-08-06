"""
公司各項 detail table（Product / Technology / Service / ... / LegalAndRegulatoryIssues）
以及 News.description / AnalysisArticle.thesis 嘅語意搜尋 embedding，
用 polymorphic 設計（entity_type + entity_id）統一存喺一個 table。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import CHAR, BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.company import Company

EMBEDDING_DIM = 1536

# entity_type 合法值：9 個 company detail table + news + article。
# news/article 可以連 0 或多間公司（透過 news_company_link / analysis_article_company_link），
# 唔似 detail table 咁一行必然屬於一間公司，所以呢兩種 entity_type 嘅 row，company_id 淨係存 NULL——
# 想搵返邊間公司相關，查返嗰兩個 link table 就得，唔喺呢度重複/緩存呢個關係，
# 否則 link 改咗（加/刪公司）就要諗埋點同步呢度嘅 company_id，徒添複雜度。
entity_type_values = (
    "product",
    "technology",
    "service",
    "governmental_program",
    "manufacturing_process",
    "supply_chain",
    "competitor",
    "risk",
    "mdna",
    "legal_issue",
    "news",
    "article",
)


class CompanyFactEmbedding(Base):
    """一個 detail table 嘅一行 description（連 name/title）對應一個 embedding。"""

    __tablename__ = "company_fact_embeddings"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", name="uq_company_fact_embeddings_entity"),
        Index(
            "ix_company_fact_embeddings_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index("ix_company_fact_embeddings_company_id", "company_id"),
        Index("ix_company_fact_embeddings_content_hash", "content_hash"),
    )

    embedding_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    # 淨係 9 個 company detail table 嘅 row 先會填（一行必然屬於一間公司）；
    # news/article 嘅 row 呢度必然係 NULL，公司關係查返 news_company_link / analysis_article_company_link。
    company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("companies.company_id", ondelete="CASCADE"), nullable=True
    )
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_id: Mapped[int] = mapped_column(nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(100), nullable=False, default="text-embedding-3-small")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    company: Mapped[Optional["Company"]] = relationship()
