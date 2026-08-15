"""
用 Claude API 分析一段新聞原文，再將分析結果連同原文一齊存入 DB
(經 DatabaseManager.add_news)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional, Any

from openai import OpenAI

from dotenv import load_dotenv
import os
from app.models import News
from app.models.company import product_category_values
import logging
from dataclasses import dataclass, field


load_dotenv()

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.manager.db_manager import DatabaseManager

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url="https://api.deepseek.com")
model = "deepseek-v4-flash"

RELATION_TYPES: list[str] = [
    "benefits",            # 令 to 受惠
    "threatens",           # 對 to 構成威胁
    "drives_demand_for",   # 帶動對 to 嘅需求
    "supplies_to",         # from 供應俾 to
    "competes_with",       # from 同 to 競爭
    "regulates",           # from (政策/監管主題) 規管緊 to
]
POLARITIES: list[str] = ["positive", "negative", "neutral"]

CONFIDENCE_GUIDANCE = """confidence 評分標準(0-1):
- 0.8-1.0：文章有明確事實/數據支持嘅斷言
- 0.5-0.8：分析員/專家明確講出嚟嘅預測或者觀點
- 0.2-0.5：文章暗示或者間接提及，唔算好肯定
- 低於 0.2 嘅關係唔好抽取，直接略過"""


_ANALYSIS_NEWS_SYSTEM_PROMPT = """你係財經新聞分析員。閱讀用戶提供嘅新聞原文，淨係回覆一個 JSON object，
唔好加任何解釋文字，唔好用 markdown code fence。JSON schema:

{
  "title": string,          // 精簡標題；原文已有標題就照用
  "description": string,    //  大約500句的摘要
  "sentiment": "positive" | "negative" | "neutral",
  "news_type": "company" | "industry" | "macro" | "other_asset",
  "tickers": string[],      // 新聞提到嘅上市公司 ticker（大寫），冇就返 []
  "tags": string[]          // 相關主題 tag，例如 "AI"、"加息"，冇就返 []
}"""

_PRODUCT_CATEGORY_OPTIONS = ", ".join(f'"{c}"' for c in product_category_values)

_ANALYSIS_COMPANY_SYSTEM_PROMPT = f"""你係財經新聞分析員。閱讀用戶提供嘅公司資料，淨係回覆一個 JSON object，
唔好加任何解釋文字，唔好用 markdown code fence。JSON schema:

