"""
Unit tests for formatting utilities.
"""
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

from app.bot.utils.formatting import (
    format_subscription_status,
    format_server_status,
    format_payment_status,
)


class TestFormatSubscriptionStatus:
    """Tests for format_subscription_status function."""
    
    def test_expired_subscription(self):
        """Test formatting for expired subscription."""
        user = Mock()
        user.server_id = None
        
        result = format_subscription_status(user)
        
        assert "🔴" in result
        assert "expired" in result.lower() or "истек" in result.lower()
    
    def test_active_unlimited_subscription(self):
        """Test formatting for active unlimited subscription."""
        user = Mock()
        user.server_id = 1
        
        result = format_subscription_status(user, expiry_timestamp=-1)
        
        assert "🟢" in result
        assert "active" in result.lower() or "актив" in result.lower()
    
    def test_active_subscription_many_days(self):
        """Test formatting for active subscription with many days left."""
        user = Mock()
        user.server_id = 1
        
        # 30 days from now
        expiry = int((datetime.now(timezone.utc) + timedelta(days=30)).timestamp() * 1000)
        
        result = format_subscription_status(user, expiry_timestamp=expiry)
        
        assert "🟢" in result
        assert "▰" in result  # Progress bar
    
    def test_expiring_soon_subscription(self):
        """Test formatting for subscription expiring soon."""
        user = Mock()
        user.server_id = 1
        
        # 5 days from now
        expiry = int((datetime.now(timezone.utc) + timedelta(days=5)).timestamp() * 1000)
        
        result = format_subscription_status(user, expiry_timestamp=expiry)
        
        assert "🟡" in result or "🟠" in result
    
    def test_expired_timestamp(self):
        """Test formatting for expired timestamp."""
        user = Mock()
        user.server_id = 1
        
        # 5 days ago
        expiry = int((datetime.now(timezone.utc) - timedelta(days=5)).timestamp() * 1000)
        
        result = format_subscription_status(user, expiry_timestamp=expiry)
        
        assert "🔴" in result


class TestFormatServerStatus:
    """Tests for format_server_status function."""
    
    def test_online_server(self):
        """Test formatting for online server."""
        server = Mock()
        server.online = True
        server.max_clients = 100
        
        result = format_server_status(server, current_clients=50)
        
        assert "🟢" in result
        assert "online" in result.lower() or "онлайн" in result.lower()
        assert "50/100" in result
        assert "50%" in result
    
    def test_offline_server(self):
        """Test formatting for offline server."""
        server = Mock()
        server.online = False
        server.max_clients = 100
        
        result = format_server_status(server, current_clients=0)
        
        assert "🔴" in result
        assert "offline" in result.lower() or "оффлайн" in result.lower()
    
    def test_high_load_server(self):
        """Test formatting for server with high load."""
        server = Mock()
        server.online = True
        server.max_clients = 100
        
        result = format_server_status(server, current_clients=90)
        
        assert "🔴" in result  # High load indicator
        assert "90/100" in result
        assert "90%" in result
    
    def test_medium_load_server(self):
        """Test formatting for server with medium load."""
        server = Mock()
        server.online = True
        server.max_clients = 100
        
        result = format_server_status(server, current_clients=60)
        
        assert "🟡" in result  # Medium load indicator
        assert "60/100" in result


class TestFormatPaymentStatus:
    """Tests for format_payment_status function."""
    
    def test_pending_status(self):
        """Test formatting for pending payment status."""
        result = format_payment_status("pending")
        
        assert "⏳" in result
        assert "pending" in result.lower() or "ожида" in result.lower()
    
    def test_completed_status(self):
        """Test formatting for completed payment status."""
        result = format_payment_status("completed")
        
        assert "✅" in result
        assert "completed" in result.lower() or "заверш" in result.lower()
    
    def test_canceled_status(self):
        """Test formatting for canceled payment status."""
        result = format_payment_status("canceled")
        
        assert "❌" in result
        assert "canceled" in result.lower() or "отмен" in result.lower()
    
    def test_refunded_status(self):
        """Test formatting for refunded payment status."""
        result = format_payment_status("refunded")
        
        assert "↩️" in result
        assert "refunded" in result.lower() or "возврат" in result.lower()
    
    def test_unknown_status(self):
        """Test formatting for unknown payment status."""
        result = format_payment_status("unknown_status")
        
        assert "❓" in result
        assert "unknown" in result.lower() or "неизвест" in result.lower()

