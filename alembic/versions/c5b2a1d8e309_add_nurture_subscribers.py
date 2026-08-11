"""add nurture_subscribers table

Revision ID: c5b2a1d8e309
Revises: 237dc0c8e259
Create Date: 2026-08-09 19:50

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c5b2a1d8e309'
down_revision: Union[str, Sequence[str], None] = '237dc0c8e259'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'nurture_subscribers',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(255), nullable=False),
        sa.Column('step', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('next_send_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('unsub_token', sa.String(64), nullable=False, server_default=''),
        sa.Column('unsubscribed_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.Index('ix_nurture_subscribers_email', 'email'),
    )


def downgrade() -> None:
    op.drop_table('nurture_subscribers')