"""Übernommener Text eines Vorschlags von Spielenden (Server 0.4.149)

Revision ID: c8e0a2b4d6f8
Revises: c6e8a0b2d4f6
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c8e0a2b4d6f8'
down_revision: Union[str, Sequence[str], None] = 'c6e8a0b2d4f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("player_proposals", sa.Column("oeffentlicher_text", sa.Text(), nullable=True))
    # Bisher übernommene: der Vorschlagstext. Hat die SL ihn beim Übernehmen geändert, passt er nicht und die Urheberin
    # sieht „closed“ – lieber zu vorsichtig als ein Hinweis auf Geheimes.
    vorschlag = sa.table("player_proposals", sa.column("state", sa.String), sa.column("text_sichtbar", sa.Boolean),
                         sa.column("detail", sa.Text), sa.column("oeffentlicher_text", sa.Text))
    op.execute(vorschlag.update().where(vorschlag.c.state == "accepted", vorschlag.c.text_sichtbar.is_(True))
               .values(oeffentlicher_text=vorschlag.c.detail))


def downgrade() -> None:
    with op.batch_alter_table("player_proposals") as b:
        b.drop_column("oeffentlicher_text")
