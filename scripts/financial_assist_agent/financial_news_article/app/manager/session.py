from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from dotenv import load_dotenv
import os

load_dotenv()  # 確保 config.py 嘅 settings 係已經 load 咗 .env 嘅，唔會有空值
settings = Settings()
DATABASE_URL = settings.DATABASE_URL


class SessionMixin:
    """負責管理 engine / session 生命週期，畀 DatabaseManager 同其他 Mixin 共用。"""

    def _init_engine(self, database_url: Optional[str] = None, echo: bool = False) -> None:
        self.engine = create_engine(
            database_url or database_url,
            echo=echo,
            pool_pre_ping=True,
            future=True,
        )
        self._SessionLocal = sessionmaker(
            bind=self.engine,
            autoflush=False,
            autocommit=False,
            # 好重要：關咗 session 之後,已經讀過嘅欄位仍然讀得到。
            # 如果唔設呢個, commit() 之後所有欄位會被 expire,
            # 一旦 session close 咗,再讀任何欄位都會拋 DetachedInstanceError。
            expire_on_commit=False,
            future=True,
        )

    @contextmanager
    def session_scope(self) -> Iterator[Session]:
        """
        開返一個完整嘅 transaction。session block 入面冇拋錯就自動 commit，
        一拋錯就自動 rollback，用完自動 close session。

        絕大部分時候你唔使直接用呢個 —— DatabaseManager 每個 CRUD method
        入面已經包埋。淨係當你想將『多過一個操作』綁做同一個 transaction
        （例如：新增公司 + 新增新聞，要就一齊成功，要就一齊唔做）先用得着：

            with db.session_scope() as session:
                company = Company(ticker="AAPL", name_en="Apple Inc.")
                session.add(company)
                session.flush()  # 攞返 company.company_id
                session.add(News(title="...", published_at=..., news_type="company"))
        """
        session = self._SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def dispose(self) -> None:
        """釋放晒個 connection pool。長駐 script / 想乾淨咁結束嗰陣可以叫呢個。"""
        self.engine.dispose()
