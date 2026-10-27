"""Kampagnen-Einstellungen mit Fassung und Protokoll (Schnittstelle 0.4.25, Server 0.4.132)

Revision ID: e4a7c1f9b3d6
Revises: d9f3b5a7c2e4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e4a7c1f9b3d6'
down_revision: Union[str, Sequence[str], None] = 'd9f3b5a7c2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("campaigns") as b:
        b.add_column(sa.Column("settings_version", sa.Integer(), nullable=False, server_default="1"))
        b.add_column(sa.Column("settings_updated_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("settings_updated_by", sa.String(36), nullable=True))
    op.create_table(
        "kampagnen_einstellungen_protokoll",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("campaign_id", sa.String(36), sa.ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False),
        sa.Column("feld", sa.String(40), nullable=False),
        sa.Column("alt", sa.String(20), nullable=True),
        sa.Column("neu", sa.String(20), nullable=True),
        sa.Column("fassung", sa.Integer(), nullable=False),
        sa.Column("von_member_id", sa.String(36), nullable=True),
        sa.Column("vom_betreiber", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("angelegt", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_kampagnen_einstellungen_protokoll_campaign_id", "kampagnen_einstellungen_protokoll",
                    ["campaign_id"])


def downgrade() -> None:
    op.drop_index("ix_kampagnen_einstellungen_protokoll_campaign_id", "kampagnen_einstellungen_protokoll")
    op.drop_table("kampagnen_einstellungen_protokoll")
    with op.batch_alter_table("campaigns") as b:
        b.drop_column("settings_updated_by")
        b.drop_column("settings_updated_at")
        b.drop_column("settings_version")
