"""Hinweis-Mails zum Guthaben, Sprache des Kontos (Server 0.4.135)

Revision ID: a9c3e5b7d1f2
Revises: f8b2d4a6c1e3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a9c3e5b7d1f2'
down_revision: Union[str, Sequence[str], None] = 'f8b2d4a6c1e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ohne Tabellen-Neubau: guthaben wird von buchungen (nur Einfügen, Trigger) referenziert
    op.add_column("guthaben", sa.Column("hinweise", sa.String(64), nullable=True))
    op.add_column("guthaben", sa.Column("hinweis_mail_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("guthaben_mails_aus", sa.Boolean(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("sprache", sa.String(8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.drop_column("sprache")
        b.drop_column("guthaben_mails_aus")
    with op.batch_alter_table("guthaben") as b:
        b.drop_column("hinweis_mail_at")
        b.drop_column("hinweise")
