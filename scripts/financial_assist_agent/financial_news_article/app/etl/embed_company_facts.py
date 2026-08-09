"""
用 OpenAI embedding API 幫 9 個 company detail table
（Product / Technology / Service / GovernmentalProgramAndRegulation / ManufacturingProcess /
SupplyChainAndLogistics / Competitor / Risk / ManagementDiscussionAndAnalysis /
LegalAndRegulatoryIssues）嘅 description（連 name/title），
以及 News.description / AnalysisArticle.thesis 計 embedding，存入 company_fact_embeddings。

news/article 個 entity_id 淨係指返佢自己嘅 news_id，唔理呢單新聞/文章連咗幾多間公司——
company_id 呢啲 row 一律存 NULL，公司關係查返 news_company_link / analysis_article_company_link
（連埋 is_primary），唔喺呢個 table 度重複緩存，以免之後 link 改咗要諗點同步。

用 content_hash 做兩層 dedup，減少唔必要嘅 embedding API call：
  1. 呢個 entity 舊有 embedding 嘅 content_hash 同新計嘅一樣 -> description 冇變過，成行 skip
  2. description 變咗，但個 content_hash 已經喺其他 entity 度出現過
     （即係文字內容一模一樣）-> 直接攞現成嘅 embedding vector 嚟用，唔使再 call API
"""
from __future__ import annotations
from __future__ import annotations

from typing import Any, Optional
from app.models.concept import EMBEDDING_DIM
import hashlib
import logging
from typing import Iterable, Optional
from openai import OpenAI
from dotenv import load_dotenv
import os

from app.config import settings
from app.database import get_session
from app.manager.db_manager import DatabaseManager
from app.models import (
    AnalysisArticle,
    Competitor,
    GovernmentalProgramAndRegulation,
    LegalAndRegulatoryIssues,
    ManagementDiscussionAndAnalysis,
    ManufacturingProcess,
    News,
    Product,
    Risk,
    Service,
    SupplyChainAndLogistics,
    Technology,
)
import sys

from pathlib import Path

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-small"

# 呢度要用返正牌 OpenAI API（唔係 LLM_analyze.py 嗰個指去 DeepSeek base_url 嘅 client）——
# 用獨立嘅 settings.EMBEDDING_API_KEY（喺 .env 度填一個有 embeddings 權限嘅真 OpenAI key），
# 唔好同 settings.OPENAI_API_KEY 撈埋（嗰個而家實際上係俾 DeepSeek client 用嘅 key）。
load_dotenv(dotenv_path=Path(__file__).parent.parent.parent.parent.parent.parent / ".env")
api_key = os.getenv("EMBEDDING_API_KEY")
if not api_key:
    raise ValueError("EMBEDDING_API_KEY 未喺環境變數度設定，請喺 .env 度填一個有 embeddings 權限嘅真 OpenAI key")
_embedding_client = OpenAI(api_key=api_key)

# entity_type -> (model class, PK 欄位名, name/title 欄位名)
_ENTITY_SPECS: dict[str, tuple[type, str, str]] = {
    "product": (Product, "product_id", "product_name"),
    "technology": (Technology, "technology_id", "technology_name"),
    "service": (Service, "service_id", "service_name"),
    "governmental_program": (GovernmentalProgramAndRegulation, "program_id", "program_name"),
    "manufacturing_process": (ManufacturingProcess, "process_id", "process_name"),
    "supply_chain": (SupplyChainAndLogistics, "supply_chain_id", "supply_chain_name"),
    "competitor": (Competitor, "competitor_id", "competitor_name"),
    "risk": (Risk, "risk_id", "risk_type"),
    "mdna": (ManagementDiscussionAndAnalysis, "mdna_id", "mdna_title"),
    "legal_issue": (LegalAndRegulatoryIssues, "issue_id", "issue_title"),
}


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _call_embedding_api(text: str) -> list[float]:
    response = _embedding_client.embeddings.create(model=EMBEDDING_MODEL, input=text)
    return response.data[0].embedding


def _iter_detail_table_rows(session) -> Iterable[tuple[str, int, Optional[int], str]]:
    """9 個 company detail table：yield (entity_type, entity_id, company_id, content_text)。"""
    for entity_type, (model, id_attr, label_attr) in _ENTITY_SPECS.items():
        for row in session.query(model).all():
            description = row.description
            if not description:
                continue
            label = getattr(row, label_attr)
            content_text = f"{label}: {description}"
            yield entity_type, getattr(row, id_attr), row.company_id, content_text


def _iter_news_rows(session) -> Iterable[tuple[str, int, Optional[int], str]]:
    """News.description：company_id 一律 NULL（唔理連咗幾多間公司，見檔案頭註解）。"""
    for news in session.query(News).all():
        if not news.description:
            continue
        content_text = f"{news.title}: {news.description}"
        yield "news", news.news_id, None, content_text


