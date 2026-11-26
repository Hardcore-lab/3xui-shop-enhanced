"""
Unit tests for validation utilities.
"""
import pytest

from app.bot.utils.validation import (
    is_valid_search_query,
    is_valid_days,
    is_valid_page,
    is_valid_limit,
    sanitize_search_query,
)


class TestIsValidSearchQuery:
    """Tests for is_valid_search_query function."""
    
    def test_valid_query(self):
        """Test valid search query."""
        assert is_valid_search_query("test") is True
        assert is_valid_search_query("user123") is True
        assert is_valid_search_query("a" * 50) is True
    
    def test_too_short_query(self):
        """Test query that is too short."""
        assert is_valid_search_query("ab") is False
        assert is_valid_search_query("a") is False
        assert is_valid_search_query("") is False
    
    def test_too_long_query(self):
        """Test query that is too long."""
        assert is_valid_search_query("a" * 101) is False
    
    def test_empty_query(self):
        """Test empty query."""
        assert is_valid_search_query("") is False
        assert is_valid_search_query("   ") is False


class TestIsValidDays:
    """Tests for is_valid_days function."""
    
    def test_valid_days(self):
        """Test valid number of days."""
        assert is_valid_days("1") is True
        assert is_valid_days("30") is True
        assert is_valid_days("365") is True
    
    def test_invalid_days(self):
        """Test invalid number of days."""
        assert is_valid_days("0") is False
        assert is_valid_days("366") is False
        assert is_valid_days("-1") is False
        assert is_valid_days("abc") is False
        assert is_valid_days("") is False


class TestIsValidPage:
    """Tests for is_valid_page function."""
    
    def test_valid_page(self):
        """Test valid page number."""
        assert is_valid_page("0") is True
        assert is_valid_page("1") is True
        assert is_valid_page("100") is True
    
    def test_invalid_page(self):
        """Test invalid page number."""
        assert is_valid_page("-1") is False
        assert is_valid_page("abc") is False
        assert is_valid_page("") is False


class TestIsValidLimit:
    """Tests for is_valid_limit function."""
    
    def test_valid_limit(self):
        """Test valid limit."""
        assert is_valid_limit("1") is True
        assert is_valid_limit("10") is True
        assert is_valid_limit("1000") is True
    
    def test_invalid_limit(self):
        """Test invalid limit."""
        assert is_valid_limit("0") is False
        assert is_valid_limit("1001") is False
        assert is_valid_limit("-1") is False
        assert is_valid_limit("abc") is False
        assert is_valid_limit("") is False


class TestSanitizeSearchQuery:
    """Tests for sanitize_search_query function."""
    
    def test_sanitize_dangerous_chars(self):
        """Test sanitization of dangerous characters."""
        query = "test'; DROP TABLE users;--"
        result = sanitize_search_query(query)
        
        assert "'" not in result
        assert ";" not in result
        assert "--" not in result
        assert "DROP" in result  # Should keep the word
    
    def test_sanitize_length(self):
        """Test sanitization of query length."""
        query = "a" * 150
        result = sanitize_search_query(query)
        
        assert len(result) <= 100
    
    def test_sanitize_strip(self):
        """Test sanitization strips whitespace."""
        query = "  test  "
        result = sanitize_search_query(query)
        
        assert result == "test"
    
    def test_sanitize_sql_injection_patterns(self):
        """Test sanitization of SQL injection patterns."""
        patterns = ["'", '"', ";", "--", "/*", "*/", "xp_", "sp_"]
        
        for pattern in patterns:
            query = f"test{pattern}query"
            result = sanitize_search_query(query)
            assert pattern not in result

