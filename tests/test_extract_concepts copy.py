"""
測試 LLM concept 抽取 pipeline。分兩部分:

1. Unit test —— `app/etl/llm_client.py` 嘅 `_parse_and_validate()` guardrail
   邏輯。純 function、唔涉及 DB / API,covers:壞 relation_type、
   超範圍 confidence、缺少必要欄位、未確認公司 reference 呢幾類情況
   會唔會正確咁被丟棄/修正。

2. Integration test —— `app/etl/extract_concepts.py` 嘅
   `process_news_for_concepts()` 主流程,用 fake `llm_extract_fn` /
   `embed_fn`(唔使真係 call Anthropic/OpenAI)去驅動,但寫入用嘅係
   真實 PostgreSQL(靠 DatabaseManager),驗證 theme 建立/去重、company
   concept 建立、relation 強化、evidence 記錄呢幾樣係咪真係兜埋一齊行得通。

前提: DATABASE_URL 指住嘅 PostgreSQL 要已經開緊機,並且已經
`alembic upgrade head`(concepts/concept_relations/concept_relation_evidence
呢幾張表要已經存在)。

執行: pytest tests/test_extract_concepts.py -v
"""
from __future__ import annotations

import uuid

import pytest

from app.etl.extract_concepts import (
    build_article_text,
    get_grounding_themes,
    get_known_companies,
    get_known_companies_for_article,
    process_article_for_concepts,
    process_news_for_concepts,
)
from app.etl.llm_client import ExtractedRelation, ExtractedTheme, ExtractionResult, _parse_and_validate
from app.manager.db_manager import DatabaseManager

KNOWN_COMPANIES = [{"ticker": "AAPL", "name": "Apple Inc."}, {"ticker": "TSM", "name": "台積電"}]


# --------------------------------------------------------------------------
# 1. Unit tests: _parse_and_validate()
# --------------------------------------------------------------------------


def test_parse_and_validate_happy_path():
    raw = {
        "themes": [{"name": "AI伺服器需求上升", "description": "AI帶動伺服器需求"}],
        "relations": [
            {
                "from_theme": "AI伺服器需求上升",
                "to_type": "company",
                "to_ref": "TSM",
                "relation_type": "benefits",
                "polarity": "positive",
                "confidence": 0.8,
                "reasoning": "台積電係主要代工廠",
            }
        ],
    }
    result = _parse_and_validate(raw, known_companies=KNOWN_COMPANIES)
    assert len(result.themes) == 1
    assert result.themes[0].name == "AI伺服器需求上升"
    assert len(result.relations) == 1
    assert result.relations[0].to_ref == "TSM"
    assert result.relations[0].confidence == 0.8


def test_parse_and_validate_drops_bad_relation_type():
    raw = {
        "themes": [{"name": "theme A"}],
        "relations": [
            {
                "from_theme": "theme A",
                "to_type": "company",
                "to_ref": "AAPL",
                "relation_type": "not_a_real_relation_type",
                "polarity": "positive",
                "confidence": 0.7,
            }
        ],
    }
    result = _parse_and_validate(raw, known_companies=KNOWN_COMPANIES)
    assert result.relations == []


def test_parse_and_validate_drops_relation_to_unconfirmed_company():
    raw = {
        "themes": [{"name": "theme A"}],
        "relations": [
            {
                "from_theme": "theme A",
                "to_type": "company",
                "to_ref": "NVDA",  # 唔喺 known_companies 入面
                "relation_type": "benefits",
                "polarity": "positive",
                "confidence": 0.7,
            }
        ],
    }
    result = _parse_and_validate(raw, known_companies=KNOWN_COMPANIES)
    assert result.relations == []


def test_parse_and_validate_clamps_out_of_range_confidence():
    raw = {
        "themes": [{"name": "theme A"}],
        "relations": [
            {
                "from_theme": "theme A",
                "to_type": "company",
                "to_ref": "AAPL",
                "relation_type": "benefits",
                "polarity": "positive",
                "confidence": 1.5,  # 超出 [0, 1]
            }
        ],
    }
    result = _parse_and_validate(raw, known_companies=KNOWN_COMPANIES)
    assert len(result.relations) == 1
    assert result.relations[0].confidence == 1.0

    raw["relations"][0]["confidence"] = -0.3
    result = _parse_and_validate(raw, known_companies=KNOWN_COMPANIES)
    assert result.relations[0].confidence == 0.0


