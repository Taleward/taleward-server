"""Zustimmung zum sofortigen Beginn beim Aufladen (Server 0.4.138)

Revision ID: b4d6f8a0c2e5
Revises: a9c3e5b7d1f2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b4d6f8a0c2e5'
down_revision: Union[str, Sequence[str], None] = 'a9c3e5b7d1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("bezahlvorgaenge", sa.Column("zustimmung_at", sa.DateTime(), nullable=True))
    op.add_column("bezahlvorgaenge", sa.Column("zustimmung_fassung", sa.String(80), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bezahlvorgaenge") as b:
        b.drop_column("zustimmung_fassung")
        b.drop_column("zustimmung_at")
