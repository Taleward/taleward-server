"""Bekannte Geräte für die Hinweis-Mail bei Anmeldung (Server 0.4.141)

Revision ID: d2f4a6b8c0e1
Revises: c6e8a0b2d4f7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd2f4a6b8c0e1'
down_revision: Union[str, Sequence[str], None] = 'c6e8a0b2d4f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "bekannte_geraete",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ort", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_bekannte_geraete_user_id", "bekannte_geraete", ["user_id"])
    op.create_index("ix_bekannte_geraete_last_seen_at", "bekannte_geraete", ["last_seen_at"])


def downgrade() -> None:
    op.drop_index("ix_bekannte_geraete_last_seen_at", table_name="bekannte_geraete")
    op.drop_index("ix_bekannte_geraete_user_id", table_name="bekannte_geraete")
    op.drop_table("bekannte_geraete")
