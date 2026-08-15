"""
成條 pipeline 嘅串連點:攞一個已經 load 咗、亦已經配對咗公司嘅 news_id
(或者一篇分析文章嘅 article_id),call LLM 抽取 theme/relation,
再寫入 Mind Map(Concept Graph)。

執行順序假設:
1. app/etl/run_daily.py 已經幫呢則新聞/文章做咗 fetch/clean/load,
   同埋(用你獨立嘅 matching pipeline)寫低咗 NewsCompanyLink / ArticleCompanyLink。
2. 呢個 module 先至跑——攞返「已確認相關嘅公司」做已知資訊,
   叫 LLM 淨係負責 theme + relation,唔使佢重新判斷邊間公司相關。

呢個檔案有兩個對外入口，共用同一套「寫入 Mind Map」邏輯：

- `process_news_for_concepts(db, news_id)` —— 由一般新聞抽取。
- `process_article_for_concepts(db, article_id)` —— 由分析員撰寫嘅分析文章抽取。
  同 News 唔同嘅地方：已知公司嚟自 `ArticleCompanyLink`(唔係
  `NewsCompanyLink`)；餵俾 LLM 嘅文字嚟自 `thesis`/`conclusion`/
  `description` 呢幾個分析員專屬欄位(唔係一般新聞嘅 `content`)；
  預設 `max_relations` 亦調高咗(一篇正式分析報告通常會覆蓋多過一個
  thesis)。兩者底層都係 call 返同一個 `_process_text_for_concepts()`。

`llm_extract_fn` / `embed_fn` 兩個參數係俾你注入測試用嘅 stub,
唔傳就用真係會 call API 嘅版本(app.etl.llm_client / app.etl.embeddings)。
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from sqlalchemy import func, select

from app.etl.embed_company_facts import embed_texts
from app.etl.LLM_analyze import ExtractedTheme, ExtractionResult, extract_concepts_and_relations
from app.models import AnalysisArticle, Concept, ConceptRelation
from app.manager.db_manager import DatabaseManager

logger = logging.getLogger(__name__)

LlmExtractFn = Callable[..., ExtractionResult]
EmbedFn = Callable[[list[str]], list[list[float]]]


def get_known_companies(db, news_id: int) -> list[dict]:
    """攞返呢則新聞已經配對咗嘅公司(ticker/name/company_id)。"""
    news = db.get_news_full(news_id)
    if news is None:
        return []
    return [
        {
            "ticker": link.company.ticker,
            "name": link.company.name_en,
            "company_id": link.company.company_id,
        }
        for link in news.company_links
    ]


def get_grounding_themes(db, company_ids: list[int], *, limit: int = 15) -> list[Concept]:
    """
    Concept Grounding 嘅候選清單:優先攞返「已經同呢啲公司有關聯」嘅現存主題,
    搵唔到(例如全新公司)就 fallback 做全域最近有更新嘅主題。
    """
    seen_ids: set[int] = set()
    themes: list[Concept] = []
    for company_id in company_ids:
        for concept in db.get_related_themes_for_company(company_id, limit=limit):
            if concept.concept_id not in seen_ids:
                seen_ids.add(concept.concept_id)
                themes.append(concept)

    if not themes:
        themes = db.get_top_themes(limit=limit)

    return themes[:limit]


def _process_text_for_concepts(
    db,
    *,
    title: str,
    content: str,
    known_companies: list[dict],
    source_id: int,
    log_label: str,
    llm_extract_fn: LlmExtractFn,
    embed_fn: EmbedFn,
    max_relations: int,
    similarity_threshold: float,
    grounding_limit: int,
) -> dict[str, int]:
    """
    共用核心：畀咗題目/內文/已知公司之後,點樣 call LLM 再寫入 Mind Map。
    `process_news_for_concepts()` 同 `process_article_for_concepts()`
    淨係負責「點樣組裝呢幾樣輸入」，實際 call LLM + 寫 DB 嘅邏輯全部喺呢度,
    避免兩個入口各自維護一份重複邏輯。

    `source_id`：寫 evidence 用嘅 `news_id`(News 同 AnalysisArticle 係
    shared-PK，所以 article_id 傳落嚟呢度一樣岩)。
    `log_label`：純粹俾 log 訊息分辨嚟自邊個入口(例如 "news_id" / "article_id")。
    """
    company_ids = [c["company_id"] for c in known_companies]

    grounding_concepts = get_grounding_themes(db, company_ids, limit=grounding_limit)
    grounding_names = [c.name for c in grounding_concepts]

    result = llm_extract_fn(
        article_title=title,
        article_content=content,
        known_companies=[{"ticker": c["ticker"], "name": c["name"]} for c in known_companies],
        grounding_themes=grounding_names,
        max_relations=max_relations,
    )

    if not result.themes and not result.relations:
        logger.info("%s=%s: LLM 冇抽取到任何 theme/relation", log_label, source_id)
        return {"themes_created": 0, "themes_reused": 0, "relations_reinforced": 0, "skipped_relations": 0}

    stats = _write_extraction_result(
        db,
        result,
        known_companies=known_companies,
        source_news_id=source_id,
        embed_fn=embed_fn,
        similarity_threshold=similarity_threshold,
    )
    logger.info(
        "%s=%s: theme 新增 %d / 沿用 %d, relation 強化 %d, 跳過 %d",
        log_label,
        source_id,
        stats["themes_created"],
        stats["themes_reused"],
        stats["relations_reinforced"],
        stats["skipped_relations"],
    )
    return stats


def process_news_for_concepts(
    db,
    news_id: int,
    *,
    llm_extract_fn: Optional[LlmExtractFn] = None,
    embed_fn: Optional[EmbedFn] = None,
    max_relations: int = 5,
    similarity_threshold: float = 0.85,
    grounding_limit: int = 15,
) -> dict[str, int]:
    """
    主流程(新聞版本)。回傳一個統計 dict,方便你跑完一批新聞之後打印/log 個總結。
    """
    llm_extract_fn = llm_extract_fn or extract_concepts_and_relations
    embed_fn = embed_fn or embed_texts

    news = db.get_news_full(news_id)
    if news is None:
        raise ValueError(f"news_id={news_id} 唔存在")

    known_companies = get_known_companies(db, news_id)

    return _process_text_for_concepts(
        db,
        title=news.title,
        content=news.content or "",
        known_companies=known_companies,
        source_id=news_id,
        log_label="news_id",
        llm_extract_fn=llm_extract_fn,
        embed_fn=embed_fn,
        max_relations=max_relations,
        similarity_threshold=similarity_threshold,
        grounding_limit=grounding_limit,
    )


def get_known_companies_for_article(db, article_id: int) -> list[dict]:
    """同 `get_known_companies()` 對應嘅 Article 版本——由 `ArticleCompanyLink` 攞。"""
    article = db.get_article_full(article_id)
    if article is None:
        return []
    return [
        {
            "ticker": link.company.ticker,
            "name": link.company.name_en,
            "company_id": link.company.company_id,
        }
        for link in article.company_links
    ]


def build_article_text(article: AnalysisArticle) -> str:
    """
    將 `AnalysisArticle` 幾個分散嘅欄位(description/thesis/conclusion)
    組成一段有標籤嘅文字畀 LLM 讀。加多一句 source context 提醒 LLM：
    呢段內容嚟自一篇正式分析文章、本身已經係一個投資論點,通常有一定分析
    依據，唔應該淨係因為「呢個係預測」就自動將 confidence 谷落中間檔。
    """
    parts = [
        "（以下內容嚟自一篇分析員/專家撰寫嘅正式分析文章，本身已經係佢哋提出嘅"
        "投資論點，通常有一定分析依據支持——請按內容有幾清晰、有幾多具體理據"
        "嚟判斷 confidence，唔使淨係因為佢係「預測」就自動谷落中間檔。）"
    ]
    if article.description:
        parts.append(f"文章簡介：{article.description}")
    if article.thesis:
        parts.append(f"分析員論點(thesis)：{article.thesis}")
    if article.conclusion:
        parts.append(f"結論：{article.conclusion}")
    return "\n\n".join(parts)


def process_article_for_concepts(
    db,
    article_id: int,
    *,
    llm_extract_fn: Optional[LlmExtractFn] = None,
    embed_fn: Optional[EmbedFn] = None,
    max_relations: int = 8,
    similarity_threshold: float = 0.85,
    grounding_limit: int = 15,
) -> dict[str, int]:
    """
    主流程(分析文章版本)。同 `process_news_for_concepts()` 對應，
    但已知公司嚟自 `ArticleCompanyLink`，餵俾 LLM 嘅文字嚟自
    `thesis`/`conclusion`/`description`(見 `build_article_text()`)。

    `max_relations` 預設 8(比 News 嗰邊嘅 5 高)：一篇正式分析報告
    通常會覆蓋多過一個 thesis，值得俾 LLM 多啲空間抽取。

    留意：`article_id` 同呢篇文章底下嗰行 News 嘅 `news_id` 係同一個
    number(shared-PK)，所以寫 evidence 嗰陣直接傳呢個值落去
    `ConceptRelationEvidence.news_id` 完全岩，唔使轉換。
    """
    llm_extract_fn = llm_extract_fn or extract_concepts_and_relations
    embed_fn = embed_fn or embed_texts

    article = db.get_article_full(article_id)
    if article is None:
        raise ValueError(f"article_id={article_id} 唔存在")

    known_companies = get_known_companies_for_article(db, article_id)
    title = article.title or (article.news.title if article.news else "")
    content = build_article_text(article)

    return _process_text_for_concepts(
        db,
        title=title,
        content=content,
        known_companies=known_companies,
        source_id=article_id,
        log_label="article_id",
        llm_extract_fn=llm_extract_fn,
        embed_fn=embed_fn,
        max_relations=max_relations,
        similarity_threshold=similarity_threshold,
        grounding_limit=grounding_limit,
    )


def _write_extraction_result(
    db,
    result: ExtractionResult,
    *,
    known_companies: list[dict],
    source_news_id: int,
    embed_fn: EmbedFn,
    similarity_threshold: float,
) -> dict[str, int]:
    # 保險起見：唔淨係信 LLM 有跟指示將 relations 用到嘅 theme 名都列晒喺
    # themes[] 度——自己再掃一次 relations,補齊漏咗嘅 theme 名，等下面
    # 逐條 relation resolve 嗰陣唔會因為「theme 冇喺 themes[] 出現過」而白白被跳過。
    description_by_name = {t.name: t.description for t in result.themes}
    all_theme_names: list[str] = list(description_by_name.keys())
    seen_names = set(all_theme_names)

    for relation in result.relations:
        if relation.from_theme not in seen_names:
            seen_names.add(relation.from_theme)
            all_theme_names.append(relation.from_theme)
        if relation.to_type == "theme" and relation.to_ref not in seen_names:
            seen_names.add(relation.to_ref)
            all_theme_names.append(relation.to_ref)

    themes_created = 0
    themes_reused = 0
    name_to_concept_id: dict[str, int] = {}

    if all_theme_names:
        theme_texts = [
            f"{name}: {description_by_name[name]}" if description_by_name.get(name) else name
            for name in all_theme_names
        ]
        embeddings = embed_fn(theme_texts)

        for name, embedding in zip(all_theme_names, embeddings):
            concept, is_new = db.get_or_create_theme_concept(
                name,
                embedding=embedding,
                description=description_by_name.get(name) or None,
                similarity_threshold=similarity_threshold,
            )
            name_to_concept_id[name] = concept.concept_id
            if is_new:
                themes_created += 1
            else:
                themes_reused += 1

    company_by_ticker = {c["ticker"]: c for c in known_companies}
    ticker_to_concept_id: dict[str, int] = {}

    relations_reinforced = 0
    skipped_relations = 0
    

    for relation in result.relations:
        from_concept_id = name_to_concept_id.get(relation.from_theme)
        if from_concept_id is None:
            logger.warning("relation 嘅 from_theme '%s' 冇對應 concept,跳過", relation.from_theme)
            skipped_relations += 1
            continue

        if relation.to_type == "theme":
            to_concept_id = name_to_concept_id.get(relation.to_ref)
            if to_concept_id is None:
                logger.warning("relation 嘅 to_ref '%s' 冇對應 concept,跳過", relation.to_ref)
                skipped_relations += 1
                continue
        else:
            company = company_by_ticker.get(relation.to_ref)
            if company is None:
                logger.warning("relation 指住未確認嘅公司 '%s',跳過", relation.to_ref)
                skipped_relations += 1
                continue
            if relation.to_ref not in ticker_to_concept_id:
                company_concept, _ = db.get_or_create_company_concept(company["company_id"])
                ticker_to_concept_id[relation.to_ref] = company_concept.concept_id
            to_concept_id = ticker_to_concept_id[relation.to_ref]

        db.reinforce_relation(
            from_concept_id,
            to_concept_id,
            relation.relation_type,
            polarity=relation.polarity,
            confidence=relation.confidence,
            source_news_id=source_news_id,
            note=relation.reasoning or None,
        )
        relations_reinforced += 1

    return {

        "themes_created": themes_created,
        "themes_reused": themes_reused,
        "relations_reinforced": relations_reinforced,
        "skipped_relations": skipped_relations,
    }

def summarize_extraction(concept_id: int) -> dict[str, int]:
    """總結某個 concept 的 extraction summary。"""
    db = DatabaseManager()
    with db.session_scope() as session:
        concept = session.get(Concept, concept_id)
        if concept is None:
            raise ValueError(f"concept_id={concept_id} 唔存在")

        # 淨係喺 session 仲開緊嗰陣直接數 count(唔攞返成個 relationship
        # collection)，唔會撞 DetachedInstanceError(session_scope() 一出咗
        # `with` 就會 close session，之後先想 lazy-load relationship 會炸)。
        relations_reinforced = session.scalar(
            select(func.count()).select_from(ConceptRelation).where(
                (ConceptRelation.from_concept_id == concept_id)
                | (ConceptRelation.to_concept_id == concept_id)
            )
        )
        concept_name = concept.name
        concept_description = concept.description

    skipped_relations = 0  # 無法從 DB 計算，因為 skipped 嘅 relation 根本冇寫入 DB

    return {
        "concept_id": concept_id,
        "concept_name": concept_name,
        "concept_description": concept_description,
        "relations_reinforced": relations_reinforced,
        "skipped_relations": skipped_relations,
    }

if __name__ == "__main__":
    db = DatabaseManager()
    stats = process_news_for_concepts(db, news_id=100)
    print(stats)