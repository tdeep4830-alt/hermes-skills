"""
DatabaseManager —— 一個 facade，畀晒你一個入口去 CRUD 所有 model。
"""
from __future__ import annotations

from typing import Optional

from app.manager.article_manager import ArticleManagerMixin
from app.manager.company_manager import CompanyManagerMixin
from app.manager.embedding_manager import EmbeddingManagerMixin
from app.manager.news_manager import NewsManagerMixin
from app.manager.concept_manager import ConceptManagerMixin
from app.manager.session import SessionMixin
from app.config import Settings
import dotenv

dotenv.load_dotenv()
settings = Settings()  # 確保 config.py 嘅 settings 係已經 load 咗 .env 嘅，唔會有空值

DATABASE_URL = settings.DATABASE_URL  # .env file path
  # .env file path

class DatabaseManager(
    SessionMixin, CompanyManagerMixin, NewsManagerMixin, ArticleManagerMixin, EmbeddingManagerMixin, ConceptManagerMixin
):
    """
    用法一 —— 簡單 CRUD（每個 method 內部自動開關 session、自動 commit）：

        from app.manager import DatabaseManager

        db = DatabaseManager()

        # Create
        company = db.create_company(ticker="AAPL", name_en="Apple Inc.", name_zh="蘋果公司")
        db.set_company_profile(company.company_id, business_model="設計/製造/銷售消費電子產品")
        db.create_product(company_id=company.company_id, product_name="iPhone")

        # Read
        company = db.get_company_by_ticker("AAPL")
        full = db.get_company_full(company.company_id)   # 連 sector/profiles/products 一齊攞
        companies = db.list_companies(limit=50)

        # Update
        db.update_company(company.company_id, website="https://www.apple.com")

        # Delete
        db.delete_product(some_product_id)

        # 新聞（一步過建立 + 連結公司/tag）
        news = db.add_news(
            title="Apple 發布新產品",
            published_at=datetime.now(timezone.utc),
            company_ids=[company.company_id],
            tag_names=["新產品發布"],
        )
        db.get_news_for_company(company.company_id)
        db.search_news(keyword="Apple")

    用法二 —— 多個操作要綁做同一個 transaction（要就一齊成功，要就一齊 rollback）：

        with db.session_scope() as session:
            ... 直接用 SQLAlchemy session 操作 ...

    完事之後（例如長駐 script 結束前）可以叫 db.dispose() 釋放 connection pool。
    """

    def __init__(self, database_url: Optional[str] = None, echo: bool = False):
        self._init_engine(database_url=DATABASE_URL, echo=echo)
