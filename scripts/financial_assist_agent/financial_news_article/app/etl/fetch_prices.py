"""
攞返公司歷史股價(日線 OHLCV)——用 `yfinance`(免費、唔使 key，包底 Yahoo
Finance 嘅非官方 API)。同 `fetch_news.py` 一樣嘅設計原則：呢層淨係負責
「攞返原始資料」，完全唔識點寫入 DB(嗰部分喺 `run_price_update.py`)，
方便獨立 test。

`_download_history()` 係刻意抽出嚟嘅薄封裝，等 test 可以直接 monkeypatch
呢個 function(唔使駁真 Yahoo Finance 網絡 call，亦唔使處理 yfinance 內部
用緊嘅 curl_cffi 網絡層)。
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _download_history(ticker: str, start_date: date, end_date: date) -> Any:
    """
    真正 call Yahoo Finance(經 `yfinance`)嘅版本。回傳 yfinance 原生嘅
    `pandas.DataFrame`(index=交易日，columns 包括 Open/High/Low/Close/Volume)。
    """
    import yfinance as yf

    return yf.Ticker(ticker).history(
        start=start_date.isoformat(),
        end=(end_date + timedelta(days=1)).isoformat(),  # yfinance 嘅 end 係 exclusive
        interval="1d",
        auto_adjust=False,
    )


def _clean_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN != NaN,唔想淨係為咗呢個引入pandas依賴嚟做isna


def _clean_int(value: Any) -> Optional[int]:
    f = _clean_float(value)
    return None if f is None else int(f)


def fetch_price_history(ticker: str, *, start_date: date, end_date: date) -> list[dict[str, Any]]:
    """
    攞返 `ticker` 喺 [start_date, end_date] 呢段區間(包含頭尾)嘅日線股價。

    回傳 by 早到遲排嘅 list：
        [{"date": date, "open": float|None, "high": float|None,
          "low": float|None, "close": float, "volume": int|None}, ...]

    攞唔到(冇網絡/ticker錯/呢段時間冇交易)就回傳空 list，唔會拋錯，等呼叫方
    (`run_price_update.py`)可以逐間公司獨立處理，一間攞唔到唔會累事成個
    pipeline死埋——同 `fetch_news.py` 入面每個來源獨立 try/except 嘅原則一致。
    """
    try:
        df = _download_history(ticker, start_date, end_date)
    except Exception:
        logger.exception("攞 %s 嘅股價歷史失敗", ticker)
        return []

    if df is None or df.empty:
        logger.info("%s 喺 %s ~ %s 呢段時間冇股價資料", ticker, start_date, end_date)
        return []

    bars: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        bar_date = idx.date() if hasattr(idx, "date") else idx
        close = _clean_float(row.get("Close"))
        if close is None:
            continue
        bars.append(
            {
                "date": bar_date,
                "open": _clean_float(row.get("Open")),
                "high": _clean_float(row.get("High")),
                "low": _clean_float(row.get("Low")),
                "close": close,
                "volume": _clean_int(row.get("Volume")),
            }
        )
    bars.sort(key=lambda b: b["date"])
    return bars
