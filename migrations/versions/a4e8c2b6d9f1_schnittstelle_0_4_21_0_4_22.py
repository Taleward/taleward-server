"""Schnittstelle 0.4.21 und 0.4.22: Auch genannt, Dublettenprüfung, Stimmproben, Nutzungsbedingungen

Revision ID: a4e8c2b6d9f1
Revises: f3c7e9a1b5d2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a4e8c2b6d9f1'
down_revision: Union[str, Sequence[str], None] = 'f3c7e9a1b5d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ohne batch: Tabellen nicht neu anlegen
    op.add_column("entries", sa.Column("aliases", sa.Text(), nullable=True))
    op.add_column("proposals", sa.Column("match", sa.Text(), nullable=True))
    op.add_column("proposals", sa.Column("merge_into_entry_id", sa.String(36), nullable=True))
    op.add_column("speakers", sa.Column("samples", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("terms_accepted_version", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("terms_accepted_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "terms_accepted_at")
    op.drop_column("users", "terms_accepted_version")
    op.drop_column("speakers", "samples")
    op.drop_column("proposals", "merge_into_entry_id")
    op.drop_column("proposals", "match")
    op.drop_column("entries", "aliases")
