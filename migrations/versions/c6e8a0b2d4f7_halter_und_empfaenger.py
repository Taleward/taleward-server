"""Halter bei Beute-Vorschlägen, Kommentare an mehrere Personen (Schnittstelle 0.4.26, Server 0.4.139)

Revision ID: c6e8a0b2d4f7
Revises: b4d6f8a0c2e5
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c6e8a0b2d4f7'
down_revision: Union[str, Sequence[str], None] = 'b4d6f8a0c2e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("proposals", sa.Column("holder_member_id", sa.String(36), nullable=True))
    op.add_column("proposals", sa.Column("holder_cleared", sa.Boolean(), nullable=False, server_default="0"))
    op.create_table(
        "comment_recipients",
        sa.Column("comment_id", sa.String(36), sa.ForeignKey("comments.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("member_id", sa.String(36), sa.ForeignKey("members.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_index("ix_comment_recipients_member_id", "comment_recipients", ["member_id"])
    # bisherige private Kommentare: der eine Empfänger
    op.execute("INSERT INTO comment_recipients (comment_id, member_id, position) "
               "SELECT id, recipient_member_id, 0 FROM comments WHERE recipient_member_id IS NOT NULL")


def downgrade() -> None:
    op.drop_index("ix_comment_recipients_member_id", "comment_recipients")
    op.drop_table("comment_recipients")
    with op.batch_alter_table("proposals") as b:
        b.drop_column("holder_cleared")
        b.drop_column("holder_member_id")
