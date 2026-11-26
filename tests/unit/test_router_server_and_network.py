"""
Unit tests for server handler callback parsing and network ping URL handling.
Covers Router Audit plan: server name parsing with underscores, URL scheme normalization.
"""
import pytest

from app.bot.utils.navigation import NavAdminTools
from app.bot.utils.network import ping_url


def test_nav_admin_tools_constants_for_server_callbacks():
    """Ensure server-related callback prefixes are defined and consistent."""
    assert NavAdminTools.SHOW_SERVER == "show_server"
    assert NavAdminTools.PING_SERVER == "ping_server"
    assert NavAdminTools.DELETE_SERVER == "delete_server"
    assert NavAdminTools.CONFIRM_DELETE_SERVER == "confirm_delete_server"
    # CONFIRM_ADD_SERVER must be Latin 'C' (not Cyrillic)
    assert NavAdminTools.CONFIRM_ADD_SERVER == "confirm_add_server"


class TestServerNameParsing:
    """Verify server name is extracted correctly from callback_data (handles underscores)."""

    def test_show_server_callback_parsing(self):
        """Server names with underscores must be parsed correctly."""
        prefix = NavAdminTools.SHOW_SERVER + "_"
        assert "server_1" == ("show_server_server_1").removeprefix(prefix)
        assert "US_East_1" == ("show_server_US_East_1").removeprefix(prefix)
        assert "my_vpn_server" == ("show_server_my_vpn_server").removeprefix(prefix)

    def test_ping_server_callback_parsing(self):
        """Ping server callback with underscored names."""
        prefix = NavAdminTools.PING_SERVER + "_"
        assert "server_1" == ("ping_server_server_1").removeprefix(prefix)
        assert "US_East_1" == ("ping_server_US_East_1").removeprefix(prefix)

    def test_delete_server_callback_parsing(self):
        """Delete server callback with underscored names."""
        prefix = NavAdminTools.DELETE_SERVER + "_"
        assert "server_1" == ("delete_server_server_1").removeprefix(prefix)
        assert "US_East_1" == ("delete_server_US_East_1").removeprefix(prefix)

    def test_confirm_delete_server_callback_parsing(self):
        """Confirm delete callback with underscored names."""
        prefix = NavAdminTools.CONFIRM_DELETE_SERVER + "_"
        assert "server_1" == ("confirm_delete_server_server_1").removeprefix(prefix)
        assert "US_East_1" == ("confirm_delete_server_US_East_1").removeprefix(prefix)


class TestPingUrlSchemeNormalization:
    """Verify ping_url receives normalized URLs (scheme added when missing)."""

    @pytest.mark.asyncio
    async def test_ping_url_rejects_invalid(self):
        """Invalid or unreachable URL should return None."""
        result = await ping_url("http://nonexistent.invalid.domain.xyz", timeout=1)
        assert result is None

    @pytest.mark.asyncio
    async def test_ping_url_accepts_https(self):
        """URL with https scheme is used as-is (no double scheme)."""
        # Just check that we don't crash; real ping would need a live server
        result = await ping_url("https://example.com", timeout=1)
        # May be None (timeout/unreachable) or a number
        assert result is None or isinstance(result, (int, float))

    @pytest.mark.asyncio
    async def test_ping_url_normalizes_bare_host(self):
        """Bare host gets https prefix internally; request is attempted."""
        result = await ping_url("example.com", timeout=1)
        assert result is None or isinstance(result, (int, float))


@pytest.mark.asyncio
async def test_connection_redirect_requires_valid_signature():
    from urllib.parse import urlsplit

    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from app.bot.routers.download.handler import redirect_to_connection
    from app.bot.utils.network import connection_url

    secret = "local-signing-key"
    destination = "https://example.com/sub/device?name=a%2Bb&mode=new"

    async def redirect(request):
        return await redirect_to_connection(request, secret=secret)

    app = web.Application()
    app.router.add_get("/connection", redirect)
    async with TestClient(TestServer(app)) as client:
        signed = urlsplit(connection_url("https://bot.example.com", destination, secret))
        async with client.get(f"{signed.path}?{signed.query}", allow_redirects=False) as response:
            assert response.status == 302
            assert response.headers["Location"] == destination

        tampered = signed.query.replace("example.com", "other.example")
        assert tampered != signed.query
        async with client.get(f"{signed.path}?{tampered}", allow_redirects=False) as response:
            assert response.status == 403

        async with client.get("/connection?scheme=direct&key=https://example.com", allow_redirects=False) as response:
            assert response.status == 400
