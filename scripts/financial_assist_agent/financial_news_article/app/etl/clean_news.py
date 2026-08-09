"""
將 fetch_news.py 攞返嚟嘅原始新聞清單做：

1. Relevance filter —— 淨係留低同 AI/Tech 有關嘅。RSS 嗰幾個來源已經係
   AI 分類 feed，理論上 100% 相關，但都照跑一次 filter 做多一重保險；
   Hacker News front page / Finnhub general(technology分類)呢兩類唔係
   AI-scoped，一定要靠呢層 filter 收窄返做 AI/Tech，唔想成個 pipeline
   咩科技新聞都收，污染晒個 Mind Map。
2. 去重 —— 同一單新聞成日會有幾個來源都報導，用 url exact match 做第一層，
   title 相似度做第二層(唔同來源標題幾乎一樣但 url 唔同嗰種情況)。
3. 公司配對 —— 喺 title/summary 度搵你 DB 已有嘅 ticker/公司名有冇被
   提到，預先填 company_ids(Finnhub company news 嗰啲已經知道係邊間
   公司，唔使再靠關鍵字估，直接用 known_tickers 對返 company_id)。

呢層完全唔識點連 DB、點 fetch，純粹做文字處理，方便獨立 test。
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

# 判斷一則新聞係咪同 AI/Tech 有關——覆蓋面刻意收窄(你話唔想太闊)，
# 淨係關注 AI 本身、同支撐 AI 嘅底層科技(晶片/半導體/雲端/資料中心)。
# 想擴闊/收窄範圍，改呢個 list 就得，唔使郁其他邏輯。
AI_TECH_KEYWORDS: list[str] = [
    "ai",
    "artificial intelligence",
    "machine learning",
    "llm",
    "large language model",
    "generative ai",
    "genai",
    "chatgpt",
    "openai",
    "anthropic",
    "gemini",
    "copilot",
    "neural network",
    "deep learning",
    "gpu",
    "chip",
    "chips",
    "semiconductor",
    "nvidia",
    "tsmc",
    "foundry",
    "data center",
    "datacenter",
    "cloud computing",
]

# 注意：呢度特登冇用 `\b` word boundary——Python嘅re對unicode字串嘅`\w`
# 包埋晒CJK表意文字，即係話「AI晶片」入面「AI」同「晶」之間根本冇`\b`
# 邊界(兩邊都算`\w`)，用`\b`會令「AI」呢類關鍵字喺冇空格隔開嘅中文句子
# (例如「AI晶片需求上升」)入面完全match唔到。改用自訂嘅lookaround：
# 淨係將ASCII英文字母/數字當做「會連住嘅字元」，CJK字元同標點都當boundary，
# 咁樣「NVDAX」先至唔會誤判做match咗「NVDA」，但「AI晶片」入面嘅「AI」
# 郁可以正常match到。
_KEYWORD_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(" + "|".join(re.escape(k) for k in AI_TECH_KEYWORDS) + r")(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def is_ai_tech_relevant(item: dict[str, Any]) -> bool:
    """Finnhub company news(已知關聯緊邊間公司)一律當相關，唔使靠關鍵字估。"""
    if item.get("known_tickers"):
        return True
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    return bool(_KEYWORD_PATTERN.search(text))


def filter_ai_tech_relevant(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if is_ai_tech_relevant(item)]


def _normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title or "").strip().lower()


def deduplicate(
    items: list[dict[str, Any]], *, title_similarity_threshold: float = 0.9
) -> list[dict[str, Any]]:
    """
    第一層用 url exact match 去重(最可靠)；第二層用 title 相似度
    (唔同來源報導同一單嘢，url 唔同但標題幾乎一樣)。保留第一次見到嗰個
    (呼叫呢個function之前嘅來源清單順序有意義：可以將編輯精選嘅RSS放前面，
    優先保留嗰個版本)。
    """
    seen_urls: set[str] = set()
    seen_titles: list[str] = []
    deduped: list[dict[str, Any]] = []

    for item in items:
        url = (item.get("url") or "").strip()
        if url and url in seen_urls:
            continue
        normalized_title = _normalize_title(item.get("title", ""))
        if normalized_title and any(
            SequenceMatcher(None, normalized_title, seen).ratio() >= title_similarity_threshold
            for seen in seen_titles
        ):
            continue
        if url:
            seen_urls.add(url)
        if normalized_title:
            seen_titles.append(normalized_title)
        deduped.append(item)

    return deduped


def match_companies(item: dict[str, Any], known_companies: list[dict[str, Any]]) -> list[int]:
    """
    喺 title/summary 度搵已知公司(ticker 或者 name_en)有冇被提到。
    Finnhub company news 已經知道係邊間公司，直接用 known_tickers 對返
    company_id，準過關鍵字估。

    known_companies: [{"company_id": int, "ticker": str, "name_en": str}, ...]
    """
    if item.get("known_tickers"):
        ticker_set = {t.upper() for t in item["known_tickers"]}
        return [c["company_id"] for c in known_companies if c["ticker"].upper() in ticker_set]

    text = f"{item.get('title', '')} {item.get('summary', '')}"
    matched_ids: list[int] = []
    for company in known_companies:
        ticker = company["ticker"]
        name = company.get("name_en") or ""
        # 同 _KEYWORD_PATTERN 一樣，唔用 `\b`——「NVDA股價上升」呢種
        # ticker同CJK字元冇空格隔開嘅寫法，`\b`會完全match唔到。
        ticker_pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])")
        if ticker_pattern.search(text) or (name and name.lower() in text.lower()):
            matched_ids.append(company["company_id"])
    return matched_ids


def clean_and_prepare(
    raw_items: list[dict[str, Any]], known_companies: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """一步過：relevance filter -> dedup -> 配對公司，回傳可以直接俾 load_news.py 用嘅清單。"""
    relevant = filter_ai_tech_relevant(raw_items)
    deduped = deduplicate(relevant)
    for item in deduped:
        item["company_ids"] = match_companies(item, known_companies)
    return deduped
