"""add_user_clients_table

Revision ID: a1b2c3d4e5f6
Revises: 9aa6ddb8e352
Create Date: 2025-12-25 06:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "9aa6ddb8e352"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create user_clients table
    op.create_table(
        "user_clients",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("client_index", sa.Integer(), nullable=False),
        sa.Column("client_email", sa.String(length=255), nullable=False),
        sa.Column("vpn_id", sa.String(length=36), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_user_clients_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["server_id"],
            ["servers.id"],
            name=op.f("fk_user_clients_server_id_servers"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_clients")),
        sa.UniqueConstraint("client_email", name=op.f("uq_user_clients_client_email")),
        sa.UniqueConstraint("vpn_id", name=op.f("uq_user_clients_vpn_id")),
    )
    
    # Create indexes for better query performance
    op.create_index(
        op.f("ix_user_clients_user_id"),
        "user_clients",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_clients_client_email"),
        "user_clients",
        ["client_email"],
        unique=True,
    )
    op.create_index(
        op.f("ix_user_clients_vpn_id"),
        "user_clients",
        ["vpn_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_user_clients_server_id"),
        "user_clients",
        ["server_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_clients_server_id"), table_name="user_clients")
    op.drop_index(op.f("ix_user_clients_vpn_id"), table_name="user_clients")
    op.drop_index(op.f("ix_user_clients_client_email"), table_name="user_clients")
    op.drop_index(op.f("ix_user_clients_user_id"), table_name="user_clients")
    op.drop_table("user_clients")

