"""Erstattung offen nach Kontolöschung (Server 0.4.153)

Revision ID: d0f2a4c6e8b1
Revises: c8e0a2b4d6f8
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd0f2a4c6e8b1'
down_revision: Union[str, Sequence[str], None] = 'c8e0a2b4d6f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "offene_erstattungen",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("guthaben_id", sa.String(36), sa.ForeignKey("guthaben.id"), nullable=False, index=True),
        sa.Column("betrag_cent", sa.Integer(), nullable=False),
        sa.Column("zahlungen", sa.Text(), nullable=False),
        sa.Column("email", sa.String(254), nullable=True),
        sa.Column("angelegt", sa.DateTime(), nullable=False),
        sa.Column("erledigt_at", sa.DateTime(), nullable=True),
        sa.Column("erledigt_wie", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("offene_erstattungen")
