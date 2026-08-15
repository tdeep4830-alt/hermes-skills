"""
測試 `app/etl/weekly_digest.py`：

1. `build_digest_prompt()` —— 純function,唔涉及DB/API,測試畀咗訊號之後
   組成嘅prompt文字岩唔岩(有冇提到相關內容、有冇帶埋『唔係預測』嘅原則)。
2. `gather_weekly_signals()` —— 讀真實 PostgreSQL,驗證真係將
   `AnalyticsManagerMixin` 嗰六類訊號兜齊晒(包括由accelerating/emerging
   揀root去追蹤嘅propagation_paths)。
3. `generate_weekly_digest()` —— 用假嘅 `llm_fn`(唔連真API)驗證主流程
   真係「讀訊號 -> 組prompt -> 交俾LLM -> 攞返摘要」呢條鏈行得通,
   而且回傳埋原始訊號同免責聲明,唔係淨係得返LLM篇文字。

執行: pytest tests/test_weekly_digest.py -v
（前提同其他 manager test 一樣：DATABASE_URL 指嘅 PostgreSQL 已經開緊機，
  並且已經 `alembic upgrade head`。）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.etl.weekly_digest import (
    DISCLAIMER,
    build_digest_prompt,
    gather_weekly_signals,
    generate_weekly_digest,
)
from app.manager.db_manager import DatabaseManager


def _random_embedding(dim: int = 1536) -> list[float]:
    vec = [0.0] * dim
    vec[uuid.uuid4().int % dim] = 1.0
    return vec


def _unique_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


@pytest.fixture
def db():
    manager = DatabaseManager()
    try:
        yield manager
    finally:
        manager.dispose()


# ------------------------------------------------------- build_digest_prompt (純function)
def test_build_digest_prompt_includes_all_signal_sections_and_narrator_principle():
    signals = {
        "days": 7,
        "accelerating_relations": [
            {
                "from_concept": "AI伺服器需求上升",
                "to_concept": "TSMC",
                "relation_type": "benefits",
                "polarity": "positive",
                "confidence": 0.75,
                "recent_evidence_count": 5,
                "baseline_evidence_count": 1,
                "acceleration": 0.5,
            }
        ],
        "emerging_themes": [
            {"name": "邊緣運算晶片需求", "description": "", "total_reinforcement": 1, "relation_count": 1}
        ],
        "polarity_conflicts": [
            {
                "from_concept": "加息預期",
                "to_concept": "科技股估值",
                "relation_type": "threatens",
                "positive_confidence": 0.6,
                "positive_reinforcement_count": 2,
                "negative_confidence": 0.4,
                "negative_reinforcement_count": 1,
            }
        ],
        "theme_breadth": [
            {"name": "AI發展", "company_count": 3, "companies": ["Nvidia", "TSMC", "Microsoft"]}
        ],
        "evidence_source_diversity": [
            {
                "from_concept": "AI發展",
                "to_concept": "Nvidia",
                "relation_type": "benefits",
                "polarity": "positive",
                "distinct_source_count": 3,
            }
        ],
        "propagation_paths": {
            "AI發展": [
                {"path": ["AI發展", "晶片需求上升", "TSMC"], "weight": 0.42, "hops": 2}
            ]
        },
    }

    prompt = build_digest_prompt(signals)

    # 六類訊號嘅內容都要出現喺prompt入面
    assert "AI伺服器需求上升" in prompt
    assert "邊緣運算晶片需求" in prompt
    assert "加息預期" in prompt
    assert "AI發展" in prompt and "3 間公司" in prompt
    assert "AI發展 -> 晶片需求上升 -> TSMC" in prompt
    assert "3 個獨立新聞來源" in prompt

    # 「LLM係敘事者唔係預言家」嘅原則要明文喺prompt入面
    assert "唔好對大市" in prompt or "唔係預言家" in prompt
    assert "投資建議" in prompt or "投資決定" in prompt


def test_build_digest_prompt_handles_empty_signals_gracefully():
    empty_signals = {
        "days": 7,
        "accelerating_relations": [],
        "emerging_themes": [],
        "polarity_conflicts": [],
        "theme_breadth": [],
        "evidence_source_diversity": [],
        "propagation_paths": {},
    }
    prompt = build_digest_prompt(empty_signals)
    # 冇資料嗰啲section要清楚講返「冇特別發現」，唔會整份prompt做成一堆空白/error
    assert "冇" in prompt
    assert "過去 7 日" in prompt


# --------------------------------------------------------- gather_weekly_signals (真DB)
def test_gather_weekly_signals_collects_all_six_categories(db):
    ticker = f"T{uuid.uuid4().hex[:8].upper()}"
    company = db.create_company(ticker=ticker, name_en=f"Weekly Digest Co {ticker}")
    theme_name = _unique_name("每週摘要測試主題")
    theme, _ = db.get_or_create_theme_concept(theme_name, embedding=_random_embedding())
    company_concept, _ = db.get_or_create_company_concept(company.company_id)

    try:
        db.reinforce_relation(
            theme.concept_id, company_concept.concept_id, "benefits", polarity="positive", confidence=0.7
        )

        signals = gather_weekly_signals(db, days=7)

        assert signals["days"] == 7
        for key in (
            "accelerating_relations",
            "emerging_themes",
            "polarity_conflicts",
            "theme_breadth",
            "evidence_source_diversity",
            "propagation_paths",
        ):
            assert key in signals

        # 呢個theme岩啱先開、仲未俾好多來源印證 -> 應該喺emerging_themes見到
        assert any(t["concept_id"] == theme.concept_id for t in signals["emerging_themes"])

        # propagation_paths 嘅 root 應該包括呢個岩岩用嚟做「新興主題」嘅 theme
        # (由 gather_weekly_signals 內部揀root嘅邏輯,會將emerging_themes嘅concept
        # 都當做root嘗試追蹤,但如果冇outgoing relation就唔會有任何path產生;
        # 呢度我哋已經連咗去company,所以應該有一條path)
        assert theme_name in signals["propagation_paths"]
        paths = signals["propagation_paths"][theme_name]
        assert any(p["path"] == [theme_name, company_concept.name] for p in paths)
    finally:
        db.delete_concept(theme.concept_id)
        db.delete_concept(company_concept.concept_id)
        db.delete_company(company.company_id)


# ------------------------------------------------------- generate_weekly_digest (假LLM)
def test_generate_weekly_digest_end_to_end_with_fake_llm(db):
    ticker = f"T{uuid.uuid4().hex[:8].upper()}"
    company = db.create_company(ticker=ticker, name_en=f"Digest E2E Co {ticker}")
    theme_name = _unique_name("端對端摘要測試主題")
    theme, _ = db.get_or_create_theme_concept(theme_name, embedding=_random_embedding())
    company_concept, _ = db.get_or_create_company_concept(company.company_id)

    captured_prompts: list[str] = []

    def fake_llm_fn(prompt: str, *, model=None) -> str:
        captured_prompts.append(prompt)
        assert theme_name in prompt  # 證明個prompt真係帶埋DB讀返嚟嘅訊號,唔係空殼
        return "本星期摘要（假LLM輸出，唔涉及真API）。"

    try:
        db.reinforce_relation(
            theme.concept_id, company_concept.concept_id, "benefits", polarity="positive", confidence=0.7
        )

        result = generate_weekly_digest(db, days=7, llm_fn=fake_llm_fn)

        assert result["digest"] == "本星期摘要（假LLM輸出，唔涉及真API）。"
        assert result["disclaimer"] == DISCLAIMER
        assert "signals" in result
        assert any(
            t["concept_id"] == theme.concept_id for t in result["signals"]["emerging_themes"]
        )
        assert len(captured_prompts) == 1
    finally:
        db.delete_concept(theme.concept_id)
        db.delete_concept(company_concept.concept_id)
        db.delete_company(company.company_id)


def test_generate_weekly_digest_passes_model_through_to_llm_fn(db):
    received_models = []

    def fake_llm_fn(prompt: str, *, model=None) -> str:
        received_models.append(model)
        return "ok"

    result = generate_weekly_digest(db, days=7, llm_fn=fake_llm_fn, model="claude-test-model")
    assert received_models == ["claude-test-model"]
    assert result["digest"] == "ok"
