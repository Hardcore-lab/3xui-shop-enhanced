"""
Integration tests for multi-device subscriptions.
"""
from dataclasses import fields

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.models import ServicesContainer
from app.bot.services.vpn import VPNService
from app.db.models import User, UserClient


@pytest.mark.asyncio
async def test_get_all_user_keys_multiple_devices():
    """Test retrieving all keys for user with multiple devices."""
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
    session.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=user),
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[user_client_1, user_client_2])))
    ))
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
        users_and_clients = session.execute.return_value
        session.execute = AsyncMock(side_effect=[users_and_clients, users_and_clients, mock_server_query])
        
        keys = await vpn_service.get_all_user_keys(user)
        
        # Verify all keys were retrieved
        assert len(keys) == 2
        assert keys[0]["index"] == 1
        assert keys[1]["index"] == 2
        assert keys[0]["key"] == "http://example.com/sub/uuid-1"
        assert keys[1]["key"] == "http://example.com/sub/uuid-2"


@pytest.mark.asyncio
async def test_create_multiple_clients(monkeypatch):
    """Test creating multiple clients for a user."""
    # Mock user
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.server_id = 1
    user.id = 1
    
    # Mock server
    server = MagicMock()
    server.id = 1
    server.host = "http://example.com"
    
    # Mock services
    services = MagicMock(spec=[field.name for field in fields(ServicesContainer)])
    connection = MagicMock()
    connection.server = server
    connection.api.client.add = AsyncMock()
    connection.api.inbound.get_list = AsyncMock(return_value=[
        MagicMock(id=1, settings=MagicMock(clients=[]))
    ])
    services.server_pool.get_connection = AsyncMock(return_value=connection)
    services.server_pool.get_inbound_id = AsyncMock(return_value=1)
    services.server_pool.assign_specific_server_to_user = AsyncMock(return_value=True)
    
    # Mock session
    session_factory = MagicMock()
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=user)
    ))
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session_factory.return_value.__aenter__ = AsyncMock(return_value=session)
    session_factory.return_value.__aexit__ = AsyncMock(return_value=None)
    
    # Mock VPNService
    vpn_service = VPNService(
        config=MagicMock(xui=MagicMock(SUBSCRIPTION_PORT=2096, SUBSCRIPTION_PATH="/sub/")),
        session=session_factory,
        server_pool_service=services.server_pool,
    )
    
    from contextlib import asynccontextmanager

    redis_client = MagicMock(close=AsyncMock())
    monkeypatch.setattr("redis.asyncio.from_url", MagicMock(return_value=redis_client))

    @asynccontextmanager
    async def acquire_lock(*args, **kwargs):
        yield

    monkeypatch.setattr("app.bot.utils.redis_lock.redis_lock", acquire_lock)
    monkeypatch.setattr(UserClient, "get_by_user_id", AsyncMock(return_value=[]))

    # Test creating multiple clients
    result = await vpn_service.create_multiple_clients(user, devices=3, duration=30, server_id=1)
    
    # Verify clients were created
    assert result is True
    # Verify client.add was called (should be called once with list of clients)
    connection.api.client.add.assert_called_once()

