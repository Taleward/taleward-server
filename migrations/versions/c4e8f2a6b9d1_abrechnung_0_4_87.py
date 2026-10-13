"""Abrechnung 0.4.87: Kostenaufträge mit Kampagne, Runde und Unterlage; ein Gutschein je Aktion und Guthaben

Revision ID: c4e8f2a6b9d1
Revises: b3d9e1f7a2c4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c4e8f2a6b9d1'
down_revision: Union[str, Sequence[str], None] = 'b3d9e1f7a2c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ohne batch: die Tabelle nicht neu anlegen, damit der Teilindex „ein laufender je Guthaben“ unverändert bleibt
    for spalte in ("campaign_id", "session_id", "document_id"):
        op.add_column("kosten_auftraege", sa.Column(spalte, sa.String(36), nullable=True))
        op.create_index(f"ix_kosten_auftraege_{spalte}", "kosten_auftraege", [spalte])
    # Bestehende Zeilen aus ihrem Auftrag ergänzen (solange es ihn noch gibt)
    op.execute("UPDATE kosten_auftraege SET session_id = (SELECT session_id FROM jobs WHERE jobs.id = "
               "kosten_auftraege.job_id), document_id = (SELECT document_id FROM jobs WHERE jobs.id = "
               "kosten_auftraege.job_id)")
    op.create_index("ix_gutscheine_aktion_eingeloest", "gutscheine", ["aktion", "eingeloest_von"], unique=True,
                    sqlite_where=sa.text("eingeloest_von IS NOT NULL"))


def downgrade() -> None:
    op.drop_index("ix_gutscheine_aktion_eingeloest", table_name="gutscheine")
    for spalte in ("document_id", "session_id", "campaign_id"):
        op.drop_index(f"ix_kosten_auftraege_{spalte}", table_name="kosten_auftraege")
        op.drop_column("kosten_auftraege", spalte)  # SQLite ab 3.35
