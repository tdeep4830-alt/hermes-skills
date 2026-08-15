"""
每週 Mind Map 動向摘要(weekly digest)——將 `AnalyticsManagerMixin` 算出嚟嗰幾類
結構化訊號(加速緊嘅relation、新興主題、極性分歧、主題廣度、傳導路徑、證據
來源多樣性),餵俾 LLM,由佢執筆寫一段俾人睇嘅每週摘要。

設計原則:LLM 喺呢度嘅角色係「敘事者(narrator)」,唔係「先知(oracle)」——
佢嘅工作係將已經計好嘅結構化訊號,組織成讀得明、有重點嘅摘要文字,解釋
「Mind Map 呢個星期有咩郁動、邊啲論述開始有更多獨立來源印證、邊度出現分歧」;
佢唔負責、亦都唔應該被問到「大市下星期會點行」呢類方向性預測——冇一個
可靠嘅回饋機制去校準呢類預測嘅準確度,勉強問只會攞到聽落好肯定、實際上
冇根據嘅答案。呢個原則喺 prompt 入面亦會明文提醒 LLM。

呢個檔案分三層,同 extract_concepts.py / llm_client.py 一致嘅分層方式:
1. `gather_weekly_signals(db, days)` —— 淨係讀 DB,唔涉及LLM,回傳純資料。
2. `build_digest_prompt(signals)`    —— 純function,將訊號組成prompt文字,
                                         唔涉及任何API call,方便獨立test。
3. `generate_weekly_digest(db, days, llm_fn, model)` —— 主流程,將以上兩步
                                         串埋一齊,再call LLM攞返摘要文字。
   `llm_fn` 俾你注入測試用嘅 stub(唔傳就用真係會 call Anthropic API 嘅版本)。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from app.config import settings
from app.etl.LLM_analyze import weekly_digest_llm_fn

logger = logging.getLogger(__name__)

LlmFn = Callable[..., str]

DISCLAIMER = (
    "本摘要純粹描述 Mind Map(市場概念關係圖)入面本星期嘅結構性變化"
    "(邊啲論述多咗獨立來源印證、邊啲主題新出現、邊度有分歧),唔係財務或投資建議，"
    "亦都唔係對大市方向嘅預測。任何投資決定請自行判斷或諮詢專業人士。"
)


# ------------------------------------------------------------- 1. 收集訊號
def gather_weekly_signals(
    db,
    *,
    days: int = 7,
    max_propagation_roots: int = 5,
    propagation_max_hops: int = 3,
) -> dict[str, Any]:
    """
    淨係讀 DB,唔call任何LLM。回傳一個純資料 dict,keys:
    - accelerating_relations: 近 `days` 日加速被印證緊嘅relation
    - emerging_themes: 近 `days*2` 日先出現、仲未被大量印證嘅新主題
    - polarity_conflicts: 現存邊度有 positive/negative並存嘅分歧
    - theme_breadth: 邊個主題直接影響最多間公司
    - evidence_source_diversity: 邊條relation嘅印證嚟自最多唔同新聞來源
    - propagation_paths: 由「本星期最值得留意」嗰幾個主題(嚟自
      accelerating_relations / emerging_themes)出發,追蹤佢哋可以點樣
      一路傳導落去(每個root一個 list of path)

    `days` 係呢個function嘅主要旋鈕:控制「近期」嘅定義,其餘幾個
    analytics method 嘅時間窗/門檻都跟住 `days` 按比例調整。
    """
    accelerating = db.get_accelerating_relations(
        recent_days=days, baseline_days=max(days * 4, 14), min_recent_evidence=2, limit=15
    )
    emerging = db.get_emerging_themes(
        recent_days=max(days * 2, 7), max_total_reinforcement=3, limit=15
    )
    conflicts = db.get_polarity_conflicts(limit=15)
    breadth = db.get_theme_breadth(min_companies=2, limit=15)
    diversity = db.get_evidence_source_diversity(min_sources=2, limit=15)

    # 傳導路徑嘅 root:優先揀「最近加速緊」同「新興」嗰啲主題,
    # 呢啲先係本星期最值得追蹤佢哋可以點樣傳導落去嘅概念。
    root_ids: list[int] = []
    root_names: dict[int, str] = {}
    for item in accelerating:
        concept_id = item["from_concept_id"]
        if concept_id not in root_names:
            root_names[concept_id] = item["from_concept"]
            root_ids.append(concept_id)
    for item in emerging:
        concept_id = item["concept_id"]
        if concept_id not in root_names:
            root_names[concept_id] = item["name"]
            root_ids.append(concept_id)
    root_ids = root_ids[:max_propagation_roots]

    propagation: dict[str, list[dict[str, Any]]] = {}
    for concept_id in root_ids:
        paths = db.get_propagation_paths(
            concept_id, max_hops=propagation_max_hops, min_confidence=0.3, limit=5
        )
        if paths:
            propagation[root_names[concept_id]] = paths

    return {
        "days": days,
        "accelerating_relations": accelerating,
        "emerging_themes": emerging,
        "polarity_conflicts": conflicts,
        "theme_breadth": breadth,
        "evidence_source_diversity": diversity,
        "propagation_paths": propagation,
    }


# --------------------------------------------------------- 2. 組成 prompt
def _format_accelerating(items: list[dict[str, Any]]) -> str:
    if not items:
        return "（本星期冇relation出現明顯加速印證嘅情況）"
    lines = []
    for item in items:
        lines.append(
            f"- {item['from_concept']} --[{item['relation_type']}/{item['polarity']}]--> "
            f"{item['to_concept']}：近期 {item['recent_evidence_count']} 篇 vs 之前基準 "
            f"{item['baseline_evidence_count']} 篇(加速度 {item['acceleration']:+.3f}/日)，"
            f"confidence={item['confidence']:.2f}"
        )
    return "\n".join(lines)


def _format_emerging(items: list[dict[str, Any]]) -> str:
    if not items:
        return "（本星期冇明顯嘅新興主題）"
    lines = []
    for item in items:
        desc = f"：{item['description']}" if item.get("description") else ""
        lines.append(
            f"- {item['name']}{desc}(累計強化次數 {item['total_reinforcement']}，"
            f"連結 {item['relation_count']} 條 relation)"
        )
    return "\n".join(lines)


def _format_conflicts(items: list[dict[str, Any]]) -> str:
    if not items:
        return "（暫時冇偵測到明顯嘅正負分歧論述）"
    lines = []
    for item in items:
        lines.append(
            f"- {item['from_concept']} --[{item['relation_type']}]--> {item['to_concept']}："
            f"睇好(positive) confidence={item['positive_confidence']:.2f}/強化{item['positive_reinforcement_count']}次 "
            f"vs 睇淡(negative) confidence={item['negative_confidence']:.2f}/強化{item['negative_reinforcement_count']}次"
        )
    return "\n".join(lines)


def _format_breadth(items: list[dict[str, Any]]) -> str:
    if not items:
        return "（暫時冇主題連結夠多唔同公司）"
    lines = []
    for item in items:
        companies = "、".join(item["companies"])
        lines.append(f"- {item['name']}：影響 {item['company_count']} 間公司（{companies}）")
    return "\n".join(lines)


def _format_diversity(items: list[dict[str, Any]]) -> str:
    if not items:
        return "（暫時冇relation嘅印證嚟源夠分散）"
    lines = []
    for item in items:
        lines.append(
            f"- {item['from_concept']} --[{item['relation_type']}/{item['polarity']}]--> "
            f"{item['to_concept']}：{item['distinct_source_count']} 個獨立新聞來源印證"
        )
    return "\n".join(lines)


def _format_propagation(propagation: dict[str, list[dict[str, Any]]]) -> str:
    if not propagation:
        return "（暫時冇追蹤到值得留意嘅多手傳導路徑）"
    blocks = []
    for root_name, paths in propagation.items():
        path_lines = []
        for path in paths[:5]:
            chain = " -> ".join(path["path"])
            path_lines.append(f"    * {chain}（權重 {path['weight']:.3f}）")
        blocks.append(f"- 由「{root_name}」出發：\n" + "\n".join(path_lines))
    return "\n".join(blocks)


def build_digest_prompt(signals: dict[str, Any]) -> str:
    """
    純function,唔涉及任何API call,方便獨立test。將 `gather_weekly_signals()`
    嘅輸出組成一份俾LLM讀嘅prompt。
    """
    days = signals.get("days", 7)
    return f"""你係一個市場研究助手，負責將一個持續生長嘅市場概念關係圖(Mind Map)入面，
