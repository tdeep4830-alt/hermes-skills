"""
Mind Map(Concept Graph)嘅 CRUD + 去重(dedup)同強化(reinforcement)邏輯。

呢層完全唔識點樣生成 embedding / 點樣用 LLM 抽取 concept——
呢個 mixin 淨係負責「畀咗一個 embedding 向量之後,點樣安全咁寫入 DB」,
實際 call embedding API / LLM 抽取果啲,應該喺呼叫呢個 manager 之前,
喺 ETL 果層做好先，keep manager 層同任何一個 provider 都無關。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import aliased

from app.manager.generic import create_obj, delete_obj, get_obj, list_obj, update_obj
from app.models import Company, Concept, ConceptRelation, ConceptRelationEvidence


class ConceptManagerMixin:
    # ------------------------------------------------------------- Concept CRUD
    def get_concept(self, concept_id: int) -> Optional[Concept]:
        with self.session_scope() as s:
            return get_obj(s, Concept, concept_id)

    def update_concept(self, concept_id: int, **kwargs: Any) -> Optional[Concept]:
        with self.session_scope() as s:
            return update_obj(s, Concept, concept_id, **kwargs)

    def delete_concept(self, concept_id: int) -> bool:
        """
        刪一個 concept 會連埋佢做 from/to 嘅所有 edge 一齊刪走
        (見 Concept model 嘅 cascade="all, delete-orphan")。
        """
        with self.session_scope() as s:
            return delete_obj(s, Concept, concept_id)

    def list_concepts(self, *, limit: int = 100, offset: int = 0, **filters: Any) -> list[Concept]:
        with self.session_scope() as s:
            return list_obj(s, Concept, limit=limit, offset=offset, **filters)

    # ------------------------------------------------------- 相似搜尋 / 去重核心
    def _find_similar_concepts(
        self,
        session,
        embedding: Sequence[float],
        *,
        concept_type: str = "theme",
        threshold: float = 0.85,
        limit: int = 5,
    ) -> list[tuple[Concept, float]]:
        distance_expr = Concept.embedding.cosine_distance(embedding)
        stmt = (
            select(Concept, distance_expr.label("distance"))
            .where(Concept.concept_type == concept_type, Concept.embedding.is_not(None))
            .order_by(distance_expr)
            .limit(limit)
        )
        matches: list[tuple[Concept, float]] = []
        for concept, distance in session.execute(stmt).all():
            similarity = 1 - distance
            if similarity >= threshold:
                matches.append((concept, similarity))
        return matches

    def find_similar_concepts(
        self,
        embedding: Sequence[float],
        *,
        concept_type: str = "theme",
        threshold: float = 0.85,
        limit: int = 5,
    ) -> list[tuple[Concept, float]]:
        """對外查詢版本:攞返一段 embedding,搵晒現存邊幾個 theme concept 夠相似。"""
        with self.session_scope() as s:
            return self._find_similar_concepts(
                s, embedding, concept_type=concept_type, threshold=threshold, limit=limit
            )

    def get_or_create_theme_concept(
        self,
        name: str,
        embedding: Sequence[float],
        description: Optional[str] = None,
        similarity_threshold: float = 0.85,
    ) -> tuple[Concept, bool]:
        """
        去重嘅核心入口。攞住新抽取到嘅主題名 + embedding:
        - 如果同現存某個 theme concept 嘅 cosine similarity >= threshold,
          就當佢係同一個概念,將呢個新講法補落 aliases,唔開新 node。
        - 唔夠相似先真係開一個新 concept。
        回傳 (concept, is_new)。
        """
        with self.session_scope() as s:
            matches = self._find_similar_concepts(
                s, embedding, concept_type="theme", threshold=similarity_threshold, limit=1
            )
            if matches:
                existing, _similarity = matches[0]
                if name != existing.name and name not in existing.aliases:
                    existing.aliases = [*existing.aliases, name]
                    s.flush()
                return existing, False

            concept = Concept(
                concept_type="theme",
                name=name,
                description=description,
                embedding=list(embedding),
                aliases=[],
            )
            s.add(concept)
            s.flush()
            return concept, True

    def get_or_create_company_concept(
        self, company_id: int, name: Optional[str] = None
    ) -> tuple[Concept, bool]:
        """
        公司 node 唔使靠 embedding 模糊配對——company_id 本身已經係精準嘅身份,
        直接 exact-match upsert 就得。
        """
        with self.session_scope() as s:
            existing = s.scalars(select(Concept).where(Concept.company_id == company_id)).first()
            if existing is not None:
                return existing, False

            company = s.get(Company, company_id)
            if company is None:
                raise ValueError(f"company_id={company_id} 唔存在,唔可以開 company concept")

            concept = Concept(
                concept_type="company",
                name=name or company.name_en,
                company_id=company_id,
                aliases=[],
            )
            s.add(concept)
            s.flush()
            return concept, True

    def get_related_themes_for_company(self, company_id: int, *, limit: int = 15) -> list[Concept]:
        """
        搵返呢間公司個 concept node 有邊啲 theme(唔理邊個方向嘅 edge)連住,
        按最近一次強化排先。公司未有任何 concept/relation 就返 []。
        """
        with self.session_scope() as s:
            company_concept = s.scalars(
                select(Concept).where(Concept.concept_type == "company", Concept.company_id == company_id)
            ).first()
            if company_concept is None:
                return []

            theme = aliased(Concept)
            stmt = (
                select(theme)
                .join(
                    ConceptRelation,
                    or_(
                        and_(
                            ConceptRelation.from_concept_id == company_concept.concept_id,
                            ConceptRelation.to_concept_id == theme.concept_id,
                        ),
                        and_(
                            ConceptRelation.to_concept_id == company_concept.concept_id,
                            ConceptRelation.from_concept_id == theme.concept_id,
                        ),
                    ),
                )
                .where(theme.concept_type == "theme")
                .order_by(ConceptRelation.last_reinforced_at.desc())
                .limit(limit)
            )
            return list(s.scalars(stmt).unique().all())

    def get_top_themes(self, *, limit: int = 15) -> list[Concept]:
        """全域最近有更新嘅 theme concept,俾冇歷史記錄嘅新公司做 grounding fallback。"""
        with self.session_scope() as s:
            stmt = (
                select(Concept)
                .where(Concept.concept_type == "theme")
                .order_by(Concept.updated_at.desc())
                .limit(limit)
            )
            return list(s.scalars(stmt).all())

    # --------------------------------------------------------- Relation (edge) CRUD
    def get_relation(self, relation_id: int) -> Optional[ConceptRelation]:
        with self.session_scope() as s:
            return get_obj(s, ConceptRelation, relation_id)

    def find_relation(
        self, from_concept_id: int, to_concept_id: int, relation_type: str, polarity: str = "positive"
    ) -> Optional[ConceptRelation]:
        with self.session_scope() as s:
            stmt = select(ConceptRelation).where(
                ConceptRelation.from_concept_id == from_concept_id,
                ConceptRelation.to_concept_id == to_concept_id,
                ConceptRelation.relation_type == relation_type,
                ConceptRelation.polarity == polarity,
            )
            return s.scalars(stmt).first()

    def delete_relation(self, relation_id: int) -> bool:
        with self.session_scope() as s:
            return delete_obj(s, ConceptRelation, relation_id)

    def list_outgoing_relations(self, concept_id: int, *, limit: int = 100) -> list[ConceptRelation]:
        with self.session_scope() as s:
            return list_obj(s, ConceptRelation, limit=limit, from_concept_id=concept_id)

    def list_incoming_relations(self, concept_id: int, *, limit: int = 100) -> list[ConceptRelation]:
        with self.session_scope() as s:
            return list_obj(s, ConceptRelation, limit=limit, to_concept_id=concept_id)

    def list_relation_evidence(self, relation_id: int, *, limit: int = 100) -> list[ConceptRelationEvidence]:
        with self.session_scope() as s:
            return list_obj(s, ConceptRelationEvidence, limit=limit, relation_id=relation_id)

    def reinforce_relation(
        self,
        from_concept_id: int,
        to_concept_id: int,
        relation_type: str,
        *,
        polarity: str = "positive",
        confidence: float = 0.5,
        source_news_id: Optional[int] = None,
        note: Optional[str] = None,
    ) -> ConceptRelation:
        """
        強化嘅核心入口。

        (from_concept_id, to_concept_id, relation_type, polarity) 呢個組合
        代表一個具體嘅因果論述(例如「AI發展 -[benefits]-> 電力需求, positive」)。
        - 第一次見到:開一條新邊, reinforcement_count=1。
        - 之後再見到同一個論述(嚟自另一篇新聞):唔開新邊,
          用 running average 更新 confidence,reinforcement_count+1,
          更新 last_reinforced_at,代表市場上不斷有新資訊印證緊呢個論點。

        留意 UNIQUE constraint 淨係包 (from, to, relation_type, polarity),
        唔包 confidence——即係同一對 concept 之間,positive 同 negative
        嘅論述可以同時存在,唔會互相覆蓋(睇好/睇淡可以並存)。

        無論邊種情況,都會加多一條 ConceptRelationEvidence,
        keep 低逐次強化嘅 citation trail,方便日後生成解釋鏈。
        """
        now = datetime.now(timezone.utc)
        with self.session_scope() as s:
            stmt = select(ConceptRelation).where(
                ConceptRelation.from_concept_id == from_concept_id,
                ConceptRelation.to_concept_id == to_concept_id,
                ConceptRelation.relation_type == relation_type,
                ConceptRelation.polarity == polarity,
            )
            relation = s.scalars(stmt).first()

            if relation is None:
                relation = ConceptRelation(
                    from_concept_id=from_concept_id,
                    to_concept_id=to_concept_id,
                    relation_type=relation_type,
                    polarity=polarity,
                    confidence=confidence,
                    reinforcement_count=1,
                    last_reinforced_at=now,
                )
                s.add(relation)
            else:
                total_confidence = relation.confidence * relation.reinforcement_count + confidence
                relation.reinforcement_count += 1
                relation.confidence = total_confidence / relation.reinforcement_count
                relation.last_reinforced_at = now

            s.flush()  # 保證有 relation_id, 俾下面 evidence 用

            s.add(
                ConceptRelationEvidence(
                    relation_id=relation.relation_id,
                    news_id=source_news_id,
                    note=note,
                )
            )
            s.flush()
            return relation
