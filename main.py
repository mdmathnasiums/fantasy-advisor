# main.py
import asyncio
import html
import os
import secrets
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from yahoo_auth import token_store, get_auth_url, exchange_code
from yahoo_api import fetch_raw_roster, get_roster, LEAGUES
from mlb_api import (
    get_probable_pitchers, enrich_pitchers,
    search_player, get_hitter_details,
)
from advisor import HitterInfo, PitcherInfo, advise_roster

app = FastAPI(title="Fantasy Baseball Advisor")
templates = Jinja2Templates(directory="templates")

# --- Auth routes ---

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
<p><a href="/">&#8594; Continue to app</a></p>
</body></html>
""")


# --- API routes ---

@app.get("/health")
async def health():
    return {"status": "ok", "authenticated": token_store.is_authenticated()}


@app.get("/api/leagues")
async def list_leagues():
    return [{"id": lid, "name": info["name"]} for lid, info in LEAGUES.items()]


@app.get("/api/debug/yahoo/{league_id}")
async def debug_yahoo(league_id: str):
    """Dump raw Yahoo API response — use this to diagnose parsing failures."""
    if league_id not in LEAGUES:
        raise HTTPException(status_code=404, detail=f"Unknown league_id: {league_id}")
    return await fetch_raw_roster(league_id)


@app.get("/api/roster/{league_id}")
async def roster(league_id: str):
    if league_id not in LEAGUES:
        raise HTTPException(status_code=404, detail=f"Unknown league_id: {league_id}")
    try:
        return await get_roster(league_id)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))


async def _enrich_hitter(
    p: dict, pitchers: dict[str, dict], season: int
) -> HitterInfo:
    """Look up MLB ID, fetch splits, build HitterInfo. Never raises."""
    mlb_id = await search_player(p["full_name"])
    splits = {"vL": None, "vR": None}
    bats = p.get("bats")
    if mlb_id:
        try:
            details = await get_hitter_details(mlb_id, season)
            splits = {"vL": details.get("vL"), "vR": details.get("vR")}
            bats = details.get("bats") or bats
        except Exception:
            pass

    pitcher_raw = pitchers.get(p["team_abbr"])
    pitcher = None
    if pitcher_raw:
        pitcher = PitcherInfo(
            name=pitcher_raw["name"],
            throws=pitcher_raw.get("throws") or "R",
            era=pitcher_raw.get("era"),
            whip=pitcher_raw.get("whip"),
        )

    return HitterInfo(
        player_id=p["player_id"],
        full_name=p["full_name"],
        team_abbr=p["team_abbr"],
        position=p["position"],
        selected_position=p["selected_position"],
        bats=bats,
        splits=splits,
        pitcher=pitcher,
    )


PITCHER_POSITIONS = {"SP", "RP", "P"}


@app.get("/api/today")
async def today_view():
    today = date.today()
    pitchers = await get_probable_pitchers(today)
    pitchers = await enrich_pitchers(pitchers)

    results = []
    for league_id, league_info in LEAGUES.items():
        try:
            raw_players = await get_roster(league_id)
        except Exception as e:
            results.append({
                "league_id": league_id,
                "league_name": league_info["name"],
                "error": str(e),
            })
            continue

        candidates = [
            p for p in raw_players
            if p["position"] not in PITCHER_POSITIONS
        ]

        hitters = await asyncio.gather(
            *[_enrich_hitter(p, pitchers, today.year) for p in candidates]
        )

        advised = advise_roster(list(hitters))

        results.append({
            "league_id": league_id,
            "league_name": league_info["name"],
            "date": today.isoformat(),
            "players": advised,
            "stats": {
                "active_hitters": sum(1 for p in advised if p["has_game"]),
                "strong_matchups": sum(1 for p in advised if p["matchup_quality"] == "good"),
                "tough_matchups": sum(1 for p in advised if p["matchup_quality"] == "bad"),
                "no_game": sum(1 for p in advised if not p["has_game"]),
            },
        })

    return results
