"""
News <-> Company Matching，由粗到細三層：

  Layer 1（直接提及，最平最準）—— 已經喺 analyze_and_save/add_news 嗰陣做咗，
      LLM 抽 ticker 直接寫低 NewsCompanyLink（match_source='direct_mention'，relevance=1.0）。
      呢個 module 唔重做，淨係將佢當做「底線」，Layer 2/3 搵到嘅公司如果已經喺呢層出現，就skip。

  Layer 2（Tag/Category 規則配對，平又快，粗篩）—— 新聞嘅 tag 對照
      TagCategoryRule（tag -> Product.category / Industry.industry_name / Sector.sector_name），
      match 到就寫一行 NewsCompanyLink（match_source='tag_rule'）。

  Layer 3（語義 Embedding 比對，最貴，做埋 Layer 1/2 漏低嘅）—— 攞呢單新聞
      （company_fact_embeddings, entity_type='news'）嘅 vector，
      同 9 個 company detail table 嘅 embedding 做 cosine similarity，
      夠 threshold 先算數，寫一行 NewsCompanyLink（match_source='embedding'，relevance=similarity）。

用法：
    from app.etl.match_news_companies import match_news_to_companies
    match_news_to_companies(news_id)
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import select

from app.database import get_session
from app.models import (
    Company,
    Industry,
    NewsCompanyLink,
    NewsTagLink,
    Product,
    Sector,
    Tag,
    TagCategoryRule,
)
from app.models.embedding import CompanyFactEmbedding, entity_type_values
from app.config import Settings

logger = logging.getLogger(__name__)

settings = Settings()

# company_fact_embeddings 入面淨係得 9 個 detail entity_type 先算「公司背景」，
# 唔可以攞 'news'/'article' 自己嚟同新聞比對。
_COMPANY_DETAIL_ENTITY_TYPES = [t for t in entity_type_values if t not in ("news", "article")]


def _get_linked_company_ids(session, news_id: int) -> set[int]:
    stmt = select(NewsCompanyLink.company_id).where(NewsCompanyLink.news_id == news_id)
    return {row[0] for row in session.execute(stmt).all()}


def _match_by_tag_rule(session, news_id: int) -> dict[int, float]:
    """新聞嘅 tag 對照 TagCategoryRule，回傳 {company_id: relevance}。"""
    tag_names = [
        row[0]
        for row in session.execute(
            select(Tag.tag_name).join(NewsTagLink, NewsTagLink.tag_id == Tag.tag_id).where(
                NewsTagLink.news_id == news_id
            )
        ).all()
    ]
    if not tag_names:
        return {}

    rules = session.scalars(
        select(TagCategoryRule).join(Tag, Tag.tag_id == TagCategoryRule.tag_id).where(Tag.tag_name.in_(tag_names))
    ).all()

    matched: dict[int, float] = {}
    for rule in rules:
        if rule.target_field == "product_category":
            stmt = select(Product.company_id).where(Product.category == rule.target_value)
        elif rule.target_field == "industry":
            stmt = (
                select(Company.company_id)
                .join(Industry, Industry.industry_id == Company.industry_id)
                .where(Industry.industry_name == rule.target_value)
            )
        elif rule.target_field == "sector":
            stmt = (
                select(Company.company_id)
                .join(Sector, Sector.sector_id == Company.sector_id)
                .where(Sector.sector_name == rule.target_value)
            )
        else:
            logger.warning("未知嘅 tag rule target_field: %s，skip", rule.target_field)
            continue

        for row in session.execute(stmt).all():
            matched[row[0]] = settings.TAG_RULE_RELEVANCE

    return matched


def _match_by_embedding(
    session,
    news_id: int,
    *,
    threshold: float = settings.EMBEDDING_SIMILARITY_THRESHOLD,
    top_k: int = settings.EMBEDDING_TOP_K,
) -> dict[int, float]:
    """新聞 embedding 對 9 個 detail table embedding 做 cosine similarity，回傳 {company_id: similarity}。"""
    news_embedding_row = session.scalars(
        select(CompanyFactEmbedding).where(
            CompanyFactEmbedding.entity_type == "news",
            CompanyFactEmbedding.entity_id == news_id,
        )
    ).first()
    if news_embedding_row is None:
        return {}

    distance_expr = CompanyFactEmbedding.embedding.cosine_distance(news_embedding_row.embedding)
    stmt = (
        select(CompanyFactEmbedding.company_id, distance_expr.label("distance"))
        .where(
            CompanyFactEmbedding.entity_type.in_(_COMPANY_DETAIL_ENTITY_TYPES),
            CompanyFactEmbedding.company_id.isnot(None),
        )
        .order_by(distance_expr)
        .limit(top_k)
    )

    best_per_company: dict[int, float] = {}
    for company_id, distance in session.execute(stmt).all():
        similarity = 1 - distance
        if similarity < threshold:
            continue
        if company_id not in best_per_company or similarity > best_per_company[company_id]:
            best_per_company[company_id] = similarity

    return best_per_company


def match_news_to_companies(
    news_id: int,
    *,
    embedding_threshold: float = settings.EMBEDDING_SIMILARITY_THRESHOLD,
    embedding_top_k: int = settings.EMBEDDING_TOP_K,
) -> dict[str, int]:
    """跑 Layer 2 + Layer 3，將搵到、Layer 1 未覆蓋嘅公司寫入 NewsCompanyLink。"""
    with get_session() as session:
        already_linked = _get_linked_company_ids(session, news_id)

        tag_matches = _match_by_tag_rule(session, news_id)
        new_from_tag = {cid: rel for cid, rel in tag_matches.items() if cid not in already_linked}
        for company_id, relevance in new_from_tag.items():
            session.add(
                NewsCompanyLink(
                    news_id=news_id, company_id=company_id, relevance=relevance, match_source="tag_rule"
                )
            )
        already_linked |= new_from_tag.keys()

        embedding_matches = _match_by_embedding(
            session, news_id, threshold=embedding_threshold, top_k=embedding_top_k
        )
        new_from_embedding = {cid: sim for cid, sim in embedding_matches.items() if cid not in already_linked}
        for company_id, similarity in new_from_embedding.items():
            session.add(
                NewsCompanyLink(
                    news_id=news_id, company_id=company_id, relevance=similarity, match_source="embedding"
                )
            )

        session.commit()

    logger.info(
        "news_id=%s: tag_rule 新增 %d, embedding 新增 %d",
        news_id,
        len(new_from_tag),
        len(new_from_embedding),
    )
    return {"tag_rule": len(new_from_tag), "embedding": len(new_from_embedding)}

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m app.etl.match_news_companies <news_id>")
        sys.exit(1)

    news_id = int(sys.argv[1])
    match_news_to_companies(news_id)