def _iter_article_rows(session) -> Iterable[tuple[str, int, Optional[int], str]]:
    """AnalysisArticle.thesis：company_id 一律 NULL，理由同 _iter_news_rows。"""
    for article in session.query(AnalysisArticle).all():
        if not article.thesis:
            continue
        label = article.title or article.news.title
        content_text = f"{label}: {article.thesis}"
        yield "article", article.news_id, None, content_text


def _iter_source_rows() -> Iterable[tuple[str, int, Optional[int], str]]:
    """yield (entity_type, entity_id, company_id, content_text)，淨係得有內容嘅 row。"""
    with get_session() as session:
        yield from _iter_detail_table_rows(session)
        yield from _iter_news_rows(session)
        yield from _iter_article_rows(session)


def embed_entity(
    db: DatabaseManager, entity_type: str, entity_id: int, company_id: Optional[int], content_text: str
) -> str:
    """
    幫單一個 entity 做 embedding（有就更新，冇就新增），用返 batch job 嗰套 content_hash dedup 邏輯。
    俾 embed_company_facts()（batch）同 embed_news()/embed_article()（即時，新聞一入 DB 就叫）共用。
    回傳 "embedded" / "reused" / "skipped" 講低發生咗咩事，方便 caller 記 log。
    """
    content_hash = _content_hash(content_text)

    existing = db.get_fact_embedding(entity_type, entity_id)
    if existing is not None and existing.content_hash == content_hash:
        return "skipped"

    reusable_embedding = db.find_embedding_by_hash(content_hash)
    if reusable_embedding is not None:
        embedding = reusable_embedding
        outcome = "reused"
    else:
        embedding = _call_embedding_api(content_text)
        outcome = "embedded"

    db.upsert_fact_embedding(
        entity_type=entity_type,
        entity_id=entity_id,
        company_id=company_id,
        content_text=content_text,
        content_hash=content_hash,
        embedding=embedding,
        embedding_model=EMBEDDING_MODEL,
    )
    return outcome


def embed_news(news_id: int, *, db: Optional[DatabaseManager] = None) -> Optional[str]:
    """即時 embed 單一則新聞嘅 description，畀 analyze_and_save() 喺新聞一入 DB 就叫。"""
    with get_session() as session:
        news = session.get(News, news_id)
        if news is None or not news.description:
            return None
        content_text = f"{news.title}: {news.description}"

    outcome = embed_entity(db or DatabaseManager(), "news", news_id, None, content_text)
    logger.info("embed_news(%s): %s", news_id, outcome)
    return outcome


def embed_article(article_id: int, *, db: Optional[DatabaseManager] = None) -> Optional[str]:
    """即時 embed 單一篇分析文章嘅 thesis，畀 analyze_and_save() 喺文章一入 DB 就叫。"""
    with get_session() as session:
        article = session.get(AnalysisArticle, article_id)
        if article is None or not article.thesis:
            return None
        label = article.title or article.news.title
        content_text = f"{label}: {article.thesis}"

    outcome = embed_entity(db or DatabaseManager(), "article", article_id, None, content_text)
    logger.info("embed_article(%s): %s", article_id, outcome)
    return outcome


def embed_company_facts() -> None:
    db = DatabaseManager()
    counts = {"embedded": 0, "reused": 0, "skipped": 0}

    for entity_type, entity_id, company_id, content_text in _iter_source_rows():
        outcome = embed_entity(db, entity_type, entity_id, company_id, content_text)
        counts[outcome] += 1

    logger.info("新 embed: %d, 重用現成 embedding: %d, 跳過(冇變): %d", counts["embedded"], counts["reused"], counts["skipped"])


def embed_text(text: str, *, client: Optional[Any] = None, model: Optional[str] = None) -> list[float]:
    """單段文字轉做一個 embedding 向量。"""
    return embed_texts([text], client=client, model=model)[0]


def embed_texts(
    texts: list[str], *, client: Optional[Any] = None, model: Optional[str] = None
) -> list[list[float]]:
    """
    Batch 版本——攞一個 list 嘅文字，一次過 call API 攞返晒啲向量。
    比逐段 call 平好多、快好多，一定要用呢個做批量處理，唔好自己寫 for loop 逐個 embed_text()。
    """
    if not texts:
        return []
    client = client or _embedding_client
    model = model or settings.EMBEDDING_MODEL

    response = client.embeddings.create(model=model, input=texts)
    vectors = [item.embedding for item in response.data]

    for vector in vectors:
        if len(vector) != EMBEDDING_DIM:
            raise ValueError(
                f"embedding 維度 ({len(vector)}) 同 EMBEDDING_DIM ({EMBEDDING_DIM}) 唔夾， "
                "換咗 model 記得同時改 app/models/concept.py 嘅 EMBEDDING_DIM 並開新 migration。"
            )

    return vectors


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    embed_company_facts()
