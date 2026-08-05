"""
匯入所有 model,等 Alembic autogenerate 同 Base.metadata.create_all() 可以搵得到晒啲 table。
"""
from app.models.base import Base
from app.models.company import (
    Sector,
    Industry,
    Company,
    CompanyProfile,
    Product,
    Technology,
    Service,
    GovernmentalProgramAndRegulation,
    ManufacturingProcess,
    SupplyChainAndLogistics,
    Competitor,
    Risk,
    ManagementDiscussionAndAnalysis,
    LegalAndRegulatoryIssues,
)
from app.models.tag import Tag, TagCategoryRule
from app.models.news_and_article import (
    News,
    NewsCompanyLink,
    NewsTagLink,
    AnalysisArticle,
    ArticleCompanyLink,
    ArticleTagLink,
    NewsArticleMatch,
)
from app.models.embedding import CompanyFactEmbedding

__all__ = [
    "Base",
    "Sector",
    "Industry",
    "Company",
    "CompanyProfile",
    "Product",
    "Technology",
    "Service",
    "GovernmentalProgramAndRegulation",
    "ManufacturingProcess",
    "SupplyChainAndLogistics",
    "Competitor",
    "Risk",
    "ManagementDiscussionAndAnalysis",
    "LegalAndRegulatoryIssues",
    "Tag",
    "TagCategoryRule",
    "News",
    "NewsCompanyLink",
    "NewsTagLink",
    "AnalysisArticle",
    "ArticleCompanyLink",
    "ArticleTagLink",
    "NewsArticleMatch",
    "CompanyFactEmbedding",
]
