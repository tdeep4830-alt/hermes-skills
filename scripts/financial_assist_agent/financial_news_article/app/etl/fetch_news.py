"""
負責由外部來源攞返「原始」AI/Tech 相關新聞，唔做 clean/dedup/公司配對
(嗰啲交俾 clean_news.py 做)。

刻意收窄用三類全部免費、支援 RSS 或 API(唔使爬蟲)嘅來源，覆蓋面對準
AI/Tech(唔想成個 pipeline 咩新聞都收，先污染晒個 Mind Map)：

1. RSS —— 幾個大型科技媒體嘅 AI 分類 feed(TechCrunch/The Verge/
   Ars Technica/VentureBeat/MIT Technology Review)。全部公開、免費、
   唔使 API key，本身已經係編輯精選過嘅 AI 分類內容。
2. Finnhub —— 免費 API key(https://finnhub.io/register)，用嚟攞：
   - 大盤 technology 分類新聞(/news?category=technology)
   - 你 DB 已經有嘅公司(Company.ticker)嘅公司專屬新聞(/company-news)——
     呢類新聞由 Finnhub 已經知道係邊間公司，唔使靠關鍵字估，準過RSS。
   免費層：大約 60 calls/min，但條款寫明淨係俾「個人、非商業」用途，
   自己用嚟做研究冇問題，日後想商業化呢個 project 就要留意升級。
3. Hacker News —— 用 hnrss.org(免費、冇 API key)攞 front page，
   反映緊科技社群熱度，幫手發現主流媒體未必即刻報導、但科技圈已經開始
   討論緊嘅嘢(例如新 model/新 chip 發布)。留意 HN front page 唔淨係
   AI/Tech，一定要經 clean_news.py 嘅關鍵字 filter 先可以入 DB。

每個 fetch 函數都盡量做到「單一個來源攞唔到唔會累事成個 pipeline 死」——
攞失敗就 log 個 warning/exception，回傳空 list，等其他來源照跑。

每個 fetch 函數回傳嘅每個 item 用統一格式：
    {
        "title": str,
        "url": str,
        "summary": str,
        "source": str,
        "published_at": datetime,
        "raw_source_type": "rss" | "hn" | "finnhub",
        "known_tickers": list[str],   # 淨係 Finnhub company news 先會填，其他來源係 []
    }
"""
from __future__ import annotations
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from app.config import settings
from app.manager.db_manager import DatabaseManager
from app.etl.clean_news import clean_and_prepare
from app.etl.load_news import _news_url_exists
from app.etl.LLM_analyze import AI_analyze, _ANALYSIS_NEWS_SYSTEM_PROMPT, _ANALYSIS_ARTICLE_SYSTEM_PROMPT
from app.etl.fetch_company import save_company, _as_list

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- RSS 來源
# 全部係公開、免費、唔使 API key 嘅 AI 分類 feed。
AI_TECH_RSS_FEEDS: dict[str, str] = {
    "TechCrunch AI": "https://techcrunch.com/category/artificial-intelligence/feed/",
    "The Verge AI": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
    "Ars Technica AI": "https://arstechnica.com/ai/feed/",
    "VentureBeat AI": "https://venturebeat.com/category/ai/feed/",
    "MIT Technology Review AI": "https://www.technologyreview.com/topic/artificial-intelligence/feed",
}

# fetch_content_from_url() 攞正文嗰陣，用邊個 CSS class 揀返段落——用網域嚟
# 揀（唔係用 source 個顯示名），因為 HTML 結構係跟緊網站本身，唔跟 caller
# 隨便傳落嚟嘅 source 標籤（The Verge 用 React 動態渲染，暫時冇對應 class）。
CONTENT_CLASS_BY_DOMAIN: dict[str, str] = {
    "techcrunch.com": "wp-block-paragraph",
    "arstechnica.com": "post-content post-content-double",
    "venturebeat.com": "article-body whitespace-pre-wrap",
    "technologyreview.com": "article-content",
}

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
HN_RSS_URL = "https://hnrss.org/frontpage"


