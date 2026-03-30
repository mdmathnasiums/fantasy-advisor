import pytest
import time
from unittest.mock import AsyncMock, patch
from yahoo_auth import TokenStore


def test_token_store_is_authenticated_when_refresh_token_set():
    ts = TokenStore()
    ts.refresh_token = "fake_refresh_token"
    assert ts.is_authenticated() is True


def test_token_store_not_authenticated_when_no_refresh_token():
    ts = TokenStore()
    ts.refresh_token = None
    assert ts.is_authenticated() is False


def test_token_store_set_tokens():
    ts = TokenStore()
    ts.set_tokens("access123", "refresh456", 3600)
    assert ts.access_token == "access123"
    assert ts.refresh_token == "refresh456"
    assert ts.expires_at > time.time()


@pytest.mark.asyncio
async def test_token_store_returns_cached_access_token():
    ts = TokenStore()
    ts.access_token = "cached_token"
    ts.expires_at = time.time() + 3600
    token = await ts.get_access_token()
    assert token == "cached_token"


@pytest.mark.asyncio
async def test_token_store_raises_when_not_authenticated():
    ts = TokenStore()
    ts.refresh_token = None
    with pytest.raises(ValueError, match="Not authenticated"):
        await ts.get_access_token()


@pytest.mark.asyncio
async def test_token_store_refreshes_when_expired():
    ts = TokenStore()
    ts.refresh_token = "old_refresh"
    ts.access_token = "old_access"
    ts.expires_at = time.time() - 1  # expired

    mock_response = {
        "access_token": "new_access",
        "refresh_token": "new_refresh",
        "expires_in": 3600,
    }
    with patch("yahoo_auth.refresh_access_token", new=AsyncMock(return_value=mock_response)):
        token = await ts.get_access_token()

    assert token == "new_access"
    assert ts.refresh_token == "new_refresh"


def test_get_auth_url_contains_client_id():
    import yahoo_auth
    from yahoo_auth import get_auth_url
    original = yahoo_auth.CLIENT_ID
    try:
        yahoo_auth.CLIENT_ID = "test_client_id"
        url = get_auth_url("mystate")
        assert "test_client_id" in url
        assert "mystate" in url
        assert "response_type=code" in url
    finally:
        yahoo_auth.CLIENT_ID = original
