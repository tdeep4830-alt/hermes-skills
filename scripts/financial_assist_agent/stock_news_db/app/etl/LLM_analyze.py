"""
用 Claude API 分析一段新聞原文，再將分析結果連同原文一齊存入 DB
(經 DatabaseManager.add_news)。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from openai import OpenAI

from app.config import settings
from app.models import News

if TYPE_CHECKING:
    from app.manager.db_manager import DatabaseManager

client = OpenAI(api_key=settings.OPENAI_API_KEY, base_url="https://api.deepseek.com")
model = "deepseek-v4-pro"

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

_ANALYSIS_COMPANY_SYSTEM_PROMPT = """你係財經新聞分析員。閱讀用戶提供嘅公司資料，淨係回覆一個 JSON object，
唔好加任何解釋文字，唔好用 markdown code fence。JSON schema:

{
  "business_model": string,          // 公司業務模式嘅簡短描述
  "main_products": string[],          // 公司主要產品/服務嘅簡短描述
  "technology": string[],              // 公司主要技術/專利嘅簡短描述
  "main_services": string[],              // 公司主要服務/平台嘅簡短描述
  "governmental_programs_and_regulations": string[],  // 公司受惠嘅政府計劃/法規嘅簡短描述
  "Manufucturing_and_Supply_Chain": string[],  // 公司製造/供應鏈嘅簡短描述
  "supply_chain": string[],  // 公司供應鏈嘅簡短描述
  "competitors": string[]  // 公司主要競爭對手嘅簡短描述
}"""

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


def AI_analyze(text: str, *, model: str = "deepseek-v4-pro", prompt) -> dict:
    """叫 Claude 分析新聞原文，回傳結構化結果 。"""
    response = client.chat.completions.create(
        model=model,
        max_tokens=4096,
        messages=[
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        stream=False,
        reasoning_effort="high",
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


def analyze_and_save_news(
    db: "DatabaseManager",
    text: str,
    *,
    source: Optional[str] = None,
    url: Optional[str] = None,
    published_at: Optional[datetime] = None,
    model: str = "deepseek-v4-pro",
) -> News:
    """分析新聞原文，再連公司 / tag 一齊存入 DB。"""
    analysis = AI_analyze(text, model=model, prompt=_ANALYSIS_NEWS_SYSTEM_PROMPT)

    company_ids = []
    for ticker in analysis.get("tickers") or []:
        company = db.get_company_by_ticker(ticker)
        if company is not None:
            company_ids.append(company.company_id)
        else:
            new_company = db.create_company(ticker=ticker, name_en=ticker)  # 冇就先創一個 company entry，name先用ticker頂住
            company_ids.append(new_company.company_id)

    return db.add_news(
        title=analysis["title"],
        description=analysis.get("description"),
        content=text,
        source=source,
        url=url,
        published_at=published_at or datetime.now(timezone.utc),
        news_type=analysis.get("news_type", "company"),
        sentiment=analysis.get("sentiment"),
        company_ids=company_ids,
        tag_names=analysis.get("tags") or [],
    )



if __name__ == "__main__":
    news = """Nvidia (NVDA) is investing $1B in South Korean internet conglomerate Naver (NHNCF) to expand the latter's AI data center currently under construction.
The capital influx will allow the site to grow from the planned 55 megawatts to 200 megawatts using the DSX platform.
Brookfield (BN) will fund up to another $9B through a nonbinding term sheet.
Separately, Nvidia (NVDA) is expanding a collaboration with SK Group that includes constructing a 2-gigawatt Vera Rubin DSX AI factory.
In June, Nvidia (NVDA) said SK Telecom (SKM) plans to build a gigawatt-scale AI Cloud in South Korea.
SK hynix (SKHY) is also partnering with Nvidia to develop next-generation AI memory."""

    result = AI_analyze(news, prompt=_ANALYSIS_NEWS_SYSTEM_PROMPT)
    print(json.dumps(result, indent=2, ensure_ascii=False))