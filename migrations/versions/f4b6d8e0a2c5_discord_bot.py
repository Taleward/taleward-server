"""Discord-Bot (Server 0.4.155, Schnittstelle 0.4.32)

Revision ID: f4b6d8e0a2c5
Revises: e2a4c6e8f0b3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f4b6d8e0a2c5'
down_revision: Union[str, Sequence[str], None] = 'e2a4c6e8f0b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bot_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by", sa.String(36), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_table(
        "discord_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("code_hash", sa.String(64), nullable=False, index=True),
        sa.Column("bot_key_id", sa.String(36), sa.ForeignKey("bot_keys.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("guild_id", sa.String(24), nullable=False),
        sa.Column("guild_name", sa.String(100), nullable=False),
        sa.Column("discord_user_id", sa.String(24), nullable=False, index=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "discord_connections",
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("guild_id", sa.String(24), nullable=False, index=True),
        sa.Column("guild_name", sa.String(100), nullable=False),
        sa.Column("bot_key_id", sa.String(36), sa.ForeignKey("bot_keys.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("discord_user_id", sa.String(24), nullable=False),
        sa.Column("connected_by_member_id", sa.String(36), nullable=True),
        sa.Column("connected_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "consent_links",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("discord_hash", sa.String(64), nullable=False),
        sa.Column("anzeigename", sa.String(40), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "discord_protokoll",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False,
                  index=True),
        sa.Column("was", sa.String(40), nullable=False),
        sa.Column("daten", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(), nullable=False),
    )
    with op.batch_alter_table("attendees") as b:
        b.add_column(sa.Column("discord_hash", sa.String(64), nullable=True))
        b.add_column(sa.Column("character_name", sa.String(60), nullable=True))
        b.add_column(sa.Column("mit_spur", sa.Boolean(), nullable=False, server_default="0"))
        b.add_column(sa.Column("consent_fassung", sa.String(40), nullable=True))
    with op.batch_alter_table("sessions") as b:
        b.add_column(sa.Column("bot_key_id", sa.String(36), nullable=True))
        b.add_column(sa.Column("recording_started_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("interrupted", sa.Boolean(), nullable=False, server_default="0"))
        b.add_column(sa.Column("bot_ereignisse", sa.Text(), nullable=True))
        b.add_column(sa.Column("bot_gestoppt_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("campaigns") as b:
        b.add_column(sa.Column("discord_pause", sa.Boolean(), nullable=False, server_default="0"))
    with op.batch_alter_table("upload_files") as b:
        b.add_column(sa.Column("track_attendee_id", sa.String(36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("upload_files") as b:
        b.drop_column("track_attendee_id")
    with op.batch_alter_table("campaigns") as b:
        b.drop_column("discord_pause")
    with op.batch_alter_table("sessions") as b:
        for c in ("bot_gestoppt_at", "bot_ereignisse", "interrupted", "recording_started_at", "bot_key_id"):
            b.drop_column(c)
    with op.batch_alter_table("attendees") as b:
        for c in ("consent_fassung", "mit_spur", "character_name", "discord_hash"):
            b.drop_column(c)
    for t in ("discord_protokoll", "consent_links", "discord_connections", "discord_links", "bot_keys"):
        op.drop_table(t)
