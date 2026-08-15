"""
「Mind Map 訊號 -> 之後實際股價表現」嘅事後配對(ex-post evaluation)。

Mind Map 入面每條 `concept_relation` 其實已經天然track住一個可以驗證嘅「訊號」：
`ConceptRelationEvidence` 記低咗「邊篇新聞(邊個時間點)」令某個 theme
--[relation_type/polarity]--> 某間公司呢條論述被強化多一次。呢個 module
淨係負責將呢啲訊號，同 `app/manager/price_manager.py` 讀返嚟嘅實際股價序列
對埋一齊，計返「訊號出現之後，呢間公司股價實際點行」，等你可以事後驗證
「呢個Mind Map平時睇好/睇淡嘅論述，係咪真係同之後嘅股價表現有關」。

呢層完全唔負責、亦都唔應該負責任何「應該點做」嘅判斷——純粹計數同配對，
輸出純資料，點解讀交返俾你或者下游(例如寫返一份report)。

已知嘅簡化(值得留意)：
- `evaluate_signal()` 用嘅 baseline 係「訊號日子當日或之後，第一個有交易嘅
  收市價」——即係話個訊號通常已經反映咗新聞出咗之後嘅市場反應，唔係
  「新聞出之前」嘅價。想再精準啲(例如評估「訊號出現一刻，市場係咪已經
  提前反映咗」)，可以自己攞埋 `get_price_bar_on_or_before()` 做對比。
- `confidence` 呢個filter/輸出用嘅係relation依家(呼叫嗰一刻)嘅aggregate
  confidence，唔係嗰條訊號發生嗰一刻嘅confidence快照(而家個schema冇儲存
  per-evidence嘅confidence)。想再精準，可以之後喺
  `ConceptRelationEvidence` 加一個 `confidence_at_time` 欄位。
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.models import Concept, ConceptRelation, ConceptRelationEvidence

DEFAULT_HORIZONS_TRADING_DAYS: tuple[int, ...] = (5, 10, 20, 60)


class EvaluationManagerMixin:
    # -------------------------------------------------------------- 訊號查詢
    def get_relation_signals(
        self,
        *,
        company_id: Optional[int] = None,
        polarity: Optional[str] = None,
        relation_type: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """
        攞返所有「主題 -> 公司」嘅relation(即係一個具體投資論述)底下嘅每一條
        evidence，each 拆做一個獨立嘅「訊號」(一個時間點 + 一條論述)。
        `signal_date` 優先用返嗰篇新聞嘅 `published_at`(市場實際知道呢件事嘅
        日子)，冇連新聞(例如手動加嘅evidence)先fallback用`evidence.created_at`。
        """
        with self.session_scope() as s:
            CompanyConcept = aliased(Concept)
            stmt = (
                select(ConceptRelation)
                .join(CompanyConcept, ConceptRelation.to_concept_id == CompanyConcept.concept_id)
                .where(
                    CompanyConcept.concept_type == "company",
                    CompanyConcept.company_id.is_not(None),
                    ConceptRelation.confidence >= min_confidence,
                )
            )
            if company_id is not None:
                stmt = stmt.where(CompanyConcept.company_id == company_id)
            if polarity is not None:
                stmt = stmt.where(ConceptRelation.polarity == polarity)
            if relation_type is not None:
                stmt = stmt.where(ConceptRelation.relation_type == relation_type)

            relations = list(s.scalars(stmt).all())

            signals: list[dict[str, Any]] = []
            for relation in relations:
                company_concept = relation.to_concept
                for evidence in relation.evidence:
                    if evidence.news is not None and evidence.news.published_at is not None:
                        signal_date = evidence.news.published_at.date()
                    else:
                        signal_date = evidence.created_at.date()
                    signals.append(
                        {
                            "evidence_id": evidence.evidence_id,
                            "relation_id": relation.relation_id,
                            "company_id": company_concept.company_id,
                            "from_concept": relation.from_concept.name,
                            "relation_type": relation.relation_type,
                            "polarity": relation.polarity,
                            "confidence": relation.confidence,
                            "signal_date": signal_date,
                            "news_id": evidence.news_id,
                            "note": evidence.note,
                        }
                    )

            signals.sort(key=lambda sig: sig["signal_date"])
            return signals[:limit]

    # ------------------------------------------------------------ 單一訊號配對
    def evaluate_signal(
        self,
        evidence_id: int,
        *,
        horizons_trading_days: Sequence[int] = DEFAULT_HORIZONS_TRADING_DAYS,
    ) -> dict[str, Any]:
        """
        將一條具體嘅 evidence(一個時間點嘅訊號)，同呢間公司之後嘅實際股價
        配對埋一齊。

        Baseline：訊號日子當日或之後，第一個有交易嘅收市價(`get_price_bars_from`
        嘅第一條)。`horizons_trading_days` 入面每個數字 N，代表「baseline 之後
        第 N 個交易日」(用真實交易日序列數，唔係calendar day，所以唔使自己
        處理週末/假期)嘅收市價，同 baseline 比較計 forward return(%)。

        如果DB完全冇呢間公司嘅股價資料(未fetch過/訊號太新)，回傳
        `{"evaluable": False, "reason": ...}`；某個horizon仲未夠交易日
        (例如訊號啱啱出、未夠60個交易日)，嗰個horizon嘅值會係 `None`，
        其他夠嘅horizon照計，唔會累事成條記錄都用唔到。
        """
        with self.session_scope() as s:
            evidence = s.get(ConceptRelationEvidence, evidence_id)
            if evidence is None:
                raise ValueError(f"evidence_id={evidence_id} 唔存在")

            relation = evidence.relation
            company_concept = relation.to_concept
            if company_concept.concept_type != "company" or company_concept.company_id is None:
                raise ValueError(
                    f"relation_id={relation.relation_id} 嘅to_concept唔係company node,"
                    "唔可以同股價配對"
                )

            if evidence.news is not None and evidence.news.published_at is not None:
                signal_date = evidence.news.published_at.date()
            else:
                signal_date = evidence.created_at.date()

            base_result = {
                "evidence_id": evidence_id,
                "company_id": company_concept.company_id,
                "from_concept": relation.from_concept.name,
                "relation_type": relation.relation_type,
                "polarity": relation.polarity,
                "confidence": relation.confidence,
                "signal_date": signal_date,
            }
            company_id = company_concept.company_id

        max_horizon = max(horizons_trading_days) if horizons_trading_days else 0
        bars = self.get_price_bars_from(company_id, signal_date, limit=max_horizon + 1)

        if not bars:
            return {
                **base_result,
                "evaluable": False,
                "reason": "冇股價資料(可能未幫呢間公司fetch過股價，或者訊號日子太新)",
            }

        baseline = bars[0]
        horizons: dict[int, Optional[dict[str, Any]]] = {}
        for horizon in horizons_trading_days:
            if horizon < len(bars):
                bar = bars[horizon]
                return_pct = (bar.close_price - baseline.close_price) / baseline.close_price * 100
                horizons[horizon] = {
                    "date": bar.price_date,
                    "close_price": bar.close_price,
                    "return_pct": round(return_pct, 3),
                }
            else:
                horizons[horizon] = None

        return {
            **base_result,
            "evaluable": True,
            "baseline_date": baseline.price_date,
            "baseline_close_price": baseline.close_price,
            "horizons": horizons,
        }

    # --------------------------------------------------------- 彙總 track record
    def evaluate_thesis_track_record(
        self,
        *,
        company_id: Optional[int] = None,
        polarity: Optional[str] = None,
        relation_type: Optional[str] = None,
        min_confidence: float = 0.0,
        horizons_trading_days: Sequence[int] = DEFAULT_HORIZONS_TRADING_DAYS,
        limit_signals: int = 500,
    ) -> dict[str, Any]:
        """
        彙總一批訊號嘅「事後track record」。

        `hit`嘅定義(淨係計 positive/negative,neutral唔計入hit_rate,但依然
        計入avg_return_pct)：
        - polarity="positive" 嘅訊號：之後forward return > 0 算 hit(估啱方向)
        - polarity="negative" 嘅訊號：之後forward return < 0 算 hit(估啱方向)

        回傳：
            {
              "total_signals": 符合filter嘅訊號總數,
              "evaluated_signals": 有股價資料、真係計到嘅訊號數,
              "skipped_signals": 冇股價資料、計唔到嘅訊號數,
              "horizons": {
                  5: {"evaluated": ..., "avg_return_pct": ..., "hit_rate": ..., "scored_for_hit_rate": ...},
                  ...
              }
            }
        """
        signals = self.get_relation_signals(
            company_id=company_id,
            polarity=polarity,
            relation_type=relation_type,
            min_confidence=min_confidence,
            limit=limit_signals,
        )

        horizon_stats: dict[int, dict[str, Any]] = {
            h: {"evaluated": 0, "hit": 0, "scored_for_hit_rate": 0, "return_sum": 0.0}
            for h in horizons_trading_days
        }
        evaluated_signals = 0
        skipped_signals = 0

        for signal in signals:
            result = self.evaluate_signal(
                signal["evidence_id"], horizons_trading_days=horizons_trading_days
            )
            if not result.get("evaluable"):
                skipped_signals += 1
                continue
            evaluated_signals += 1

            for horizon, outcome in result["horizons"].items():
                if outcome is None:
                    continue
                stat = horizon_stats[horizon]
                stat["evaluated"] += 1
                stat["return_sum"] += outcome["return_pct"]
                if signal["polarity"] in ("positive", "negative"):
                    stat["scored_for_hit_rate"] += 1
                    is_hit = (
                        outcome["return_pct"] > 0
                        if signal["polarity"] == "positive"
                        else outcome["return_pct"] < 0
                    )
                    if is_hit:
                        stat["hit"] += 1

        horizons_summary = {}
        for horizon, stat in horizon_stats.items():
            horizons_summary[horizon] = {
                "evaluated": stat["evaluated"],
                "avg_return_pct": (
                    round(stat["return_sum"] / stat["evaluated"], 3) if stat["evaluated"] else None
                ),
                "hit_rate": (
                    round(stat["hit"] / stat["scored_for_hit_rate"], 3)
                    if stat["scored_for_hit_rate"]
                    else None
                ),
                "scored_for_hit_rate": stat["scored_for_hit_rate"],
            }

        return {
            "total_signals": len(signals),
            "evaluated_signals": evaluated_signals,
            "skipped_signals": skipped_signals,
            "horizons": horizons_summary,
        }
