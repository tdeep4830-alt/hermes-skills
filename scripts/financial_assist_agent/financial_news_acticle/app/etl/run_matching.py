"""
定時執行嘅 matching 入口，同 embedding（喺 analyze_and_save() 新聞一入 DB 就即時做）分開，
淨係做返 match_news_companies.py / match_news_articles.py 兩層 matching。

分開嚟嘅原因：matching（尤其 Layer 3 embedding 比對）想食盡成個 company_fact_embeddings
corpus，遲少少行、夾埋一批新聞一齊跑冇問題；embedding 就想新聞一入 DB 即刻有，
兩者對「即時性」嘅要求唔同，所以拆開兩條 cron。

本地測試： python -m app.etl.run_matching
部署：cron 定時觸發（例如每 15 分鐘一次，喺 run_daily.py 攞完新聞之後）。
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.database import get_session
from app.etl.match_news_articles import match_news_to_articles
from app.etl.match_news_companies import match_news_to_companies
from app.models import News

logger = logging.getLogger(__name__)


def run_matching() -> None:
    """對所有新聞跑一次 Layer 2/3 matching。兩個 matcher 都係 idempotent（淨係補未覆蓋嘅
    match），重複執行唔會產生重複 row，所以呢度淨係簡單咁攞晒全部 news_id 嚟跑，
    唔使額外追蹤邊啲已經 match 過。"""
    with get_session() as session:
        news_ids = [row[0] for row in session.execute(select(News.news_id)).all()]

    logger.info("開始跑 matching，一共 %d 則新聞", len(news_ids))
    for news_id in news_ids:
        try:
            match_news_to_companies(news_id)
            match_news_to_articles(news_id)
        except Exception:
            logger.exception("news_id=%s matching 失敗，skip 呢一則，唔影響其他新聞", news_id)

    logger.info("matching 跑完。")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_matching()
