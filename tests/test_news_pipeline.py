"""
測試每日 AI/Tech 新聞 pipeline(fetch_news.py / clean_news.py / load_news.py)。

分幾部分:
1. clean_news.py 純function unit test —— relevance filter、去重、公司配對。
2. fetch_news.py 嘅純helper function(HTML strip、時間parse、Finnhub格式轉換)，
   同埋用monkeypatch假嘅feedparser.parse/requests.get去測試fetch_rss_feeds()/
   fetch_finnhub_*()嘅邏輯——唔使真係連網，亦唔會受呢個sandbox冇網絡出去嘅
   限制影響。
3. load_news.py integration test —— 用真實PostgreSQL驗證url去重、company_ids
   連結、AI tag自動建立。

執行: pytest tests/test_news_pipeline.py -v
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.etl import fetch_news
from app.etl.clean_news import (
    AI_TECH_KEYWORDS,
    clean_and_prepare,
    deduplicate,
    filter_ai_tech_relevant,
    is_ai_tech_relevant,
    match_companies,
)
from app.etl.load_news import load_news_items
from app.manager.db_manager import DatabaseManager

KNOWN_COMPANIES = [
    {"company_id": 1, "ticker": "NVDA", "name_en": "NVIDIA Corporation"},
    {"company_id": 2, "ticker": "TSM", "name_en": "Taiwan Semiconductor Manufacturing"},
]


# --------------------------------------------------------------------------
# 1. clean_news.py —— relevance / dedup / company matching
# --------------------------------------------------------------------------


def test_is_ai_tech_relevant_true_for_ai_keyword():
    item = {"title": "OpenAI 發布新一代模型", "summary": "呢個模型用咗最新嘅transformer架構"}
    assert is_ai_tech_relevant(item) is True


def test_is_ai_tech_relevant_true_for_chip_keyword():
    item = {"title": "TSMC 擴建晶圓廠", "summary": "回應對 semiconductor 嘅需求上升"}
    assert is_ai_tech_relevant(item) is True


def test_is_ai_tech_relevant_false_for_unrelated_news():
    item = {"title": "本地餐廳新開張", "summary": "提供多款地道美食"}
    assert is_ai_tech_relevant(item) is False


def test_is_ai_tech_relevant_true_when_known_tickers_present():
    # Finnhub company news 已知關聯緊邊間公司，一律當相關(唔靠關鍵字)
    item = {"title": "隨便一個標題，冇任何AI/Tech字眼", "summary": "", "known_tickers": ["NVDA"]}
    assert is_ai_tech_relevant(item) is True


def test_filter_ai_tech_relevant():
    items = [
        {"title": "AI晶片需求上升", "summary": ""},
        {"title": "本地餐廳新開張", "summary": ""},
        {"title": "隨便標題", "summary": "", "known_tickers": ["NVDA"]},
    ]
    filtered = filter_ai_tech_relevant(items)
    assert len(filtered) == 2
    assert filtered[0]["title"] == "AI晶片需求上升"
    assert filtered[1]["known_tickers"] == ["NVDA"]


def test_deduplicate_by_exact_url():
    items = [
        {"title": "AI新聞A", "url": "https://example.com/a"},
        {"title": "AI新聞A(轉載)", "url": "https://example.com/a"},  # 同url
        {"title": "AI新聞B", "url": "https://example.com/b"},
    ]
    deduped = deduplicate(items)
    assert len(deduped) == 2
    assert deduped[0]["title"] == "AI新聞A"
    assert deduped[1]["title"] == "AI新聞B"


def test_deduplicate_by_similar_title():
    items = [
        {"title": "NVIDIA announces new AI chip for data centers", "url": "https://a.com/1"},
        {"title": "NVIDIA announces new AI chip for data centers.", "url": "https://b.com/2"},  # 幾乎一樣嘅標題,唔同url
        {"title": "Completely unrelated headline about something else", "url": "https://c.com/3"},
    ]
    deduped = deduplicate(items)
    assert len(deduped) == 2
    urls = {item["url"] for item in deduped}
    assert urls == {"https://a.com/1", "https://c.com/3"}


def test_match_companies_by_ticker_word_boundary():
    item = {"title": "NVDA hits new high on AI demand", "summary": ""}
    matched = match_companies(item, KNOWN_COMPANIES)
    assert matched == [1]


def test_match_companies_ticker_adjacent_to_chinese_text_no_space():
    # Python嘅unicode regex當CJK字元都係\w，所以`\b`喺「NVDA股價」呢種
    # 冇空格分隔嘅寫法度完全搵唔到boundary——呢條test防止呢個bug翻生。
    item = {"title": "NVDA股價創新高，AI晶片需求持續強勁", "summary": ""}
    matched = match_companies(item, KNOWN_COMPANIES)
    assert matched == [1]


def test_is_ai_tech_relevant_true_for_keyword_adjacent_to_chinese_no_space():
    item = {"title": "AI晶片需求上升", "summary": ""}
    assert is_ai_tech_relevant(item) is True


def test_match_companies_ticker_does_not_match_substring():
    # "NVDAX" 唔應該當match "NVDA"(word boundary保護)
    item = {"title": "NVDAX is an unrelated fund ticker", "summary": ""}
    matched = match_companies(item, KNOWN_COMPANIES)
    assert matched == []


def test_match_companies_by_company_name():
    item = {"title": "Taiwan Semiconductor Manufacturing 擴產", "summary": ""}
    matched = match_companies(item, KNOWN_COMPANIES)
    assert matched == [2]


def test_match_companies_uses_known_tickers_when_present():
    # Finnhub company news 已知edge case：文字入面完全冇提到公司名，但known_tickers已經話咗係邊間
    item = {"title": "隨便標題", "summary": "", "known_tickers": ["TSM"]}
    matched = match_companies(item, KNOWN_COMPANIES)
    assert matched == [2]


def test_clean_and_prepare_end_to_end():
    raw_items = [
        {
            "title": "NVDA AI chip demand surges",
            "url": "https://a.com/1",
            "summary": "",
            "known_tickers": [],
        },
        {
            "title": "NVDA AI chip demand surges!",  # 重複(相似title)
            "url": "https://a-mirror.com/1",
            "summary": "",
            "known_tickers": [],
        },
        {"title": "本地餐廳新開張", "url": "https://c.com/3", "summary": "", "known_tickers": []},
    ]
    cleaned = clean_and_prepare(raw_items, KNOWN_COMPANIES)
    assert len(cleaned) == 1
    assert cleaned[0]["company_ids"] == [1]


# --------------------------------------------------------------------------
# 2. fetch_news.py —— 純helper + monkeypatch假嘅feedparser/requests(唔連真網)
# --------------------------------------------------------------------------


class FakeFeedParserDict(dict):
    """假嘅 feedparser.parse() 回傳值：支援 .get()(dict自帶) 同 .entries(屬性)。"""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name)


def test_strip_html():
    assert fetch_news._strip_html("<p>Hello <b>World</b></p>") == "Hello  World"


def test_parse_entry_datetime_uses_published_parsed():
    import time

    struct = time.struct_time((2026, 1, 1, 12, 0, 0, 0, 0, 0))
    entry = {"published_parsed": struct}
    result = fetch_news._parse_entry_datetime(entry)
    assert result.year == 2026 and result.month == 1 and result.day == 1


def test_parse_entry_datetime_falls_back_to_now():
    result = fetch_news._parse_entry_datetime({})
    assert (datetime.now(timezone.utc) - result).total_seconds() < 5


def test_finnhub_item_to_dict():
    raw = {"headline": "NVDA hits record high", "url": "https://x.com/1", "summary": "sum", "datetime": 1700000000}
    item = fetch_news._finnhub_item_to_dict(raw, source="Finnhub", tickers=["NVDA"])
    assert item["title"] == "NVDA hits record high"
    assert item["known_tickers"] == ["NVDA"]
    assert item["raw_source_type"] == "finnhub"
    assert item["published_at"].tzinfo is not None


def test_fetch_rss_feeds_with_fake_feedparser(monkeypatch):
    def fake_parse(url):
        return FakeFeedParserDict(
            {
                "bozo": False,
                "entries": [
                    {
                        "title": "Fake AI article",
                        "link": "https://fake.com/1",
                        "summary": "<p>fake summary</p>",
                    }
                ],
            }
        )

    monkeypatch.setattr("feedparser.parse", fake_parse)

    items = fetch_news.fetch_rss_feeds({"Fake Source": "https://fake.com/feed"})
    assert len(items) == 1
    assert items[0]["title"] == "Fake AI article"
    assert items[0]["source"] == "Fake Source"
    assert items[0]["raw_source_type"] == "rss"
    assert "fake summary" in items[0]["summary"]


def test_fetch_rss_feeds_one_bad_feed_does_not_crash_others(monkeypatch):
    def fake_parse(url):
        if "bad" in url:
            raise RuntimeError("network error")
        return FakeFeedParserDict({"bozo": False, "entries": [{"title": "ok", "link": "https://ok.com/1"}]})

    monkeypatch.setattr("feedparser.parse", fake_parse)

    items = fetch_news.fetch_rss_feeds({"Bad": "https://bad.com/feed", "Good": "https://good.com/feed"})
    assert len(items) == 1
    assert items[0]["title"] == "ok"


def test_fetch_finnhub_general_news_skips_when_no_api_key(monkeypatch):
    monkeypatch.setattr(fetch_news.settings, "FINNHUB_API_KEY", "")
    assert fetch_news.fetch_finnhub_general_news() == []


def test_fetch_finnhub_general_news_with_fake_requests(monkeypatch):
    monkeypatch.setattr(fetch_news.settings, "FINNHUB_API_KEY", "fake-key")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"headline": "AI news", "url": "https://x.com/1", "summary": "s", "datetime": 1700000000}]

    monkeypatch.setattr("requests.get", lambda *a, **kw: FakeResponse())

    items = fetch_news.fetch_finnhub_general_news()
    assert len(items) == 1
    assert items[0]["title"] == "AI news"
    assert items[0]["known_tickers"] == []


def test_fetch_finnhub_company_news_with_fake_requests(monkeypatch):
    monkeypatch.setattr(fetch_news.settings, "FINNHUB_API_KEY", "fake-key")

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"headline": "NVDA news", "url": "https://x.com/2", "summary": "s", "datetime": 1700000000}]

    monkeypatch.setattr("requests.get", lambda *a, **kw: FakeResponse())

    items = fetch_news.fetch_finnhub_company_news(["NVDA"])
    assert len(items) == 1
    assert items[0]["known_tickers"] == ["NVDA"]


def test_fetch_finnhub_company_news_one_ticker_failure_does_not_crash_others(monkeypatch):
    monkeypatch.setattr(fetch_news.settings, "FINNHUB_API_KEY", "fake-key")

    def fake_get(url, params=None, timeout=None):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                if params["symbol"] == "BAD":
                    raise ValueError("bad json")
                return [{"headline": "ok news", "url": "https://x.com/3", "datetime": 1700000000}]

        return FakeResponse()

    monkeypatch.setattr("requests.get", fake_get)

    items = fetch_news.fetch_finnhub_company_news(["BAD", "NVDA"])
    assert len(items) == 1
    assert items[0]["known_tickers"] == ["NVDA"]


# --------------------------------------------------------------------------
# 3. load_news.py —— 用真實 PostgreSQL 驗證 url 去重 + company連結 + AI tag
# --------------------------------------------------------------------------


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


def test_load_news_items_inserts_and_links_company(db, seeded_company):
    unique_url = f"https://example.com/{uuid.uuid4().hex}"
    items = [
        {
            "title": "測試AI新聞",
            "url": unique_url,
            "summary": "測試內容",
            "source": "Fake Source",
            "published_at": datetime.now(timezone.utc),
            "company_ids": [seeded_company.company_id],
        }
    ]
    stats = load_news_items(db, items)
    assert stats == {"inserted": 1, "skipped_existing": 0, "skipped_invalid": 0}

    matching_news = db.search_news(keyword="測試AI新聞")
    assert len(matching_news) == 1
    news = matching_news[0]
    assert news.news_type == "company"

    full = db.get_news_full(news.news_id)
    linked_tickers = [link.company.ticker for link in full.company_links]
    linked_tags = [link.tag.tag_name for link in full.tag_links]
    assert linked_tickers == [seeded_company.ticker]
    assert linked_tags == ["AI"]

    db.delete_news(news.news_id)


def test_load_news_items_skips_existing_url(db, seeded_company):
    unique_url = f"https://example.com/{uuid.uuid4().hex}"
    item = {
        "title": "重複測試新聞",
        "url": unique_url,
        "summary": "",
        "source": "Fake Source",
        "published_at": datetime.now(timezone.utc),
        "company_ids": [],
    }
    stats_1 = load_news_items(db, [item])
    assert stats_1["inserted"] == 1

    stats_2 = load_news_items(db, [item])
    assert stats_2 == {"inserted": 0, "skipped_existing": 1, "skipped_invalid": 0}

    news_list = db.search_news(keyword="重複測試新聞")
    assert len(news_list) == 1
    db.delete_news(news_list[0].news_id)


def test_load_news_items_no_company_gets_industry_type(db):
    unique_url = f"https://example.com/{uuid.uuid4().hex}"
    items = [
        {
            "title": "無公司關聯嘅AI新聞",
            "url": unique_url,
            "summary": "",
            "source": "Fake Source",
            "published_at": datetime.now(timezone.utc),
            "company_ids": [],
        }
    ]
    stats = load_news_items(db, items)
    assert stats["inserted"] == 1

    news_list = db.search_news(keyword="無公司關聯嘅AI新聞")
    assert news_list[0].news_type == "industry"
    db.delete_news(news_list[0].news_id)


def test_load_news_items_skips_invalid_missing_title_or_url(db):
    items = [
        {"title": "", "url": "https://example.com/missing-title", "summary": "", "published_at": datetime.now(timezone.utc)},
        {"title": "冇URL嘅新聞", "url": "", "summary": "", "published_at": datetime.now(timezone.utc)},
    ]
    stats = load_news_items(db, items)
    assert stats == {"inserted": 0, "skipped_existing": 0, "skipped_invalid": 2}