def test_parse_and_validate_drops_missing_required_fields():
    raw = {
        "themes": [{"name": ""}, {"description": "冇 name 嘅 theme"}],
        "relations": [
            {"from_theme": "", "to_type": "company", "to_ref": "AAPL", "relation_type": "benefits", "confidence": 0.5},
            {"from_theme": "theme A", "to_type": "company", "to_ref": "", "relation_type": "benefits", "confidence": 0.5},
            {"from_theme": "theme A", "to_type": "company", "to_ref": "AAPL", "relation_type": "benefits", "confidence": "not_a_number"},
        ],
    }
    result = _parse_and_validate(raw, known_companies=KNOWN_COMPANIES)
    assert result.themes == []
    assert result.relations == []


def test_parse_and_validate_defaults_invalid_polarity_to_positive():
    raw = {
        "themes": [],
        "relations": [
            {
                "from_theme": "theme A",
                "to_type": "company",
                "to_ref": "AAPL",
                "relation_type": "benefits",
                "polarity": "bullish",  # 唔喺 POLARITIES enum
                "confidence": 0.6,
            }
        ],
    }
    result = _parse_and_validate(raw, known_companies=KNOWN_COMPANIES)
    assert len(result.relations) == 1
    assert result.relations[0].polarity == "positive"


# --------------------------------------------------------------------------
# 2. Integration test: process_news_for_concepts() 用真實 Postgres + fake LLM/embed
# --------------------------------------------------------------------------


FAKE_EMBEDDING_DIM = 1536


def _fake_embedding(seed: int) -> list[float]:
    """用一個簡單、確定性嘅向量,唔使真係 call embedding API。"""
    vec = [0.0] * FAKE_EMBEDDING_DIM
    vec[seed % FAKE_EMBEDDING_DIM] = 1.0
    return vec


