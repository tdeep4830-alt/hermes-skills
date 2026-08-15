"""
測試 `AnalyticsManagerMixin`(app/manager/analytics_manager.py)嘅六類訊號查詢。

因為呢啲query係跨成個DB嘅concept/relation做aggregate,而sandbox嘅DB會有
其他test/demo script留低嘅資料,所以每條test都：
1. 用 uuid 做唯一嘅 concept name(theme)/ticker(company),避免同其他資料撞名；
2. 攞到query結果之後,用自己created嗰個 concept_id/relation_id 做filter先斷言,
   唔假設「成個list嘅長度」——避免其他殘留資料令assertion變得脆弱。

執行: pytest tests/test_analytics_manager.py -v
（前提同其他 manager test 一樣：DATABASE_URL 指嘅 PostgreSQL 已經開緊機，
  並且已經 `alembic upgrade head`。）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.manager.db_manager import DatabaseManager
from app.models import ConceptRelationEvidence


def _random_embedding(dim: int = 1536) -> list[float]:
    """一次性隨機one-hot向量,避免同其他test/殘留concept嘅embedding撞埋一齊。"""
    vec = [0.0] * dim
    vec[uuid.uuid4().int % dim] = 1.0
    return vec


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def db():
    manager = DatabaseManager()
    try:
        yield manager
    finally:
        manager.dispose()


@pytest.fixture
def make_company(db):
    created = []

    def _make(name_prefix: str = "TestCo"):
        ticker = f"T{uuid.uuid4().hex[:8].upper()}"
        company = db.create_company(ticker=ticker, name_en=f"{name_prefix} {ticker}")
        created.append(company.company_id)
        return company

    yield _make
    for company_id in created:
        db.delete_company(company_id)


@pytest.fixture
def make_theme(db):
    created = []

    def _make(name_prefix: str = "主題"):
        name = _unique_name(name_prefix)
        concept, _is_new = db.get_or_create_theme_concept(name, embedding=_random_embedding())
        created.append(concept.concept_id)
        return concept

    yield _make
    for concept_id in created:
        db.delete_concept(concept_id)


@pytest.fixture
def make_company_concept(db, make_company):
    created = []

    def _make():
        company = make_company("ConceptCo")
        concept, _is_new = db.get_or_create_company_concept(company.company_id)
        created.append(concept.concept_id)
        return company, concept

    yield _make
    for concept_id in created:
        # company concept 可能已經因為 make_company fixture 刪公司而連帶被cascade刪走，
        # 但保底自己都試多次刪，刪唔到(已經無咗)冇所謓。
        db.delete_concept(concept_id)


@pytest.fixture
def make_news(db):
    created = []

    def _make(title_prefix: str, *, source: str):
        news = db.add_news(
            title=_unique_name(title_prefix),
            published_at=datetime.now(timezone.utc),
            source=source,
            news_type="industry",
        )
        created.append(news.news_id)
        return news

    yield _make
    for news_id in created:
        db.delete_news(news_id)


# --------------------------------------------------------------- accelerating
def test_get_accelerating_relations_separates_recent_from_baseline(db, make_theme, make_company_concept):
    theme = make_theme("加速測試")
    _company, company_concept = make_company_concept()

    relation = db.reinforce_relation(
        theme.concept_id, company_concept.concept_id, "benefits", polarity="positive", confidence=0.6
    )
    # 再加多兩次「近期」reinforcement(evidence created_at 用預設 now())
    db.reinforce_relation(
        theme.concept_id, company_concept.concept_id, "benefits", polarity="positive", confidence=0.6
    )
    db.reinforce_relation(
        theme.concept_id, company_concept.concept_id, "benefits", polarity="positive", confidence=0.6
    )

    # 手動插一條好耐之前(baseline window)嘅 evidence，模擬「20日前都有人講過」
    old_ts = datetime.now(timezone.utc) - timedelta(days=20)
    with db.session_scope() as s:
        s.add(
            ConceptRelationEvidence(
                relation_id=relation.relation_id,
                news_id=None,
                note="baseline evidence",
                created_at=old_ts,
            )
        )

    results = db.get_accelerating_relations(recent_days=7, baseline_days=30, min_recent_evidence=2)
    match = next((r for r in results if r["relation_id"] == relation.relation_id), None)
    assert match is not None
    assert match["recent_evidence_count"] == 3
    assert match["baseline_evidence_count"] == 1
    assert match["acceleration"] == pytest.approx(3 / 7 - 1 / 30, abs=1e-3)


def test_get_accelerating_relations_excludes_below_min_recent_evidence(db, make_theme, make_company_concept):
    theme = make_theme("加速門檻測試")
    _company, company_concept = make_company_concept()
    relation = db.reinforce_relation(
        theme.concept_id, company_concept.concept_id, "benefits", polarity="positive", confidence=0.6
    )
    results = db.get_accelerating_relations(recent_days=7, baseline_days=30, min_recent_evidence=5)
    assert all(r["relation_id"] != relation.relation_id for r in results)


# ------------------------------------------------------------------ emerging
def test_get_emerging_themes_includes_low_reinforcement_recent_theme(
    db, make_theme, make_company_concept
):
    theme = make_theme("新興主題")
    _company, company_concept = make_company_concept()
    db.reinforce_relation(theme.concept_id, company_concept.concept_id, "benefits", confidence=0.5)

    results = db.get_emerging_themes(recent_days=14, max_total_reinforcement=3)
    match = next((r for r in results if r["concept_id"] == theme.concept_id), None)
    assert match is not None
    assert match["total_reinforcement"] == 1


def test_get_emerging_themes_excludes_heavily_reinforced_theme(db, make_theme, make_company_concept):
    theme = make_theme("已成熟主題")
    _company, company_concept = make_company_concept()
    for _ in range(4):
        db.reinforce_relation(theme.concept_id, company_concept.concept_id, "benefits", confidence=0.5)

    results = db.get_emerging_themes(recent_days=14, max_total_reinforcement=3)
    assert all(r["concept_id"] != theme.concept_id for r in results)


# --------------------------------------------------------------- polarity conflicts
def test_get_polarity_conflicts_detects_coexisting_positive_and_negative(
    db, make_theme, make_company_concept
):
    theme = make_theme("分歧主題")
    _company, company_concept = make_company_concept()
    db.reinforce_relation(
        theme.concept_id, company_concept.concept_id, "benefits", polarity="positive", confidence=0.7
    )
    db.reinforce_relation(
        theme.concept_id, company_concept.concept_id, "benefits", polarity="negative", confidence=0.6
    )

    results = db.get_polarity_conflicts()
    match = next(
        (
            c
            for c in results
            if c["from_concept_id"] == theme.concept_id and c["to_concept_id"] == company_concept.concept_id
        ),
        None,
    )
    assert match is not None
    assert match["relation_type"] == "benefits"
    assert match["positive_confidence"] == pytest.approx(0.7)
    assert match["negative_confidence"] == pytest.approx(0.6)


def test_get_polarity_conflicts_no_false_positive_when_only_one_polarity(
    db, make_theme, make_company_concept
):
    theme = make_theme("冇分歧主題")
    _company, company_concept = make_company_concept()
    db.reinforce_relation(
        theme.concept_id, company_concept.concept_id, "benefits", polarity="positive", confidence=0.7
    )

    results = db.get_polarity_conflicts()
    assert all(
        not (c["from_concept_id"] == theme.concept_id and c["to_concept_id"] == company_concept.concept_id)
        for c in results
    )


# ------------------------------------------------------------------- breadth
def test_get_theme_breadth_counts_distinct_connected_companies(
    db, make_theme, make_company_concept
):
    theme = make_theme("廣度測試")
    _company1, concept1 = make_company_concept()
    _company2, concept2 = make_company_concept()
    db.reinforce_relation(theme.concept_id, concept1.concept_id, "benefits", confidence=0.6)
    db.reinforce_relation(theme.concept_id, concept2.concept_id, "drives_demand_for", confidence=0.6)

    results = db.get_theme_breadth(min_companies=2)
    match = next((r for r in results if r["concept_id"] == theme.concept_id), None)
    assert match is not None
    assert match["company_count"] == 2
    assert set(match["companies"]) == {_company1.name_en, _company2.name_en}


def test_get_theme_breadth_excludes_theme_below_min_companies(db, make_theme, make_company_concept):
    theme = make_theme("窄廣度測試")
    _company, concept = make_company_concept()
    db.reinforce_relation(theme.concept_id, concept.concept_id, "benefits", confidence=0.6)

    results = db.get_theme_breadth(min_companies=2)
    assert all(r["concept_id"] != theme.concept_id for r in results)


# --------------------------------------------------------------- propagation
def test_get_propagation_paths_follows_multi_hop_chain_with_confidence_product(
    db, make_theme, make_company_concept
):
    theme_a = make_theme("鏈路起點")
    theme_b = make_theme("鏈路中段")
    _company, company_concept = make_company_concept()

    db.reinforce_relation(
        theme_a.concept_id, theme_b.concept_id, "drives_demand_for", confidence=0.8
    )
    db.reinforce_relation(
        theme_b.concept_id, company_concept.concept_id, "benefits", confidence=0.5
    )

    paths = db.get_propagation_paths(theme_a.concept_id, max_hops=3, min_confidence=0.3)

    one_hop = next((p for p in paths if p["path"] == [theme_a.name, theme_b.name]), None)
    two_hop = next(
        (p for p in paths if p["path"] == [theme_a.name, theme_b.name, company_concept.name]), None
    )
    assert one_hop is not None
    assert one_hop["weight"] == pytest.approx(0.8)
    assert two_hop is not None
    assert two_hop["weight"] == pytest.approx(0.4)
    # 由高到低排序：一手路徑權重應該喺兩手路徑之前
    assert paths.index(one_hop) < paths.index(two_hop)


def test_get_propagation_paths_respects_min_confidence_cutoff(db, make_theme, make_company_concept):
    theme_a = make_theme("低信心起點")
    theme_b = make_theme("低信心中段")
    db.reinforce_relation(theme_a.concept_id, theme_b.concept_id, "drives_demand_for", confidence=0.1)

    paths = db.get_propagation_paths(theme_a.concept_id, max_hops=3, min_confidence=0.3)
    assert all(p["path"] != [theme_a.name, theme_b.name] for p in paths)


def test_get_propagation_paths_returns_empty_for_missing_concept(db):
    assert db.get_propagation_paths(-1) == []


# --------------------------------------------------------- evidence diversity
def test_get_evidence_source_diversity_counts_distinct_sources(
    db, make_news, make_theme, make_company_concept
):
    theme = make_theme("來源多樣性測試")
    _company, company_concept = make_company_concept()
    news_1 = make_news("來源一", source="TechCrunch")
    news_2 = make_news("來源二", source="The Verge")

    relation = db.reinforce_relation(
        theme.concept_id,
        company_concept.concept_id,
        "benefits",
        confidence=0.6,
        source_news_id=news_1.news_id,
    )
    db.reinforce_relation(
        theme.concept_id,
        company_concept.concept_id,
        "benefits",
        confidence=0.6,
        source_news_id=news_2.news_id,
    )

    results = db.get_evidence_source_diversity(min_sources=2)
    match = next((r for r in results if r["relation_id"] == relation.relation_id), None)
    assert match is not None
    assert match["distinct_source_count"] == 2


def test_get_evidence_source_diversity_excludes_single_source_relation(
    db, make_news, make_theme, make_company_concept
):
    theme = make_theme("單一來源測試")
    _company, company_concept = make_company_concept()
    news_1 = make_news("單一來源", source="TechCrunch")

    relation = db.reinforce_relation(
        theme.concept_id,
        company_concept.concept_id,
        "benefits",
        confidence=0.6,
        source_news_id=news_1.news_id,
    )

    results = db.get_evidence_source_diversity(min_sources=2)
    assert all(r["relation_id"] != relation.relation_id for r in results)