def _strip_html(text: str) -> str:
    """RSS entry 嘅 summary 成日夾埋 HTML tag，攞個大概文字版就夠。"""
    return re.sub(r"<[^<]+?>", " ", text or "").strip()


def _parse_entry_datetime(entry: Any) -> datetime:
    struct = entry.get("published_parsed") or entry.get("updated_parsed")
    if struct:
        return datetime.fromtimestamp(time.mktime(struct), tz=timezone.utc)
    return datetime.now(timezone.utc)


def fetch_rss_feeds(feeds: Optional[dict[str, str]] = None) -> list[dict[str, Any]]:
    """
    用 feedparser 逐個攞返晒 RSS feed 嘅 entry。單一個 feed 攞唔到
    (網絡問題/feed 轉咗 URL)唔會累事成個 pipeline 死，淨係 log 個
    warning 繼續攞第個。
    """
    import feedparser

    feeds = feeds if feeds is not None else AI_TECH_RSS_FEEDS
    items: list[dict[str, Any]] = []
    for source_name, feed_url in feeds.items():
        try:
            parsed = feedparser.parse(feed_url)
            if parsed.get("bozo") and not parsed.entries:
                logger.warning("RSS feed 攞唔到/parse失敗: %s (%s)", source_name, feed_url)
                continue
            for entry in parsed.entries:
                items.append(
                    {
                        "title": (entry.get("title") or "").strip(),
                        "url": (entry.get("link") or "").strip(),
                        "summary": _strip_html(entry.get("summary", "")),
                        "source": source_name,
                        "published_at": _parse_entry_datetime(entry),
                        "raw_source_type": "rss",
                        "known_tickers": [],
                    }
                )
        except Exception:
            logger.exception("Fetch RSS feed 失敗: %s (%s)", source_name, feed_url)
    return items


# ------------------------------------------------------------- Hacker News
def fetch_hacker_news(min_points: int = 50, limit: int = 50) -> list[dict[str, Any]]:
    """
    用 hnrss.org(免費、冇 API key)攞返 HN front page，用 min_points
    篩走啲討論度未算熱嘅 submission。留意 HN 淨係反映科技社群興趣，
    唔淨係 AI/Tech，所以呢度攞到嘅嘢一定要經 clean_news.py 嘅關鍵字
    filter 先可以入 DB。
    """
    import feedparser

    url = f"{HN_RSS_URL}?points={min_points}"
    items: list[dict[str, Any]] = []
    try:
        parsed = feedparser.parse(url)
        for entry in parsed.entries[:limit]:
            items.append(
                {
                    "title": (entry.get("title") or "").strip(),
                    "url": (entry.get("link") or "").strip(),
                    "summary": _strip_html(entry.get("summary", "")),
                    "source": "Hacker News",
                    "published_at": _parse_entry_datetime(entry),
                    "raw_source_type": "hn",
                    "known_tickers": [],
                }
            )
    except Exception:
        logger.exception("Fetch Hacker News 失敗")
    return items


# --------------------------------------------------------------- Finnhub
def _finnhub_item_to_dict(item: dict, *, source: str, tickers: list[str]) -> dict[str, Any]:
    unix_ts = item.get("datetime")
    published_at = (
        datetime.fromtimestamp(unix_ts, tz=timezone.utc) if unix_ts else datetime.now(timezone.utc)
    )
    return {
        "title": (item.get("headline") or "").strip(),
        "url": (item.get("url") or "").strip(),
        "summary": (item.get("summary") or "").strip(),
        "source": source,
        "published_at": published_at,
        "raw_source_type": "finnhub",
        "known_tickers": tickers,
    }


