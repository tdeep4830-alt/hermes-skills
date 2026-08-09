"""
示範 Mind Map(Concept Graph)嘅去重 + 強化邏輯。

呢個 demo 用合成(隨機)向量代替真正嘅 embedding API,
純粹想證明 DB 層嘅去重/強化邏輯行為正確,唔關邊個 embedding provider 事。
你實際用嗰陣,將 `fake_embedding()` 換做真係 call OpenAI/其他 provider 攞返嚟嘅向量就得,
其他 code 完全唔使改。

執行： python -m scripts.concept_demo
"""
import random
import uuid
from datetime import datetime, timezone

from app.manager import DatabaseManager
from app.models.concept import EMBEDDING_DIM


def fake_embedding(seed: int) -> list[float]:
    """生成一個固定隨機向量,模擬某段文字嘅 embedding。"""
    rng = random.Random(seed)
    return [rng.uniform(-1, 1) for _ in range(EMBEDDING_DIM)]


def perturb(vector: list[float], noise: float = 0.02, seed: int = 0) -> list[float]:
    """喺原本向量度加返少少雜訊,模擬「同一個概念、唔同講法」嘅 embedding。"""
    rng = random.Random(seed)
    return [v + rng.uniform(-noise, noise) for v in vector]


def main() -> None:
    db = DatabaseManager()

    # 用 uuid 做獨一無二嘅 ticker,每次都開一間新公司,跑完喺 finally 度清走——
    # 下面 fake_embedding() 用嘅係固定 seed,如果唔清返上一次留低嘅 row,
    # dedup 邏輯會撞返上次嗰個 concept,令呢個 demo 淨係喺一個乾淨嘅 DB 先跑得晒。
    ticker = f"DEMO{uuid.uuid4().hex[:8].upper()}"
    company = db.create_company(ticker=ticker, name_en=f"Demo Co {ticker}")
    print("公司:", company.ticker)

    theme_a = theme_c = company_node = news = news_2 = None
    try:
        # ---------- 去重測試 ----------
        ai_vec_1 = fake_embedding(seed=42)
        ai_vec_2 = perturb(ai_vec_1, noise=0.02, seed=1)  # 「同一個概念、唔同講法」

        theme_a, is_new_a = db.get_or_create_theme_concept("AI發展", embedding=ai_vec_1)
        print(f"第一次建立 'AI發展': concept_id={theme_a.concept_id}, is_new={is_new_a}")
        assert is_new_a is True, "第一次應該開新 node"

        theme_b, is_new_b = db.get_or_create_theme_concept("人工智能增長", embedding=ai_vec_2)
        print(f"第二次(近似embedding,唔同名) '人工智能增長': concept_id={theme_b.concept_id}, is_new={is_new_b}")
        assert theme_a.concept_id == theme_b.concept_id, "應該合併做同一個 concept"
        assert is_new_b is False, "第二次應該係 dedup 命中,唔係開新 node"

        merged = db.get_concept(theme_a.concept_id)
        print("合併後嘅 aliases:", merged.aliases)
        assert "人工智能增長" in merged.aliases

        # 完全唔相關嘅主題 -> 應該開一個新 node
        unrelated_vec = fake_embedding(seed=999)
        theme_c, is_new_c = db.get_or_create_theme_concept("加息預期", embedding=unrelated_vec)
        print(f"完全唔相關嘅 '加息預期': concept_id={theme_c.concept_id}, is_new={is_new_c}")
        assert is_new_c is True
        assert theme_c.concept_id != theme_a.concept_id

        # ---------- 公司 node ----------
        company_node, is_new_company = db.get_or_create_company_concept(company.company_id)
        print(f"公司 node: concept_id={company_node.concept_id}, is_new={is_new_company}")
        # 再攞一次應該係 exact match,唔會重複開
        company_node_2, is_new_company_2 = db.get_or_create_company_concept(company.company_id)
        assert company_node.concept_id == company_node_2.concept_id
        assert is_new_company_2 is False

        # ---------- 強化測試 ----------
        news = db.add_news(title="AI需求帶動硬件銷售", published_at=datetime.now(timezone.utc))

        relation_1 = db.reinforce_relation(
            theme_a.concept_id, company_node.concept_id,
            relation_type="benefits", polarity="positive",
            confidence=0.6, source_news_id=news.news_id, note="第一篇報導",
        )
        print(f"第一次強化: reinforcement_count={relation_1.reinforcement_count}, confidence={relation_1.confidence:.3f}")
        assert relation_1.reinforcement_count == 1

        news_2 = db.add_news(title="AI伺服器需求持續強勁", published_at=datetime.now(timezone.utc))
        relation_2 = db.reinforce_relation(
            theme_a.concept_id, company_node.concept_id,
            relation_type="benefits", polarity="positive",
            confidence=0.8, source_news_id=news_2.news_id, note="第二篇報導,信心更高",
        )
        print(f"第二次強化(同一條邊): reinforcement_count={relation_2.reinforcement_count}, confidence={relation_2.confidence:.3f}")
        assert relation_2.relation_id == relation_1.relation_id, "應該係同一條邊,唔係開新邊"
        assert relation_2.reinforcement_count == 2
        expected_confidence = (0.6 * 1 + 0.8) / 2
        assert abs(relation_2.confidence - expected_confidence) < 1e-9, "confidence 應該係 running average"

        evidence = db.list_relation_evidence(relation_1.relation_id)
        print(f"呢條邊嘅 evidence 數量: {len(evidence)}")
        assert len(evidence) == 2

        # 相反論述(睇淡) -> 應該係獨立一條新邊,唔會覆蓋返正面嗰條
        relation_negative = db.reinforce_relation(
            theme_a.concept_id, company_node.concept_id,
            relation_type="benefits", polarity="negative",
            confidence=0.3, source_news_id=news.news_id, note="有分析員擔心估值過高",
        )
        print(f"相反論述(negative)嘅邊: relation_id={relation_negative.relation_id}")
        assert relation_negative.relation_id != relation_1.relation_id, "positive/negative 應該係兩條獨立嘅邊"

        outgoing = db.list_outgoing_relations(theme_a.concept_id)
        print(f"'AI發展' 呢個 concept 一共有 {len(outgoing)} 條出邊(positive+negative)")
        assert len(outgoing) == 2

        print("=== concept_demo 全部檢查完成 ===")
    finally:
        # 刪 concept 會連埋佢做 from/to 嘅所有 relation/evidence 一齊刪走(見 cascade),
        # 呢度唔理中途有冇 assert 爆咗,總之開過嘅嘢都要清走,等下次重跑係一個乾淨嘅 DB。
        for concept in (theme_a, theme_c, company_node):
            if concept is not None:
                db.delete_concept(concept.concept_id)
        for n in (news, news_2):
            if n is not None:
                db.delete_news(n.news_id)
        db.delete_company(company.company_id)
        db.dispose()


if __name__ == "__main__":
    main()
