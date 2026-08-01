"""
Sector / Company / CompanyProfile / Product 嘅 CRUD + 常用查詢。
呢個 mixin 會俾 DatabaseManager 用 multiple inheritance 嘅方式砌埋一齊，
所以入面啲 method 靠 self.session_scope()（喺 SessionMixin 定義）。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.manager.generic import count_obj, create_obj, delete_obj, get_obj, list_obj, update_obj
from app.models import Company, CompanyProfile, Product, Sector


class CompanyManagerMixin:
    # ---------------------------------------------------------------- Sector
    def create_sector(self, **kwargs: Any) -> Sector:
        with self.session_scope() as s:
            return create_obj(s, Sector, **kwargs)

    def get_sector(self, sector_id: int) -> Optional[Sector]:
        with self.session_scope() as s:
            return get_obj(s, Sector, sector_id)

    def update_sector(self, sector_id: int, **kwargs: Any) -> Optional[Sector]:
        with self.session_scope() as s:
            return update_obj(s, Sector, sector_id, **kwargs)

    def delete_sector(self, sector_id: int) -> bool:
        with self.session_scope() as s:
            return delete_obj(s, Sector, sector_id)

    def list_sectors(self, *, limit: int = 100, offset: int = 0, **filters: Any) -> list[Sector]:
        with self.session_scope() as s:
            return list_obj(s, Sector, limit=limit, offset=offset, **filters)

    # --------------------------------------------------------------- Company
    def create_company(self, **kwargs: Any) -> Company:
        with self.session_scope() as s:
            return create_obj(s, Company, **kwargs)

    def get_company(self, company_id: int) -> Optional[Company]:
        with self.session_scope() as s:
            return get_obj(s, Company, company_id)

    def get_company_by_ticker(self, ticker: str) -> Optional[Company]:
        with self.session_scope() as s:
            return s.scalars(select(Company).where(Company.ticker == ticker)).first()

    def update_company(self, company_id: int, **kwargs: Any) -> Optional[Company]:
        with self.session_scope() as s:
            return update_obj(s, Company, company_id, **kwargs)

    def delete_company(self, company_id: int) -> bool:
        with self.session_scope() as s:
            return delete_obj(s, Company, company_id)

    def list_companies(self, *, limit: int = 100, offset: int = 0, **filters: Any) -> list[Company]:
        with self.session_scope() as s:
            return list_obj(s, Company, limit=limit, offset=offset, **filters)

    def count_companies(self, **filters: Any) -> int:
        with self.session_scope() as s:
            return count_obj(s, Company, **filters)

    def get_company_full(self, company_id: int) -> Optional[Company]:
        """
        一次過將 sector / profiles / products 全部攞埋
        (用 selectinload 預先 join 好,session 關咗之後都讀得到)。
        淨係用 get_company() 嘅話,離開咗 session 之後再讀
        company.products 呢類 relationship 會拋 DetachedInstanceError。
        """
        with self.session_scope() as s:
            stmt = (
                select(Company)
                .options(
                    selectinload(Company.sector),
                    selectinload(Company.profiles),
                    selectinload(Company.products),
                )
                .where(Company.company_id == company_id)
            )
            return s.scalars(stmt).first()

    # ---------------------------------------------------- CompanyProfile (versioned)
    def get_current_profile(self, company_id: int) -> Optional[CompanyProfile]:
        with self.session_scope() as s:
            stmt = select(CompanyProfile).where(
                CompanyProfile.company_id == company_id,
                CompanyProfile.is_current.is_(True),
            )
            return s.scalars(stmt).first()

    def set_company_profile(
        self,
        company_id: int,
        business_model: Optional[str] = None,
        description: Optional[str] = None,
        effective_date: Optional[date] = None,
    ) -> CompanyProfile:
        """
        新增一個新版本嘅 profile，並將現有 is_current=True 嗰個標記做 False。
        用嚟保留 Business Model 改動嘅歷史記錄，而唔係直接覆蓋舊資料 (SCD Type 2)。
        """
        with self.session_scope() as s:
            stmt = select(CompanyProfile).where(
                CompanyProfile.company_id == company_id,
                CompanyProfile.is_current.is_(True),
            )
            current = s.scalars(stmt).first()
            next_version = 1
            if current is not None:
                current.is_current = False
                next_version = current.version + 1

            new_profile = CompanyProfile(
                company_id=company_id,
                business_model=business_model,
                description=description,
                version=next_version,
                effective_date=effective_date,
                is_current=True,
            )
            s.add(new_profile)
            s.flush()
            return new_profile

    def list_profile_history(self, company_id: int) -> list[CompanyProfile]:
        with self.session_scope() as s:
            stmt = (
                select(CompanyProfile)
                .where(CompanyProfile.company_id == company_id)
                .order_by(CompanyProfile.version.asc())
            )
            return list(s.scalars(stmt).all())

    # --------------------------------------------------------------- Product
    def create_product(self, **kwargs: Any) -> Product:
        with self.session_scope() as s:
            return create_obj(s, Product, **kwargs)

    def get_product(self, product_id: int) -> Optional[Product]:
        with self.session_scope() as s:
            return get_obj(s, Product, product_id)

    def update_product(self, product_id: int, **kwargs: Any) -> Optional[Product]:
        with self.session_scope() as s:
            return update_obj(s, Product, product_id, **kwargs)

    def delete_product(self, product_id: int) -> bool:
        with self.session_scope() as s:
            return delete_obj(s, Product, product_id)

    def list_products(
        self, *, company_id: Optional[int] = None, limit: int = 100, offset: int = 0
    ) -> list[Product]:
        filters: dict[str, Any] = {}
        if company_id is not None:
            filters["company_id"] = company_id
        with self.session_scope() as s:
            return list_obj(s, Product, limit=limit, offset=offset, **filters)