過去 {days} 日嘅結構性變化，整理成一份俾人一眼睇明嘅每週摘要。

# 重要原則
- 你嘅角色係敘事者，唔係預言家。你嘅工作淨係將下面已經計算好嘅結構化訊號，
  組織成清晰、有重點嘅文字，解釋「呢個星期Mind Map有咩郁動」。
- 唔好對大市、任何個股嘅未來價格或方向作出預測或者給予「應該買/沽」呢類建議。
  呢啲訊號淨係反映緊「新聞論述層面」嘅變化(邊個論點多咗人講、邊度有分歧)，
  唔代表事實一定會咁樣發生。
- 如果某一類訊號底下冇資料，直接講返「呢方面本星期冇特別發現」，唔好靠估補充。
- 用繁體中文書寫，語氣客觀、精簡，可以用小標題分段，唔使太長。

# 訊號一：加速被印證緊嘅論述(reinforcement velocity)
{_format_accelerating(signals.get("accelerating_relations", []))}

# 訊號二：新興主題(emerging themes)
{_format_emerging(signals.get("emerging_themes", []))}

# 訊號三：正負分歧論述(polarity conflicts)
{_format_conflicts(signals.get("polarity_conflicts", []))}

# 訊號四：主題廣度(theme breadth)
{_format_breadth(signals.get("theme_breadth", []))}

