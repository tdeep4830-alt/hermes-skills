"""
News <-> AnalysisArticle Matching，由粗到細三層（同 match_news_companies.py 一樣嘅思路）：

  Layer 1（shared_company，最平）—— News X 同 Article Y 各自經
      NewsCompanyLink / ArticleCompanyLink 連咗同一間公司，就當相關。

  Layer 2（shared_tag）—— News 同 Article 各自嘅 tag
      （NewsTagLink / ArticleTagLink）有交集，就算 match。

  Layer 3（embedding，最貴）—— company_fact_embeddings 入面
      entity_type='news' 嘅 vector 同 entity_type='article' 嘅 vector 做 cosine similarity。

AnalysisArticle 本身其實係一行 News 加咗 thesis 等分析欄位，
所以呢度嘅 article_id 就係嗰篇文章底層嗰行 news 嘅 news_id，
match 嗰陣要排除自己同自己（news_id == article_id）。

用法：
    from app.etl.match_news_articles import match_news_to_articles
    match_news_to_articles(news_id)
"""
from __future__ import annotations

import logging

from sqlalchemy import select


from app.database import get_session
from app.models import ArticleCompanyLink, ArticleTagLink, NewsArticleMatch, NewsCompanyLink, NewsTagLink
from app.models.embedding import CompanyFactEmbedding
import os
from app.config import Settings


logger = logging.getLogger(__name__)
settings = Settings()  # 確保 config.py 嘅 settings 係已經 load 咗 .env 嘅，唔會有空值

SHARED_COMPANY_RELEVANCE = settings.SHARED_COMPANY_RELEVANCE
SHARED_TAG_RELEVANCE = settings.SHARED_TAG_RELEVANCE

# Layer 3 嘅預設參數，同 match_news_companies.py 一樣，日後有多啲數據先再校準。
EMBEDDING_SIMILARITY_THRESHOLD = settings.EMBEDDING_SIMILARITY_THRESHOLD
EMBEDDING_TOP_K = settings.EMBEDDING_TOP_K


def _get_matched_article_ids(session, news_id: int) -> set[int]:
    stmt = select(NewsArticleMatch.article_id).where(NewsArticleMatch.news_id == news_id)
    return {row[0] for row in session.execute(stmt).all()}


def _match_by_shared_company(session, news_id: int) -> dict[int, float]:
    news_company_ids = {
        row[0] for row in session.execute(select(NewsCompanyLink.company_id).where(NewsCompanyLink.news_id == news_id)).all()
    }
    if not news_company_ids:
        return {}

    stmt = (
        select(ArticleCompanyLink.article_id)
        .where(ArticleCompanyLink.company_id.in_(news_company_ids), ArticleCompanyLink.article_id != news_id)
        .distinct()
    )
    return {row[0]: SHARED_COMPANY_RELEVANCE for row in session.execute(stmt).all()}


def _match_by_shared_tag(session, news_id: int) -> dict[int, float]:
    news_tag_ids = {
        row[0] for row in session.execute(select(NewsTagLink.tag_id).where(NewsTagLink.news_id == news_id)).all()
    }
    if not news_tag_ids:
        return {}

    stmt = (
        select(ArticleTagLink.article_id)
        .where(ArticleTagLink.tag_id.in_(news_tag_ids), ArticleTagLink.article_id != news_id)
        .distinct()
    )
    return {row[0]: SHARED_TAG_RELEVANCE for row in session.execute(stmt).all()}


def _match_by_embedding(
    session, news_id: int, *, threshold: float = EMBEDDING_SIMILARITY_THRESHOLD, top_k: int = EMBEDDING_TOP_K
) -> dict[int, float]:
    news_embedding_row = session.scalars(
        select(CompanyFactEmbedding).where(
            CompanyFactEmbedding.entity_type == "news", CompanyFactEmbedding.entity_id == news_id
        )
    ).first()
    if news_embedding_row is None:
        return {}

    distance_expr = CompanyFactEmbedding.embedding.cosine_distance(news_embedding_row.embedding)
    stmt = (
        select(CompanyFactEmbedding.entity_id, distance_expr.label("distance"))
        .where(CompanyFactEmbedding.entity_type == "article", CompanyFactEmbedding.entity_id != news_id)
        .order_by(distance_expr)
        .limit(top_k)
    )

    matched: dict[int, float] = {}
    for article_id, distance in session.execute(stmt).all():
        similarity = 1 - distance
        if similarity < threshold:
            continue
        matched[article_id] = similarity
    return matched


def match_news_to_articles(
    news_id: int,
    *,
    embedding_threshold: float = EMBEDDING_SIMILARITY_THRESHOLD,
    embedding_top_k: int = EMBEDDING_TOP_K,
) -> dict[str, int]:
    """跑三層 matching，將搵到、未被較粗層覆蓋嘅 article 寫入 news_article_matches。"""
    with get_session() as session:
        already_matched = _get_matched_article_ids(session, news_id)

        company_matches = _match_by_shared_company(session, news_id)
        new_from_company = {aid: rel for aid, rel in company_matches.items() if aid not in already_matched}
        for article_id, relevance in new_from_company.items():
            session.add(
                NewsArticleMatch(
                    news_id=news_id, article_id=article_id, relevance=relevance, match_source="shared_company"
                )
            )
        already_matched |= new_from_company.keys()

        tag_matches = _match_by_shared_tag(session, news_id)
        new_from_tag = {aid: rel for aid, rel in tag_matches.items() if aid not in already_matched}
        for article_id, relevance in new_from_tag.items():
            session.add(
                NewsArticleMatch(news_id=news_id, article_id=article_id, relevance=relevance, match_source="shared_tag")
            )
        already_matched |= new_from_tag.keys()

        embedding_matches = _match_by_embedding(
            session, news_id, threshold=embedding_threshold, top_k=embedding_top_k
        )
        new_from_embedding = {aid: sim for aid, sim in embedding_matches.items() if aid not in already_matched}
        for article_id, similarity in new_from_embedding.items():
            session.add(
                NewsArticleMatch(
                    news_id=news_id, article_id=article_id, relevance=similarity, match_source="embedding"
                )
            )

        session.commit()

    logger.info(
        "news_id=%s: shared_company 新增 %d, shared_tag 新增 %d, embedding 新增 %d",
        news_id,
        len(new_from_company),
        len(new_from_tag),
        len(new_from_embedding),
    )
    return {
        "shared_company": len(new_from_company),
        "shared_tag": len(new_from_tag),
        "embedding": len(new_from_embedding),
    }

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    import sys

    if len(sys.argv) != 2:
        print("Usage: python -m app.etl.match_news_articles <news_id>")
        sys.exit(1)

    news_id = int(sys.argv[1])
    match_news_to_articles(news_id)