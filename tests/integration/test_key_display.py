"""
Integration tests for key display in user profile.
"""
from dataclasses import fields

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.models import ServicesContainer
from app.bot.services.vpn import VPNService
from app.db.models import User, UserClient


@pytest.mark.asyncio
async def test_key_display_single_device():
    """Test key display for single device subscription."""
    # Mock user
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.server_id = 1
    user.id = 1
    
    # Mock server
    server = MagicMock()
    server.id = 1
    server.host = "http://example.com"
    server.name = "Test Server"
    
    # Mock services
    services = MagicMock(spec=[field.name for field in fields(ServicesContainer)])
    connection = MagicMock()
    connection.server = server
    services.server_pool.get_connection = AsyncMock(return_value=connection)
    
    # Mock session (no UserClient records - old schema)
    session_factory = MagicMock()
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=user),
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    ))
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    
    # Mock VPNService
    vpn_service = VPNService(
        config=MagicMock(xui=MagicMock(SUBSCRIPTION_PORT=2096, SUBSCRIPTION_PATH="/sub/")),
        session=session_factory,
        server_pool_service=services.server_pool,
    )
    vpn_service.get_key = AsyncMock(return_value="http://example.com/sub/uuid")
    vpn_service.get_vless_key = AsyncMock(return_value="vless://uuid@example.com")
    
    # Test getting keys (should fallback to old schema)
    keys = await vpn_service.get_all_user_keys(user)
    
    # Verify single key was returned
    assert len(keys) == 1
    assert keys[0]["key"] == "http://example.com/sub/uuid"
    assert keys[0]["vless_key"] == "vless://uuid@example.com"


@pytest.mark.asyncio
async def test_key_display_multiple_devices():
    """Test key display for multiple devices subscription."""
    # Mock user
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.server_id = 1
    user.id = 1
    
    # Mock UserClient records
    user_client_1 = MagicMock(spec=UserClient)
    user_client_1.client_email = "123456789_1"
    user_client_1.vpn_id = "uuid-1"
    user_client_1.server_id = 1
    user_client_1.client_index = 1
    
    user_client_2 = MagicMock(spec=UserClient)
    user_client_2.client_email = "123456789_2"
    user_client_2.vpn_id = "uuid-2"
    user_client_2.server_id = 1
    user_client_2.client_index = 2
    
    # Mock server
    server = MagicMock()
    server.id = 1
    server.host = "http://example.com"
    server.name = "Test Server"
    
    # Mock services
    services = MagicMock(spec=[field.name for field in fields(ServicesContainer)])
    connection = MagicMock()
    connection.server = server
    services.server_pool.get_connection = AsyncMock(return_value=connection)
    
    # Mock session
    session_factory = MagicMock()
    session = MagicMock()
    mock_user_query = MagicMock()
    mock_user_query.scalar_one_or_none = MagicMock(return_value=user)
    mock_clients_query = MagicMock()
    mock_clients_query.scalars = MagicMock(return_value=MagicMock(all=MagicMock(return_value=[user_client_1, user_client_2])))
    session.execute = AsyncMock(side_effect=[mock_user_query, mock_clients_query])
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    
    # Mock VPNService
    vpn_service = VPNService(
        config=MagicMock(xui=MagicMock(SUBSCRIPTION_PORT=2096, SUBSCRIPTION_PATH="/sub/")),
        session=session_factory,
        server_pool_service=services.server_pool,
    )
    vpn_service._get_key_for_client = AsyncMock(side_effect=[
        "http://example.com/sub/uuid-1",
        "http://example.com/sub/uuid-2",
    ])
    vpn_service._get_vless_key_for_client = AsyncMock(side_effect=[
        "vless://uuid-1@example.com",
        "vless://uuid-2@example.com",
    ])
    
    # Test getting all keys
    with patch('app.db.models.Server') as mock_server:
        mock_server_query = MagicMock()
        mock_server_query.scalars.return_value.all.return_value = [server]
        session.execute = AsyncMock(side_effect=[
            mock_user_query,
            mock_clients_query,
            mock_server_query,
            mock_server_query,
        ])
        
        keys = await vpn_service.get_all_user_keys(user)
        
        # Verify all keys were returned
        assert len(keys) == 2
        assert keys[0]["index"] == 1
        assert keys[1]["index"] == 2
        assert all("key" in key for key in keys)
        assert all("vless_key" in key for key in keys)

