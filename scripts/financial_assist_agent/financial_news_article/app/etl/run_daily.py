"""
每日排程執行嘅入口。
本地測試： python -m app.etl.run_daily
未來部署：可以用 cron / Airflow 每日觸發呢個 script。
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from app.database import get_session
from app.manager.db_manager import DatabaseManager
from app.etl.embed_company_facts import embed_article, embed_all_facts, embed_news, embed_entity
from app.etl.LLM_analyze import AI_analyze, _ANALYSIS_NEWS_SYSTEM_PROMPT, _ANALYSIS_ARTICLE_SYSTEM_PROMPT
from app.etl.fetch_company import save_company, _as_list
from app.models import News
import logging
from app.etl.match_news_companies import match_news_to_companies


from app.etl.fetch_news import daily_news_fetch 


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)



def analyze_and_save(
    type_of_analysis: str,
    text: str,
    *,
    source: Optional[str] = None,
    url: Optional[str] = None,
    published_at: Optional[datetime] = None,
    model: str = "deepseek-v4-pro",
) -> News:
    """分析新聞原文，再連公司 / tag 一齊存入 DB。"""
    if type_of_analysis == "news":
        prompt = _ANALYSIS_NEWS_SYSTEM_PROMPT
    elif type_of_analysis == "article":
        prompt = _ANALYSIS_ARTICLE_SYSTEM_PROMPT
    else:
        raise ValueError(f"未知嘅 type_of_analysis: {type_of_analysis!r}，要 'news' 或者 'article'")

    db = DatabaseManager()
    analysis = AI_analyze(text, model=model, prompt=prompt)
    effective_published_at = published_at or datetime.now(timezone.utc)

    company_ids = []
    for ticker in _as_list(analysis.get("tickers")):
        ticker = ticker.upper()
        company = db.get_company_by_ticker(ticker)
        if company is None:
            # 冇呢間公司就用返 save_company 攞齊 yfinance/SEC/LLM 資料先存，
            # 唔淨係得個 ticker 頂住個空殼 company。
            save_company(ticker)
            company = db.get_company_by_ticker(ticker)
        if company is not None:
            company_ids.append(company.company_id)

    if type_of_analysis == "news":
        news = db.add_news(
            title=analysis["title"],
            description=analysis.get("description"),
            content=text,
            source=source,
            url=url,
            published_at=effective_published_at,
            news_type=analysis.get("news_type", "company"),
            sentiment=analysis.get("sentiment"),
            company_ids=company_ids,
            tag_names=_as_list(analysis.get("tags")),
        )
        try:
            embed_news(news.news_id, db=db)
        except Exception:
            logger.exception("即時 embed_news(%s) 失敗，等下次 embed_all_facts() batch job 補返", news.news_id)

        return news
    
    elif type_of_analysis == "article":
        article = db.add_analysis_article(
            title=analysis["title"],
            description=analysis.get("description"),
            content=text,
            source=source,
            url=url,
            published_at=effective_published_at,
            thesis=analysis.get("thesis"),
            conclusion=analysis.get("conclusion"),
            sentiment=analysis.get("sentiment"),
            company_ids=company_ids,
            tag_names=_as_list(analysis.get("tags")),
        )
        # article 底層都係一行 news，兩邊都要 embed：news.description 一份、article.thesis 一份。
        try:
            embed_article(article.news_id, db=db)
        except Exception:

            logger.exception("即時 embed_article(%s) 失敗，等下次 embed_all_facts() batch job 補返", article.news_id)

        return article





def embedding_job() -> None:
    """每日排程執行嘅 embedding job：幫公司 facts / news / article 補返漏低嘅 embedding。"""
    logger.info("開始執行每日 embedding job...")
    embed_all_facts()


def run_process_news_for_concepts(news_ids: list[int], *, db: DatabaseManager) -> None:
    """
    每日排程執行嘅 concept extraction job：攞返呢次 daily_news_fetch() 新插入嘅新聞
    (唔包 skipped_existing 嗰啲舊聞，唔會重複做多次 LLM extraction)，逐條抽
    theme/relation 寫入 Mind Map，等 Concept Graph 可以跟住新聞每日累積。

    單一條新聞 LLM 抽取失敗唔應該累個 batch 齊 fail，log 低就跳去下一條。
    """
    from app.etl.extract_concepts import process_news_for_concepts

    logger.info("開始執行每日 concept extraction job，共 %d 條新聞...", len(news_ids))
    for news_id in news_ids:
        try:
            process_news_for_concepts(db, news_id)
        except Exception:
            logger.exception("news_id=%s 嘅 concept extraction 失敗，跳過", news_id)





if __name__ == "__main__":
    logger.info("每日新聞排程執行。")
    daily_news_stats = daily_news_fetch()
    logger.info("每日 fetch 新聞統計: %s", daily_news_stats)
    
    logger.info("開始執行每日 embedding job...")
    embed_all_facts()
    logger.info("開始Matching")

    logger.info("開始執行每日 matching job 及 Concept Extraction...")
    db = DatabaseManager()
    today = datetime.now(timezone.utc).date()
    logger.info("查詢日期: %s", today)
    today_news_items = db.search_news(start_date=today, end_date=today)
    logger.info("今日新聞共 %d 條", len(today_news_items))
    
    # 開始逐條做 matching job 及 concept extraction job
    
    for item in today_news_items:
        try:
            logger.info("news_id=%s 嘅 matching job 開始...", item.news_id)
            match_news_to_companies(item.news_id)
        except Exception:
            logger.exception("news_id=%s 嘅 matching 失敗，跳過", item.news_id)
        
        from app.etl.extract_concepts import process_news_for_concepts
        try:
            logger.info("news_id=%s 嘅 concept extraction job 開始...", item.news_id)
            process_news_for_concepts(DatabaseManager(), item.news_id)
        except Exception:
            logger.exception("news_id=%s 嘅 concept extraction 失敗，跳過", item.news_id)


    




