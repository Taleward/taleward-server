"""Passkeys und vertraute Geräte als zweiter Faktor (Server 0.4.143)

Revision ID: e3a5c7d9f1b2
Revises: d2f4a6b8c0e1
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e3a5c7d9f1b2'
down_revision: Union[str, Sequence[str], None] = 'd2f4a6b8c0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "passkeys",
        sa.Column("id", sa.String(1024), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("transports", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("rp_id", sa.String(255), nullable=False),
        sa.Column("name", sa.String(40), nullable=False),
        sa.Column("auffaellig", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_passkeys_user_id", "passkeys", ["user_id"])
    op.create_table(
        "vertraute_geraete",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("bereich", sa.String(16), nullable=False),
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("faktor_fassung", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bezeichnung", sa.String(60), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_vertraute_geraete_user_id", "vertraute_geraete", ["user_id"])
    op.create_index("ix_vertraute_geraete_expires_at", "vertraute_geraete", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_vertraute_geraete_expires_at", table_name="vertraute_geraete")
    op.drop_index("ix_vertraute_geraete_user_id", table_name="vertraute_geraete")
    op.drop_table("vertraute_geraete")
    op.drop_index("ix_passkeys_user_id", table_name="passkeys")
    op.drop_table("passkeys")
