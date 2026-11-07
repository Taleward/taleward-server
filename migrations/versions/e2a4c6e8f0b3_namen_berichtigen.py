"""Namen einmal berichtigen (Server 0.4.154, Schnittstelle 0.4.31)

Revision ID: e2a4c6e8f0b3
Revises: d0f2a4c6e8b1
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e2a4c6e8f0b3'
down_revision: Union[str, Sequence[str], None] = 'd0f2a4c6e8b1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "name_fixes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("von", sa.String(64), nullable=False),
        sa.Column("nach", sa.String(64), nullable=False),
        sa.Column("changed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rule", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("orte", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_member_id", sa.String(36), nullable=True),
        sa.Column("undo_until", sa.DateTime(), nullable=True),
        sa.Column("undone_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "name_fix_undo",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("fix_id", sa.String(36), sa.ForeignKey("name_fixes.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("place", sa.String(32), nullable=False),
        sa.Column("oid", sa.String(36), nullable=False),
        sa.Column("label_de", sa.String(300), nullable=False),
        sa.Column("label_en", sa.String(300), nullable=False),
        sa.Column("alt", sa.Text(), nullable=False),
        sa.Column("pruefsumme", sa.String(64), nullable=False),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True),
        sa.Column("entry_id", sa.String(36), sa.ForeignKey("entries.id", ondelete="CASCADE"), nullable=True),
        sa.Column("proposal_id", sa.String(36), sa.ForeignKey("proposals.id", ondelete="CASCADE"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("name_fix_undo")
    op.drop_table("name_fixes")
