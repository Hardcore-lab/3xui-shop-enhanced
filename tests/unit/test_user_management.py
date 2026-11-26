"""
Unit tests for UserManagementService critical operations.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.services.user_management import UserManagementService
from app.db.models import User, Server


@pytest.mark.asyncio
async def test_change_user_server_validation():
    """Test input validation in change_user_server."""
    session = MagicMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    
    user_management = UserManagementService(session=session)
    
    services = MagicMock()
    services.server_pool = MagicMock()
    services.server_pool.sync_servers = AsyncMock()
    services.vpn = MagicMock()
    services.vpn.get_client_data = AsyncMock(return_value=None)
    
    # Mock get_user_details
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.id = 1
    user.server_id = 1
    
    user_management.get_user_details = AsyncMock(return_value=user)
    
    # Test invalid server_id (None)
    result = await user_management.change_user_server(tg_id=123456789, new_server_id=None, services=services)
    assert result is False
    
    # Test invalid server_id (not int)
    result = await user_management.change_user_server(tg_id=123456789, new_server_id="invalid", services=services)
    assert result is False
    
    # Test invalid server_id (negative)
    result = await user_management.change_user_server(tg_id=123456789, new_server_id=-1, services=services)
    assert result is False
    
    # Test invalid server_id (zero)
    result = await user_management.change_user_server(tg_id=123456789, new_server_id=0, services=services)
    assert result is False


@pytest.mark.asyncio
async def test_add_bonus_days_validation():
    """Test input validation in add_bonus_days."""
    session = MagicMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    
    user_management = UserManagementService(session=session)
    
    services = MagicMock()
    services.vpn = MagicMock()
    services.vpn.get_client_data = AsyncMock(return_value=None)
    services.vpn.process_bonus_days = AsyncMock(return_value=True)
    
    # Mock get_user_details
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.id = 1
    user.server_id = 1
    
    user_management.get_user_details = AsyncMock(return_value=user)
    
    # Test invalid tg_id
    result = await user_management.add_bonus_days(tg_id=0, days=10, services=services)
    assert result is False
    
    result = await user_management.add_bonus_days(tg_id=-1, days=10, services=services)
    assert result is False
    
    # Test invalid days
    result = await user_management.add_bonus_days(tg_id=123456789, days=0, services=services)
    assert result is False
    
    result = await user_management.add_bonus_days(tg_id=123456789, days=366, services=services)
    assert result is False

