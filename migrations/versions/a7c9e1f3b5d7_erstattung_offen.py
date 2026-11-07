"""Offene Erstattung je Bezahlvorgang (Server 0.4.158)

Revision ID: a7c9e1f3b5d7
Revises: f4b6d8e0a2c5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a7c9e1f3b5d7'
down_revision: Union[str, Sequence[str], None] = 'f4b6d8e0a2c5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("bezahlvorgaenge") as t:
        t.add_column(sa.Column("erstattung_offen_cent", sa.Integer(), nullable=True))
        t.add_column(sa.Column("erstattung_offen_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("bezahlvorgaenge") as t:
        t.drop_column("erstattung_offen_at")
        t.drop_column("erstattung_offen_cent")
