from types import SimpleNamespace

"""
Unit tests for VPNService _get_inbound_client_data method.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from app.bot.services.vpn import VPNService


@pytest.mark.asyncio
async def test_get_inbound_client_data_success():
    """Test successful retrieval of client data from inbound."""
    config = MagicMock()
    config.xui.SUBSCRIPTION_PORT = 2096
    config.xui.SUBSCRIPTION_PATH = "/sub/"
    session_factory = MagicMock()
    server_pool = MagicMock()
    
    vpn_service = VPNService(config=config, session=session_factory, server_pool_service=server_pool)
    
    # Mock connection
    connection = MagicMock()
    
    # Mock inbound client
    inbound_client = MagicMock()
    inbound_client.email = "test@example.com"
    inbound_client.expiryTime = 1735420800000  # 2025-12-28 in milliseconds
    inbound_client.limitIp = 5
    inbound_client.flow = "xtls-rprx-vision"
    inbound_client.id = "test-uuid"
    
    # Mock inbound settings
    inbound_settings = MagicMock()
    inbound_settings.clients = [inbound_client]
    
    # Mock inbound
    inbound = MagicMock()
    inbound.settings = inbound_settings
    
    # Mock inbounds list
    inbounds = [inbound]
    
    # Mock API
    connection.api.inbound.get_list = AsyncMock(return_value=inbounds)
    
    result = await vpn_service._get_inbound_client_data(connection, "test@example.com")
    
    assert result is not None
    assert result['expiry_time'] == 1735420800000
    assert result['limit_ip'] == 5
    assert result['flow'] == "xtls-rprx-vision"
    assert result['id'] == "test-uuid"


@pytest.mark.asyncio
async def test_get_inbound_client_data_not_found():
    """Test when client is not found in inbound."""
    config = MagicMock()
    config.xui.SUBSCRIPTION_PORT = 2096
    config.xui.SUBSCRIPTION_PATH = "/sub/"
    session_factory = MagicMock()
    server_pool = MagicMock()
    
    vpn_service = VPNService(config=config, session=session_factory, server_pool_service=server_pool)
    
    connection = MagicMock()
    
    # Mock inbound with different client
    inbound_client = MagicMock()
    inbound_client.email = "other@example.com"
    
    inbound_settings = MagicMock()
    inbound_settings.clients = [inbound_client]
    
    inbound = MagicMock()
    inbound.settings = inbound_settings
    
    connection.api.inbound.get_list = AsyncMock(return_value=[inbound])
    
    result = await vpn_service._get_inbound_client_data(connection, "test@example.com")
    
    assert result is None


@pytest.mark.asyncio
async def test_get_inbound_client_data_with_cache():
    """Test using cached inbound list."""
    config = MagicMock()
    config.xui.SUBSCRIPTION_PORT = 2096
    config.xui.SUBSCRIPTION_PATH = "/sub/"
    session_factory = MagicMock()
    server_pool = MagicMock()
    
    vpn_service = VPNService(config=config, session=session_factory, server_pool_service=server_pool)
    
    connection = MagicMock()
    
    # Mock inbound client
    inbound_client = MagicMock()
    inbound_client.email = "test@example.com"
    inbound_client.expiryTime = 1735420800000
    inbound_client.limitIp = 5
    inbound_client.flow = "xtls-rprx-vision"
    inbound_client.id = "test-uuid"
    
    inbound_settings = MagicMock()
    inbound_settings.clients = [inbound_client]
    
    inbound = MagicMock()
    inbound.settings = inbound_settings
    
    inbounds_cache = [inbound]
    
    # Should not call get_list when cache is provided
    connection.api.inbound.get_list = AsyncMock()
    
    result = await vpn_service._get_inbound_client_data(connection, "test@example.com", inbounds_cache)
    
    assert result is not None
    assert result['expiry_time'] == 1735420800000
    # Verify get_list was not called
    connection.api.inbound.get_list.assert_not_called()


@pytest.mark.asyncio
async def test_get_inbound_client_data_api_error():
    """Test handling of API errors."""
    config = MagicMock()
    config.xui.SUBSCRIPTION_PORT = 2096
    config.xui.SUBSCRIPTION_PATH = "/sub/"
    session_factory = MagicMock()
    server_pool = MagicMock()
    
    vpn_service = VPNService(config=config, session=session_factory, server_pool_service=server_pool)
    
    connection = MagicMock()
    connection.api.inbound.get_list = AsyncMock(side_effect=Exception("API Error"))
    
    result = await vpn_service._get_inbound_client_data(connection, "test@example.com")
    
    assert result is None


@pytest.mark.asyncio
async def test_get_inbound_client_data_expiry_time_variants():
    """Test different expiry_time attribute variants."""
    config = MagicMock()
    config.xui.SUBSCRIPTION_PORT = 2096
    config.xui.SUBSCRIPTION_PATH = "/sub/"
    session_factory = MagicMock()
    server_pool = MagicMock()
    
    vpn_service = VPNService(config=config, session=session_factory, server_pool_service=server_pool)
    
    connection = MagicMock()
    
    # Test with expiryTime (camelCase)
    inbound_client_1 = SimpleNamespace()
    inbound_client_1.email = "test1@example.com"
    inbound_client_1.expiryTime = 1735420800000
    inbound_client_1.limitIp = 5
    inbound_client_1.flow = "xtls-rprx-vision"
    inbound_client_1.id = "test-uuid-1"
    
    # Test with expiry_time (snake_case)
    inbound_client_2 = SimpleNamespace()
    inbound_client_2.email = "test2@example.com"
    inbound_client_2.expiry_time = 1735420900000
    inbound_client_2.limitIp = 5
    inbound_client_2.flow = "xtls-rprx-vision"
    inbound_client_2.id = "test-uuid-2"
    
    # Test with expiry_time = 0 (should return -1)
    inbound_client_3 = SimpleNamespace()
    inbound_client_3.email = "test3@example.com"
    inbound_client_3.expiryTime = 0
    inbound_client_3.limitIp = 5
    inbound_client_3.flow = "xtls-rprx-vision"
    inbound_client_3.id = "test-uuid-3"
    
    inbound_settings = MagicMock()
    inbound_settings.clients = [inbound_client_1, inbound_client_2, inbound_client_3]
    
    inbound = MagicMock()
    inbound.settings = inbound_settings
    
    connection.api.inbound.get_list = AsyncMock(return_value=[inbound])
    
    result1 = await vpn_service._get_inbound_client_data(connection, "test1@example.com")
    assert result1 is not None
    assert result1['expiry_time'] == 1735420800000
    
    result2 = await vpn_service._get_inbound_client_data(connection, "test2@example.com")
    assert result2 is not None
    assert result2['expiry_time'] == 1735420900000
    
    result3 = await vpn_service._get_inbound_client_data(connection, "test3@example.com")
    assert result3 is not None
    assert result3['expiry_time'] == -1  # 0 should be converted to -1

