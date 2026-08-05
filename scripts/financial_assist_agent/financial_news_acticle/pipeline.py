"""
Hermes Agent 專用嘅薄封裝層,包住 stock_news_db 個 pipeline,淨係露出 4 個 function：
ingest_news / ingest_article / run_matching / find_favorable_news。

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

_STOCK_NEWS_DB_ROOT = Path(__file__).resolve().parent.parent / "stock_news_db"
if str(_STOCK_NEWS_DB_ROOT) not in sys.path:
    sys.path.insert(0, str(_STOCK_NEWS_DB_ROOT))

load_dotenv(_STOCK_NEWS_DB_ROOT / ".env")

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


if __name__ == "__main__":
    news_id = ingest_news("Galaxy Digital (GLXY) and Bank of New York Mellon (BNY) have strategically collaborated to advance digital asset infrastructure for institutional markets, including support for staking. BNY's digital asset custody platform with Galaxy's proof-of-stake network aims to help customers participate in staking through a secure, streamlined workflow. Additionally, Galaxy will serve as a design partner to further BNY's digital asset platform infrastructure. With the addition of staking, we will be providing clients with a more comprehensive digital asset custody offering built on the governance, controls, and resiliency they expect from BNY, said BNY's chief product and innovation officer, Carolyn Weinberg. Galaxy has spent years building the institutional-grade infrastructure to drive that shift, including staking. Our collaboration with BNY brings that work into a framework the worlds largest institutions can trust. As a design partner on BNYs platform infrastructure, we are helping shape the foundation on which these services will run, said Steve Kurz, global co-head of digital assets at Galaxy.")
    
    print(f"News ingested successfully. News ID: {news_id}")

