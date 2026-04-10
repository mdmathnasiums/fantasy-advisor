# main.py
import asyncio
import html
import os
import secrets
from datetime import date

import httpx

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from yahoo_auth import token_store, get_auth_url, exchange_code
from yahoo_api import fetch_raw_roster, get_roster, get_available_players, LEAGUES
from mlb_api import (
    get_probable_pitchers, enrich_pitchers, get_confirmed_lineups,
    search_player, get_hitter_details, get_career_vs_pitcher,
    PARK_FACTORS,
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


@app.get("/api/debug/mlb")
async def debug_mlb(name: str = "Freddie Freeman"):
    """Diagnose MLB API data for a player. Usage: /api/debug/mlb?name=Freddie+Freeman"""
    from mlb_api import _get_stats_direct, MLB_BASE
    season = date.today().year

    mlb_id = await search_player(name)
    if not mlb_id:
        return {"error": f"Player '{name}' not found via search_player", "mlb_id": None}

    try:
        async with httpx.AsyncClient() as client:
            splits_r, season_r, gamelog_r, person_r = await asyncio.gather(
                client.get(f"{MLB_BASE}/people/{mlb_id}/stats",
                           params={"stats": "statSplits", "group": "hitting", "season": season},
                           timeout=10.0),
                client.get(f"{MLB_BASE}/people/{mlb_id}/stats",
                           params={"stats": "season", "group": "hitting", "season": season},
                           timeout=10.0),
                client.get(f"{MLB_BASE}/people/{mlb_id}/stats",
                           params={"stats": "gameLog", "group": "hitting", "season": season},
                           timeout=10.0),
                client.get(f"{MLB_BASE}/people/{mlb_id}", timeout=10.0),
            )
        # Also try prior year splits
        async with httpx.AsyncClient() as client2:
            prior_splits_r = await client2.get(
                f"{MLB_BASE}/people/{mlb_id}/stats",
                params={"stats": "statSplits", "group": "hitting", "season": season - 1},
                timeout=10.0,
            )
        return {
            "mlb_id": mlb_id,
            "current_season": season,
            "person_bats": person_r.json().get("people", [{}])[0].get("batSide", {}).get("code"),
            "splits_status": splits_r.status_code,
            "splits_raw": splits_r.json().get("stats", []),
            "prior_splits_status": prior_splits_r.status_code,
            "prior_splits_raw": prior_splits_r.json().get("stats", []),
            "season_status": season_r.status_code,
            "season_ops": [s.get("stat", {}).get("ops") for sg in season_r.json().get("stats", []) for s in sg.get("splits", [])],
            "gamelog_status": gamelog_r.status_code,
            "gamelog_last_5_dates": [s.get("date") for sg in gamelog_r.json().get("stats", []) for s in sg.get("splits", [])][-5:],
        }
    except Exception as e:
        return {"mlb_id": mlb_id, "error": str(e)}


@app.get("/api/roster/{league_id}")
async def roster(league_id: str):
    if league_id not in LEAGUES:
        raise HTTPException(status_code=404, detail=f"Unknown league_id: {league_id}")
    try:
        return await get_roster(league_id)
    except ValueError as e:
        raise HTTPException(status_code=502, detail=str(e))


async def _enrich_hitter(
    p: dict,
    pitchers: dict[str, dict],
    teams_with_game: set[str],
    game_venue: dict[str, str],
    season: int,
    confirmed_lineups: dict | None = None,
) -> HitterInfo:
    """Look up MLB ID, fetch all hitter/pitcher enrichment in parallel. Never raises."""
    team = p["team_abbr"]
    pitcher_raw = pitchers.get(team)
    pitcher_mlb_id = pitcher_raw.get("mlb_id") if pitcher_raw else None

    mlb_id = await search_player(p["full_name"], team_abbr=p.get("team_abbr"))

    splits = {"vL": None, "vR": None, "home_avg": None, "away_avg": None}
    bats = p.get("bats")
    ops = None
    recent_avg = None
    career_vs_pitcher = None

    if mlb_id:
        try:
            tasks = [get_hitter_details(mlb_id, season)]
            if pitcher_mlb_id:
                tasks.append(get_career_vs_pitcher(mlb_id, pitcher_mlb_id))

            results = await asyncio.gather(*tasks, return_exceptions=True)

            if not isinstance(results[0], Exception):
                details = results[0]
                splits = {
                    "vL": details.get("vL"),
                    "vR": details.get("vR"),
                    "home_avg": details.get("home_avg"),
                    "away_avg": details.get("away_avg"),
                }
                bats = details.get("bats") or bats
                ops = details.get("ops")
                recent_avg = details.get("recent_avg")

            if len(results) > 1 and not isinstance(results[1], Exception):
                career_vs_pitcher = results[1]
        except Exception:
            pass

    on_il = p.get("selected_position", "") in ("IL", "IL+")
    has_game = (team in teams_with_game) and not on_il
    home_team = game_venue.get(team, team)
    is_home = home_team == team
    park_factor = PARK_FACTORS.get(home_team, 1.0)

    # Confirmed lineup status (only meaningful when lineups are posted)
    confirmed_start: bool | None = None
    confirmed_sit: bool | None = None
    if has_game and mlb_id and confirmed_lineups is not None:
        team_lineup = confirmed_lineups.get(team)
        if team_lineup is not None:
            # Lineup posted for this team — we know definitively
            if mlb_id in team_lineup:
                confirmed_start = True
            else:
                confirmed_sit = True
        # else: lineup not posted yet — leave both None

    pitcher = None
    if has_game and pitcher_raw:
        pitcher = PitcherInfo(
            name=pitcher_raw["name"],
            throws=pitcher_raw.get("throws") or "R",
            era=pitcher_raw.get("era"),
            whip=pitcher_raw.get("whip"),
            recent_era=pitcher_raw.get("recent_era"),
            recent_whip=pitcher_raw.get("recent_whip"),
            eff_era=pitcher_raw.get("eff_era"),
            eff_whip=pitcher_raw.get("eff_whip"),
        )
    elif has_game:
        pitcher = PitcherInfo(name="TBD", throws="R", era=None, whip=None)

    return HitterInfo(
        player_id=p["player_id"],
        full_name=p["full_name"],
        team_abbr=team,
        position=p["position"],
        selected_position=p["selected_position"],
        bats=bats,
        splits=splits,
        pitcher=pitcher,
        ops=ops,
        recent_avg=recent_avg,
        career_vs_pitcher=career_vs_pitcher,
        park_factor=park_factor,
        is_home=is_home,
        confirmed_start=confirmed_start,
        confirmed_sit=confirmed_sit,
    )


PITCHER_POSITIONS = {"SP", "RP", "P"}


async def _fetch_league(
    league_id: str,
    query_date,
    pitchers: dict,
    teams_with_game: set,
    game_venue: dict,
    confirmed_lineups: dict,
) -> dict:
    """Fetch + score one league's roster. Returns a single league result dict."""
    league_info = LEAGUES[league_id]
    try:
        raw_players = await get_roster(league_id)
    except Exception as e:
        return {"league_id": league_id, "league_name": league_info["name"], "error": str(e)}

    candidates = [
        p for p in raw_players
        if p["position"] not in PITCHER_POSITIONS
        and not any(ep in PITCHER_POSITIONS for ep in p.get("eligible_positions", []))
    ]

    hitters = await asyncio.gather(
        *[_enrich_hitter(p, pitchers, teams_with_game, game_venue, query_date.year, confirmed_lineups)
          for p in candidates]
    )

    advised = advise_roster(list(hitters))
    return {
        "league_id": league_id,
        "league_name": league_info["name"],
        "date": query_date.isoformat(),
        "players": advised,
        "stats": {
            "active_hitters": sum(1 for p in advised if p["has_game"]),
            "strong_matchups": sum(1 for p in advised if p["matchup_quality"] == "good"),
            "tough_matchups": sum(1 for p in advised if p["matchup_quality"] == "bad"),
            "no_game": sum(1 for p in advised if not p["has_game"]),
        },
    }


async def _get_schedule(query_date) -> tuple[dict, set, dict, dict]:
    """Fetch pitchers + lineups for a date. Returns (pitchers, teams_with_game, game_venue, confirmed_lineups)."""
    try:
        (pitchers, teams_with_game, game_venue), confirmed_lineups = await asyncio.gather(
            get_probable_pitchers(query_date),
            get_confirmed_lineups(query_date),
        )
        pitchers = await enrich_pitchers(pitchers)
        return pitchers, teams_with_game, game_venue, confirmed_lineups
    except Exception as exc:
        print(f"[main] MLB schedule fetch failed: {exc}")
        return {}, set(), {}, {}


@app.get("/api/roster")
async def roster_view(game_date: str | None = None, league_id: str | None = None):
    """Fetch scored roster data.

    - game_date: YYYY-MM-DD (default today)
    - league_id: if provided, returns a single league dict; otherwise returns list of all leagues.
    """
    if game_date:
        try:
            query_date = date.fromisoformat(game_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    else:
        query_date = date.today()

    if league_id and league_id not in LEAGUES:
        raise HTTPException(status_code=404, detail=f"Unknown league_id: {league_id}")

    pitchers, teams_with_game, game_venue, confirmed_lineups = await _get_schedule(query_date)

    if league_id:
        # Single-league mode — half the API calls, used by the UI
        return await _fetch_league(league_id, query_date, pitchers, teams_with_game, game_venue, confirmed_lineups)

    # All-leagues mode (kept for backward compat / legacy /api/today alias)
    results = []
    for lid in LEAGUES:
        results.append(await _fetch_league(lid, query_date, pitchers, teams_with_game, game_venue, confirmed_lineups))
    return results


# Legacy alias so any bookmarked /api/today links still work
@app.get("/api/today")
async def today_view():
    return await roster_view()


@app.get("/api/waivers")
async def waivers_view(league_id: str, game_date: str | None = None):
    if league_id not in LEAGUES:
        raise HTTPException(status_code=404, detail=f"Unknown league_id: {league_id}")
    if game_date:
        try:
            query_date = date.fromisoformat(game_date)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date format. Use YYYY-MM-DD.")
    else:
        query_date = date.today()

    # Fetch schedule data and available players in parallel
    try:
        (pitchers, teams_with_game, game_venue), confirmed_lineups, raw_players = await asyncio.gather(
            get_probable_pitchers(query_date),
            get_confirmed_lineups(query_date),
            get_available_players(league_id),
        )
        pitchers = await enrich_pitchers(pitchers)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Data fetch failed: {exc}")

    candidates = [
        p for p in raw_players
        if p["position"] not in PITCHER_POSITIONS
        and not any(ep in PITCHER_POSITIONS for ep in p.get("eligible_positions", []))
    ]

    hitters = await asyncio.gather(
        *[_enrich_hitter(p, pitchers, teams_with_game, game_venue, query_date.year, confirmed_lineups)
          for p in candidates]
    )

    advised = advise_roster(list(hitters))
    # Return only players with a game today, sorted by score descending, top 10
    with_game = [p for p in advised if p["has_game"]]
    with_game.sort(key=lambda x: -x["score"])
    return {
        "league_id": league_id,
        "league_name": LEAGUES[league_id]["name"],
        "date": query_date.isoformat(),
        "players": with_game[:10],
    }
