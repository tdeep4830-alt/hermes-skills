"""
管理 TagCategoryRule（News-Company Matching Layer 2 用嘅
tag -> product_category / industry / sector crosswalk）嘅小工具。

TagCategoryRule 冇任何 pipeline 步驟會自動填——冇 Layer 2 rule 唔會令 pipeline
出錯，淨係話 Layer 2 貢獻 0 個 match，成日靠 Layer 1(直接提及) + Layer 3
(embedding) 去補。想 Layer 2 都真係用得到，要人手根據你 DB 已有嘅
Product.category / Industry.industry_name / Sector.sector_name 定義規則。

用法：
    1. 先睇下你 DB 而家有咩 tag、咩 category/industry/sector 值可以對應：
       python -m manage_tag_rules list-reference

    2. 睇低你想加嘅規則，喺底下 RULES 度加一行 (tag_name, target_field, target_value)，
       再跑：
       python -m manage_tag_rules apply

    3. 隨時可以查返而家已經有嘅 rule：
       python -m manage_tag_rules list-rules
"""
from __future__ import annotations

import argparse

from sqlalchemy import select

from app.database import get_session
from app.models import Industry, Product, Sector, Tag, TagCategoryRule
from app.models.tag import tag_rule_target_fields

# ---------------------------------------------------------------------------
# 喺呢度加你想要嘅規則，然後跑 `python -m manage_tag_rules apply`。
# tag_name 唔存在會自動幫你建(tag_type 用 "theme")；target_value 一定要
# 同 DB 已有嘅 Product.category / Industry.industry_name / Sector.sector_name
# 一字不差(唔會自動幫你 fuzzy match)，唔啱先會喺 apply 嗰陣提你、自動跳過。
RULES: list[tuple[str, str, str]] = [
    # (tag_name, target_field, target_value)
    # 例子：
    # ("記憶體", "product_category", "Memory/DRAM"),
    # ("半導體", "industry", "Semiconductors"),
]


def list_reference_data() -> None:
    """列返而家 DB 已有嘅 tag，同埋三個 target_field 可以對應嘅合法值，方便你決定點寫 RULES。"""
    with get_session() as session:
        tags = session.scalars(select(Tag.tag_name).order_by(Tag.tag_name)).all()
        categories = session.scalars(
            select(Product.category).where(Product.category.is_not(None)).distinct().order_by(Product.category)
        ).all()
        industries = session.scalars(
            select(Industry.industry_name).distinct().order_by(Industry.industry_name)
        ).all()
        sectors = session.scalars(select(Sector.sector_name).distinct().order_by(Sector.sector_name)).all()

    print(f"== 現存 Tag（{len(tags)} 個）==")
    for t in tags:
        print(f"  - {t}")

    print(f"\n== target_field='product_category' 可以用嘅值（{len(categories)} 個）==")
    for c in categories:
        print(f"  - {c}")

    print(f"\n== target_field='industry' 可以用嘅值（{len(industries)} 個）==")
    for i in industries:
        print(f"  - {i}")

    print(f"\n== target_field='sector' 可以用嘅值（{len(sectors)} 個）==")
    for s in sectors:
        print(f"  - {s}")


def list_existing_rules() -> None:
    """列返而家 tag_category_rules 入面已經有嘅規則。"""
    with get_session() as session:
        rules = session.scalars(
            select(TagCategoryRule).join(Tag, Tag.tag_id == TagCategoryRule.tag_id).order_by(Tag.tag_name)
        ).all()

    if not rules:
        print("而家 tag_category_rules 係空——一條規則都未有。")
        return

    print(f"== 現存 TagCategoryRule（{len(rules)} 條）==")
    for r in rules:
        print(f"  - {r.tag.tag_name} -> {r.target_field}={r.target_value!r}")


def _target_value_exists(session, target_field: str, target_value: str) -> bool:
    if target_field == "product_category":
        stmt = select(Product.product_id).where(Product.category == target_value).limit(1)
    elif target_field == "industry":
        stmt = select(Industry.industry_id).where(Industry.industry_name == target_value).limit(1)
    else:  # "sector"
        stmt = select(Sector.sector_id).where(Sector.sector_name == target_value).limit(1)
    return session.execute(stmt).first() is not None


def add_rule(session, tag_name: str, target_field: str, target_value: str, *, tag_type: str = "theme") -> str:
    """加一條規則，回傳 "created" / "exists" / "skipped"（DB 搵唔到對應嘅 target_value）。"""
    if target_field not in tag_rule_target_fields:
        raise ValueError(f"target_field 要係 {tag_rule_target_fields} 之一，收到 {target_field!r}")

    if not _target_value_exists(session, target_field, target_value):
        print(f"  ⚠️ 跳過：DB 搵唔到 {target_field}={target_value!r}（打錯字？定係相關公司資料仲未入 DB？）")
        return "skipped"

    tag = session.scalars(select(Tag).where(Tag.tag_name == tag_name)).first()
    if tag is None:
        tag = Tag(tag_name=tag_name, tag_type=tag_type)
        session.add(tag)
        session.flush()

    existing = session.scalars(
        select(TagCategoryRule).where(
            TagCategoryRule.tag_id == tag.tag_id,
            TagCategoryRule.target_field == target_field,
            TagCategoryRule.target_value == target_value,
        )
    ).first()
    if existing is not None:
        return "exists"

    session.add(TagCategoryRule(tag_id=tag.tag_id, target_field=target_field, target_value=target_value))
    return "created"


def apply_rules() -> None:
    if not RULES:
        print("RULES 清單係空——先喺檔案頂部加返你想要嘅 (tag_name, target_field, target_value) 先。")
        return

    counts = {"created": 0, "exists": 0, "skipped": 0}
    with get_session() as session:
        for tag_name, target_field, target_value in RULES:
            outcome = add_rule(session, tag_name, target_field, target_value)
            counts[outcome] += 1
            print(f"  [{outcome}] {tag_name} -> {target_field}={target_value!r}")
        session.commit()

    print(f"\n完成：新增 {counts['created']} 條，已存在 {counts['exists']} 條，跳過 {counts['skipped']} 條")


def main() -> None:
    parser = argparse.ArgumentParser(description="管理 TagCategoryRule 嘅小工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("list-reference", help="列返 DB 已有嘅 tag / product_category / industry / sector")
    subparsers.add_parser("list-rules", help="列返而家已經有嘅 TagCategoryRule")
    subparsers.add_parser("apply", help="將檔案頂部 RULES 清單入面嘅規則寫入 DB")

    args = parser.parse_args()
    if args.command == "list-reference":
        list_reference_data()
    elif args.command == "list-rules":
        list_existing_rules()
    elif args.command == "apply":
        apply_rules()


if __name__ == "__main__":
    main()
