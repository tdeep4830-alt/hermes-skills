"""
示範點樣用 DatabaseManager 做晒晒 CRUD。
執行前：docker compose up -d + alembic upgrade head 先。

執行： python -m scripts.manager_demo
"""
from datetime import datetime, timezone

from app.manager import DatabaseManager
from app.manager.generic import model_to_dict


def main() -> None:
    db = DatabaseManager()

    # ---------- Create ----------
    sector = db.create_sector(sector_name="Technology")
    company = db.create_company(
        ticker="AAPL",
        name_en="Apple Inc.",
        name_zh="蘋果公司",
        exchange="NASDAQ",
        country="US",
        sector_id=sector.sector_id,
    )
    db.set_company_profile(
        company.company_id,
        business_model="設計、製造同銷售消費電子產品、軟件同服務",
        description="全球市值最大嘅科技公司之一",
    )
    db.create_product(company_id=company.company_id, product_name="iPhone", category="Hardware")
    print("新增公司:", model_to_dict(company))

    # ---------- Read ----------
    fetched = db.get_company_by_ticker("AAPL")
    print("用 ticker 查返:", fetched.name_zh)

    full = db.get_company_full(company.company_id)
    print("完整資料 - sector:", full.sector.sector_name, "| 產品數量:", len(full.products))

    # ---------- Update ----------
    db.update_company(company.company_id, website="https://www.apple.com")
    print("更新後 website:", db.get_company(company.company_id).website)

    # Business model 有更新 -> 用 set_company_profile 起多一個新版本 (保留返舊版本)
    db.set_company_profile(company.company_id, business_model="加埋 Apple Intelligence / AI 服務")
    history = db.list_profile_history(company.company_id)
    print(f"profile 歷史版本數量: {len(history)}，最新版本: v{history[-1].version}")

    # ---------- 新聞：一步過新增 + 連結公司 + tag ----------
    news = db.add_news(
        title="Apple 發布新一代 iPhone",
        content="Apple 今日發布新一代 iPhone，帶有更快嘅晶片同 AI 功能。",
        published_at=datetime.now(timezone.utc),
        source="Demo Wire",
        company_ids=[company.company_id],
        tag_names=["新產品發布"],
    )
    print("新增新聞:", news.title)

    news_full = db.get_news_full(news.news_id)
    linked_companies = [link.company.ticker for link in news_full.company_links]
    linked_tags = [link.tag.tag_name for link in news_full.tag_links]
    print("呢則新聞連結緊嘅公司:", linked_companies, "| tags:", linked_tags)

    company_news = db.get_news_for_company(company.company_id)
    print(f"AAPL 相關新聞數量: {len(company_news)}")

    search_result = db.search_news(keyword="iPhone")
    print(f"關鍵字 'iPhone' 搜尋到 {len(search_result)} 則新聞")

    # ---------- Delete ----------
    deleted = db.delete_news(news.news_id)
    print("刪除新聞成功:", deleted)

    # 呢個 demo 淨係做示範,唔應該喺 DB 度留低垃圾——刪走自己開嘅 company(cascade
    # 埋 profile/product)同 sector,等隨時可以重跑,唔會撞 ticker 嘅 unique constraint。
    db.delete_company(company.company_id)
    db.delete_sector(sector.sector_id)

    db.dispose()


if __name__ == "__main__":
    main()