# 訊號五：傳導路徑(propagation paths)
{_format_propagation(signals.get("propagation_paths", {}))}

# 訊號六：證據來源多樣性(evidence source diversity)
{_format_diversity(signals.get("evidence_source_diversity", []))}

# 你嘅任務
根據以上六類訊號，寫一份結構清晰嘅每週 Mind Map 動向摘要，包括：
1. 本星期最值得留意嘅 2-4 個重點(可以綜合幾類訊號一齊講，例如「某主題加速
   被印證，同時亦都影響緊好多間公司」)。
2. 如果有正負分歧論述，簡短指出邊度有分歧，唔使替讀者判斷邊一方啱。
3. 如果有值得留意嘅傳導路徑，簡短講解「一個主題可以點樣影響落去下一層」。
4. 摘要結尾加返一句提醒：呢份摘要唔係投資建議，純粹描述Mind Map本身嘅結構變化。
"""


# --------------------------------------------------------------- 3. 主流程



def generate_weekly_digest(
    db,
    *,
    days: int = 7,
    model: Optional[str] = None,
) -> dict[str, Any]:
    """
    主流程:攞返過去 `days` 日嘅結構化訊號 -> 組成 prompt -> 叫 LLM 執筆寫摘要。

    `llm_fn` 俾你注入測試用嘅 stub(簽名 `llm_fn(prompt: str, *, model=None) -> str`)，
    唔傳就用真係會 call Anthropic API 嘅 `_default_llm_fn`。

    回傳 dict：`{"digest": <LLM寫嘅摘要文字>, "signals": <gather_weekly_signals()嘅原始輸出>,
    "disclaimer": <免責聲明>}`——連埋原始訊號一齊回傳,方便你想自己再核對/存底,
    唔使淨係信LLM篇文字。
    """
    prompt = build_digest_prompt(gather_weekly_signals(db, days=days))

    signals = gather_weekly_signals(db, days=days)
    prompt = build_digest_prompt(signals)

    logger.info(
        "組成 weekly digest prompt 完成(days=%d)，加速relation %d 條、新興主題 %d 個、"
        "分歧 %d 條、傳導路徑 root %d 個，準備 call LLM...",
        days,
        len(signals["accelerating_relations"]),
        len(signals["emerging_themes"]),
        len(signals["polarity_conflicts"]),
        len(signals["propagation_paths"]),
    )

    digest_text = weekly_digest_llm_fn(prompt, model=model)

    return {
        "digest": digest_text,
        "signals": signals,
        "disclaimer": DISCLAIMER,
    }


if __name__ == "__main__":
    from app.manager import DatabaseManager

    logging_db = DatabaseManager()
    try:
        result = generate_weekly_digest(logging_db)
        print(result["digest"])
        print("\n---\n" + result["disclaimer"])
    finally:
        logging_db.dispose()
