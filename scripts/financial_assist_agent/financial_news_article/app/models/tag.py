from __future__ import annotations

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Tag(Base):
    """
    用嚟標記同「特定公司」無直接關係嘅新聞，
    例如宏觀主題（加息、地緣政治）或者其他資產類別（原油、美元指數）。
    """

    __tablename__ = "tags"

    tag_id: Mapped[int] = mapped_column(primary_key=True)
    tag_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    tag_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # tag_type 例子: "macro" / "asset_class" / "theme" / "industry"

    article_links: Mapped[list["ArticleTagLink"]] = relationship(
        back_populates="tag", cascade="all, delete-orphan"
    )


# tag_category_rules.target_field 合法值。
tag_rule_target_fields = ("product_category", "industry", "sector")


class TagCategoryRule(Base):
    """
    News-Company Matching Layer 2（Tag/Category 規則配對）用嘅 crosswalk。
    例如 tag「記憶體」對應 target_field="product_category", target_value="Memory/DRAM"，
    即係話但凡新聞打咗「記憶體」呢個 tag，就同「有產品 category 係 Memory/DRAM」嘅公司做規則配對，
    唔使用到 LLM 或者 embedding。一個 tag 可以夾幾多個 target_value。
    """

    __tablename__ = "tag_category_rules"
    __table_args__ = (UniqueConstraint("tag_id", "target_field", "target_value", name="uq_tag_category_rule"),)

    rule_id: Mapped[int] = mapped_column(primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.tag_id"), nullable=False)
    target_field: Mapped[str] = mapped_column(String(20), nullable=False)
    target_value: Mapped[str] = mapped_column(String(255), nullable=False)

    tag: Mapped["Tag"] = relationship()
