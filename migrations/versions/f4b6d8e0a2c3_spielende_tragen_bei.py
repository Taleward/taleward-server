"""Spielende tragen bei: Vorschläge, Merken, Pronomen, Zähler (Schnittstelle 0.4.28, Server 0.4.144)

Revision ID: f4b6d8e0a2c3
Revises: e3a5c7d9f1b2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f4b6d8e0a2c3'
down_revision: Union[str, Sequence[str], None] = 'e3a5c7d9f1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ohne Tabellen-Neubau (nur neue Spalten mit Standardwert bzw. leer)
    op.add_column("campaigns", sa.Column("player_proposals", sa.Boolean(), nullable=False, server_default="1"))
    op.add_column("campaigns", sa.Column("counter_kinds", sa.Text(), nullable=True))
    op.add_column("members", sa.Column("character_pronouns", sa.String(32), nullable=True))
    op.add_column("members", sa.Column("counters", sa.Text(), nullable=True))
    op.add_column("entries", sa.Column("pronouns", sa.String(32), nullable=True))
    op.add_column("uploads", sa.Column("spans", sa.Text(), nullable=True))
    op.create_table(
        "player_proposals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("author_member_id", sa.String(36), nullable=True),
        sa.Column("entry_type", sa.String(16), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("target_entry_id", sa.String(36), nullable=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="open"),
        sa.Column("result_entry_id", sa.String(36), nullable=True),
        sa.Column("text_sichtbar", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("decided_at", sa.DateTime(), nullable=True),
    )
    for spalte in ("campaign_id", "author_member_id", "state", "created_at", "decided_at"):
        op.create_index(f"ix_player_proposals_{spalte}", "player_proposals", [spalte])
    op.create_table(
        "bookmarks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("member_id", sa.String(36), sa.ForeignKey("members.id", ondelete="CASCADE"), nullable=False),
        sa.Column("at", sa.DateTime(), nullable=False),
        sa.Column("note", sa.String(280), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bookmarks_session_id", "bookmarks", ["session_id"])
    op.create_index("ix_bookmarks_member_id", "bookmarks", ["member_id"])


def downgrade() -> None:
    op.drop_index("ix_bookmarks_member_id", table_name="bookmarks")
    op.drop_index("ix_bookmarks_session_id", table_name="bookmarks")
    op.drop_table("bookmarks")
    for spalte in ("decided_at", "created_at", "state", "author_member_id", "campaign_id"):
        op.drop_index(f"ix_player_proposals_{spalte}", table_name="player_proposals")
    op.drop_table("player_proposals")
    with op.batch_alter_table("uploads") as b:
        b.drop_column("spans")
    with op.batch_alter_table("entries") as b:
        b.drop_column("pronouns")
    with op.batch_alter_table("members") as b:
        b.drop_column("counters")
        b.drop_column("character_pronouns")
    with op.batch_alter_table("campaigns") as b:
        b.drop_column("counter_kinds")
        b.drop_column("player_proposals")