{{
  "business_model": string,          // 公司業務模式嘅簡短描述
  "main_products": [                  // 公司主要產品/服務
    {{
      "name": string,                 // 產品名，簡短(建議<20字)
      "category": string,             // 一定要係以下其中一個: {_PRODUCT_CATEGORY_OPTIONS}；唔啱就揀 "Other"
      "description": string           // 呢個產品嘅簡短描述
    }}
  ],
  "technology": string[],              // 公司主要技術/專利嘅簡短描述
  "main_services": string[],              // 公司主要服務/平台嘅簡短描述
  "governmental_programs_and_regulations": string[],  // 公司受惠嘅政府計劃/法規嘅簡短描述
  "Manufucturing_and_Supply_Chain": string[],  // 公司製造/供應鏈嘅簡短描述
  "supply_chain": string[],  // 公司供應鏈嘅簡短描述
  "competitors": string[]  // 公司主要競爭對手嘅簡短描述
}}"""

_ANALYSIS_RISK_SYSTEM_PROMPT = """你係財經新聞分析員。閱讀用戶提供嘅公司資料原文，淨係回覆一個 JSON array，
入面裝住一個或多個代表風險因素嘅 JSON object（即使得一個都要用 array 包住），
唔好加任何解釋文字，唔好用 markdown code fence。每個 object 嘅 JSON schema:
{
  "risk_type": "financial" | "operational" | "strategic" | "compliance" | "reputational" | "market" | "other",
  "risk_description": string  // 風險因素嘅簡短描述
}"""

_ANALYZE_LEGAL_PROCEEDINGS_PROMPT = """你係財經新聞分析員。閱讀用戶提供嘅公司資料原文，淨係回覆一個 JSON array，
入面裝住一個或多個代表法律程序嘅 JSON object（即使得一個都要用 array 包住，冇就返 []），
唔好加任何解釋文字，唔好用 markdown code fence。每個 object 嘅 JSON schema:
{
  "legal_proceeding_type": "litigation" | "regulatory" | "compliance" | "other",
  "legal_proceeding_description": string  // 法律程序嘅簡短描述
}"""

_ANALYSIS_ARTICLE_SYSTEM_PROMPT = """你係財經新聞分析員。閱讀用戶提供嘅文章原文，淨係回覆一個 JSON object，
唔好加任何解釋文字，唔好用 markdown code fence。JSON schema:
{
  "title": string,          // 精簡標題；原文已有標題就照用
  "description": string,    //  大約500句的摘要
  "sentiment": "positive" | "negative" | "neutral",
  "thesis": string,          // 文章的論點
  "conclusion": string,     // 文章結論
  "tickers": string[],      // 文章提到嘅上市公司 ticker（大寫），冇就返 []
  "tags": string[]          // 相關主題
}"""


EXTRACTION_TOOL_SCHEMA: dict[str, Any] = {
    "name": "record_extraction",
    "description": "記錄由文章入面抽取到嘅市場主題，同埋主題之間/主題同公司之間嘅因果或影響關係。",
    "parameters": {
        "type": "object",
        "properties": {
            "themes": {
                "type": "array",
                "description": "文章入面提到嘅市場主題",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "主題名，簡短(建議<15字)"},
                        "description": {"type": "string", "description": "一句話解釋呢個主題"},
                    },
                    "required": ["name"],
                },
            },
            "relations": {
                "type": "array",
                "description": "主題之間，或者主題同已確認公司之間嘅因果/影響關係",
                "items": {
                    "type": "object",
                    "properties": {
                        "from_theme": {"type": "string", "description": "邊個主題(必須係上面 themes 入面嘅名)"},
                        "to_type": {"type": "string", "enum": ["theme", "company"]},
                        "to_ref": {
                            "type": "string",
                            "description": "to_type='theme' 填主題名；to_type='company' 填 ticker(必須係已確認公司清單入面嘅)",
                        },
                        "relation_type": {"type": "string", "enum": RELATION_TYPES},
                        "polarity": {"type": "string", "enum": POLARITIES},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "reasoning": {"type": "string", "description": "一句話解釋點解有呢個關係"},
                    },
                    "required": ["from_theme", "to_type", "to_ref", "relation_type", "polarity", "confidence"],
                },
            },
        },
        "required": ["themes", "relations"],
    },
}


def AI_analyze(text: str, *, model: str = "deepseek-v4-flash", prompt) -> dict:
    """叫 Claude 分析新聞原文，回傳結構化結果 。"""
    response = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        stream=False,
        reasoning_effort="low",
    )
    choice = response.choices[0]
    raw = (choice.message.content or "").strip()
    if not raw:
        raise ValueError(
            f"LLM 冇返任何內容 (finish_reason={choice.finish_reason!r})，"
            "如果係 length 就代表 max_tokens 唔夠俾 reasoning + 答案用，加大啲。"
        )
    raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    return json.loads(raw)

def weekly_digest_llm_fn(prompt: str, *, model: Optional[str] = None) -> str:
    """
    呢度用普通文字生成(唔強制 tool_choice),因為輸出係俾人讀嘅摘要文字,唔係結構化資料。
    """

    
    model = model or "deepseek-v4-flash"

    response = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
        reasoning_effort="low",
    )

    message = response.choices[0].message
    content = (message.content or "").strip()
    if not content:
        content = (getattr(message, "reasoning_content", None) or "").strip()
        logger.warning(
            "weekly_digest_llm_fn: message.content 係空,改用 reasoning_content (finish_reason=%r)",
            response.choices[0].finish_reason,
        )
    return content

@dataclass
class ExtractedTheme:
    name: str
    description: str = ""


@dataclass
class ExtractedRelation:
    from_theme: str
    to_type: str  # "theme" | "company"
    to_ref: str  # to_type="theme" 就係 theme name；to_type="company" 就係 ticker
    relation_type: str
    polarity: str = "positive"
    confidence: float = 0.5
    reasoning: str = ""


@dataclass
class ExtractionResult:
    themes: list[ExtractedTheme] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)

  
def build_extraction_prompt(
    article_title: str,
    article_content: str,
    known_companies: list[dict],
    grounding_themes: list[str],
    *,
    max_relations: int = 5,
) -> str:
    """
    純function,唔涉及任何API call,方便獨立test。

    known_companies: [{"ticker": "AAPL", "name": "Apple Inc."}, ...]
                      呢啲已經係經過你獨立嘅matching pipeline確認同呢篇文相關嘅公司。
    grounding_themes: 現存主題名清單 —— Concept Grounding 嘅核心，
                       叫LLM有得揀就攞返現成嘅名，減少之後要靠embedding先去重嘅重複。
    """
    companies_block = (
        "\n".join(f"- {c['ticker']}: {c['name']}" for c in known_companies)
        or "(冇已確認相關嘅公司)"
    )
    grounding_block = "\n".join(f"- {name}" for name in grounding_themes) or "(暫時未有現存主題可以參考)"
    relation_types_block = ", ".join(RELATION_TYPES)

    return f"""你係一個財經新聞分析助手，負責由文章入面抽取「市場主題」(theme)同埋佢哋之間嘅
