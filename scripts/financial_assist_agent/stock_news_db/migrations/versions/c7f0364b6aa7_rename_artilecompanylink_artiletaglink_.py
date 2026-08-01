"""rename ArtileCompanyLink/ArtileTagLink news_id to article_id

Revision ID: c7f0364b6aa7
Revises: 3d0c6d4e219e
Create Date: 2026-07-31 13:29:47.108412

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7f0364b6aa7'
down_revision: Union[str, None] = '3d0c6d4e219e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 用 alter_column 淨係改名，唔好用 add+drop（會累積現有 row 嘅 company/tag link 資料）。
    op.alter_column('analysis_article_company_link', 'news_id', new_column_name='article_id')
    op.alter_column('analysis_article_tag_link', 'news_id', new_column_name='article_id')


def downgrade() -> None:
    op.alter_column('analysis_article_company_link', 'article_id', new_column_name='news_id')
    op.alter_column('analysis_article_tag_link', 'article_id', new_column_name='news_id')
