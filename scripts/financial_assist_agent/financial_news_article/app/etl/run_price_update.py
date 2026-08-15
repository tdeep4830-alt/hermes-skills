"""
股價更新 pipeline 執行入口——負責幫 DB 入面已有嘅每一間公司,攞返最新嘅
日線收市價,寫入 `stock_prices`。

呢個 pipeline 存在嘅理由：Mind Map 入面每條 `concept_relation` 已經記低咗
「邊個時間點(evidence)、因為咩證據，開始睇好/睇淡邊間公司」；有咗呢張表
之後,先可以事後攞返「嗰個時間點之後，呢間公司股價實際點行」，兩者對得埋，
做返一個可以驗證嘅track record(見 `app/manager/evaluation_manager.py`)。

Incremental fetch：已經有記錄嘅公司，淨係攞返「最新一條記錄之後」嘅新資料；
全新、未攞過股價嘅公司，先攞返 `lookback_days`(預設 400，即大概一年半交易日)
咁多日歷史，等一開始就有足夠資料做回測。

同 `run_daily.py` 一樣嘅原則：呢個 script 唔負責、亦都唔應該負責點樣排程
執行(cron / GitHub Actions / 其他)——淨係要求 `DATABASE_URL` 設定好，就可以
喺任何排程環境入面直接 `python -m app.etl.run_price_update` 咁跑。

執行： python -m app.etl.run_price_update
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Optional

from app.etl.fetch_prices import fetch_price_history
from app.manager import DatabaseManager

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 400


def run(*, lookback_days: int = DEFAULT_LOOKBACK_DAYS, today: Optional[date] = None) -> dict[str, dict[str, int]]:
    """
    `today` 俾你(或者test)注入,避免直接用 `date.today()`，方便test唔使受
    執行果一刻嘅真實日期影響。回傳 `{ticker: {"fetched": int, "inserted": int, "updated": int}}`。
    """
    if today is None:
        today = date.today()

    db = DatabaseManager()
    try:
        companies = db.list_companies(limit=1000)
        logger.info("開始更新股價，一共 %d 間公司", len(companies))

        stats: dict[str, dict[str, int]] = {}
        for company in companies:
            latest_date = db.get_latest_price_date(company.company_id)
            start_date = (
                latest_date + timedelta(days=1) if latest_date else today - timedelta(days=lookback_days)
            )
            if start_date > today:
                stats[company.ticker] = {"fetched": 0, "inserted": 0, "updated": 0}
                continue

            bars = fetch_price_history(company.ticker, start_date=start_date, end_date=today)
            if not bars:
                stats[company.ticker] = {"fetched": 0, "inserted": 0, "updated": 0}
                continue

            upsert_stats = db.upsert_price_bars(company.company_id, bars)
            stats[company.ticker] = {"fetched": len(bars), **upsert_stats}
            logger.info(
                "%s：攞到 %d 條(新增 %d、更新 %d)",
                company.ticker,
                len(bars),
                upsert_stats["inserted"],
                upsert_stats["updated"],
            )

        return stats
    finally:
        db.dispose()


if __name__ == "__main__":
    run()
