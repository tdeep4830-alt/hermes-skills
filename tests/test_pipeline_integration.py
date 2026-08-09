"""
成個「每日新聞」pipeline 嘅端對端(end-to-end)test —— 唔淨係測單一個function,
而係直接call `app/etl/run_daily.py` 嘅真正入口 `run()`,用假嘅外部來源
(RSS/Hacker News/Finnhub 全部用 monkeypatch 假網絡call,唔連真網)去驅動,
但寫入/讀出用真實 PostgreSQL,確認 fetch -> clean -> load 呢條鏈實際兜得埋。

覆蓋咗幾樣單一function test唔到嘅嘢:
1. `fetch_all()` 攞返嚟嗰堆嚟自唔同來源(RSS/HN/Finnhub general/Finnhub
   company)嘅原始資料,經 `clean_and_prepare()` relevance filter + 去重
   之後,數量啱唔啱、邊幾條先留低。
2. Finnhub company news(已知邊間公司) vs 其他來源(要靠關鍵字/公司名估)
   最終喺 DB 度嘅 `news_type`/company連結係咪岩。
3. 冧幂等(idempotency)——同一個 `run()` call 兩次,第二次應該全部因為
   `News.url` 已存在而跳過,唔會插重複行(呢個對「每日排程都攞返最近
   一兩日新聞,同前一日會有重疊」呢個現實情況好重要)。
4. 成條鏈一路去到 Mind Map——攞其中一條由 `run()` 寫入嘅新聞,用假LLM
   跑 `process_news_for_concepts()`,確認由「fetch 到嘅原始新聞」到
   「Mind Map 入面嘅 concept/relation」呢個完整旅程真係行得通
   (即使正式運作嗰陣呢兩步係分開,冇自動連埋一齊跑)。

前提: DATABASE_URL 指住嘅 PostgreSQL 要已經開緊機,並且已經
`alembic upgrade head`。

執行: pytest tests/test_pipeline_integration.py -v
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import pytest

from app.etl import fetch_news, run_daily
from app.etl.extract_concepts import process_news_for_concepts
from app.etl.LLM_analyze import ExtractedRelation, ExtractedTheme, ExtractionResult
from app.manager.db_manager import DatabaseManager


class FakeFeedParserDict(dict):
    """假嘅 feedparser.parse() 回傳值：支援 .get()(dict自帶) 同 .entries(屬性)。"""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


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
    db.delete_company(company.company_id)


def test_run_daily_end_to_end_with_fake_sources(monkeypatch, db, seeded_company):
    ticker = seeded_company.ticker
    fixed_ts = int(time.time())

    # ---- 假 RSS(TechCrunch/The Verge/Ars Technica/VentureBeat/MIT Tech Review + HN) ----
    # 特登喺 VentureBeat 度放一條同 TechCrunch 完全一樣 url 嘅「重複報導」,
    # 用嚟驗證 dedup；HN 度放一條相關(AI chip)、一條唔相關(pizza recipe)，
    # 用嚟驗證 relevance filter。
    rss_content_by_domain = {
        "techcrunch.com": [
            {
                "title": "OpenAI announces new model",
                "link": "https://techcrunch.com/openai-new-model",
                "summary": "AI breakthrough in language models",
            }
        ],
        "theverge.com": [
            {
                "title": "GPU shortage continues amid AI demand",
                "link": "https://theverge.com/gpu-shortage",
                "summary": "chip demand rises",
            }
        ],
        "arstechnica.com": [],
        "venturebeat.com": [
            {
                "title": "OpenAI announces new model",  # 同 TechCrunch 果條一樣嘅 url -> 應該被dedup
                "link": "https://techcrunch.com/openai-new-model",
                "summary": "duplicate report via another outlet",
            }
        ],
        "technologyreview.com": [],
    }
    hn_entries = [
        {
            "title": "Show HN: my new AI chip startup",
            "link": "https://hn.example.com/ai-chip-startup",
            "summary": "",
        },
        {
            "title": "Ask HN: best pizza recipe",  # 同AI/Tech冇關 -> 應該被relevance filter篩走
            "link": "https://hn.example.com/pizza-recipe",
            "summary": "",
        },
    ]

    def fake_feedparser_parse(url):
        for domain, entries in rss_content_by_domain.items():
            if domain in url:
                return FakeFeedParserDict({"bozo": False, "entries": entries})
        if "hnrss.org" in url:
            return FakeFeedParserDict({"bozo": False, "entries": hn_entries})
        return FakeFeedParserDict({"bozo": False, "entries": []})

    # ---- 假 Finnhub(general technology news + 你已追蹤緊嗰個ticker嘅company news) ----
    def fake_requests_get(url, params=None, timeout=None):
        if url.endswith("/company-news"):
            symbol = (params or {}).get("symbol")
            if symbol == ticker:
                return FakeResponse(
                    [
                        {
                            "headline": f"{symbol} stock rises on AI chip demand",
                            "url": f"https://finnhub.example.com/company/{symbol}",
                            "summary": "",
                            "datetime": fixed_ts,
                        }
                    ]
                )
            return FakeResponse([])
        if url.endswith("/news"):
            return FakeResponse(
                [
                    {
                        "headline": "Semiconductor foundry expands capacity",
                        "url": "https://finnhub.example.com/general/1",
                        "summary": "",
                        "datetime": fixed_ts,
                    }
                ]
            )
        return FakeResponse([])

    monkeypatch.setattr("feedparser.parse", fake_feedparser_parse)
    monkeypatch.setattr("requests.get", fake_requests_get)
    monkeypatch.setattr(fetch_news.settings, "FINNHUB_API_KEY", "fake-key")

    created_news_ids: list[int] = []
    created_concept_ids: list[int] = []

    try:
        # ------------------------------------------------------------- 第一次執行
        stats_1 = run_daily.run()

        # 5條應該入到DB：TechCrunch(AI) + The Verge(GPU/chip) + HN(AI chip startup)
        # + Finnhub general(semiconductor) + Finnhub company(已知ticker)。
        # VentureBeat 嗰條(同TechCrunch同url)俾dedup刪咗；HN pizza嗰條俾relevance filter篩走。
        assert stats_1["inserted"] == 5
        assert stats_1["skipped_existing"] == 0
        assert stats_1["skipped_invalid"] == 0

        # ---- 驗證關鍵字/dedup/filter 真係生效 ----
        assert len(db.search_news(keyword="OpenAI announces new model")) == 1  # 冇因為dedup失敗而插咗兩次
        assert len(db.search_news(keyword="pizza recipe")) == 0  # relevance filter 篩走咗

        # ---- 驗證 Finnhub company news 正確連結返公司、其他來源冇亂咁連 ----
        techcrunch_news = db.search_news(keyword="OpenAI announces new model")[0]
        assert techcrunch_news.news_type == "industry"  # 冇提到已知公司，唔應該有company link
        created_news_ids.append(techcrunch_news.news_id)

        finnhub_company_news = db.search_news(keyword=f"{ticker} stock rises")[0]
        assert finnhub_company_news.news_type == "company"
        full = db.get_news_full(finnhub_company_news.news_id)
        linked_tickers = [link.company.ticker for link in full.company_links]
        assert linked_tickers == [ticker]
        linked_tags = [link.tag.tag_name for link in full.tag_links]
        assert linked_tags == ["AI"]
        created_news_ids.append(finnhub_company_news.news_id)

        for keyword in ["GPU shortage", "AI chip startup", "Semiconductor foundry"]:
            matches = db.search_news(keyword=keyword)
            assert len(matches) == 1
            created_news_ids.append(matches[0].news_id)

        # ------------------------------------------------------------- 第二次執行(冧幂等)
        # 假來源回傳返一樣嘅資料(url一樣)，模擬「排程隔一日又攞到同一批新聞」，
        # 應該全部因為url已存在而skip，唔會插多次。
        stats_2 = run_daily.run()
        assert stats_2["inserted"] == 0
        assert stats_2["skipped_existing"] == 5
        assert len(db.search_news(keyword="OpenAI announces new model")) == 1  # 仲係得一條，冇插多咗

        # --------------------------------------------------- 成條鏈跑到 Mind Map
        # 攞返其中一條(Finnhub company news，已經有連結公司)，用假LLM/embedding
        # 跑 process_news_for_concepts()，證明「fetch 到嘅原始新聞」一路到
        # 「Mind Map concept/relation」呢個完整旅程真係兜得埋。
        def fake_llm_extract_fn(*, article_title, article_content, known_companies, grounding_themes, max_relations):
            assert any(c["ticker"] == ticker for c in known_companies)
            return ExtractionResult(
                themes=[ExtractedTheme(name="AI晶片需求上升", description="AI帶動晶片需求")],
                relations=[
                    ExtractedRelation(
                        from_theme="AI晶片需求上升",
                        to_type="company",
                        to_ref=ticker,
                        relation_type="benefits",
                        polarity="positive",
                        confidence=0.7,
                        reasoning="pipeline整合測試",
                    )
                ],
            )

        def fake_embed_fn(texts):
            # 用隨機起點嘅one-hot向量(唔用固定index)，避免同其他test留低嘅
            # concept embedding撞埋一齊，令dedup誤判做「搵到相似concept」。
            vectors = []
            for _ in texts:
                seed = uuid.uuid4().int % 1536
                vec = [0.0] * 1536
                vec[seed] = 1.0
                vectors.append(vec)
            return vectors

        extraction_stats = process_news_for_concepts(
            db,
            finnhub_company_news.news_id,
            llm_extract_fn=fake_llm_extract_fn,
            embed_fn=fake_embed_fn,
        )
        assert extraction_stats["themes_created"] == 1
        assert extraction_stats["relations_reinforced"] == 1

        # 用theme名exact match(唔靠embedding round-trip)搵返啱啱由呢單新聞抽取出嚟嗰個concept
        theme_concepts = db.list_concepts(concept_type="theme", name="AI晶片需求上升")
        assert len(theme_concepts) == 1
        theme_concept = theme_concepts[0]
        created_concept_ids.append(theme_concept.concept_id)
        company_concept, _ = db.get_or_create_company_concept(seeded_company.company_id)
        created_concept_ids.append(company_concept.concept_id)
        relation = db.find_relation(
            theme_concept.concept_id, company_concept.concept_id, "benefits", polarity="positive"
        )
        assert relation is not None
        evidence = db.list_relation_evidence(relation.relation_id)
        assert evidence[0].news_id == finnhub_company_news.news_id

    finally:
        # ------------------------------------------------------------------- Cleanup
        for concept_id in created_concept_ids:
            db.delete_concept(concept_id)
        for news_id in created_news_ids:
            db.delete_news(news_id)
