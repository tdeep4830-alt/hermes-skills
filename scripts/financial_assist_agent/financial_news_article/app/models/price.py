"""
公司股價嘅日線 OHLCV 資料——純粹用嚟同 Mind Map 嘅 concept_relation 做「事後配對」：
一條relation/evidence記低咗「邊個時間點、因為咩證據，開始睇好/睇淡邊間公司」，
呢張表就俾你事後查返「嗰個時間點之後，呢間公司股價實際點行」，兩者對得埋，
先可以做返一個可以驗證嘅track record(見 `app/manager/evaluation_manager.py`)。

呢張表本身淨係一個純粹嘅事實表(fact table)，唔識點樣攞返資料——實際
call Yahoo Finance(經 `yfinance`，免費、唔使key)攞歷史股價嘅邏輯喺
`app/etl/fetch_prices.py`。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import BigInteger, Date, DateTime, Float, ForeignKey, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class StockPrice(Base):
    """一間公司喺某一個交易日嘅 OHLCV 收市資料。"""

    __tablename__ = "stock_prices"
    __table_args__ = (
        UniqueConstraint("company_id", "price_date", name="uq_stock_prices_company_date"),
    )

    price_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), nullable=False)
    price_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    open_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    high_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    low_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    close_price: Mapped[float] = mapped_column(Float, nullable=False)
    # close_price 用嘅係 yfinance 嘅 "Close"(唔係 auto-adjusted Close)——
    # 想做長年期回測、要理埋派息/拆股嘅話,可以之後加多一欄 adjusted_close。
    volume: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    company: Mapped["Company"] = relationship(back_populates="price_bars")


# 補一個 import 俾 type hint 用 (避免 circular import 報錯)
from app.models.company import Company  # noqa: E402