因果/影響關係(relation)，用嚟建立一個持續生長嘅市場知識圖譜(Mind Map)。

# 文章標題
{article_title}

# 文章內容
{article_content}

# 已經確認同呢篇文相關嘅公司
（呢啲係經獨立配對程序確認嘅，你抽取 relation 嗰陣，to_type="company" 淨係可以喺呢個清單入面揀，
唔好自己另外提出第啲公司、亦唔好自己估佢哋嘅 ticker）
{companies_block}

# 現存主題名（Concept Grounding）
（如果文章講緊嘅主題同下面某一個相符或者好相近，請直接攞返呢個名嚟用，唔好自己作一個新講法；
真係搵唔到啱嘅先自己作新名）
{grounding_block}

# 你嘅任務
1. 抽取文章入面提到嘅市場主題(theme)，簡短、概念化(例如「AI伺服器需求上升」、「加息預期」)。
2. 判斷呢啲主題之間，或者主題同上面已確認公司之間，有冇清楚、文章有支持嘅因果/影響關係。
3. relation_type 必須由以下揀一個: {relation_types_block}
4. polarity 必須係 positive / negative / neutral。
5. {CONFIDENCE_GUIDANCE}
6. 淨係抽取文章有清楚支持嘅關係，唔好過度推論或者穿鑿附會。最多抽取 {max_relations} 條你最有信心嘅 relation。
7. 如果文章冇任何明確嘅因果關係，themes/relations 可以係空 list——唔好為咗填滿而勉強作嘢出嚟。
8. 重要：themes 呢個 list 要包括「所有喺 relations 入面用到嘅 theme 名」，
   即使呢個 theme 唔係文章嘅主角、係你由「現存主題名」清單度攞返嚟做 relation 目標嘅，
   都要一併列喺 themes 入面(description 可以留空)。唔好淨係列「新提到」嘅主題。

