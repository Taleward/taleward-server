"""Abrechnung mit Guthaben (0.4.85): Buchungsbuch, laufende Kostenaufträge, Gutscheine, Protokoll

Revision ID: b3d9e1f7a2c4
Revises: a7c2e5f9b1d3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b3d9e1f7a2c4'
down_revision: Union[str, Sequence[str], None] = 'a7c2e5f9b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NUR_EINFUEGEN = ("buchungen", "abrechnung_protokoll")


def upgrade() -> None:
    op.create_table(
        "guthaben",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("art", sa.String(16), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
                  unique=True),
        sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id", ondelete="SET NULL"),
                  nullable=True, unique=True),
        sa.Column("freigabe_ab_cent", sa.Integer(), nullable=False),
        sa.Column("bogen_seiten", sa.Integer(), nullable=False),
        sa.Column("boegen_je_spieler", sa.Integer(), nullable=False),
        sa.Column("angelegt", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "buchungen",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("guthaben_id", sa.String(36), sa.ForeignKey("guthaben.id"), nullable=False, index=True),
        sa.Column("art", sa.String(16), nullable=False),
        sa.Column("betrag_cent", sa.Integer(), nullable=False),
        sa.Column("waehrung", sa.String(3), nullable=False),
        sa.Column("job_id", sa.String(36), nullable=True),
        sa.Column("gutschein_id", sa.String(36), nullable=True),
        sa.Column("gegen_buchung_id", sa.String(36), nullable=True, unique=True),
        sa.Column("ereignis_id", sa.String(200), nullable=True, unique=True),
        sa.Column("grund", sa.Text(), nullable=True),
        sa.Column("von", sa.String(36), nullable=False),
        sa.Column("angelegt", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "kosten_auftraege",
        sa.Column("job_id", sa.String(36), primary_key=True),
        sa.Column("guthaben_id", sa.String(36), sa.ForeignKey("guthaben.id"), nullable=False, index=True),
        sa.Column("weg", sa.String(16), nullable=False),
        sa.Column("art", sa.String(16), nullable=False),
        sa.Column("menge", sa.Integer(), nullable=False),
        sa.Column("schaetzung_cent", sa.Integer(), nullable=False),
        sa.Column("zustand", sa.String(16), nullable=False),
        sa.Column("betrag_cent", sa.Integer(), nullable=True),
        sa.Column("angelegt", sa.DateTime(), nullable=False),
        sa.Column("abgerechnet_at", sa.DateTime(), nullable=True),
    )
    # Höchstens ein laufender kostenpflichtiger Auftrag je Guthaben – geprüft von der Datenbank selbst
    op.create_index("ix_kosten_auftraege_ein_laufender", "kosten_auftraege", ["guthaben_id"], unique=True,
                    sqlite_where=sa.text("zustand = 'laeuft'"))
    op.create_table(
        "gutscheine",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("aktion", sa.String(100), nullable=False, index=True),
        sa.Column("betrag_cent", sa.Integer(), nullable=False),
        sa.Column("gueltig_bis", sa.DateTime(), nullable=True),
        sa.Column("eingeloest_von", sa.String(36), nullable=True),
        sa.Column("eingeloest_at", sa.DateTime(), nullable=True),
        sa.Column("angelegt_von", sa.String(36), nullable=False),
        sa.Column("angelegt", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "abrechnung_protokoll",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("von", sa.String(36), nullable=False),
        sa.Column("guthaben_id", sa.String(36), nullable=True, index=True),
        sa.Column("was", sa.String(40), nullable=False),
        sa.Column("daten", sa.Text(), nullable=False),
        sa.Column("angelegt", sa.DateTime(), nullable=False),
    )
    for tabelle in NUR_EINFUEGEN:  # Buchungen und Protokoll lassen sich nicht ändern oder löschen
        for vorgang in ("UPDATE", "DELETE"):
            op.execute(f"CREATE TRIGGER {tabelle}_kein_{vorgang.lower()} BEFORE {vorgang} ON {tabelle} "
                       f"BEGIN SELECT RAISE(ABORT, '{tabelle}: nur Einfuegen erlaubt'); END")
    with op.batch_alter_table("campaigns") as b:
        b.add_column(sa.Column("zahler_user_id", sa.String(36), nullable=True))
        b.add_column(sa.Column("zahler_angebot_user_id", sa.String(36), nullable=True))
    with op.batch_alter_table("jobs") as b:
        b.add_column(sa.Column("kosten_freigegeben_at", sa.DateTime(), nullable=True))
    with op.batch_alter_table("uploads") as b:
        b.add_column(sa.Column("dauer_sekunden", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("uploads") as b:
        b.drop_column("dauer_sekunden")
    with op.batch_alter_table("jobs") as b:
        b.drop_column("kosten_freigegeben_at")
    with op.batch_alter_table("campaigns") as b:
        b.drop_column("zahler_angebot_user_id")
        b.drop_column("zahler_user_id")
    for tabelle in NUR_EINFUEGEN:
        for vorgang in ("update", "delete"):
            op.execute(f"DROP TRIGGER IF EXISTS {tabelle}_kein_{vorgang}")
    op.drop_table("abrechnung_protokoll")
    op.drop_table("gutscheine")
    op.drop_index("ix_kosten_auftraege_ein_laufender", table_name="kosten_auftraege")
    op.drop_table("kosten_auftraege")
    op.drop_table("buchungen")
    op.drop_table("guthaben")
