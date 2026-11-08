"""Herkunft einer Merkstelle: app oder discord (Server 0.4.161, Schnittstelle 0.4.35)

Revision ID: b8d0f2a4c6e8
Revises: a7c9e1f3b5d7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b8d0f2a4c6e8'
down_revision: Union[str, Sequence[str], None] = 'a7c9e1f3b5d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("bookmarks") as t:
        t.add_column(sa.Column("source", sa.String(length=16), nullable=False, server_default="app"))


def downgrade() -> None:
    with op.batch_alter_table("bookmarks") as t:
        t.drop_column("source")
