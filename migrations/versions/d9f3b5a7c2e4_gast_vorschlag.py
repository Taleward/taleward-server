"""Stimmvorschlag für Gäste (Schnittstelle 0.4.24, Server 0.4.129)

Revision ID: d9f3b5a7c2e4
Revises: c8e2a4f6b1d3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd9f3b5a7c2e4'
down_revision: Union[str, Sequence[str], None] = 'c8e2a4f6b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("speakers", sa.Column("suggested_guest_name", sa.String(200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("speakers") as b:
        b.drop_column("suggested_guest_name")
