"""
`StockPrice`(日線 OHLCV)嘅 CRUD + 查詢。

呢層完全唔識點樣攞返股價資料(嗰部分喺 `app/etl/fetch_prices.py`)，
淨係負責「畀咗一批日線資料之後，點樣安全咁 upsert 落 DB」，同埋俾
`app/manager/evaluation_manager.py` 用嚟查詢「某個日子之後嘅股價序列」。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional, Sequence

from sqlalchemy import select

from app.manager.generic import count_obj, delete_obj, get_obj, list_obj
from app.models import StockPrice


class PriceManagerMixin:
    # ----------------------------------------------------------------- CRUD
    def get_price_bar(self, price_id: int) -> Optional[StockPrice]:
        with self.session_scope() as s:
            return get_obj(s, StockPrice, price_id)

    def delete_price_bar(self, price_id: int) -> bool:
        with self.session_scope() as s:
            return delete_obj(s, StockPrice, price_id)

    def list_price_bars(
        self, *, limit: int = 100, offset: int = 0, **filters: Any
    ) -> list[StockPrice]:
        with self.session_scope() as s:
            return list_obj(
                s, StockPrice, limit=limit, offset=offset, order_by=StockPrice.price_date, **filters
            )

    def count_price_bars(self, **filters: Any) -> int:
        with self.session_scope() as s:
            return count_obj(s, StockPrice, **filters)

    def upsert_price_bars(self, company_id: int, bars: Sequence[dict[str, Any]]) -> dict[str, int]:
        """
        一步過寫入一批日線資料(通常嚟自 `fetch_prices.fetch_price_history()`)。
        用 (company_id, price_date) 做 idempotent key——同一日已經有記錄就更新
        (例如 fetch 到修正過嘅 close，或者之前 volume 缺咗)，冇就新增，等你
        可以安全咁日日重覆跑同一段日子都唔會炒重複行。

        每個 bar dict 要有 `date`(datetime.date) 同 `close`，
        `open`/`high`/`low`/`volume` 可選。

        回傳 `{"inserted": int, "updated": int}`。
        """
        inserted = 0
        updated = 0
        with self.session_scope() as s:
            for bar in bars:
                price_date = bar["date"]
                existing = s.scalars(
                    select(StockPrice).where(
                        StockPrice.company_id == company_id, StockPrice.price_date == price_date
                    )
                ).first()
                if existing is None:
                    s.add(
                        StockPrice(
                            company_id=company_id,
                            price_date=price_date,
                            open_price=bar.get("open"),
                            high_price=bar.get("high"),
                            low_price=bar.get("low"),
                            close_price=bar["close"],
                            volume=bar.get("volume"),
                        )
                    )
                    inserted += 1
                else:
                    existing.open_price = bar.get("open")
                    existing.high_price = bar.get("high")
                    existing.low_price = bar.get("low")
                    existing.close_price = bar["close"]
                    existing.volume = bar.get("volume")
                    updated += 1
            s.flush()
        return {"inserted": inserted, "updated": updated}

    # ------------------------------------------------------------- 查詢
    def get_latest_price_date(self, company_id: int) -> Optional[date]:
        """依家 DB 度呢間公司最新一條股價記錄係邊日——俾 `fetch_prices.py`
        做 incremental fetch(淨係攞返呢日之後嘅新資料，唔使成段歷史重複攞)。"""
        with self.session_scope() as s:
            stmt = (
                select(StockPrice.price_date)
                .where(StockPrice.company_id == company_id)
                .order_by(StockPrice.price_date.desc())
                .limit(1)
            )
            return s.scalars(stmt).first()

    def get_price_bars_from(
        self, company_id: int, start_date: date, *, limit: Optional[int] = None
    ) -> list[StockPrice]:
        """
        攞返呢間公司喺 `start_date` 當日或之後嘅股價序列，由早到遲排(適合
        做「事後配對」嘅核心查詢：第一條就係「訊號出現之後，第一個有交易嘅
        日子」嘅收市價，做 baseline；再之後第 N 條就係 N 個交易日之後嘅收市價，
        用嚟計forward return)。
        """
        with self.session_scope() as s:
            stmt = (
                select(StockPrice)
                .where(StockPrice.company_id == company_id, StockPrice.price_date >= start_date)
                .order_by(StockPrice.price_date.asc())
            )
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(s.scalars(stmt).all())

    def get_price_bar_on_or_before(self, company_id: int, target_date: date) -> Optional[StockPrice]:
        """攞返 `target_date` 當日或之前，最近一個有交易嘅日子嘅收市價。"""
        with self.session_scope() as s:
            stmt = (
                select(StockPrice)
                .where(StockPrice.company_id == company_id, StockPrice.price_date <= target_date)
                .order_by(StockPrice.price_date.desc())
                .limit(1)
            )
            return s.scalars(stmt).first()
