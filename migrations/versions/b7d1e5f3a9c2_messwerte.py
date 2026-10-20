"""Messwerte je Auftrag (Server 0.4.114): Laufzeit je Schritt, Token/s, Grafikkarte

Revision ID: b7d1e5f3a9c2
Revises: a4e8c2b6d9f1
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b7d1e5f3a9c2'
down_revision: Union[str, Sequence[str], None] = 'a4e8c2b6d9f1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("messwerte", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "messwerte")
