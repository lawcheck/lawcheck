"""add entry_ref/entry_url to orders

Revision ID: a7f1c93b6d24
Revises: c5b2a1d8e309
Create Date: 2026-08-13 12:10

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7f1c93b6d24'
down_revision: Union[str, Sequence[str], None] = 'c5b2a1d8e309'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('entry_ref', sa.String(300), nullable=False,
                                      server_default=''))
    op.add_column('orders', sa.Column('entry_url', sa.String(300), nullable=False,
                                      server_default=''))


def downgrade() -> None:
    op.drop_column('orders', 'entry_url')
    op.drop_column('orders', 'entry_ref')
