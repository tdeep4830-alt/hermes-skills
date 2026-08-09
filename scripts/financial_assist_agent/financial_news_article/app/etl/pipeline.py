"""
Hermes Agent 專用嘅薄封裝層,包住 stock_news_db 個 pipeline,淨係露出 4 個 function：
ingest_news / ingest_article / run_matching / extract_concepts。

stock_news_db 本身唔係一個安裝咗嘅 package,佢嘅 code 一定要喺 cwd=stock_news_db/ 之下
用 `python -m app.xxx` 先 import 得到(見 stock_news_db/README.md)。呢個 module 一 import
就將 stock_news_db/ 加落 sys.path,等呼叫方(Hermes)唔使理呢個限制,喺邊度 import 都得。

`app/config.py` 讀 `.env` 用嘅係 cwd-relative path,所以呢度仲要喺 import 任何 `app.*`
之前,明確用 `python-dotenv` 讀 `stock_news_db/.env` 落 os.environ,咪理呼叫方個 cwd 係邊。

真正嘅業務邏輯全部留喺 stock_news_db/app/ 入面,呢度唔重複實現任何嘢。
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional, TypedDict

from dotenv import load_dotenv

# financial_news_article/ 呢層本身唔係一個裝咗嘅 package,`app.*` 一定要用呢層做
# import root 先搵到。呼叫方(Hermes agent)嘅 cwd/sys.path 可以喺任何地方,
# 所以呢度明確將呢層加落 sys.path,等 `from app.etl...` 喺邊度 import 都得。
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# .env 實際放喺 repo root(financial_news_article/ 嘅太爺目錄),同樣唔理呼叫方個
# cwd 係邊,讀返呢條絕對路徑,等 DATABASE_URL/API key 一定讀得到。
load_dotenv(_PROJECT_ROOT.parent.parent.parent / ".env")


from app.etl.extract_concepts import process_news_for_concepts, process_article_for_concepts  # noqa: E402
from app.etl.run_daily import analyze_and_save  # noqa: E402
from app.etl.run_matching import run_matching as _run_matching  # noqa: E402
from app.manager.db_manager import DatabaseManager  # noqa: E402


db = DatabaseManager()

class FavorableNewsItem(TypedDict):
    news_id: int
    title: str
    published_at: datetime
    url: Optional[str]
    relevance: float


def ingest_news(text: str, *, source: Optional[str] = None, url: Optional[str] = None) -> int:
    """
    分析一則新聞原文(事實/事件報導)並存入 Supabase：LLM 抽 title/description/sentiment/
    tickers/tags -> 冇嘅公司自動用 yfinance + SEC 10-K 建檔 -> 寫 news + NewsCompanyLink/
    NewsTagLink -> 即時 embed description。
    回傳 news_id。
    """
    news = analyze_and_save(
        "news", text, source=source, url=url, published_at=datetime.now(timezone.utc)
    )
    return news.news_id


def ingest_article(text: str, *, source: Optional[str] = None, url: Optional[str] = None) -> int:
    """
    分析一篇分析/觀點文章(有 thesis + conclusion,例如 Seeking Alpha 風格長文)並存入
    Supabase。步驟同 ingest_news,額外存埋 thesis/conclusion,並多 embed 一次 thesis。
    回傳 news_id(article 底層都係一行 news)。
    """
    article = analyze_and_save(
        "article", text, source=source, url=url, published_at=datetime.now(timezone.utc)
    )
    return article.news_id


def _coerce_date(value: Optional[date | datetime | str]) -> date:
    """將 `date` 參數統一轉做 `datetime.date`,等呼叫方唔使理傳嘅係 str/datetime/date。"""
    if value is None:
        return datetime.now(timezone.utc).date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise TypeError(f"date 要係 None/date/datetime/ISO 格式 str,而唔係 {type(value).__name__}")


def run_matching(date: Optional[date | datetime | str] = None) -> None:
    """
    對 DB 入面全部新聞跑一次 News-Company(tag 規則 + embedding 語義比對) /
    News-Article matching。Idempotent,隨時可以重跑,唔會產生重複 link。
    夾埋一批新聞/文章 ingest 完之後先跑一次,唔好逐次 ingest 就即刻跑。

    `date`: 淨係處理呢日或之後嘅新聞,冇傳就預設今日(UTC)。接受 date/datetime/
    ISO 格式(YYYY-MM-DD) str 三種格式。
    """

    resolved_date = _coerce_date(date)
    _run_matching(resolved_date)


def new_extract_concepts(news_id: int) -> dict[str, int]:
    """
    幫一則新聞抽 theme/relation,寫入 Mind Map(Concept Graph):LLM 抽取 -> theme 去重
    (embedding cosine similarity)/新建 -> relation 強化(reinforce)或新增。

    要喺 ingest_news/ingest_article + run_matching 之後先跑——靠 NewsCompanyLink
    嚟判斷「呢則新聞已確認相關嘅公司」,等 LLM 淨係負責 theme + relation,唔使佢
    重新判斷邊間公司相關。

    回傳統計 dict：{"themes_created", "themes_reused", "relations_reinforced",
    "skipped_relations"}。
    """
    return process_news_for_concepts(db, news_id)

def article_extract_concepts(article_id: int) -> dict[str, int]:
    """
    幫一篇分析文章抽 theme/relation,寫入 Mind Map(Concept Graph):LLM 抽取 -> theme 去重
    (embedding cosine similarity)/新建 -> relation 強化(reinforce)或新增。

    要喺 ingest_article + run_matching 之後先跑——靠 NewsCompanyLink
    嚟判斷「呢篇文章已確認相關嘅公司」,等 LLM 淨係負責 theme + relation,唔使佢
    重新判斷邊間公司相關。

    回傳統計 dict：{"themes_created", "themes_reused", "relations_reinforced",
    "skipped_relations"}。
    """
    return process_article_for_concepts(db, article_id)