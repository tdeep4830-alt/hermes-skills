"""
Mind Map 嘅骨幹:一個持續生長嘅概念關係網絡(Concept Graph)。

- Concept:圖入面嘅node。分兩種:
    * concept_type="theme"    抽象主題,例如「AI發展」「記憶體需求上升」
    * concept_type="company"  某間公司本身(company_id 指返 companies 表),
                               等公司可以自然咁做圖嘅 leaf node,唔使喺
                               ConceptRelation 度另開一種 polymorphic 邊。
- ConceptRelation:node之間嘅有向邊,帶住 relation_type/polarity/confidence,
  同一個原因(from,to,relation_type,polarity)嘅重複引用會做「強化」而唔係開新邊。
- ConceptRelationEvidence:每次強化一條邊嘅逐條證據(邊篇新聞/文章話事),
  俾日後 LLM 生成解釋鏈嗰陣有齊晒 citation。
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

# 要同你揀嘅 embedding model 對得上 (例如 OpenAI text-embedding-3-small = 1536)。
# 日後換 model 如果維度唔同,要開新 migration 改呢個維度。
EMBEDDING_DIM = 1536


class Concept(Base, TimestampMixin):
    """Mind Map 入面嘅一個 node。"""

    __tablename__ = "concepts"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_concepts_company_id"),
    )

    concept_id: Mapped[int] = mapped_column(primary_key=True)
    concept_type: Mapped[str] = mapped_column(String(20), nullable=False)
    # concept_type: "theme" (抽象主題) / "company" (公司node,見 company_id)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # 淨係 concept_type="company" 先會填,做返一個真正嘅 FK,
    # 令公司 node 嘅身份唔使靠 embedding 模糊配對,靠 company_id 精準 upsert。
    company_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("companies.company_id"), nullable=True
    )

    # 呢段字嘅 embedding,用嚟做 theme concept 之間嘅去重 / 相似搜尋。
    # company node 通常唔需要自己 embedding (佢用 company_profiles 嗰邊嘅),
    # 但都留低做得到,方便你想用「公司名 + 主要業務」一齊 embed 嚟輔助搜尋。
    embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)

    # 呢個主題喺唔同文章入面出現過嘅唔同講法,例如
    # ["AI發展", "人工智能增長", "AI市場擴張"]。純粹方便人手睇/keyword輔助搜尋,
    # 唔係去重嘅判斷依據 (去重淨係靠 embedding similarity)。
    aliases: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, nullable=False)

    company: Mapped[Optional["Company"]] = relationship()

    outgoing_relations: Mapped[list["ConceptRelation"]] = relationship(
        foreign_keys="ConceptRelation.from_concept_id",
        back_populates="from_concept",
        cascade="all, delete-orphan",
    )
    incoming_relations: Mapped[list["ConceptRelation"]] = relationship(
        foreign_keys="ConceptRelation.to_concept_id",
        back_populates="to_concept",
        cascade="all, delete-orphan",
    )


class ConceptRelation(Base, TimestampMixin):
    """Mind Map 入面嘅一條有向邊:from_concept --relation_type--> to_concept。"""

    __tablename__ = "concept_relations"
    __table_args__ = (
        UniqueConstraint(
            "from_concept_id", "to_concept_id", "relation_type", "polarity",
            name="uq_concept_relations_edge",
        ),
    )

    relation_id: Mapped[int] = mapped_column(primary_key=True)
    from_concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.concept_id"), nullable=False)
    to_concept_id: Mapped[int] = mapped_column(ForeignKey("concepts.concept_id"), nullable=False)

    relation_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # relation_type 例子: "drives_demand_for" / "benefits" / "threatens" / "supplies_to"
    polarity: Mapped[str] = mapped_column(String(10), nullable=False, default="positive")
    # polarity: "positive" / "negative" / "neutral"
    # 同一對 concept 之間,可以同時存在 positive 同 negative 嘅邊 (例如市場有睇好有睇淡),
    # UNIQUE constraint 特登唔包 confidence,等呢兩種對立論述可以並存,唔會互相覆蓋。

    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    # 每次強化,用 running average 更新 (見 ConceptManagerMixin.reinforce_relation)

    reinforcement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    last_reinforced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    is_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 你人手 confirm 過先算數;未 review 過嘅邊,喺下游應用 (例如 signals) 應該打折睇待。

    from_concept: Mapped["Concept"] = relationship(
        foreign_keys=[from_concept_id], back_populates="outgoing_relations"
    )
    to_concept: Mapped["Concept"] = relationship(
        foreign_keys=[to_concept_id], back_populates="incoming_relations"
    )
    evidence: Mapped[list["ConceptRelationEvidence"]] = relationship(
        back_populates="relation", cascade="all, delete-orphan"
    )


class ConceptRelationEvidence(Base):
    """
    每次強化一條邊嘅逐條證據記錄——邊篇新聞令你(或者LLM)相信呢條因果鏈。
    reinforcement_count/last_reinforced_at 係呢個表嘅聚合快取(方便快速讀取),
    真正嘅逐條記錄同 citation 嚟源全部喺呢度。
    """

    __tablename__ = "concept_relation_evidence"

    evidence_id: Mapped[int] = mapped_column(primary_key=True)
    relation_id: Mapped[int] = mapped_column(
        ForeignKey("concept_relations.relation_id"), nullable=False
    )
    news_id: Mapped[Optional[int]] = mapped_column(ForeignKey("news.news_id"), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # note: 可以存低 LLM 喺呢篇新聞入面點樣支持呢條edge嘅簡短解釋
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    relation: Mapped["ConceptRelation"] = relationship(back_populates="evidence")
    news: Mapped[Optional["News"]] = relationship()


# 補 import 俾 type hint 用 (避免 circular import)
from app.models.company import Company  # noqa: E402
from app.models.news_and_article import News  # noqa: E402
