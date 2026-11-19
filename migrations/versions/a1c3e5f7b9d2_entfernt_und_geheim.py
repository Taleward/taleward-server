"""Entfernte Mitglieder und geheim Mitgebrachtes (Server 0.4.170)

Revision ID: a1c3e5f7b9d2
Revises: c9e1f3a5b7d9
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a1c3e5f7b9d2'
down_revision: Union[str, Sequence[str], None] = 'c9e1f3a5b7d9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("members") as t:
        t.add_column(sa.Column("removed_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("entries") as t:
        t.add_column(sa.Column("origin_secret", sa.Boolean(), nullable=False, server_default="0"))
    with op.batch_alter_table("proposals") as t:
        t.add_column(sa.Column("origin_secret", sa.Boolean(), nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("proposals") as t:
        t.drop_column("origin_secret")
    with op.batch_alter_table("entries") as t:
        t.drop_column("origin_secret")
    with op.batch_alter_table("members") as t:
        t.drop_column("removed_at")
