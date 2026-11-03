"""Klasse und Stufe am Charakter (Schnittstelle 0.4.29); feste Fassung statt mistral-large-latest (Server 0.4.145)

Revision ID: a5c7e9f1b3d4
Revises: f4b6d8e0a2c3
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'a5c7e9f1b3d4'
down_revision: Union[str, Sequence[str], None] = 'f4b6d8e0a2c3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Am 12.11.2026 wechselt „mistral-large-latest“ auf Large 4 (teurer, andere Antwortform). Wer den Namen gespeichert
# hat, bleibt bei Large 3 – umstellen geht danach bewusst in der Verwaltung.
_SCHLUESSEL = ("llm.api_modell", "llm.api_modell_vorschlaege", "llm.api_modell_notizen")


def upgrade() -> None:
    op.add_column("members", sa.Column("character_classes", sa.Text(), nullable=True))
    op.add_column("members", sa.Column("character_level", sa.String(64), nullable=True))
    meta = sa.table("server_meta", sa.column("key", sa.String), sa.column("value", sa.Text))
    op.execute(meta.update().where(meta.c.key.in_(_SCHLUESSEL), meta.c.value == "mistral-large-latest")
               .values(value="mistral-large-2512"))


def downgrade() -> None:
    with op.batch_alter_table("members") as b:
        b.drop_column("character_level")
        b.drop_column("character_classes")
