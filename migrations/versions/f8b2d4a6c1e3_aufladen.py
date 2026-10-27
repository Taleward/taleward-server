"""Aufladen über Zahlungsanbieter (Server 0.4.133)

Revision ID: f8b2d4a6c1e3
Revises: e4a7c1f9b3d6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f8b2d4a6c1e3'
down_revision: Union[str, Sequence[str], None] = 'e4a7c1f9b3d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ohne Tabellen-Neubau: guthaben wird von buchungen (nur Einfügen, Trigger) referenziert
    op.add_column("guthaben", sa.Column("gesperrt_at", sa.DateTime(), nullable=True))
    op.create_table(
        "bezahlvorgaenge",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("guthaben_id", sa.String(36), sa.ForeignKey("guthaben.id"), nullable=False),
        sa.Column("von", sa.String(36), nullable=False),
        sa.Column("anbieter", sa.String(16), nullable=False),
        sa.Column("netto_cent", sa.Integer(), nullable=False),
        sa.Column("brutto_cent", sa.Integer(), nullable=False),
        sa.Column("zustand", sa.String(16), nullable=False, server_default="offen"),
        sa.Column("anbieter_ref", sa.String(100), nullable=True),
        sa.Column("zahlung_ref", sa.String(100), nullable=True),
        sa.Column("erstattet_cent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("buchung_id", sa.String(36), nullable=True),
        sa.Column("angelegt", sa.DateTime(), nullable=False),
        sa.Column("abgeschlossen", sa.DateTime(), nullable=True),
    )
    for spalte in ("guthaben_id", "von", "anbieter_ref", "zahlung_ref"):
        op.create_index(f"ix_bezahlvorgaenge_{spalte}", "bezahlvorgaenge", [spalte])


def downgrade() -> None:
    op.drop_table("bezahlvorgaenge")
    with op.batch_alter_table("guthaben") as b:
        b.drop_column("gesperrt_at")
