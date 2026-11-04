"""Kapitel teilen als verschlüsselte Datei (Schnittstelle 0.4.30, Server 0.4.148)

Revision ID: c6e8a0b2d4f6
Revises: a5c7e9f1b3d4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c6e8a0b2d4f6'
down_revision: Union[str, Sequence[str], None] = 'a5c7e9f1b3d4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "share_keys",
        sa.Column("member_id", sa.String(36), sa.ForeignKey("members.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("key_id", sa.String(64), nullable=False, unique=True),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("bindung", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("share_keys")
