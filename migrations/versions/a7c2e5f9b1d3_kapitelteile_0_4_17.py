"""Teile im Kapitel (Schnittstelle 0.4.17): recaps.parts

Revision ID: a7c2e5f9b1d3
Revises: f1a6c3e9d2b7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c2e5f9b1d3'
down_revision: Union[str, Sequence[str], None] = 'f1a6c3e9d2b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("recaps") as b:
        b.add_column(sa.Column("parts", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("recaps") as b:
        b.drop_column("parts")
