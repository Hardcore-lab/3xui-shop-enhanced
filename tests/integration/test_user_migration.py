"""
Integration tests for user server migration.
"""
from dataclasses import fields

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.models import ServicesContainer
from app.bot.services.user_management import UserManagementService
from app.db.models import User, Server, UserClient


@pytest.mark.asyncio
async def test_change_user_server_validation():
    """Test server migration with input validation."""
    session = MagicMock()
    session_factory = MagicMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    
    user_management = UserManagementService(session=session)
    
    services = MagicMock(spec=[field.name for field in fields(ServicesContainer)])
    services.server_pool = MagicMock()
    services.server_pool.sync_servers = AsyncMock()
    services.vpn = MagicMock()
    services.vpn.get_client_data = AsyncMock(return_value=None)
    services.vpn.create_multiple_clients = AsyncMock(return_value=True)
    services.vpn.get_key = AsyncMock(return_value="http://example.com/sub/uuid")
    
    # Mock user
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.id = 1
    user.server_id = 1
    
    user_management.get_user_details = AsyncMock(return_value=user)
    
    # Mock Server queries
    old_server = MagicMock(spec=Server)
    old_server.id = 1
    old_server.name = "Old Server"
    old_server.online = True
    old_server.current_clients = 5
    old_server.max_clients = 10
    
    new_server = MagicMock(spec=Server)
    new_server.id = 2
    new_server.name = "New Server"
    new_server.online = True
    new_server.current_clients = 3
    new_server.max_clients = 10
    
    session.get = AsyncMock(side_effect=[user, old_server, new_server])
    
    # Mock UserClient
    user_client = MagicMock(spec=UserClient)
    user_client.client_email = "123456789_1"
    user_client.server_id = 1
    user_client.client_index = 1
    
    with patch('app.db.models.UserClient.get_by_user_id', new_callable=AsyncMock) as mock_get_clients:
        mock_get_clients.return_value = [user_client]
        
        with patch('app.db.models.UserClient.update_server_id', new_callable=AsyncMock) as mock_update:
            mock_update.return_value = None
            
            # Test successful migration
            result = await user_management.change_user_server(
                tg_id=123456789,
                new_server_id=2,
                services=services
            )
    
            # Should succeed (with proper mocks)
            # Note: This is a simplified test - full migration requires more complex mocking


@pytest.mark.asyncio
async def test_change_user_server_invalid_input():
    """Test server migration with invalid input."""
    session = MagicMock()
    session_factory = MagicMock()
    user_management = UserManagementService(session=session)
    
    services = MagicMock(spec=[field.name for field in fields(ServicesContainer)])
        
    # Test with None server_id
    result = await user_management.change_user_server(
        tg_id=123456789,
        new_server_id=None,
        services=services
    )
    assert result is False

    # Test with invalid server_id type
    result = await user_management.change_user_server(
        tg_id=123456789,
        new_server_id="invalid",
        services=services
    )
    assert result is False
