"""
喺 Mind Map(Concept Graph)之上做嘅圖分析(graph analytics)——呢層唔負責
寫入任何嘢,淨係負責讀取現存嘅 concept/relation/evidence,計算出幾類
「值得留意」嘅訊號,俾 `app/etl/weekly_digest.py` 攞去砌成每週摘要,
或者你自己直接攞嚟睇。

六類訊號:
1. `get_accelerating_relations()` —— 邊條relation最近先加速被引用(reinforcement
   velocity):比較「近期」同「之前」兩段時間嘅evidence密度。
2. `get_emerging_themes()`         —— 啱啱先出現、仲未俾好多獨立來源印證嘅新主題。
3. `get_polarity_conflicts()`      —— 同一對concept、同一種relation_type,
   但同時有positive同negative論述並存(市場有分歧)。
4. `get_theme_breadth()`           —— 邊個主題直接影響緊最多間唔同公司。
5. `get_propagation_paths()`       —— 由一個concept出發,沿outgoing relation
   做BFS,追蹤主題可以點樣一路傳導落去(confidence連乘做path權重)。
6. `get_evidence_source_diversity()` —— 邊條relation嘅evidence嚟自最多唔同
   嘅新聞來源(嚟源愈分散,獨立印證程度理應愈高)。

呢層完全唔負責解讀呢啲數字代表咩意思、更加唔會作任何「大市會點行」嘅
判斷——純粹將Mind Map入面已有嘅結構化資訊,整理成方便下游(人或者LLM)
消化嘅形狀。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import aliased

from app.models import Concept, ConceptRelation, ConceptRelationEvidence, News


class AnalyticsManagerMixin:
    # --------------------------------------------------- 1. Reinforcement 加速度
    def get_accelerating_relations(
        self,
        *,
        recent_days: int = 7,
        baseline_days: int = 30,
        min_recent_evidence: int = 2,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        近期(recent_days)evidence密度 vs 再之前(baseline_days,唔重疊recent_days
        嗰段)嘅密度,搵「最近先突然多番好多篇獨立新聞印證緊」嘅relation。
        `acceleration` = 近期每日平均evidence數 - baseline每日平均evidence數,
        數值愈高代表呢個論述最近升溫得愈快。
        """
        now = datetime.now(timezone.utc)
        recent_cutoff = now - timedelta(days=recent_days)
        baseline_cutoff = now - timedelta(days=recent_days + baseline_days)

        with self.session_scope() as s:
            recent_count_expr = func.count(
                case((ConceptRelationEvidence.created_at >= recent_cutoff, 1))
            )
            baseline_count_expr = func.count(
                case(
                    (
                        ConceptRelationEvidence.created_at.between(baseline_cutoff, recent_cutoff),
                        1,
                    )
                )
            )
            stmt = (
                select(
                    ConceptRelation,
                    recent_count_expr.label("recent_count"),
                    baseline_count_expr.label("baseline_count"),
                )
                .join(
                    ConceptRelationEvidence,
                    ConceptRelationEvidence.relation_id == ConceptRelation.relation_id,
                )
                .where(ConceptRelationEvidence.created_at >= baseline_cutoff)
                .group_by(ConceptRelation.relation_id)
                .having(recent_count_expr >= min_recent_evidence)
            )
            rows = s.execute(stmt).all()

            results = []
            for relation, recent_count, baseline_count in rows:
                recent_rate = recent_count / recent_days if recent_days else 0.0
                baseline_rate = baseline_count / baseline_days if baseline_days else 0.0
                results.append(
                    {
                        "relation_id": relation.relation_id,
                        "from_concept_id": relation.from_concept_id,
                        "to_concept_id": relation.to_concept_id,
                        "from_concept": relation.from_concept.name,
                        "to_concept": relation.to_concept.name,
                        "relation_type": relation.relation_type,
                        "polarity": relation.polarity,
                        "confidence": relation.confidence,
                        "recent_evidence_count": recent_count,
                        "baseline_evidence_count": baseline_count,
                        "recent_rate_per_day": round(recent_rate, 3),
                        "baseline_rate_per_day": round(baseline_rate, 3),
                        "acceleration": round(recent_rate - baseline_rate, 3),
                    }
                )

            results.sort(key=lambda r: r["acceleration"], reverse=True)
            return results[:limit]

    # ----------------------------------------------------------- 2. 新興主題
    def get_emerging_themes(
        self,
        *,
        recent_days: int = 14,
        max_total_reinforcement: int = 3,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        recent_days 內先開嘅 theme concept,而且掛喺佢身上(進/出)嘅 relation
        總 reinforcement_count 仲好低(<= max_total_reinforcement)——代表
        呢個主題可能啱啱開始被市場提及,值得留意但仲未有好多獨立來源印證,
        同「已經好多篇新聞reinforce緊」嘅成熟主題分開嚟睇。
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=recent_days)

        with self.session_scope() as s:
            relation_count_expr = func.count(ConceptRelation.relation_id)
            total_reinforcement_expr = func.coalesce(
                func.sum(ConceptRelation.reinforcement_count), 0
            )
            stmt = (
                select(
                    Concept,
                    relation_count_expr.label("relation_count"),
                    total_reinforcement_expr.label("total_reinforcement"),
                )
                .outerjoin(
                    ConceptRelation,
                    or_(
                        ConceptRelation.from_concept_id == Concept.concept_id,
                        ConceptRelation.to_concept_id == Concept.concept_id,
                    ),
                )
                .where(Concept.concept_type == "theme", Concept.created_at >= cutoff)
                .group_by(Concept.concept_id)
                .having(total_reinforcement_expr <= max_total_reinforcement)
                .order_by(Concept.created_at.desc())
                .limit(limit)
            )
            rows = s.execute(stmt).all()
            return [
                {
                    "concept_id": concept.concept_id,
                    "name": concept.name,
                    "description": concept.description,
                    "created_at": concept.created_at,
                    "relation_count": relation_count,
                    "total_reinforcement": total_reinforcement,
                }
                for concept, relation_count, total_reinforcement in rows
            ]

    # --------------------------------------------------------- 3. 極性衝突
    def get_polarity_conflicts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """
        同一對 concept、同一種 relation_type,但同時有 positive 同 negative
        兩條 edge 並存——即市場對呢件事有分歧(有人睇好有人睇淡)。呢啲
        值得特別標出嚟,唔應該將兩個對立論述夾埋做一個「平均confidence」
        掩蓋咗當中嘅分歧。
        """
        with self.session_scope() as s:
            stmt = select(ConceptRelation).where(
                ConceptRelation.polarity.in_(["positive", "negative"])
            )
            relations = list(s.scalars(stmt).all())

            grouped: dict[tuple[int, int, str], dict[str, ConceptRelation]] = {}
            for relation in relations:
                key = (relation.from_concept_id, relation.to_concept_id, relation.relation_type)
                grouped.setdefault(key, {})[relation.polarity] = relation

            conflicts: list[dict[str, Any]] = []
            for (_from_id, _to_id, relation_type), by_polarity in grouped.items():
                if "positive" not in by_polarity or "negative" not in by_polarity:
                    continue
                pos = by_polarity["positive"]
                neg = by_polarity["negative"]
                conflicts.append(
                    {
                        "from_concept_id": pos.from_concept_id,
                        "to_concept_id": pos.to_concept_id,
                        "from_concept": pos.from_concept.name,
                        "to_concept": pos.to_concept.name,
                        "relation_type": relation_type,
                        "positive_confidence": pos.confidence,
                        "positive_reinforcement_count": pos.reinforcement_count,
                        "negative_confidence": neg.confidence,
                        "negative_reinforcement_count": neg.reinforcement_count,
                        "total_reinforcement": pos.reinforcement_count + neg.reinforcement_count,
                    }
                )

            conflicts.sort(key=lambda c: c["total_reinforcement"], reverse=True)
            return conflicts[:limit]

    # ------------------------------------------------------------- 4. 主題廣度
    def get_theme_breadth(self, *, min_companies: int = 2, limit: int = 20) -> list[dict[str, Any]]:
        """
        邊個主題直接連住(outgoing relation)最多間唔重複嘅 company concept——
        數字愈大代表呢個主題影響面愈廣,唔係得一兩間公司嘅小圈子論述。
        """
        with self.session_scope() as s:
            CompanyConcept = aliased(Concept)
            distinct_company_expr = func.count(func.distinct(CompanyConcept.company_id))
            stmt = (
                select(Concept, distinct_company_expr.label("company_count"))
                .join(ConceptRelation, ConceptRelation.from_concept_id == Concept.concept_id)
                .join(CompanyConcept, ConceptRelation.to_concept_id == CompanyConcept.concept_id)
                .where(Concept.concept_type == "theme", CompanyConcept.concept_type == "company")
                .group_by(Concept.concept_id)
                .having(distinct_company_expr >= min_companies)
                .order_by(distinct_company_expr.desc())
                .limit(limit)
            )
            rows = s.execute(stmt).all()

            results: list[dict[str, Any]] = []
            for concept, company_count in rows:
                companies_stmt = (
                    select(CompanyConcept.name)
                    .join(ConceptRelation, ConceptRelation.to_concept_id == CompanyConcept.concept_id)
                    .where(
                        ConceptRelation.from_concept_id == concept.concept_id,
                        CompanyConcept.concept_type == "company",
                    )
                    .distinct()
                )
                companies = list(s.scalars(companies_stmt).all())
                results.append(
                    {
                        "concept_id": concept.concept_id,
                        "name": concept.name,
                        "company_count": company_count,
                        "companies": companies,
                    }
                )
            return results

    # ------------------------------------------------------------ 5. 傳導路徑
    def get_propagation_paths(
        self,
        from_concept_id: int,
        *,
        max_hops: int = 3,
        min_confidence: float = 0.3,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        由一個 concept 出發,沿住 outgoing relation 做 BFS,追蹤「呢個主題可以
        點樣一路傳導落去」(例如 AI發展 -> 晶片需求上升 -> TSMC受惠),用逐段
        confidence 相乘做成條路徑嘅整體權重(愈長愈多手,權重自然打折)。
        忽略 confidence 低於 min_confidence 嘅單條 edge,避免將好牽強嘅推論
        都計落條 path 度;同一個 concept 喺同一條 path 入面唔會行返轉頭
        (避免因為 relation 成環而無限loop)。回傳所有搵到嘅路徑(唔止leaf),
        按整體權重由高到低排。
        """
        with self.session_scope() as s:
            start = s.get(Concept, from_concept_id)
            if start is None:
                return []

            paths: list[dict[str, Any]] = []
            # queue item: (current_concept, path_names, path_relation_labels, weight, visited_ids)
            queue: list[tuple[Concept, list[str], list[str], float, set[int]]] = [
                (start, [start.name], [], 1.0, {start.concept_id})
            ]

            while queue:
                current, path_names, path_rel_labels, weight, visited = queue.pop(0)
                if len(path_names) - 1 >= max_hops:
                    continue

                stmt = (
                    select(ConceptRelation)
                    .where(
                        ConceptRelation.from_concept_id == current.concept_id,
                        ConceptRelation.confidence >= min_confidence,
                    )
                    .order_by(ConceptRelation.confidence.desc())
                )
                for relation in s.scalars(stmt).all():
                    if relation.to_concept_id in visited:
                        continue
                    next_concept = relation.to_concept
                    next_weight = weight * relation.confidence
                    next_path_names = path_names + [next_concept.name]
                    next_rel_labels = path_rel_labels + [
                        f"{relation.relation_type}({relation.polarity})"
                    ]
                    paths.append(
                        {
                            "path": next_path_names,
                            "relation_types": next_rel_labels,
                            "weight": round(next_weight, 4),
                            "hops": len(next_path_names) - 1,
                            "end_concept_type": next_concept.concept_type,
                        }
                    )
                    queue.append(
                        (
                            next_concept,
                            next_path_names,
                            next_rel_labels,
                            next_weight,
                            visited | {relation.to_concept_id},
                        )
                    )

            paths.sort(key=lambda p: p["weight"], reverse=True)
            return paths[:limit]

    # -------------------------------------------------------- 6. 證據來源多樣性
    def get_evidence_source_diversity(
        self, *, min_sources: int = 2, limit: int = 20
    ) -> list[dict[str, Any]]:
        """
        邊條 relation 嘅 evidence 嚟自最多唔同嘅新聞來源(News.source)——
        嚟源愈分散,代表呢個論述唔係得單一間媒體自己講,而係多個獨立來源
        都印證緊,可信度理應更高(相對於同一間媒體反覆報導同一單嘢)。
        """
        with self.session_scope() as s:
            source_count_expr = func.count(func.distinct(News.source))
            stmt = (
                select(ConceptRelation, source_count_expr.label("source_count"))
                .join(
                    ConceptRelationEvidence,
                    ConceptRelationEvidence.relation_id == ConceptRelation.relation_id,
                )
                .join(News, News.news_id == ConceptRelationEvidence.news_id)
                .where(News.source.is_not(None))
                .group_by(ConceptRelation.relation_id)
                .having(source_count_expr >= min_sources)
                .order_by(source_count_expr.desc())
                .limit(limit)
            )
            rows = s.execute(stmt).all()
            return [
                {
                    "relation_id": relation.relation_id,
                    "from_concept": relation.from_concept.name,
                    "to_concept": relation.to_concept.name,
                    "relation_type": relation.relation_type,
                    "polarity": relation.polarity,
                    "confidence": relation.confidence,
                    "distinct_source_count": source_count,
                }
                for relation, source_count in rows
            ]
