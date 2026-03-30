import base64
import os
import time
import urllib.parse
import httpx

CLIENT_ID = os.getenv("YAHOO_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("YAHOO_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv(
    "YAHOO_REDIRECT_URI",
    "https://fantasy-advisor.onrender.com/auth/callback"
)
AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"


def get_auth_url(state: str = "") -> str:
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def _basic_auth_header() -> str:
    creds = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    return f"Basic {creds}"


async def exchange_code(code: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()


async def refresh_access_token(refresh_token: str) -> dict:
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            TOKEN_URL,
            headers={
                "Authorization": _basic_auth_header(),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()


class TokenStore:
    def __init__(self):
        self.access_token: str | None = None
        self.refresh_token: str | None = os.getenv("YAHOO_REFRESH_TOKEN")
        self.expires_at: float = 0.0

    def set_tokens(self, access_token: str, refresh_token: str, expires_in: int):
        self.access_token = access_token
        self.refresh_token = refresh_token
        self.expires_at = time.time() + expires_in - 60  # 60s safety buffer

    def is_authenticated(self) -> bool:
        return bool(self.refresh_token)

    async def get_access_token(self) -> str:
        if self.access_token and time.time() < self.expires_at:
            return self.access_token
        if not self.refresh_token:
            raise ValueError("Not authenticated. Visit /login.")
        data = await refresh_access_token(self.refresh_token)
        self.set_tokens(
            data["access_token"],
            data.get("refresh_token", self.refresh_token),
            data["expires_in"],
        )
        return self.access_token


token_store = TokenStore()
