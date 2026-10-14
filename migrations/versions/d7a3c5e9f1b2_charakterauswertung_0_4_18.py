"""Schnittstelle 0.4.18: Charakterbögen und Hintergründe auswerten

Revision ID: d7a3c5e9f1b2
Revises: c4e8f2a6b9d1
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd7a3c5e9f1b2'
down_revision: Union[str, Sequence[str], None] = 'c4e8f2a6b9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Ohne batch: bestehende Tabellen nicht neu anlegen (Teilindizes bleiben unverändert)
_SPALTEN = (("jobs", True), ("usage_log", False), ("kosten_auftraege", True), ("proposals", True),
            ("gm_notices", False))


def upgrade() -> None:
    op.create_table(
        "character_analyses",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(36), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("document_id", sa.String(36), nullable=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("uses_cloud", sa.Boolean(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("pages_limited", sa.Boolean(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("abschnitte", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
    )
    for spalte in ("campaign_id", "member_id", "document_id", "state"):
        op.create_index(f"ix_character_analyses_{spalte}", "character_analyses", [spalte])
    op.add_column("campaigns", sa.Column("allow_character_analysis", sa.Boolean(), nullable=False,
                                         server_default="0"))
    for tabelle, index in _SPALTEN:
        op.add_column(tabelle, sa.Column("analysis_id", sa.String(36), nullable=True))
        if index:
            op.create_index(f"ix_{tabelle}_analysis_id", tabelle, ["analysis_id"])


def downgrade() -> None:
    for tabelle, index in reversed(_SPALTEN):
        if index:
            op.drop_index(f"ix_{tabelle}_analysis_id", table_name=tabelle)
        op.drop_column(tabelle, "analysis_id")  # SQLite ab 3.35
    op.drop_column("campaigns", "allow_character_analysis")
    for spalte in ("state", "document_id", "member_id", "campaign_id"):
        op.drop_index(f"ix_character_analyses_{spalte}", table_name="character_analyses")
    op.drop_table("character_analyses")
