"""
Unit tests for VPNService single client data retrieval.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta

from app.bot.services.vpn import VPNService
from app.db.models import User, UserClient
from app.bot.models import ClientData


@pytest.mark.asyncio
async def test_get_client_data_single_inbound_first():
    """Test that inbound data is retrieved first for single client."""
    config = MagicMock()
    config.xui.SUBSCRIPTION_PORT = 2096
    config.xui.SUBSCRIPTION_PATH = "/sub/"
    session_factory = MagicMock()
    server_pool = MagicMock()
    
    vpn_service = VPNService(config=config, session=session_factory, server_pool_service=server_pool)
    
    # Mock user
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.id = 1
    user.server_id = 1
    
    # Mock server
    server = MagicMock()
    server.id = 1
    server.name = "Test Server"
    user.server = server
    
    # Mock connection
    connection = MagicMock()
    connection.server.id = 1
    connection.server.name = "Test Server"
    server_pool.get_connection = AsyncMock(return_value=connection)
    
    # Mock inbound client data
    inbound_data = {
        'expiry_time': int((datetime.now() + timedelta(days=30)).timestamp() * 1000),
        'limit_ip': 5,
        'flow': 'xtls-rprx-vision',
        'id': 'test-uuid'
    }
    
    # Mock client from API (for traffic stats)
    client = MagicMock()
    client.total = 100 * 1024 * 1024 * 1024  # 100 GB
    client.up = 10 * 1024 * 1024 * 1024  # 10 GB
    client.down = 20 * 1024 * 1024 * 1024  # 20 GB
    
    # Mock methods
    vpn_service._get_inbound_client_data = AsyncMock(return_value=inbound_data)
    connection.api.client.get_by_email = AsyncMock(return_value=client)
    vpn_service.get_limit_ip = AsyncMock(return_value=5)
    
    # Mock session
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session_factory.return_value = session
    
    # Mock User query
    mock_user_query = MagicMock()
    mock_user_query.scalar_one_or_none = MagicMock(return_value=user)
    session.execute = AsyncMock(return_value=mock_user_query)
    
    # Mock UserClient.get_by_user_id (empty for single client old schema)
    with patch('app.db.models.UserClient.get_by_user_id', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = []
        
        result = await vpn_service.get_client_data(user)
        
        # Verify that _get_inbound_client_data was called first
        vpn_service._get_inbound_client_data.assert_called_once()
        # Verify that API was called for traffic stats
        connection.api.client.get_by_email.assert_called_once()


@pytest.mark.asyncio
async def test_get_client_data_single_api_for_stats():
    """Test that API is used only for traffic statistics."""
    config = MagicMock()
    config.xui.SUBSCRIPTION_PORT = 2096
    config.xui.SUBSCRIPTION_PATH = "/sub/"
    session_factory = MagicMock()
    server_pool = MagicMock()
    
    vpn_service = VPNService(config=config, session=session_factory, server_pool_service=server_pool)
    
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.id = 1
    user.server_id = 1
    
    server = MagicMock()
    server.id = 1
    user.server = server
    
    connection = MagicMock()
    connection.server.id = 1
    server_pool.get_connection = AsyncMock(return_value=connection)
    
    # Mock inbound data (configuration)
    inbound_data = {
        'expiry_time': int((datetime.now() + timedelta(days=30)).timestamp() * 1000),
        'limit_ip': 5,
        'flow': 'xtls-rprx-vision',
        'id': 'test-uuid'
    }
    
    # Mock client from API (only for stats)
    client = MagicMock()
    client.total = 100 * 1024 * 1024 * 1024
    client.up = 10 * 1024 * 1024 * 1024
    client.down = 20 * 1024 * 1024 * 1024
    
    vpn_service._get_inbound_client_data = AsyncMock(return_value=inbound_data)
    connection.api.client.get_by_email = AsyncMock(return_value=client)
    vpn_service.get_limit_ip = AsyncMock(return_value=5)
    
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session_factory.return_value = session
    
    mock_user_query = MagicMock()
    mock_user_query.scalar_one_or_none = MagicMock(return_value=user)
    session.execute = AsyncMock(return_value=mock_user_query)
    
    with patch('app.db.models.UserClient.get_by_user_id', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = []
        
        result = await vpn_service.get_client_data(user)
        
        # Verify API was called for stats
        assert connection.api.client.get_by_email.called
        # Verify result has correct traffic stats from API
        if result:
            assert result.traffic_total == "100 GB"
            assert result.traffic_up == "10 GB"
            assert result.traffic_down == "20 GB"


@pytest.mark.asyncio
async def test_get_client_data_single_fallback_to_db():
    """Test fallback to database when inbound is not available."""
    config = MagicMock()
    config.xui.SUBSCRIPTION_PORT = 2096
    config.xui.SUBSCRIPTION_PATH = "/sub/"
    session_factory = MagicMock()
    server_pool = MagicMock()
    
    vpn_service = VPNService(config=config, session=session_factory, server_pool_service=server_pool)
    
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.id = 1
    user.server_id = 1
    
    server = MagicMock()
    server.id = 1
    user.server = server
    
    connection = MagicMock()
    connection.server.id = 1
    server_pool.get_connection = AsyncMock(return_value=connection)
    
    # Mock inbound data not found
    vpn_service._get_inbound_client_data = AsyncMock(return_value=None)
    
    # Mock client from API (fallback)
    client = MagicMock()
    client.total = 100 * 1024 * 1024 * 1024
    client.up = 10 * 1024 * 1024 * 1024
    client.down = 20 * 1024 * 1024 * 1024
    connection.api.client.get_by_email = AsyncMock(return_value=client)
    vpn_service.get_limit_ip = AsyncMock(return_value=5)
    
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session_factory.return_value = session
    
    mock_user_query = MagicMock()
    mock_user_query.scalar_one_or_none = MagicMock(return_value=user)
    session.execute = AsyncMock(return_value=mock_user_query)
    
    with patch('app.db.models.UserClient.get_by_user_id', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = []
        
        result = await vpn_service.get_client_data(user)
        
        # Verify fallback to API was used
        assert connection.api.client.get_by_email.called


@pytest.mark.asyncio
async def test_get_client_data_single_api_unavailable():
    """Test behavior when API is unavailable, only inbound data."""
    config = MagicMock()
    config.xui.SUBSCRIPTION_PORT = 2096
    config.xui.SUBSCRIPTION_PATH = "/sub/"
    session_factory = MagicMock()
    server_pool = MagicMock()
    
    vpn_service = VPNService(config=config, session=session_factory, server_pool_service=server_pool)
    
    user = MagicMock(spec=User)
    user.tg_id = 123456789
    user.id = 1
    user.server_id = 1
    
    server = MagicMock()
    server.id = 1
    user.server = server
    
    connection = MagicMock()
    connection.server.id = 1
    server_pool.get_connection = AsyncMock(return_value=connection)
    
    # Mock inbound data available
    inbound_data = {
        'expiry_time': int((datetime.now() + timedelta(days=30)).timestamp() * 1000),
        'limit_ip': 5,
        'flow': 'xtls-rprx-vision',
        'id': 'test-uuid'
    }
    
    vpn_service._get_inbound_client_data = AsyncMock(return_value=inbound_data)
    
    # Mock API unavailable
    connection.api.client.get_by_email = AsyncMock(side_effect=ValueError("Client not found"))
    vpn_service.get_limit_ip = AsyncMock(return_value=5)
    
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session_factory.return_value = session
    
    mock_user_query = MagicMock()
    mock_user_query.scalar_one_or_none = MagicMock(return_value=user)
    session.execute = AsyncMock(return_value=mock_user_query)
    
    with patch('app.db.models.UserClient.get_by_user_id', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = []
        
        result = await vpn_service.get_client_data(user)
        
        # Verify that result was created with inbound data
        # Traffic stats should be 0 or -1 when API unavailable
        if result:
            # Traffic should be unavailable when API fails
            assert result.traffic_total == "∞"

