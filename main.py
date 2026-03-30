import html
import os
import secrets
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from yahoo_auth import token_store, get_auth_url, exchange_code
from yahoo_api import get_roster, LEAGUES, API_BASE

app = FastAPI(title="Fantasy Baseball Advisor")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not token_store.is_authenticated():
        return RedirectResponse("/login")
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/login")
async def login():
    state = secrets.token_urlsafe(16)
    return RedirectResponse(get_auth_url(state))


@app.get("/auth/callback")
async def auth_callback(code: str, state: str = ""):
    try:
        data = await exchange_code(code)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {e}")
    token_store.set_tokens(
        data["access_token"],
        data.get("refresh_token") or token_store.refresh_token or "",
        data.get("expires_in", 3600),
    )
    rt = html.escape(data.get("refresh_token", ""))
    return HTMLResponse(f"""
<!DOCTYPE html><html><body style="font-family:sans-serif;padding:2rem">
<h2>&#10003; Authentication successful!</h2>
<p>Save this as <code>YAHOO_REFRESH_TOKEN</code> in Render environment variables:</p>
<pre style="background:#f4f4f4;padding:1rem;word-break:break-all;border-radius:4px">{rt}</pre>
<ol>
  <li>Render dashboard &rarr; fantasy-advisor &rarr; Environment</li>
  <li>Add: <code>YAHOO_REFRESH_TOKEN</code> = token above</li>
  <li>Save &rarr; service redeploys automatically</li>
</ol>
<p><a href="/">→ Continue to app</a></p>
</body></html>
""")


@app.get("/health")
async def health():
    return {"status": "ok", "authenticated": token_store.is_authenticated()}


@app.get("/api/leagues")
async def list_leagues():
    return [{"id": lid, "name": info["name"]} for lid, info in LEAGUES.items()]


@app.get("/api/debug/yahoo/{league_id}")
async def debug_yahoo(league_id: str):
    """Dump raw Yahoo API response — use this to diagnose parsing failures."""
    league_key = LEAGUES[league_id]["key"]
    access_token = await token_store.get_access_token()
    url = (
        f"{API_BASE}/users;use_login=1/games;game_keys=mlb"
        f"/leagues;league_keys={league_key}/teams/roster/players"
    )
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url,
            params={"format": "json"},
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.json()


@app.get("/api/roster/{league_id}")
async def roster(league_id: str):
    return await get_roster(league_id)
