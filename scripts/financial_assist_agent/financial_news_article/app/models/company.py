from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Date, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin

if TYPE_CHECKING:
    from stock_news_db.app.models.news_and_article import NewsCompanyLink


risk_type_enum = ("financial", "operational", "strategic", "compliance", "reputational", "market", "other")

# Product.category 嘅固定清單(唔係 DB-level enum，淨係 app 層驗證)——刻意收窄做
# controlled vocabulary，等 TagCategoryRule(target_field="product_category")做
# Layer 2 tag/category 規則配對嗰陣，唔會因為 LLM 自由發揮出「AI Chip」/「AI晶片」
# 呢類意思一樣但字面唔同嘅 category，match 唔中。範圍對準呢個 pipeline 專注嘅
# AI/Tech(同 clean_news.py 嘅 AI_TECH_KEYWORDS 對齊)。
product_category_values = (
    "GPU/AI Chip",
    "Semiconductor/Foundry",
    "Memory/Storage",
    "Cloud/Data Center Infrastructure",
    "Networking Hardware",
    "Consumer Hardware",
    "Generative AI/LLM",
    "AI Agent/Automation Software",
    "Enterprise Software/SaaS",
    "Consumer Software/Platform",
    "Cybersecurity",
    "Robotics",
    "Fintech/Payments",
    "Other",
)


class Sector(Base):
    """行業分類（可以做層級：sector -> sub-sector）"""

    __tablename__ = "sectors"

    sector_id: Mapped[int] = mapped_column(primary_key=True)
    sector_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_sector_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sectors.sector_id"), nullable=True
    )

    industries: Mapped[list["Industry"]] = relationship(back_populates="sector")
    companies: Mapped[list["Company"]] = relationship(back_populates="sector")


class Industry(Base):
    """行業分類（可以做層級：sector -> sub-sector）"""

    __tablename__ = "industries"

    industry_id: Mapped[int] = mapped_column(primary_key=True)
    industry_name: Mapped[str] = mapped_column(String(100), nullable=False)
    parent_industry_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("industries.industry_id"), nullable=True
    )
    sector_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sectors.sector_id"), nullable=True
    )

    sector: Mapped[Optional["Sector"]] = relationship(back_populates="industries")
    companies: Mapped[list["Company"]] = relationship(back_populates="industry")


class Company(Base, TimestampMixin):
    """公司主表 —— 低頻更新嘅基本資料"""

    __tablename__ = "companies"

    company_id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    name_en: Mapped[str] = mapped_column(String(255), nullable=False)
    name_zh: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    exchange: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    country: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    sector_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sectors.sector_id"), nullable=True
    )
    industry_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("industries.industry_id"), nullable=True
    )
    listing_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active", nullable=False)
    website: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    sector: Mapped[Optional["Sector"]] = relationship(back_populates="companies")
    industry: Mapped[Optional["Industry"]] = relationship(back_populates="companies")
    profiles: Mapped[list["CompanyProfile"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    products: Mapped[list["Product"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    technologies: Mapped[list["Technology"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    services: Mapped[list["Service"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    governmental_programs_and_regulations: Mapped[list["GovernmentalProgramAndRegulation"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    manufacturing_processes: Mapped[list["ManufacturingProcess"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    supply_chains_and_logistics: Mapped[list["SupplyChainAndLogistics"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    competitors: Mapped[list["Competitor"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    risks: Mapped[list["Risk"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    management_discussions_and_analyses: Mapped[list["ManagementDiscussionAndAnalysis"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    legal_and_regulatory_issues: Mapped[list["LegalAndRegulatoryIssues"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    news_links: Mapped[list["NewsCompanyLink"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    article_links: Mapped[list["ArticleCompanyLink"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    price_bars: Mapped[list["StockPrice"]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class CompanyProfile(Base, TimestampMixin):
    """
    公司 Business Model / 背景描述。
    拆開做獨立 table (同 versioning 機制) 係方便日後業務轉型時保留歷史版本，
    而唔係直接覆蓋舊資料 (SCD Type 2 概念)。
    """

    __tablename__ = "company_profiles"

    profile_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), nullable=False)
    business_model: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(default=1, nullable=False)
    effective_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_current: Mapped[bool] = mapped_column(default=True, nullable=False)

    company: Mapped["Company"] = relationship(back_populates="profiles")


class Product(Base):
    """一間公司可以有多個主要產品（一對多）"""

    __tablename__ = "products"

    product_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), nullable=False)
    product_name: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="products")


class Technology(Base):
    """一間公司可以有多個主要技術/專利/核心能力（一對多）"""

    __tablename__ = "technologies"

    technology_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), nullable=False)
    technology_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="technologies")


class Service(Base):
    """一間公司可以有多個主要服務/解決方案（一對多）"""

    __tablename__ = "services"

    service_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), nullable=False)
    service_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="services")


class GovernmentalProgramAndRegulation(Base):
    """一間公司可以有多個主要政府計劃/法規/政策影響（一對多）"""

    __tablename__ = "governmental_programs_and_regulations"

    program_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), nullable=False)
    program_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="governmental_programs_and_regulations")


class ManufacturingProcess(Base):
    """一間公司可以有多個主要製造流程/生產工藝（一對多）"""

    __tablename__ = "manufacturing_processes"

    process_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), nullable=False)
    process_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="manufacturing_processes")


class SupplyChainAndLogistics(Base):
    """一間公司可以有多個主要供應鏈/物流/分銷模式（一對多）"""

    __tablename__ = "supply_chains_and_logistics"

    supply_chain_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), nullable=False)
    supply_chain_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="supply_chains_and_logistics")


class Competitor(Base):
    """一間公司可以有多個主要競爭對手（一對多）"""

    __tablename__ = "competitors"

    competitor_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), nullable=False)
    competitor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="competitors")


class Risk(Base):
    """一間公司可以有多個主要業務風險/挑戰（一對多）"""

    __tablename__ = "risks"

    risk_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), nullable=False)
    risk_type: Mapped[str] = mapped_column(Enum(*risk_type_enum, name="risk_type_enum"), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="risks")


class ManagementDiscussionAndAnalysis(Base):
    """一間公司可以有多個主要管理層討論與分析（MD&A）段落（一對多）"""

    __tablename__ = "management_discussions_and_analyses"

    mdna_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), nullable=False)
    mdna_title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="management_discussions_and_analyses")


class LegalAndRegulatoryIssues(Base):
    """一間公司可以有多個主要法律與監管議題（一對多）"""

    __tablename__ = "legal_and_regulatory_issues"

    issue_id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.company_id"), nullable=False)
    issue_title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    company: Mapped["Company"] = relationship(back_populates="legal_and_regulatory_issues")
