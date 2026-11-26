"""increase_payment_id_length

Revision ID: c2a9b7d3c4f0
Revises: a1b2c3d4e5f6, f1a2b3c4d5e6
Create Date: 2026-01-29 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c2a9b7d3c4f0"
down_revision: Union[str, Sequence[str], None] = ("a1b2c3d4e5f6", "f1a2b3c4d5e6")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.alter_column(
            "payment_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=255),
            existing_nullable=False,
        )

    if not sa.inspect(op.get_bind()).has_table("referrer_rewards"):
        op.create_table(
            "referrer_rewards",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_tg_id", sa.Integer(), sa.ForeignKey("users.tg_id", ondelete="CASCADE"), nullable=False),
            sa.Column("reward_type", sa.Enum("DAYS", "MONEY", name="referrerrewardtype"), nullable=False),
            sa.Column("reward_level", sa.Enum("FIRST_LEVEL", "SECOND_LEVEL", name="referrerrewardlevel")),
            sa.Column("amount", sa.Numeric(38, 18), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("rewarded_at", sa.DateTime()),
            sa.Column("payment_id", sa.String(255), nullable=False),
            sa.UniqueConstraint("user_tg_id", "payment_id", name="uq_user_payment"),
        )
        return

    with op.batch_alter_table("referrer_rewards", schema=None) as batch_op:
        batch_op.alter_column(
            "payment_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=255),
            existing_nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("referrer_rewards", schema=None) as batch_op:
        batch_op.alter_column(
            "payment_id",
            existing_type=sa.String(length=255),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    with op.batch_alter_table("transactions", schema=None) as batch_op:
        batch_op.alter_column(
            "payment_id",
            existing_type=sa.String(length=255),
            type_=sa.String(length=64),
            existing_nullable=False,
        )
