"""enable pgvector extension

Revision ID: 8f7b4b5b59fa
Revises: c5dc64ab6027
Create Date: 2026-07-30 10:22:50.805268

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8f7b4b5b59fa'
down_revision: Union[str, None] = 'c5dc64ab6027'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS vector")
