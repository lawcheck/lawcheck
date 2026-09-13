"""add consent_log (факт согласия на обработку ПДн)

Revision ID: f4d2b8e61a07
Revises: e3c9a71b4f52
Create Date: 2026-09-13 18:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'f4d2b8e61a07'
down_revision: Union[str, Sequence[str], None] = 'e3c9a71b4f52'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consent_log",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("form", sa.String(length=32), nullable=False),
        sa.Column("ref", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("version", sa.String(length=16), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_consent_log_ref", "consent_log", ["ref"])
    op.create_index("ix_consent_log_created_at", "consent_log", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_consent_log_created_at", table_name="consent_log")
    op.drop_index("ix_consent_log_ref", table_name="consent_log")
    op.drop_table("consent_log")
