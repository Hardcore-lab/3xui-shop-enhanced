"""
Integration tests for subscription deletion flow.
"""
from dataclasses import fields

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.models import ServicesContainer
from app.bot.services.user_management import UserManagementService
from app.db.models import User, UserClient


@pytest.mark.asyncio
async def test_delete_subscription_single_client():
    """Test deletion of subscription with single client."""
    # Mock user
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.server_id = 1
    user.id = 1
    
    # Mock services
    services = MagicMock(spec=[field.name for field in fields(ServicesContainer)])
    connection = MagicMock()
    connection.api.client.get_by_email = AsyncMock(return_value=MagicMock(id="client-id"))
    connection.api.client.delete = AsyncMock()
    connection.api.inbound.get_list = AsyncMock(return_value=[
        MagicMock(settings=MagicMock(clients=[]))  # Empty after deletion
    ])
    services.server_pool.get_connection = AsyncMock(return_value=connection)
    services.server_pool.get_inbound_id = AsyncMock(return_value=1)
    
    # Mock session
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
    session.begin = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=None)))
    session.commit = AsyncMock()
    
    # Mock UserManagementService
    user_service = UserManagementService(session)
    user_service.get_user_details = AsyncMock(return_value=user)
    user_service._verify_client_deletion = AsyncMock(return_value=True)
    
    # Test deletion
    result = await user_service.delete_subscription(user.tg_id, services)
    
    # Verify deletion was successful
    assert result is True
    connection.api.client.delete.assert_called_once()


@pytest.mark.asyncio
async def test_delete_subscription_multiple_clients():
    """Test deletion of subscription with multiple clients."""
    # Mock user
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.server_id = 1
    user.id = 1
    
    # Mock UserClient records
    user_client_1 = MagicMock(spec=UserClient)
    user_client_1.client_email = "123456789_1"
    user_client_1.id = 1
    
    user_client_2 = MagicMock(spec=UserClient)
    user_client_2.client_email = "123456789_2"
    user_client_2.id = 2
    
    # Mock services
    services = MagicMock(spec=[field.name for field in fields(ServicesContainer)])
    connection = MagicMock()
    connection.api.client.get_by_email = AsyncMock(return_value=MagicMock(id="client-id"))
    connection.api.client.delete = AsyncMock()
    connection.api.inbound.get_list = AsyncMock(return_value=[
        MagicMock(settings=MagicMock(clients=[
            MagicMock(email=user_client_1.client_email, id="client-1"),
            MagicMock(email=user_client_2.client_email, id="client-2"),
        ]))
    ])
    services.server_pool.get_connection = AsyncMock(return_value=connection)
    services.server_pool.get_inbound_id = AsyncMock(return_value=1)
    
    # Mock session
    session = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.all = MagicMock(return_value=[user_client_1, user_client_2])
    mock_result = MagicMock(scalars=MagicMock(return_value=mock_scalars))
    session.execute = AsyncMock(return_value=mock_result)
    session.begin = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=None)))
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    
    # Mock UserManagementService
    user_service = UserManagementService(session)
    user_service.get_user_details = AsyncMock(return_value=user)
    user_service._verify_client_deletion = AsyncMock(return_value=True)
    
    # Test deletion
    result = await user_service.delete_subscription(user.tg_id, services)
    
    # Verify deletion was successful
    assert result is True
    # Verify all clients were deleted
    connection.api.client.delete.assert_awaited_once_with(1, ["client-1", "client-2"])
    # Verify UserClient records were deleted
    assert session.delete.call_count == 2


@pytest.mark.asyncio
async def test_delete_subscription_retry_logic():
    """Test deletion retry logic when first attempt fails."""
    # Mock user
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.server_id = 1
    user.id = 1
    
    # Mock services
    services = MagicMock(spec=[field.name for field in fields(ServicesContainer)])
    connection = MagicMock()
    connection.api.client.get_by_email = AsyncMock(return_value=MagicMock(id="client-id"))
    
    # First call fails, second succeeds
    call_count = 0
    async def mock_delete(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("Temporary failure")
    
    connection.api.client.delete = AsyncMock(side_effect=mock_delete)
    connection.api.inbound.get_list = AsyncMock(return_value=[
        MagicMock(settings=MagicMock(clients=[]))  # Empty after deletion
    ])
    services.server_pool.get_connection = AsyncMock(return_value=connection)
    services.server_pool.get_inbound_id = AsyncMock(return_value=1)
    
    # Mock session
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))))
    session.begin = MagicMock(return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock(return_value=None)))
    session.commit = AsyncMock()
    
    # Mock UserManagementService
    user_service = UserManagementService(session)
    user_service.get_user_details = AsyncMock(return_value=user)
    user_service._verify_client_deletion = AsyncMock(return_value=True)
    user_service.get_client_ids = AsyncMock(return_value=["client_id_1"])
    
    # Test deletion with retry
    result = await user_service.delete_subscription(user.tg_id, services, max_retries=3)
    
    # Verify retry was attempted
    assert result is True
    assert connection.api.client.delete.await_count == 2

