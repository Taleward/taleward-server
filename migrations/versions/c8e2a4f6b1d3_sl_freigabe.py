"""SL-Freigabe je Konto (Schnittstelle 0.4.23, Server 0.4.128)

Bestehende Konten, die schon SL einer Kampagne sind oder ein Verwaltungskonto haben, bekommen granted (ohne
Entscheidungszeitpunkt), alle anderen none.

Revision ID: c8e2a4f6b1d3
Revises: b7d1e5f3a9c2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c8e2a4f6b1d3'
down_revision: Union[str, Sequence[str], None] = 'b7d1e5f3a9c2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users") as b:
        b.add_column(sa.Column("gm_permission", sa.String(16), nullable=False, server_default="none"))
        b.add_column(sa.Column("gm_requested_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("gm_decided_at", sa.DateTime(), nullable=True))
        b.add_column(sa.Column("gm_decided_by", sa.String(36), nullable=True))
        b.add_column(sa.Column("gm_sprache", sa.String(8), nullable=True))
        b.add_column(sa.Column("gm_mail_fehler", sa.Boolean(), nullable=False, server_default="0"))
    op.execute("UPDATE users SET gm_permission = 'granted' WHERE id IN "
               "(SELECT user_id FROM members WHERE role = 'gm' AND user_id IS NOT NULL) "
               "OR id IN (SELECT user_id FROM org_members WHERE role = 'admin')")


def downgrade() -> None:
    with op.batch_alter_table("users") as b:
        for spalte in ("gm_mail_fehler", "gm_sprache", "gm_decided_by", "gm_decided_at", "gm_requested_at",
                       "gm_permission"):
            b.drop_column(spalte)
