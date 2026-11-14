"""Guthaben-Info und Startguthaben (Server 0.4.167)

Revision ID: c9e1f3a5b7d9
Revises: b8d0f2a4c6e8
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c9e1f3a5b7d9'
down_revision: Union[str, Sequence[str], None] = 'b8d0f2a4c6e8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as t:
        t.add_column(sa.Column("guthaben_info_at", sa.DateTime(), nullable=True))
        t.add_column(sa.Column("startguthaben_at", sa.DateTime(), nullable=True))
    op.create_table(
        "startguthaben_adressen",
        sa.Column("adresse_hash", sa.String(length=64), primary_key=True),
        sa.Column("betrag_cent", sa.Integer(), nullable=False),
        sa.Column("angelegt", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_startguthaben_adressen_angelegt", "startguthaben_adressen", ["angelegt"])


def downgrade() -> None:
    op.drop_index("ix_startguthaben_adressen_angelegt", table_name="startguthaben_adressen")
    op.drop_table("startguthaben_adressen")
    with op.batch_alter_table("users") as t:
        t.drop_column("startguthaben_at")
        t.drop_column("guthaben_info_at")
