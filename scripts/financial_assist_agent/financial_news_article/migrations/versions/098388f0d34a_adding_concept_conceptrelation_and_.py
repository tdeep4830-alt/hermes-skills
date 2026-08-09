"""adding concept, conceptRelation and conceptRelationEvidence

Revision ID: 098388f0d34a
Revises: 528fae4bb566
Create Date: 2026-08-06 20:57:09.046188

呢個 revision 原本同 528fae4bb566(baseline)重複晒 create_table concepts/
concept_relations/concept_relation_evidence(舊 baseline 漏咗大部分 table,
補漏嗰陣執漏咗手)。而家 528fae4bb566 已經係補齊晒全部 27 張表嘅真.baseline,
包埋呢三張,所以呢度冇嘢做,淨係保留呢個 revision id 唔好搞亂已經 stamp
咗喺 production 嘅 alembic_version chain。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '098388f0d34a'
down_revision: Union[str, None] = '528fae4bb566'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
