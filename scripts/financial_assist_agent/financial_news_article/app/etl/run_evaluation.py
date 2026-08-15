"""
事後配對(ex-post evaluation) pipeline 執行入口——負責跑一次「Mind Map 嘅
睇好/睇淡論述，事後同實際股價表現配對」嘅工作，攞返一份可以睇/可以存底嘅
report。

同 `run_daily.py` / `run_price_update.py` 一樣嘅原則：分開獨立步驟，唔會
自動連埋一齊跑——呢個pipeline假設 `app/etl/run_price_update.py` 已經幫你
已追蹤緊嘅公司攞咗最新股價；佢本身淨係讀 DB 做計算，唔會再call任何外部
API，你可以自己揀幾耐跑一次(例如每個星期/每個月跑一次，睇返過去嘅論述
track record點)。

輸出:
1. 用 logging 印一份人睇得明嘅摘要——整體(全部/睇好/睇淡)嘅 track record，
   同每間有訊號嘅公司分別嘅 track record。
2. 如果指定咗 `output_path`，仲會將完整report寫做一個JSON檔，方便你自己
   keep低歷史(因為 `EvaluationManagerMixin` 本身刻意冇persist任何嘢落DB，
   每次都係即時計算——見 `app/manager/evaluation_manager.py` 嘅docstring)。

呢個pipeline唔負責、亦都唔應該負責任何「應該點做」嘅判斷或者「大市會點行」
嘅預測——純粹將Mind Map嘅論述同事後股價表現配對，計返hit rate/平均回報
呢啲純數字，點解讀交返俾你，唔係財務建議。

執行： python -m app.etl.run_evaluation
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence, Union

from app.manager import DatabaseManager
from app.manager.evaluation_manager import DEFAULT_HORIZONS_TRADING_DAYS

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def _log_track_record(label: str, track_record: dict[str, Any]) -> None:
    logger.info(
        "%s：總訊號 %d 條，可評估 %d 條，冇股價資料跳過 %d 條",
        label,
        track_record["total_signals"],
        track_record["evaluated_signals"],
        track_record["skipped_signals"],
    )
    for horizon, stat in track_record["horizons"].items():
        if stat["evaluated"] == 0:
            logger.info("  - %d個交易日後：暫時冇足夠股價資料評估", horizon)
            continue
        hit_rate_text = f"{stat['hit_rate']:.1%}" if stat["hit_rate"] is not None else "N/A"
        logger.info(
            "  - %d個交易日後：evaluated=%d, avg_return=%.2f%%, hit_rate=%s(樣本%d條)",
            horizon,
            stat["evaluated"],
            stat["avg_return_pct"],
            hit_rate_text,
            stat["scored_for_hit_rate"],
        )


def weekly_evaluation_fn(
    *,
    horizons_trading_days: Sequence[int] = DEFAULT_HORIZONS_TRADING_DAYS,
    min_confidence: float = 0.0,
    output_path: Optional[Union[str, Path]] = None,
) -> dict[str, Any]:
    """
    跑一次事後配對，回傳一份完整report(dict)。

    `horizons_trading_days` / `min_confidence` 直接傳俾底層嘅
    `evaluate_thesis_track_record()`。`output_path` 俾你指定要唔要將report
    寫成JSON檔存底(唔傳就淨係log + return，唔寫檔)。

    Report結構：
        {
          "generated_at": ISO timestamp,
          "horizons_trading_days": [...],
          "min_confidence": ...,
          "overall": {
              "all": {...},        # 唔篩polarity，全部訊號一齊計
              "positive": {...},   # 淨係睇好論述
              "negative": {...},   # 淨係睇淡論述
          },
          "per_company": {
              "<ticker>": {...},   # 淨係包括「有至少一條可查訊號」嘅公司
              ...
          },
        }
    """
    db = DatabaseManager()
    try:
        logger.info(
            "開始事後配對(horizons=%s交易日, min_confidence=%.2f)...",
            list(horizons_trading_days),
            min_confidence,
        )

        overall_all = db.evaluate_thesis_track_record(
            min_confidence=min_confidence, horizons_trading_days=horizons_trading_days
        )
        overall_positive = db.evaluate_thesis_track_record(
            polarity="positive", min_confidence=min_confidence, horizons_trading_days=horizons_trading_days
        )
        overall_negative = db.evaluate_thesis_track_record(
            polarity="negative", min_confidence=min_confidence, horizons_trading_days=horizons_trading_days
        )

        logger.info("=== 整體 track record ===")
        _log_track_record("全部論述", overall_all)
        _log_track_record("睇好(positive)論述", overall_positive)
        _log_track_record("睇淡(negative)論述", overall_negative)

        # 逐間公司嘅track record——淨係計「依家至少有一條可查訊號」嘅公司，
        # 避免成千間冇任何Mind Map訊號嘅公司都白行一次query。
        per_company: dict[str, Any] = {}
        companies = db.list_companies(limit=1000)
        for company in companies:
            has_signal = db.get_relation_signals(
                company_id=company.company_id, min_confidence=min_confidence, limit=1
            )
            if not has_signal:
                continue
            per_company[company.ticker] = db.evaluate_thesis_track_record(
                company_id=company.company_id,
                min_confidence=min_confidence,
                horizons_trading_days=horizons_trading_days,
            )

        if per_company:
            logger.info("=== 逐間公司 track record(%d 間有訊號) ===", len(per_company))
            for ticker, track_record in per_company.items():
                _log_track_record(ticker, track_record)
        else:
            logger.info("暫時冇任何公司有可查嘅訊號(Mind Map仲未有relation連住company node)")

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "horizons_trading_days": list(horizons_trading_days),
            "min_confidence": min_confidence,
            "overall": {
                "all": overall_all,
                "positive": overall_positive,
                "negative": overall_negative,
            },
            "per_company": per_company,
        }

        if output_path is not None:
            output_path = Path(output_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            logger.info("已將完整report寫低: %s", output_path)

        return report
    finally:
        db.dispose()


if __name__ == "__main__":
    weekly_evaluation_fn(output_path="reports/evaluation_latest.json")
