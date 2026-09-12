"""add job_runs (сторож за задачами по расписанию)

Revision ID: e3c9a71b4f52
Revises: a7f1c93b6d24
Create Date: 2026-09-12 15:40

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e3c9a71b4f52'
down_revision: Union[str, Sequence[str], None] = 'a7f1c93b6d24'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job_runs",
        sa.Column("name", sa.String(length=64), primary_key=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_alert_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("job_runs")