def _make_fake_embed_fn():
    """
    回傳一個 embed_fn:對相同文字永遠回傳相同向量(靠一個 dict cache),
    對唔同文字回傳唔相似(近乎正交)嘅向量,確保 get_or_create_theme_concept()
    嘅去重判斷唔會誤判。
    """
    cache: dict[str, list[float]] = {}
    counter = {"n": 0}

    def embed_fn(texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            if text not in cache:
                cache[text] = _fake_embedding(counter["n"])
                counter["n"] += 1
            vectors.append(cache[text])
        return vectors

    return embed_fn


@pytest.fixture
def db():
    manager = DatabaseManager()
    try:
        yield manager
    finally:
        manager.dispose()


@pytest.fixture
def seeded_company(db):
    """開一間獨一無二嘅測試公司(用 uuid 做 ticker 避免同其他 test 撞名)。"""
    ticker = f"TST{uuid.uuid4().hex[:8].upper()}"
    company = db.create_company(ticker=ticker, name_en=f"Test Co {ticker}")
    yield company
    db.delete_company(company.company_id)


@pytest.fixture
def seeded_news(db, seeded_company):
    from datetime import datetime, timezone

    news = db.add_news(
        title="測試新聞:AI伺服器需求帶動晶片需求",
        published_at=datetime.now(timezone.utc),
        content="分析指出,AI伺服器需求持續上升,將帶動相關晶片供應商受惠。",
        news_type="company",
        company_ids=[seeded_company.company_id],
    )
    yield news
    db.delete_news(news.news_id)


def test_get_known_companies(db, seeded_company, seeded_news):
    companies = get_known_companies(db, seeded_news.news_id)
    assert len(companies) == 1
    assert companies[0]["ticker"] == seeded_company.ticker
    assert companies[0]["company_id"] == seeded_company.company_id


def test_get_known_companies_missing_news_returns_empty(db):
    assert get_known_companies(db, news_id=-1) == []


def test_get_grounding_themes_falls_back_to_top_themes_for_new_company(db, seeded_company):
    # 全新公司,未有任何 relation 歷史 -> get_related_themes_for_company() 應該搵唔到嘢,
    # 於是 fallback 去 get_top_themes()。呢度淨係驗證唔會 raise,亦唔會靠估 DB 已有嘅 global 資料。
    themes = get_grounding_themes(db, [seeded_company.company_id], limit=5)
    assert isinstance(themes, list)


def test_process_news_for_concepts_end_to_end(db, seeded_company, seeded_news):
    ticker = seeded_company.ticker

    def fake_llm_extract_fn(*, article_title, article_content, known_companies, grounding_themes, max_relations):
        assert article_title == seeded_news.title
        assert any(c["ticker"] == ticker for c in known_companies)
        return ExtractionResult(
            themes=[ExtractedTheme(name="AI伺服器需求上升", description="AI帶動伺服器需求")],
            relations=[
                ExtractedRelation(
                    from_theme="AI伺服器需求上升",
                    to_type="company",
                    to_ref=ticker,
                    relation_type="benefits",
                    polarity="positive",
                    confidence=0.75,
                    reasoning="測試 reasoning",
                )
            ],
        )

    embed_fn = _make_fake_embed_fn()

    stats = process_news_for_concepts(
        db,
        seeded_news.news_id,
        llm_extract_fn=fake_llm_extract_fn,
        embed_fn=embed_fn,
    )

    assert stats["themes_created"] == 1
    assert stats["themes_reused"] == 0
    assert stats["relations_reinforced"] == 1
    assert stats["skipped_relations"] == 0

    # 驗證真係寫落 DB:theme concept、company concept 都開咗,relation 都建立咗
    theme_concept = db.find_similar_concepts(embed_fn(["AI伺服器需求上升: AI帶動伺服器需求"])[0], threshold=0.99)
    assert len(theme_concept) == 1
    theme_id = theme_concept[0][0].concept_id

    company_concept, is_new = db.get_or_create_company_concept(seeded_company.company_id)
    assert is_new is False  # 應該已經被 process_news_for_concepts() 建立咗,呢度應該搵到現存嗰個

    relation = db.find_relation(theme_id, company_concept.concept_id, "benefits", polarity="positive")
    assert relation is not None
    assert relation.reinforcement_count == 1
    assert relation.confidence == pytest.approx(0.75)

    evidence = db.list_relation_evidence(relation.relation_id)
    assert len(evidence) == 1
    assert evidence[0].news_id == seeded_news.news_id
    assert evidence[0].note == "測試 reasoning"

    # 再處理多一次「同一單新聞」,模擬第二篇新聞印證返同一個論述 -> 應該係強化(reinforce)
    # 而唔係開多一條新 relation。
    stats_2 = process_news_for_concepts(
        db,
        seeded_news.news_id,
        llm_extract_fn=fake_llm_extract_fn,
        embed_fn=embed_fn,
    )
    assert stats_2["themes_created"] == 0
    assert stats_2["themes_reused"] == 1
    assert stats_2["relations_reinforced"] == 1

    relation_after = db.find_relation(theme_id, company_concept.concept_id, "benefits", polarity="positive")
    assert relation_after.reinforcement_count == 2
    assert relation_after.confidence == pytest.approx(0.75)  # 兩次都係 0.75,running average 仍然係 0.75

    evidence_after = db.list_relation_evidence(relation_after.relation_id)
    assert len(evidence_after) == 2

    # cleanup:刪走呢個 test 建立嘅 concept,唔留低垃圾喺 DB(company concept 會因為
    # seeded_company fixture 刪公司嗰陣連鎖刪走 news_links,但 concept 表同 company 冇 cascade,
    # 要自己手動清理)
    db.delete_concept(theme_id)
    db.delete_concept(company_concept.concept_id)


def test_process_news_for_concepts_no_extraction_returns_zero_stats(db, seeded_company, seeded_news):
    def empty_llm_extract_fn(**kwargs):
        return ExtractionResult(themes=[], relations=[])

    stats = process_news_for_concepts(
        db,
        seeded_news.news_id,
        llm_extract_fn=empty_llm_extract_fn,
        embed_fn=_make_fake_embed_fn(),
    )
    assert stats == {"themes_created": 0, "themes_reused": 0, "relations_reinforced": 0, "skipped_relations": 0}


def test_process_news_for_concepts_raises_for_missing_news(db):
    with pytest.raises(ValueError):
        process_news_for_concepts(
            db,
            news_id=-1,
            llm_extract_fn=lambda **kwargs: ExtractionResult(),
            embed_fn=_make_fake_embed_fn(),
        )


# --------------------------------------------------------------------------
# 3. Integration test: process_article_for_concepts() —— 分析文章版本
# --------------------------------------------------------------------------


@pytest.fixture
def seeded_article(db, seeded_company):
    from datetime import datetime, timezone

    article = db.add_article(
        title="測試分析文章:AI發展帶動電力需求",
        published_at=datetime.now(timezone.utc),
        description="分析AI發展對電力需求嘅影響",
        thesis="AI伺服器耗電量持續上升，將帶動相關電力公司受惠",
        conclusion="睇好",
        sentiment="positive",
        company_ids=[seeded_company.company_id],
        primary_company_id=seeded_company.company_id,
    )
    yield article
    db.delete_news(article.news_id)  # 同 shared-PK 一致：刪 news 會連埋 article 一齊清


def test_get_known_companies_for_article(db, seeded_company, seeded_article):
    companies = get_known_companies_for_article(db, seeded_article.news_id)
    assert len(companies) == 1
    assert companies[0]["ticker"] == seeded_company.ticker
    assert companies[0]["company_id"] == seeded_company.company_id


def test_get_known_companies_for_article_missing_article_returns_empty(db):
    assert get_known_companies_for_article(db, article_id=-1) == []


def test_build_article_text_includes_source_context_and_fields(db, seeded_article):
    article = db.get_article_full(seeded_article.news_id)
    text = build_article_text(article)
    # source context 提醒 LLM 呢段內容嚟自正式分析文章，唔應該淨係因為「係預測」就自動壓confidence
    assert "分析文章" in text
    assert "confidence" in text
    assert article.description in text
    assert article.thesis in text
    assert article.conclusion in text


def test_process_article_for_concepts_end_to_end(db, seeded_company, seeded_article):
    ticker = seeded_company.ticker

    def fake_llm_extract_fn(*, article_title, article_content, known_companies, grounding_themes, max_relations):
        assert article_title == seeded_article.title
        assert any(c["ticker"] == ticker for c in known_companies)
        # 確認 article 專屬嘅 thesis/conclusion 內容真係傳咗俾 LLM(唔係得個 title)
        assert "AI伺服器耗電量持續上升" in article_content
        assert max_relations == 8  # Article 版本嘅預設值應該高過 News 嘅 5
        return ExtractionResult(
            themes=[ExtractedTheme(name="AI帶動電力需求", description="AI伺服器耗電量上升")],
            relations=[
                ExtractedRelation(
                    from_theme="AI帶動電力需求",
                    to_type="company",
                    to_ref=ticker,
                    relation_type="benefits",
                    polarity="positive",
                    confidence=0.8,
                    reasoning="分析文章測試 reasoning",
                )
            ],
        )

    embed_fn = _make_fake_embed_fn()

    stats = process_article_for_concepts(
        db,
        seeded_article.news_id,
        llm_extract_fn=fake_llm_extract_fn,
        embed_fn=embed_fn,
    )

    assert stats["themes_created"] == 1
    assert stats["themes_reused"] == 0
    assert stats["relations_reinforced"] == 1
    assert stats["skipped_relations"] == 0

    theme_concept = db.find_similar_concepts(embed_fn(["AI帶動電力需求: AI伺服器耗電量上升"])[0], threshold=0.99)
    assert len(theme_concept) == 1
    theme_id = theme_concept[0][0].concept_id

    company_concept, is_new = db.get_or_create_company_concept(seeded_company.company_id)
    assert is_new is False

    relation = db.find_relation(theme_id, company_concept.concept_id, "benefits", polarity="positive")
    assert relation is not None
    assert relation.reinforcement_count == 1

    # evidence.news_id 應該直接等於 article.news_id(shared-PK，唔使轉換)
    evidence = db.list_relation_evidence(relation.relation_id)
    assert len(evidence) == 1
    assert evidence[0].news_id == seeded_article.news_id

    db.delete_concept(theme_id)
    db.delete_concept(company_concept.concept_id)


def test_process_article_for_concepts_raises_for_missing_article(db):
    with pytest.raises(ValueError):
        process_article_for_concepts(
            db,
            article_id=-1,
            llm_extract_fn=lambda **kwargs: ExtractionResult(),
            embed_fn=_make_fake_embed_fn(),
        )