用 record_extraction 呢個 tool 嚟回答。"""


def _parse_and_validate(raw: dict, *, known_companies: list[dict]) -> ExtractionResult:
    """
    純function,將LLM嘅原始輸出做guardrail驗證:
    - relation_type 唔喺固定enum入面 -> 丟棄
    - to_type="company" 但唔喺已確認公司清單入面 -> 丟棄(防止LLM hallucinate公司)
    - confidence clamp 去 [0, 1]
    - 缺少必要欄位 -> 丟棄
    每次丟棄都會 log 低,方便你之後review LLM輸出質素。
    """
    known_tickers = {c["ticker"] for c in known_companies}

    themes: list[ExtractedTheme] = []
    for item in raw.get("themes", []) or []:
        name = (item.get("name") or "").strip()
        if not name:
            logger.warning("跳過冇 name 嘅 theme: %r", item)
            continue
        themes.append(ExtractedTheme(name=name, description=(item.get("description") or "").strip()))

    relations: list[ExtractedRelation] = []
    for item in raw.get("relations", []) or []:
        from_theme = (item.get("from_theme") or "").strip()
        to_type = item.get("to_type")
        to_ref = (item.get("to_ref") or "").strip()
        relation_type = item.get("relation_type")
        polarity = item.get("polarity") or "positive"
        confidence_raw = item.get("confidence")

        if not from_theme or not to_ref:
            logger.warning("跳過唔完整嘅 relation(缺 from_theme/to_ref): %r", item)
            continue
        if relation_type not in RELATION_TYPES:
            logger.warning("relation_type '%s' 唔喺允許嘅 enum 入面,跳過: %r", relation_type, item)
            continue
        if to_type not in ("theme", "company"):
            logger.warning("to_type '%s' 無效,跳過: %r", to_type, item)
            continue
        if to_type == "company" and to_ref not in known_tickers:
            logger.warning("relation 指住一間未確認嘅公司 '%s',跳過: %r", to_ref, item)
            continue
        if polarity not in POLARITIES:
            polarity = "positive"
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            logger.warning("confidence '%r' 無效,跳過: %r", confidence_raw, item)
            continue
        confidence = max(0.0, min(1.0, confidence))

        relations.append(
            ExtractedRelation(
                from_theme=from_theme,
                to_type=to_type,
                to_ref=to_ref,
                relation_type=relation_type,
                polarity=polarity,
                confidence=confidence,
                reasoning=(item.get("reasoning") or "").strip(),
            )
        )

    return ExtractionResult(themes=themes, relations=relations)


def extract_concepts_and_relations(
    article_title: str,
    article_content: str,
    known_companies: list[dict],
    grounding_themes: list[str],
    *,
    max_relations: int = 5,
    client: Optional[Any] = None,
    model : Optional[str] = model,
  ) -> ExtractionResult:
  client = client or OpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url="https://api.deepseek.com")
  model = model or "deepseek-chat"

  prompt = build_extraction_prompt(
      article_title, article_content, known_companies, grounding_themes, max_relations=max_relations
  )

  # client 係 OpenAI SDK 指去 DeepSeek(base_url="https://api.deepseek.com")，唔係
  # Anthropic client——要用 chat.completions.create() + OpenAI 個 tool-calling
  # 格式(function wrapper)，唔係 Anthropic 嗰套 messages.create()/input_schema。
  response = client.chat.completions.create(
      model=model,
      max_tokens=2048,
      tools=[{"type": "function", "function": EXTRACTION_TOOL_SCHEMA}],
      tool_choice="auto",
      messages=[{"role": "user", "content": prompt}],
  )

  message = response.choices[0].message
  if not message.tool_calls:
      # tool_choice="auto" 冧強制一定要 call 個 tool——DeepSeek 個模型間中會
      # 揀直接用文字回覆(或者判斷冧嘢好抽)，唔 call `record_extraction`。
      # 呢種情況當「呢篇文章/新聞冧抽到任何 theme/relation」處理，返回空
      # ExtractionResult，等 caller(_process_text_for_concepts)嘅
      # 「冧themes冧relations就skip」邏輯自然接住，唔好累個 batch job 死。
      logger.warning(
          "extract_concepts_and_relations: LLM 冇 call tool，當冇嘢可抽。finish_reason=%r, content=%r",
          response.choices[0].finish_reason,
          message.content,
      )
      return ExtractionResult()

  tool_call = message.tool_calls[0]
  arguments = json.loads(tool_call.function.arguments)
  return _parse_and_validate(arguments, known_companies=known_companies)




