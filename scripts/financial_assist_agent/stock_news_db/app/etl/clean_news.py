"""
負責清理 + 分類原始新聞：
  1. 去重 / 標準化欄位
  2. 判斷 news_type (company / industry / macro / other_asset)
  3. 判斷同邊間公司有關 (company matching)，同埋相關 tag

呢部分未來可以接 NLP model（例如簡單 keyword matching 開始，
之後升級做 embedding similarity 或者用 LLM 做分類）。
"""
from __future__ import annotations

from datetime import datetime


def normalize_article(raw: dict) -> dict:
    """將原始新聞轉做入 DB 前嘅標準格式。"""
    return {
        "title": (raw.get("title") or "").strip(),
        "content": (raw.get("content") or "").strip(),
        "source": raw.get("source"),
        "url": raw.get("url"),
        "published_at": _parse_datetime(raw.get("published_at")),
    }


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def match_companies(article: dict, company_lookup: dict[str, int]) -> list[tuple[int, float]]:
    """
    簡單版：用公司名 / ticker 喺標題+內文做 keyword match。
    company_lookup: {"AAPL": company_id, "Apple": company_id, ...}

    回傳 [(company_id, relevance), ...]
    TODO: 日後可以換成更準確嘅 NER / embedding 做法。
    """
    text = f"{article.get('title', '')} {article.get('content', '')}".lower()
    matches: list[tuple[int, float]] = []
    for keyword, company_id in company_lookup.items():
        if keyword.lower() in text:
            matches.append((company_id, 1.0))
    return matches


def classify_news_type(article: dict, matched_companies: list) -> str:
    """粗略規則：有配對到公司就當 company 新聞，否則預設 macro。"""
    if matched_companies:
        return "company"
    return "macro"
