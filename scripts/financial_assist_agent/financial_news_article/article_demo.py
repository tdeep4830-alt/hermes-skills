"""
示範點樣用 DatabaseManager 操作分析文章(AnalysisArticle)嘅 CRUD,
同埋點樣由分析文章跑 Mind Map 抽取(process_article_for_concepts，用假LLM/embedding，
唔使真係有 API key 都跑得，方便你喺冇 key 嘅環境都睇到成條流程點運作)。

執行前：docker compose up -d + alembic upgrade head 先。

執行： python -m scripts.article_demo
"""
from datetime import datetime, timezone

from app.etl.extract_concepts import process_article_for_concepts
from app.etl.LLM_analyze import ExtractedRelation, ExtractedTheme, ExtractionResult
from app.manager import DatabaseManager
from app.manager.generic import model_to_dict

EMBEDDING_DIM = 1536


def fake_embed_fn(texts: list[str]) -> list[list[float]]:
    """假嘅 embedding function，唔使真係 call OpenAI，靠 hash 生成確定性向量。"""
    vectors = []
    for i, text in enumerate(texts):
        vec = [0.0] * EMBEDDING_DIM
        vec[hash(text) % EMBEDDING_DIM] = 1.0
        vectors.append(vec)
    return vectors


def fake_llm_extract_fn(
    *, article_title, article_content, known_companies, grounding_themes, max_relations
):
    """假嘅 LLM，模擬由分析文章抽取到一個 theme + 一條 relation。"""
    ticker = known_companies[0]["ticker"] if known_companies else "UNKNOWN"
    return ExtractionResult(
        themes=[ExtractedTheme(name="AI帶動電力需求", description="AI伺服器耗電量持續上升")],
        relations=[
            ExtractedRelation(
                from_theme="AI帶動電力需求",
                to_type="company",
                to_ref=ticker,
                relation_type="benefits",
                polarity="positive",
                confidence=0.8,
                reasoning="分析文章明確指出AI伺服器耗電量上升將帶動電力公司受惠",
            )
        ],
    )


def main() -> None:
    db = DatabaseManager()

    # ---------- Create ----------
    company = db.create_company(ticker="PWRDEMO", name_en="Power Demo Co.")
    article = db.add_article(
        title="分析報告：AI發展帶動電力需求",
        published_at=datetime.now(timezone.utc),
        description="分析AI伺服器耗電量對電力行業嘅影響",
        thesis="AI伺服器耗電量持續上升，將帶動相關電力公司受惠",
        conclusion="睇好，建議關注電力供應商",
        sentiment="positive",
        source="Demo Research",
        company_ids=[company.company_id],
        primary_company_id=company.company_id,
        tag_names=["AI基建"],
    )
    print("新增分析文章:", model_to_dict(article))

    # ---------- Read ----------
    fetched = db.get_article(article.news_id)
    print("用 news_id 查返:", fetched.title)

    full = db.get_article_full(article.news_id)
    linked_companies = [link.company.ticker for link in full.company_links]
    linked_tags = [link.tag.tag_name for link in full.tag_links]
    print("呢篇文章連結緊嘅公司:", linked_companies, "| tags:", linked_tags, "| 底下News標題:", full.news.title)

    company_articles = db.get_articles_for_company(company.company_id)
    print(f"PWRDEMO 相關分析文章數量: {len(company_articles)}")

    search_result = db.search_articles(keyword="電力需求")
    print(f"關鍵字 '電力需求' 搜尋到 {len(search_result)} 篇文章")

    # ---------- Update ----------
    db.update_article(article.news_id, conclusion="睇好，上調目標價")
    print("更新後 conclusion:", db.get_article(article.news_id).conclusion)

    # ---------- 用假 LLM/embedding 跑 Mind Map 抽取(唔使真係有 API key) ----------
    stats = process_article_for_concepts(
        db,
        article.news_id,
        llm_extract_fn=fake_llm_extract_fn,
        embed_fn=fake_embed_fn,
    )
    print("process_article_for_concepts() 統計:", stats)

    theme_matches = db.find_similar_concepts(
        fake_embed_fn(["AI帶動電力需求: AI伺服器耗電量持續上升"])[0], threshold=0.99
    )
    assert len(theme_matches) == 1, "應該搵到啱啱由分析文章抽取出嚟嗰個 theme concept"
    theme_concept = theme_matches[0][0]
    company_concept, _ = db.get_or_create_company_concept(company.company_id)
    relation = db.find_relation(theme_concept.concept_id, company_concept.concept_id, "benefits")
    assert relation is not None, "應該搵到由分析文章抽取出嚟嗰條 relation"
    evidence = db.list_relation_evidence(relation.relation_id)
    print(f"由呢篇分析文章建立嘅 relation: reinforcement_count={relation.reinforcement_count}, "
          f"evidence.news_id={evidence[0].news_id}(應該等於 article.news_id={article.news_id})")
    assert evidence[0].news_id == article.news_id

    # ---------- Delete (清理返晒 Concept Graph + 分析文章 + 公司，等呢個 script 可以重複執行) ----------
    db.delete_concept(theme_concept.concept_id)
    db.delete_concept(company_concept.concept_id)
    deleted = db.delete_news(article.news_id)  # shared-PK：刪 news 會連埋 article/link 一齊清
    print("刪除分析文章(連底下News)成功:", deleted)
    db.delete_company(company.company_id)

    db.dispose()
    print("=== article_demo 全部檢查完成 ===")


if __name__ == "__main__":
    main()
