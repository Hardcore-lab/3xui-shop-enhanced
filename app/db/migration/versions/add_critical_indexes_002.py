"""Add missing critical indexes for performance

Revision ID: add_critical_indexes_002
Revises: encrypt_passwords_001
Create Date: 2026-02-13

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'add_critical_indexes_002'
down_revision: Union[str, None] = 'encrypt_passwords_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add missing indexes for frequently queried columns."""
    # Add indexes on transactions table
    op.create_index('ix_transactions_tg_id', 'transactions', ['tg_id'], unique=False)
    op.create_index('ix_transactions_status', 'transactions', ['status'], unique=False)
    op.create_index('ix_transactions_created_at', 'transactions', ['created_at'], unique=False)
    
    # Add indexes on referrals table
    op.create_index('ix_referrals_referrer_tg_id', 'referrals', ['referrer_tg_id'], unique=False)
    
    # Add indexes on referrer_rewards table
    op.create_index('ix_referrer_rewards_user_tg_id', 'referrer_rewards', ['user_tg_id'], unique=False)
    op.create_index('ix_referrer_rewards_rewarded_at', 'referrer_rewards', ['rewarded_at'], unique=False)
    
    # Add indexes on promocodes table
    op.create_index('ix_promocodes_is_activated', 'promocodes', ['is_activated'], unique=False)
    
    # Add indexes on plans table
    op.create_index('ix_plans_is_active', 'plans', ['is_active'], unique=False)
    op.create_index('ix_plans_devices', 'plans', ['devices'], unique=False)
    
    # Add index on users.source_invite_name (used in invite stats)
    op.create_index('ix_users_source_invite_name', 'users', ['source_invite_name'], unique=False)


def downgrade() -> None:
    """Remove the indexes."""
    op.drop_index('ix_transactions_tg_id', table_name='transactions')
    op.drop_index('ix_transactions_status', table_name='transactions')
    op.drop_index('ix_transactions_created_at', table_name='transactions')
    op.drop_index('ix_referrals_referrer_tg_id', table_name='referrals')
    op.drop_index('ix_referrer_rewards_user_tg_id', table_name='referrer_rewards')
    op.drop_index('ix_referrer_rewards_rewarded_at', table_name='referrer_rewards')
    op.drop_index('ix_promocodes_is_activated', table_name='promocodes')
    op.drop_index('ix_plans_is_active', table_name='plans')
    op.drop_index('ix_plans_devices', table_name='plans')
    op.drop_index('ix_users_source_invite_name', table_name='users')
