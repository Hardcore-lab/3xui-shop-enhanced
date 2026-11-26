"""Increase server password column size for encryption

Revision ID: encrypt_passwords_001
Revises: c2a9b7d3c4f0
Create Date: 2026-02-13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'encrypt_passwords_001'
down_revision: Union[str, None] = "c2a9b7d3c4f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Increase password column size to accommodate encrypted passwords."""
    # Increase password field from 255 to 512 characters for Fernet encryption
    with op.batch_alter_table('servers', schema=None) as batch_op:
        batch_op.alter_column('password',
                   existing_type=sa.String(length=255),
                   type_=sa.String(length=512),
                   existing_nullable=True)


def downgrade() -> None:
    """Revert password column size back to 255."""
    with op.batch_alter_table('servers', schema=None) as batch_op:
        batch_op.alter_column('password',
                   existing_type=sa.String(length=512),
                   type_=sa.String(length=255),
                   existing_nullable=True)
