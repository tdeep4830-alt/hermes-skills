"""
放一啲測試/初始資料，方便你剛 setup 完就有嘢可以試 query。
執行： python -m scripts.seed_data
"""
from app.database import get_session
from app.models import Company, CompanyProfile, Sector


def seed() -> None:
    with get_session() as session:
        tech = Sector(sector_name="Technology")
        session.add(tech)
        session.flush()

        apple = Company(
            ticker="AAPL",
            name_en="Apple Inc.",
            name_zh="蘋果公司",
            exchange="NASDAQ",
            country="US",
            sector_id=tech.sector_id,
        )
        session.add(apple)
        session.flush()

        session.add(
            CompanyProfile(
                company_id=apple.company_id,
                business_model="設計、製造同銷售消費電子產品、軟件同服務",
                description="全球市值最大嘅科技公司之一",
            )
        )

        session.commit()
        print(f"Seed 完成：新增咗 company_id={apple.company_id} (AAPL)")


if __name__ == "__main__":
    seed()