def fetch_finnhub_general_news(category: str = "technology") -> list[dict[str, Any]]:
    """大盤分類新聞(免費層有，但要有 FINNHUB_API_KEY)。"""
    import requests

    if not settings.FINNHUB_API_KEY:
        logger.info("FINNHUB_API_KEY 未設定，跳過 Finnhub general news")
        return []

    try:
        resp = requests.get(
            f"{FINNHUB_BASE_URL}/news",
            params={"category": category, "token": settings.FINNHUB_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        raw_items = resp.json()
    except Exception:
        logger.exception("Fetch Finnhub general news 失敗")
        return []

    return [_finnhub_item_to_dict(item, source="Finnhub", tickers=[]) for item in raw_items]


def fetch_finnhub_company_news(tickers: list[str], *, days_back: int = 1) -> list[dict[str, Any]]:
    """
    逐個 ticker 攞返公司專屬新聞(免費層有)。因為已知邊個 ticker，
    呢啲新聞可以直接同對應公司連結，唔使靠關鍵字/公司名做 matching。
    """
    import requests

    if not settings.FINNHUB_API_KEY:
        logger.info("FINNHUB_API_KEY 未設定，跳過 Finnhub company news")
        return []

    today = datetime.now(timezone.utc).date()
    from_date = today - timedelta(days=days_back)

    items: list[dict[str, Any]] = []
    for ticker in tickers:
        try:
            resp = requests.get(
                f"{FINNHUB_BASE_URL}/company-news",
                params={
                    "symbol": ticker,
                    "from": from_date.isoformat(),
                    "to": today.isoformat(),
                    "token": settings.FINNHUB_API_KEY,
                },
                timeout=10,
            )
            resp.raise_for_status()
            raw_items = resp.json()
        except Exception:
            logger.exception("Fetch Finnhub company news 失敗: %s", ticker)
            continue
        items.extend(_finnhub_item_to_dict(item, source="Finnhub", tickers=[ticker]) for item in raw_items)
    return items


def fetch_all(
    *,
    tracked_tickers: Optional[list[str]] = None,
    rss_feeds: Optional[dict[str, str]] = None,
    hn_min_points: int = 50,
    finnhub_days_back: int = 1,
) -> list[dict[str, Any]]:
    """一步過攞晒三類來源，回傳未去重/未 filter 嘅原始清單。"""
    items: list[dict[str, Any]] = []
    items.extend(fetch_rss_feeds(rss_feeds))
    items.extend(fetch_finnhub_general_news())
    if tracked_tickers:
        items.extend(fetch_finnhub_company_news(tracked_tickers, days_back=finnhub_days_back))
    return items

def fetch_content_from_url(url: str, content_class: Optional[str] = None) -> str:
    """
    用 requests + BeautifulSoup 攞返一個 URL 嘅正文內容。
    淨係揀正文段落嘅 CSS class（唔用 `soup.get_text()` 成頁攞，因為會連
    nav/廣告/footer 嘅雜訊都夾埋一齊攞返嚟）。

    `content_class` 冇傳就用返 `CONTENT_CLASS_BY_DOMAIN` 靠 url 個網域自動揀；
    網域唔喺個 dict 度(例如未支援嘅網站)就攞唔到嘢，返回空字串——刻意唔 fallback
    做全頁 `get_text()`，唔想淨係為咗有嘢好過冇嘢就摻返晒啲雜訊落個 content。
    """
    import requests
    from bs4 import BeautifulSoup
    from urllib.parse import urlparse

    if content_class is None:
        domain = urlparse(url).netloc.removeprefix("www.")
        content_class = CONTENT_CLASS_BY_DOMAIN.get(domain)
        if content_class is None:
            logger.warning("網域 %s 未有對應嘅 content class，冇得攞正文: %s", domain, url)
            return ""

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        paragraphs = soup.find_all(class_=content_class)
        if not paragraphs:
            logger.warning("URL 冇搵到 class=%r 嘅段落: %s", content_class, url)
            return ""
        if len(paragraphs) < 0:
            paragraphs = soup.find_all("p")  # fallback 全頁攞 p tag，唔想冇嘢好過冇嘢
            logger.warning("URL 冇搵到 class=%r 嘅段落，fallback 全頁攞 p tag: %s", content_class, url)
        return "\n".join(p.get_text(separator=" ", strip=True) for p in paragraphs)
    except Exception:
        logger.exception("Fetch content from URL 失敗: %s", url)
        return ""

def daily_news_fetch() -> dict[str, int]:
    db = DatabaseManager()
    try:
        known_companies = [
            {"company_id": c.company_id, "ticker": c.ticker, "name_en": c.name_en}
            for c in db.list_companies(limit=1000)
        ]
        tracked_tickers = [c["ticker"] for c in known_companies]

        logger.info("開始 fetch 新聞(RSS + Hacker News + Finnhub)...")
        raw_items = fetch_all(tracked_tickers=tracked_tickers)
        logger.info("Fetch 完成，一共 %d 條原始新聞", len(raw_items))

        cleaned_items = clean_and_prepare(raw_items, known_companies)
        logger.info(
            "Clean 完成(AI/Tech relevance filter + dedup)，剩返 %d 條(篩走 %d 條)",
            len(cleaned_items),
            len(raw_items) - len(cleaned_items),
        )
        inserted = 0
        skipped_existing = 0
        skipped_no_content = 0
        failed = 0
        inserted_news_ids: list[int] = []

        for cleaned_item in cleaned_items[0:10]:
            logger.info("Cleaned item: %s", cleaned_item)
            url = cleaned_item["url"]

            if _news_url_exists(db, url):
                logger.info("News.url 已存在，跳過: %s", url)
                skipped_existing += 1
                continue

            try:
                content = fetch_content_from_url(url)
                if not content:
                    logger.warning("攞唔到正文內容，跳過: %s", url)
                    skipped_no_content += 1
                    continue
                cleaned_item["text"] = content
                AI_analysis = AI_analyze(cleaned_item["text"], model="deepseek-v4-flash", prompt=_ANALYSIS_NEWS_SYSTEM_PROMPT)

                company_ids = []
                for ticker in _as_list(AI_analysis.get("tickers")):
                    ticker = ticker.upper()
                    company = db.get_company_by_ticker(ticker)
                    if company is None:
                        # 冇呢間公司就用返 save_company 攞齊 yfinance/SEC/LLM 資料先存，
                        # 唔淨係得個 ticker 頂住個空殼 company。
                        save_company(ticker)
                        company = db.get_company_by_ticker(ticker)
                    if company is not None:
                        company_ids.append(company.company_id)

                cleaned_item["title"] = AI_analysis.get("title") or cleaned_item["title"]
                cleaned_item["description"] = AI_analysis.get("description") or cleaned_item["summary"]
                cleaned_item["news_type"] = AI_analysis.get("news_type", "company")
                cleaned_item["sentiment"] = AI_analysis.get("sentiment")
                cleaned_item["company_ids"] = company_ids
                cleaned_item["tags"] = _as_list(AI_analysis.get("tags"))

                saved_news = db.add_news(
                    title=cleaned_item["title"],
                    description=cleaned_item.get("description"),
                    content=cleaned_item["text"],
                    source=cleaned_item["source"],
                    url=cleaned_item["url"],
                    published_at=cleaned_item["published_at"],
                    news_type=cleaned_item.get("news_type", "company"),
                    sentiment=cleaned_item.get("sentiment"),
                    company_ids=cleaned_item.get("company_ids", []),
                    tag_names=cleaned_item.get("tags", []),
                )
                logger.info("已存入 DB: %s (news_id=%s)", cleaned_item["title"], saved_news.news_id)
                inserted += 1
                inserted_news_ids.append(saved_news.news_id)
            except Exception:
                # 單一條新聞攞內容/LLM分析/寫入失敗(例如 finish_reason='length')唔應該
                # 累成個 batch 死——log 低就跳去下一條，等其他新聞照樣處理得到。
                logger.exception("處理呢條新聞失敗，跳過: %s", url)
                failed += 1
                continue

        stats = {
            "raw_count": len(raw_items),
            "cleaned_count": len(cleaned_items),
            "inserted": inserted,
            "skipped_existing": skipped_existing,
            "skipped_no_content": skipped_no_content,
            "failed": failed,
            "inserted_news_ids": inserted_news_ids,
        }
        logger.info(
            "寫入完成：新增 %d 條，跳過(已存在) %d 條，跳過(冇內容) %d 條，處理失敗 %d 條",
            inserted, skipped_existing, skipped_no_content, failed,
        )

        return stats
    finally:
        db.dispose()

if __name__ == "__main__":
    stats = daily_news_fetch()
    logger.info("每日 fetch 新聞完成，統計: %s", stats)

        



