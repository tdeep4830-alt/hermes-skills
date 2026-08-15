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
from app.etl.fetch_news import fetch_all, fetch_content_from_url, CONTENT_CLASS_BY_DOMAIN  # noqa: E402


db = DatabaseManager()

class FavorableNewsItem(TypedDict):
    news_id: int
    title: str
    published_at: datetime
    url: Optional[str]
    relevance: float


def ingest_news(item: dict[str], *, source: Optional[str] = None, url: Optional[str] = None) -> int:
    """
    分析一則新聞原文(事實/事件報導)並存入 Supabase：LLM 抽 title/description/sentiment/
    tickers/tags -> 冇嘅公司自動用 yfinance + SEC 10-K 建檔 -> 寫 news + NewsCompanyLink/
    NewsTagLink -> 即時 embed description。
    回傳 news_id。
    """
    text = fetch_content_from_url(url, content_class=CONTENT_CLASS_BY_DOMAIN.get(source, ""))
    item["text"] = text
    news = analyze_and_save(
        "news", item.get("text"), source=source, url=url, published_at=item.get("published_at")
    )
    return news.news_id


def ingest_article(item: dict[str], *, source: Optional[str] = None, url: Optional[str] = None) -> int:
    """
    分析一篇分析/觀點文章(有 thesis + conclusion,例如 Seeking Alpha 風格長文)並存入
    Supabase。步驟同 ingest_news,額外存埋 thesis/conclusion,並多 embed 一次 thesis。
    回傳 news_id(article 底層都係一行 news)。
    """
    text = fetch_content_from_url(url, content_class=CONTENT_CLASS_BY_DOMAIN.get(source, ""))
    item["text"] = text
    article = analyze_and_save(
        "article", item.get("text"), source=source, url=url, published_at=item.get("published_at")
    )
    return article.news_id



def run_matching() -> None:
    """對所有新聞跑一次 Layer 2/3 matching。兩個 matcher 都係 idempotent（淨係補未覆蓋嘅
    match），重複執行唔會產生重複 row，所以呢度淨係簡單咁攞晒全部 news_id 嚟跑,
    唔使額外追蹤邊啲已經 match 過。"""
    _run_matching()


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

def fetch_news() -> list[dict[str, str]]:
    """
    從 Finnhub/其他 source 攞新聞,存入 Supabase,並做 embedding。
    回傳一個 list,每個 item 係 {"news_id", "title", "published_at", "url", "relevance"}。
    """
    return fetch_all()

if __name__ == "__main__":
    # 本地測試用,唔好喺 production cron 用,因為會攞晒所有新聞去跑 matching,太慢。
    items = fetch_news()
    for item in items:
        print
        news_content = fetch_content_from_url(item["url"], content_class=CONTENT_CLASS_BY_DOMAIN.get(item.get("source", ""), ""))
        print(f"攞到新聞正文，長度 {len(news_content)} 字元: {item['url']}，由 {item.get('source', 'unknown source')} 發佈")
        