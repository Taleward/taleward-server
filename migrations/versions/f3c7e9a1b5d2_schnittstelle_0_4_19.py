"""Schnittstelle 0.4.19: Beziehung bei Vorschlägen aus der mitgebrachten Welt

Revision ID: f3c7e9a1b5d2
Revises: e2b6d4f8a1c3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f3c7e9a1b5d2'
down_revision: Union[str, Sequence[str], None] = 'e2b6d4f8a1c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ohne batch: Tabelle nicht neu anlegen
    op.add_column("proposals", sa.Column("relation", sa.String(120), nullable=True))


def downgrade() -> None:
    op.drop_column("proposals", "relation")  # SQLite ab 3.35
