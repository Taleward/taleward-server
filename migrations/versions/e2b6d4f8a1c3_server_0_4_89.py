"""Server 0.4.89: Herkunft von Einträgen aus einer Auswertung (S31); Kampagne an alten Kostenaufträgen nachtragen

Revision ID: e2b6d4f8a1c3
Revises: d7a3c5e9f1b2
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'e2b6d4f8a1c3'
down_revision: Union[str, Sequence[str], None] = 'd7a3c5e9f1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ohne batch: Tabellen nicht neu anlegen
    op.add_column("entries", sa.Column("origin_analysis_id", sa.String(36), nullable=True))
    # Einträge, die schon aus einer Auswertung übernommen wurden (0.4.88), nachträglich markieren
    op.execute("UPDATE entries SET origin_analysis_id = (SELECT p.analysis_id FROM proposals p WHERE p.target_entry_id "
               "= entries.id AND p.analysis_id IS NOT NULL AND p.action = 'create' AND p.decision = 'accepted' "
               "LIMIT 1) WHERE origin_analysis_id IS NULL")
    # Kostenaufträge aus 0.4.85/0.4.86 ohne Kampagne: aus Runde bzw. Unterlage ergänzen, solange es sie gibt
    op.execute("UPDATE kosten_auftraege SET campaign_id = COALESCE("
               "(SELECT campaign_id FROM sessions WHERE sessions.id = kosten_auftraege.session_id), "
               "(SELECT campaign_id FROM documents WHERE documents.id = kosten_auftraege.document_id)) "
               "WHERE campaign_id IS NULL")


def downgrade() -> None:
    op.drop_column("entries", "origin_analysis_id")  # SQLite ab 3.35
