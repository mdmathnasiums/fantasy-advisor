# Fantasy Baseball Start/Sit Advisor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a hosted FastAPI web app that pulls rosters from two Yahoo Fantasy Baseball leagues, enriches hitters with MLB split data and probable pitcher matchups, and renders start/sit recommendations in an interactive UI.

**Architecture:** Single FastAPI app with server-side Yahoo OAuth (refresh token stored as Render env var), two Yahoo league rosters enriched via MLB Stats API, advisor scoring logic, and a Jinja2-served single-page UI calling JSON endpoints.

**Tech Stack:** Python 3.11+, FastAPI, httpx (async HTTP), Jinja2, pytest + pytest-asyncio, deployed on Render free tier via render.yaml.

---

## File Map

| File | Responsibility |
|------|---------------|
| `main.py` | FastAPI app, all routes, startup event |
| `yahoo_auth.py` | OAuth URL generation, code exchange, token refresh, `TokenStore` singleton |
| `yahoo_api.py` | Yahoo roster fetch, nested-array parser, `LEAGUES` config |
| `mlb_api.py` | Probable pitchers, pitcher details, player search, hitter splits |
| `advisor.py` | `HitterInfo`/`PitcherInfo` dataclasses, scoring, recommendation |
| `templates/index.html` | Single-page UI — tabs, cards, table, filters |
| `requirements.txt` | Python dependencies |
| `render.yaml` | Render deploy config |
| `tests/test_yahoo_api.py` | Unit tests for Yahoo parsing |
| `tests/test_mlb_api.py` | Unit tests for MLB API parsing |
| `tests/test_advisor.py` | Unit tests for scoring logic |

---

## Task 1: Bootstrap — Project Structure + Render Hello World

**Files:**
- Create: `main.py`
- Create: `requirements.txt`
- Create: `render.yaml`
- Create: `.gitignore`
- Create: `templates/index.html` (placeholder)
- Create: `tests/__init__.py`

- [ ] **Step 1: Write requirements.txt**

```
fastapi==0.115.0
uvicorn[standard]==0.32.0
httpx==0.27.2
python-dotenv==1.0.1
jinja2==3.1.4
pytest==8.3.3
pytest-asyncio==0.24.0
anyio==4.6.2
```

- [ ] **Step 2: Write render.yaml**

```yaml
services:
  - type: web
    name: fantasy-advisor
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: YAHOO_CLIENT_ID
        sync: false
      - key: YAHOO_CLIENT_SECRET
        sync: false
      - key: YAHOO_REDIRECT_URI
        sync: false
      - key: YAHOO_REFRESH_TOKEN
        sync: false
```

- [ ] **Step 3: Write .gitignore**

```
__pycache__/
*.pyc
.env
.venv/
venv/
*.egg-info/
.pytest_cache/
```

- [ ] **Step 4: Write main.py hello world**

```python
import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="Fantasy Baseball Advisor")
templates = Jinja2Templates(directory="templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/health")
async def health():
    return {"status": "ok"}
```

- [ ] **Step 5: Write templates/index.html placeholder**

```html
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>Fantasy Baseball Advisor</title></head>
<body>
  <h1>Fantasy Baseball Advisor</h1>
  <p>Loading...</p>
</body>
</html>
```

- [ ] **Step 6: Write tests/__init__.py**

```python
```
(empty file)

- [ ] **Step 7: Create GitHub repo and push**

```bash
cd /Users/mattdiamond/fantasy-advisor
git init
git add .
git commit -m "feat: bootstrap FastAPI project"
gh repo create fantasy-advisor --public --source=. --remote=origin --push
```

Expected: GitHub URL printed, e.g. `https://github.com/YOUR_USERNAME/fantasy-advisor`

- [ ] **Step 8: Connect repo to Render**

1. Go to https://dashboard.render.com → New → Web Service
2. Connect your GitHub account if not already connected
3. Select the `fantasy-advisor` repo
4. Render will detect `render.yaml` automatically — click **Apply**
5. Wait for deploy (2-3 minutes)

- [ ] **Step 9: Verify Render deploy**

Visit `https://fantasy-advisor.onrender.com/health`

Expected response: `{"status": "ok"}`

- [ ] **Step 10: Commit**

```bash
git add .
git commit -m "feat: scaffold project with Render deploy config"
git push
```

---

## Task 2: Yahoo OAuth Web Flow

**Files:**
- Create: `yahoo_auth.py`
- Modify: `main.py` — add `/login`, `/auth/callback` routes

- [ ] **Step 1: Write yahoo_auth.py**

```python
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
```

- [ ] **Step 2: Add OAuth routes to main.py**

Replace the full contents of `main.py` with:

```python
import os
import secrets
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from yahoo_auth import token_store, get_auth_url, exchange_code

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
        data.get("refresh_token", ""),
        data.get("expires_in", 3600),
    )
    rt = data.get("refresh_token", "")
    return HTMLResponse(f"""
<!DOCTYPE html><html><body style="font-family:sans-serif;padding:2rem">
<h2>✅ Authentication successful!</h2>
<p>Save this refresh token as <code>YAHOO_REFRESH_TOKEN</code> in your Render environment:</p>
<pre style="background:#f4f4f4;padding:1rem;word-break:break-all;border-radius:4px">{rt}</pre>
<ol>
  <li>Go to Render dashboard → your <strong>fantasy-advisor</strong> service</li>
  <li>Environment → Add Environment Variable</li>
  <li>Key: <code>YAHOO_REFRESH_TOKEN</code> &nbsp; Value: the token above</li>
  <li>Save → service will redeploy automatically</li>
</ol>
<p><a href="/">→ Continue to app</a></p>
</body></html>
""")


@app.get("/health")
async def health():
    return {"status": "ok", "authenticated": token_store.is_authenticated()}
```

- [ ] **Step 3: Set env vars for local testing**

Create a `.env` file (already gitignored):

```
YAHOO_CLIENT_ID=dj0yJmk9R2ltRVBHYmNNZktGJmQ9WVdrOVFtaHRTV0ZaVFdvbWNHbzlNQT09JnM9Y29uc3VtZXJzZWNyZXQmc3Y9MCZ4PTU4
YAHOO_CLIENT_SECRET=9757395de36755ce59318b8a02eef48be98a8c14
YAHOO_REDIRECT_URI=http://localhost:8000/auth/callback
```

- [ ] **Step 4: Test OAuth flow locally**

```bash
cd /Users/mattdiamond/fantasy-advisor
pip install -r requirements.txt
uvicorn main:app --reload
```

Visit http://localhost:8000/login — should redirect to Yahoo login page.
Complete login → should redirect to http://localhost:8000/auth/callback → shows refresh token page.

- [ ] **Step 5: Set env vars in Render**

In Render dashboard → Environment, set:
- `YAHOO_CLIENT_ID` — from .env above
- `YAHOO_CLIENT_SECRET` — from .env above
- `YAHOO_REDIRECT_URI` — `https://fantasy-advisor.onrender.com/auth/callback`

Then visit `https://fantasy-advisor.onrender.com/login` to do the first OAuth and get your refresh token, then set `YAHOO_REFRESH_TOKEN` in Render too.

- [ ] **Step 6: Commit**

```bash
git add yahoo_auth.py main.py .gitignore
git commit -m "feat: Yahoo OAuth web flow with token storage"
git push
```

---

## Task 3: Yahoo Roster Parser

**Files:**
- Create: `yahoo_api.py`
- Create: `tests/test_yahoo_api.py`

- [ ] **Step 1: Write failing tests for parse_player_info**

```python
# tests/test_yahoo_api.py
from yahoo_api import parse_player_info, parse_player, _parse_eligible_positions


def test_parse_player_info_extracts_flat_fields():
    info_list = [
        {"player_id": "8967"},
        {"name": {"full": "Mike Trout", "first": "Mike", "last": "Trout"}},
        {"editorial_team_abbr": "LAA"},
        {"display_position": "OF"},
        {"eligible_positions": {"position": ["OF", "Util"]}},
    ]
    result = parse_player_info(info_list)
    assert result["player_id"] == "8967"
    assert result["name"]["full"] == "Mike Trout"
    assert result["editorial_team_abbr"] == "LAA"
    assert result["display_position"] == "OF"


def test_parse_player_info_ignores_non_dicts():
    info_list = ["some_string", {"player_id": "123"}, 42]
    result = parse_player_info(info_list)
    assert result["player_id"] == "123"
    assert len(result) == 1


def test_parse_player_extracts_full_name():
    player_data = [
        [
            {"player_id": "8967"},
            {"name": {"full": "Mike Trout"}},
            {"editorial_team_abbr": "LAA"},
            {"display_position": "OF"},
            {"eligible_positions": {"position": ["OF", "Util"]}},
        ],
        {"selected_position": {"position": "OF"}},
    ]
    result = parse_player(player_data)
    assert result["full_name"] == "Mike Trout"
    assert result["team_abbr"] == "LAA"
    assert result["position"] == "OF"
    assert result["selected_position"] == "OF"
    assert "OF" in result["eligible_positions"]
    assert result["player_id"] == "8967"
    assert result["bats"] is None
    assert result["mlb_id"] is None


def test_parse_player_handles_missing_selected_position():
    player_data = [
        [{"player_id": "1"}, {"name": {"full": "Test Player"}},
         {"editorial_team_abbr": "NYY"}, {"display_position": "1B"}],
    ]
    result = parse_player(player_data)
    assert result["selected_position"] == ""


def test_parse_eligible_positions_string():
    assert _parse_eligible_positions({"position": "1B"}) == ["1B"]


def test_parse_eligible_positions_list():
    assert _parse_eligible_positions({"position": ["1B", "Util"]}) == ["1B", "Util"]


def test_parse_eligible_positions_empty():
    assert _parse_eligible_positions({}) == []
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
cd /Users/mattdiamond/fantasy-advisor
python -m pytest tests/test_yahoo_api.py -v
```

Expected: `ModuleNotFoundError: No module named 'yahoo_api'`

- [ ] **Step 3: Write yahoo_api.py with parse functions**

```python
# yahoo_api.py
import httpx
from yahoo_auth import token_store

API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

LEAGUES = {
    "4594": {"name": "The Gold Members", "key": "mlb.l.4594"},
    "20959": {"name": "YCQ League", "key": "mlb.l.20959"},
}


def parse_player_info(info_list: list) -> dict:
    """Collapse Yahoo's array-of-single-key-dicts into a flat dict."""
    player = {}
    for item in info_list:
        if isinstance(item, dict):
            for k, v in item.items():
                player[k] = v
    return player


def _parse_eligible_positions(ep_obj) -> list[str]:
    if not isinstance(ep_obj, dict):
        return []
    pos = ep_obj.get("position", [])
    if isinstance(pos, str):
        return [pos]
    return list(pos) if isinstance(pos, list) else []


def parse_player(player_data: list) -> dict:
    """Parse a Yahoo player entry [info_array, meta_dict] into a clean dict."""
    info_list = player_data[0] if player_data else []
    meta = player_data[1] if len(player_data) > 1 else {}
    raw = parse_player_info(info_list)

    name_obj = raw.get("name", {})
    full_name = name_obj.get("full", "") if isinstance(name_obj, dict) else str(name_obj)

    selected_pos = ""
    if isinstance(meta, dict):
        sp = meta.get("selected_position", {})
        if isinstance(sp, dict):
            selected_pos = sp.get("position", "")
        elif isinstance(sp, list):
            for item in sp:
                if isinstance(item, dict) and "position" in item:
                    selected_pos = item["position"]
                    break

    return {
        "player_id": str(raw.get("player_id", "")),
        "full_name": full_name,
        "team_abbr": raw.get("editorial_team_abbr", ""),
        "position": raw.get("display_position", ""),
        "selected_position": selected_pos,
        "eligible_positions": _parse_eligible_positions(raw.get("eligible_positions", {})),
        "bats": None,
        "mlb_id": None,
    }
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_yahoo_api.py -v
```

Expected: all 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add yahoo_api.py tests/test_yahoo_api.py
git commit -m "feat: Yahoo roster parser with tests"
git push
```

---

## Task 4: Yahoo API — Roster Fetch

**Files:**
- Modify: `yahoo_api.py` — add `get_roster()` and `_extract_players_from_roster()`
- Modify: `main.py` — add `/api/debug/yahoo/{league_id}` and `/api/leagues` endpoints

- [ ] **Step 1: Add roster fetch to yahoo_api.py**

Append to the end of `yahoo_api.py`:

```python
def _extract_players_from_roster(roster_data: dict) -> list[dict]:
    """Extract player list from Yahoo's roster sub-object."""
    players_obj = roster_data.get("0", {}).get("players", {})
    count = int(players_obj.get("count", 0))
    players = []
    for i in range(count):
        player_entry = players_obj.get(str(i), {}).get("player", [])
        if player_entry:
            players.append(parse_player(player_entry))
    return players


async def get_roster(league_id: str) -> list[dict]:
    """Fetch the authenticated user's roster for a given league.

    Uses Yahoo compound resource query to get roster in one API call.
    """
    if league_id not in LEAGUES:
        raise ValueError(f"Unknown league_id: {league_id}")
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
        data = resp.json()

    return _parse_roster_response(data)


def _parse_roster_response(data: dict) -> list[dict]:
    """Navigate Yahoo's deeply nested response to find roster players.

    Yahoo's compound query nests: fantasy_content → users → 0 → user →
    [2] → games → 0 → game → [1] → leagues → 0 → league →
    [1] → teams → 0 → team → [1] → roster
    """
    try:
        fc = data["fantasy_content"]
        user = fc["users"]["0"]["user"]
        game = user[2]["games"]["0"]["game"]
        league = game[1]["leagues"]["0"]["league"]
        team = league[1]["teams"]["0"]["team"]
        roster = team[1]["roster"]
        return _extract_players_from_roster(roster)
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(
            f"Failed to parse Yahoo roster response. "
            f"Use /api/debug/yahoo/{{league_id}} to inspect raw response. Error: {e}"
        )
```

- [ ] **Step 2: Add debug and leagues endpoints to main.py**

Add these routes to `main.py` (after the `/health` route):

```python
from yahoo_api import get_roster, LEAGUES, API_BASE


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
```

- [ ] **Step 3: Test locally — verify roster fetch**

```bash
uvicorn main:app --reload
```

Visit http://localhost:8000/api/debug/yahoo/4594

If response structure doesn't match `_parse_roster_response`, inspect the raw JSON and update the navigation path in `_parse_roster_response` to match.

Then visit http://localhost:8000/api/roster/4594 — should return a list of player dicts.

- [ ] **Step 4: Commit**

```bash
git add yahoo_api.py main.py
git commit -m "feat: Yahoo roster fetch with debug endpoint"
git push
```

---

## Task 5: MLB Probable Pitchers

**Files:**
- Create: `mlb_api.py`
- Create: `tests/test_mlb_api.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_mlb_api.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from mlb_api import get_probable_pitchers, get_pitcher_details


MOCK_SCHEDULE = {
    "dates": [
        {
            "games": [
                {
                    "teams": {
                        "home": {
                            "team": {"abbreviation": "NYY"},
                            "probablePitcher": {"fullName": "Gerrit Cole", "id": 543037},
                        },
                        "away": {
                            "team": {"abbreviation": "BOS"},
                            "probablePitcher": {"fullName": "Chris Sale", "id": 519242},
                        },
                    }
                }
            ]
        }
    ]
}

MOCK_PITCHER_PERSON = {
    "people": [
        {
            "pitchHand": {"code": "R"},
            "stats": [
                {
                    "splits": [
                        {
                            "stat": {"era": "2.85", "whip": "0.95"}
                        }
                    ]
                }
            ],
        }
    ]
}


def make_mock_client(json_data):
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status.return_value = None
    mock_get = AsyncMock(return_value=mock_resp)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock(get=mock_get))
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


@pytest.mark.asyncio
async def test_get_probable_pitchers_parses_home_and_away():
    with patch("mlb_api.httpx.AsyncClient", return_value=make_mock_client(MOCK_SCHEDULE)):
        result = await get_probable_pitchers()
    assert "NYY" in result
    assert result["NYY"]["name"] == "Gerrit Cole"
    assert result["NYY"]["mlb_id"] == 543037
    assert "BOS" in result
    assert result["BOS"]["name"] == "Chris Sale"


@pytest.mark.asyncio
async def test_get_probable_pitchers_skips_missing_pitcher():
    data = {
        "dates": [{"games": [{"teams": {
            "home": {"team": {"abbreviation": "NYY"}, "probablePitcher": None},
            "away": {"team": {"abbreviation": "BOS"}},
        }}]}]
    }
    with patch("mlb_api.httpx.AsyncClient", return_value=make_mock_client(data)):
        result = await get_probable_pitchers()
    assert "NYY" not in result
    assert "BOS" not in result


@pytest.mark.asyncio
async def test_get_pitcher_details_extracts_throws_era_whip():
    with patch("mlb_api.httpx.AsyncClient", return_value=make_mock_client(MOCK_PITCHER_PERSON)):
        result = await get_pitcher_details(543037)
    assert result["throws"] == "R"
    assert result["era"] == 2.85
    assert result["whip"] == 0.95
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_mlb_api.py -v
```

Expected: `ModuleNotFoundError: No module named 'mlb_api'`

- [ ] **Step 3: Write mlb_api.py**

```python
# mlb_api.py
import asyncio
from datetime import date
import httpx

MLB_BASE = "https://statsapi.mlb.com/api/v1"


async def get_probable_pitchers(game_date: date | None = None) -> dict[str, dict]:
    """Return {team_abbr: {name, mlb_id, throws, era, whip}} for today's games."""
    date_str = (game_date or date.today()).strftime("%Y-%m-%d")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{MLB_BASE}/schedule",
            params={"sportId": 1, "date": date_str, "hydrate": "probablePitcher,team"},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

    pitchers = {}
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            for side in ("home", "away"):
                team = game.get("teams", {}).get(side, {})
                abbr = team.get("team", {}).get("abbreviation", "")
                pitcher = team.get("probablePitcher")
                if pitcher and abbr:
                    pitchers[abbr] = {
                        "name": pitcher.get("fullName", ""),
                        "mlb_id": pitcher.get("id"),
                        "throws": None,
                        "era": None,
                        "whip": None,
                    }
    return pitchers


async def get_pitcher_details(mlb_id: int) -> dict:
    """Get pitcher's throws (L/R), current-season ERA and WHIP."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{MLB_BASE}/people/{mlb_id}",
            params={"hydrate": f"stats(group=pitching,type=season,season={date.today().year})"},
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

    person = data.get("people", [{}])[0]
    throws = person.get("pitchHand", {}).get("code", "R")

    era = whip = None
    for stat_group in person.get("stats", []):
        splits = stat_group.get("splits", [])
        if splits:
            stat = splits[0].get("stat", {})
            if "era" in stat:
                try:
                    era = float(stat["era"])
                except (ValueError, TypeError):
                    pass
            if "whip" in stat:
                try:
                    whip = float(stat["whip"])
                except (ValueError, TypeError):
                    pass
            break

    return {"throws": throws, "era": era, "whip": whip}


async def enrich_pitchers(pitchers: dict[str, dict]) -> dict[str, dict]:
    """Fetch throws/ERA/WHIP for all pitchers in parallel."""
    async def _enrich_one(abbr: str, info: dict) -> tuple[str, dict]:
        if info.get("mlb_id"):
            try:
                details = await get_pitcher_details(info["mlb_id"])
                return abbr, {**info, **details}
            except Exception:
                pass
        return abbr, info

    pairs = await asyncio.gather(*[_enrich_one(a, i) for a, i in pitchers.items()])
    return dict(pairs)


async def search_player(name: str) -> int | None:
    """Look up MLB player ID by full name. Returns first match or None."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{MLB_BASE}/people/search",
                params={"names": name, "sportId": 1},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return None
    people = data.get("people", [])
    return people[0].get("id") if people else None


async def get_hitter_details(mlb_id: int, season: int | None = None) -> dict:
    """Get batter's bat side (L/R/S) and vL/vR split AVG for the season."""
    season = season or date.today().year
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{MLB_BASE}/people/{mlb_id}",
            params={
                "hydrate": f"stats(group=hitting,type=statSplits,season={season})"
            },
            timeout=10.0,
        )
        resp.raise_for_status()
        data = resp.json()

    person = data.get("people", [{}])[0]
    bats = person.get("batSide", {}).get("code", "R")

    vl_avg = vr_avg = None
    for stat_group in person.get("stats", []):
        for split in stat_group.get("splits", []):
            code = split.get("split", {}).get("code", "")
            avg_str = split.get("stat", {}).get("avg")
            if avg_str:
                try:
                    avg = float(avg_str)
                    if code == "vl":
                        vl_avg = avg
                    elif code == "vr":
                        vr_avg = avg
                except (ValueError, TypeError):
                    pass

    return {"bats": bats, "vL": vl_avg, "vR": vr_avg}
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_mlb_api.py -v
```

Expected: all 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add mlb_api.py tests/test_mlb_api.py
git commit -m "feat: MLB probable pitchers and pitcher details with tests"
git push
```

---

## Task 6: Advisor Scoring Logic

**Files:**
- Create: `advisor.py`
- Create: `tests/test_advisor.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_advisor.py
import pytest
from advisor import (
    PitcherInfo, HitterInfo, score_hitter, is_ace,
    get_matchup_quality, recommend, advise_roster,
)


def pitcher(throws="R", era=4.00, whip=1.30) -> PitcherInfo:
    return PitcherInfo(name="Test Pitcher", throws=throws, era=era, whip=whip)


def hitter(vL=0.250, vR=0.280, pitcher_obj=None, pid="1") -> HitterInfo:
    return HitterInfo(
        player_id=pid,
        full_name="Test Hitter",
        team_abbr="NYY",
        position="OF",
        selected_position="OF",
        bats="R",
        splits={"vL": vL, "vR": vR},
        pitcher=pitcher_obj,
    )


# --- score_hitter ---

def test_score_uses_vR_vs_righty():
    h = hitter(vR=0.300, pitcher_obj=pitcher(throws="R", era=4.0, whip=1.30))
    expected = 0.300 * 3.0 + (1 / 4.0) * 2.0 + (1 / 1.30) * 1.5
    assert abs(score_hitter(h) - expected) < 0.001


def test_score_uses_vL_vs_lefty():
    h = hitter(vL=0.250, pitcher_obj=pitcher(throws="L", era=4.0, whip=1.30))
    expected = 0.250 * 3.0 + (1 / 4.0) * 2.0 + (1 / 1.30) * 1.5
    assert abs(score_hitter(h) - expected) < 0.001


def test_score_zero_no_game():
    h = hitter(pitcher_obj=None)
    assert score_hitter(h) == 0.0


def test_score_uses_fallback_era_when_none():
    h = hitter(vR=0.280, pitcher_obj=PitcherInfo("X", "R", era=None, whip=1.30))
    score = score_hitter(h)
    assert score > 0  # uses fallback ERA of 4.50


# --- is_ace ---

def test_is_ace_low_era():
    assert is_ace(pitcher(era=2.99, whip=1.20)) is True


def test_is_ace_low_whip():
    assert is_ace(pitcher(era=3.50, whip=0.99)) is True


def test_is_ace_normal():
    assert is_ace(pitcher(era=4.00, whip=1.30)) is False


# --- get_matchup_quality ---

def test_matchup_good_high_avg():
    h = hitter(vR=0.270, pitcher_obj=pitcher(throws="R"))
    assert get_matchup_quality(h) == "good"


def test_matchup_bad_low_avg():
    h = hitter(vR=0.229, pitcher_obj=pitcher(throws="R"))
    assert get_matchup_quality(h) == "bad"


def test_matchup_ok_middle():
    h = hitter(vR=0.250, pitcher_obj=pitcher(throws="R"))
    assert get_matchup_quality(h) == "ok"


def test_matchup_ok_no_game():
    h = hitter(pitcher_obj=None)
    assert get_matchup_quality(h) == "ok"


# --- recommend ---

def test_recommend_top_score_is_start():
    scores = [1.0, 1.5, 2.0, 2.5, 3.0]
    assert recommend(3.0, scores, False) == "Start"


def test_recommend_bottom_score_is_sit():
    scores = [1.0, 1.5, 2.0, 2.5, 3.0]
    assert recommend(1.0, scores, False) == "Sit"


def test_recommend_ace_downgrades_start_to_flex():
    scores = [1.0, 1.5, 2.0, 2.5, 3.0]
    assert recommend(3.0, scores, True) == "Flex"


def test_recommend_ace_downgrades_flex_to_sit():
    scores = [1.0, 1.5, 2.0, 2.5, 3.0]
    # Middle score → Flex without ace, → Sit with ace
    assert recommend(2.0, scores, True) == "Sit"


def test_recommend_empty_scores_returns_flex():
    assert recommend(1.0, [], False) == "Flex"


# --- advise_roster ---

def test_advise_roster_sorted_by_score_descending():
    h1 = hitter(vR=0.320, pitcher_obj=pitcher(era=4.0, whip=1.3), pid="1")
    h2 = hitter(vR=0.200, pitcher_obj=pitcher(era=5.0, whip=1.5), pid="2")
    result = advise_roster([h1, h2])
    assert result[0]["score"] >= result[1]["score"]


def test_advise_roster_no_game_is_sit():
    h = hitter(pitcher_obj=None, pid="1")
    result = advise_roster([h])
    assert result[0]["recommendation"] == "Sit"
    assert result[0]["has_game"] is False


def test_advise_roster_includes_expected_keys():
    h = hitter(pitcher_obj=pitcher(), pid="1")
    result = advise_roster([h])
    row = result[0]
    for key in ("player_id", "full_name", "team_abbr", "position", "selected_position",
                "bats", "has_game", "pitcher", "splits", "matchup_quality", "score",
                "recommendation"):
        assert key in row, f"Missing key: {key}"
```

- [ ] **Step 2: Run tests — verify they fail**

```bash
python -m pytest tests/test_advisor.py -v
```

Expected: `ModuleNotFoundError: No module named 'advisor'`

- [ ] **Step 3: Write advisor.py**

```python
# advisor.py
from dataclasses import dataclass


@dataclass
class PitcherInfo:
    name: str
    throws: str      # "L" or "R"
    era: float | None
    whip: float | None


@dataclass
class HitterInfo:
    player_id: str
    full_name: str
    team_abbr: str
    position: str
    selected_position: str
    bats: str | None     # "L", "R", "S"
    splits: dict         # {"vL": float|None, "vR": float|None}
    pitcher: PitcherInfo | None   # None = no game today


def is_ace(pitcher: PitcherInfo) -> bool:
    if pitcher.era is not None and pitcher.era < 3.00:
        return True
    if pitcher.whip is not None and pitcher.whip < 1.00:
        return True
    return False


def score_hitter(hitter: HitterInfo) -> float:
    """Score = (split_avg * 3.0) + (1/ERA * 2.0) + (1/WHIP * 1.5)"""
    if hitter.pitcher is None:
        return 0.0
    p = hitter.pitcher
    split_avg = (
        hitter.splits.get("vL") if p.throws == "L" else hitter.splits.get("vR")
    )
    era = p.era if p.era and p.era > 0 else 4.50   # league-average fallback
    whip = p.whip if p.whip and p.whip > 0 else 1.30
    score = (split_avg * 3.0 if split_avg is not None else 0.0)
    score += (1.0 / era) * 2.0
    score += (1.0 / whip) * 1.5
    return score


def get_matchup_quality(hitter: HitterInfo) -> str:
    if hitter.pitcher is None:
        return "ok"
    p = hitter.pitcher
    avg = hitter.splits.get("vL") if p.throws == "L" else hitter.splits.get("vR")
    if avg is None:
        return "ok"
    if avg >= 0.270:
        return "good"
    if avg <= 0.230:
        return "bad"
    return "ok"


def recommend(score: float, all_scores: list[float], is_ace_pitcher: bool) -> str:
    """
    Start = top 60% (above 40th percentile)
    Sit   = bottom 25% (at or below 25th percentile)
    Flex  = middle
    Ace pitcher downgrades by one level.
    """
    if not all_scores:
        return "Flex"
    sorted_scores = sorted(all_scores)
    n = len(sorted_scores)
    bottom_threshold = sorted_scores[max(0, int(n * 0.25) - 1)]
    top_threshold = sorted_scores[max(0, int(n * 0.40) - 1)]

    if score <= bottom_threshold:
        rec = "Sit"
    elif score >= top_threshold:
        rec = "Start"
    else:
        rec = "Flex"

    if is_ace_pitcher:
        if rec == "Start":
            rec = "Flex"
        elif rec == "Flex":
            rec = "Sit"

    return rec


def advise_roster(hitters: list[HitterInfo]) -> list[dict]:
    scores = {h.player_id: score_hitter(h) for h in hitters}
    active_scores = [scores[h.player_id] for h in hitters if h.pitcher is not None]

    result = []
    for h in hitters:
        score = scores[h.player_id]
        p = h.pitcher
        ace = is_ace(p) if p else False
        rec = recommend(score, active_scores, ace) if p else "Sit"

        result.append({
            "player_id": h.player_id,
            "full_name": h.full_name,
            "team_abbr": h.team_abbr,
            "position": h.position,
            "selected_position": h.selected_position,
            "bats": h.bats,
            "has_game": p is not None,
            "pitcher": {
                "name": p.name,
                "throws": p.throws,
                "era": p.era,
                "whip": p.whip,
                "is_ace": ace,
            } if p else None,
            "splits": h.splits,
            "matchup_quality": get_matchup_quality(h),
            "score": round(score, 4),
            "recommendation": rec,
        })

    result.sort(key=lambda x: x["score"], reverse=True)
    return result
```

- [ ] **Step 4: Run tests — verify they pass**

```bash
python -m pytest tests/test_advisor.py -v
```

Expected: all 17 tests PASS

- [ ] **Step 5: Run full test suite**

```bash
python -m pytest -v
```

Expected: all tests PASS across all three test files

- [ ] **Step 6: Commit**

```bash
git add advisor.py tests/test_advisor.py
git commit -m "feat: advisor scoring logic with full test suite"
git push
```

---

## Task 7: /api/today Endpoint

**Files:**
- Modify: `main.py` — add `/api/today` with full wiring

- [ ] **Step 1: Replace main.py with full wired version**

```python
# main.py
import asyncio
import os
import secrets
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from yahoo_auth import token_store, get_auth_url, exchange_code
from yahoo_api import get_roster, LEAGUES, API_BASE
from mlb_api import (
    get_probable_pitchers, enrich_pitchers,
    search_player, get_hitter_details,
)
from advisor import HitterInfo, PitcherInfo, advise_roster
import httpx

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
        data.get("refresh_token", ""),
        data.get("expires_in", 3600),
    )
    rt = data.get("refresh_token", "")
    return HTMLResponse(f"""
<!DOCTYPE html><html><body style="font-family:sans-serif;padding:2rem">
<h2>✅ Authentication successful!</h2>
<p>Save this as <code>YAHOO_REFRESH_TOKEN</code> in Render environment variables:</p>
<pre style="background:#f4f4f4;padding:1rem;word-break:break-all;border-radius:4px">{rt}</pre>
<ol>
  <li>Render dashboard → fantasy-advisor → Environment</li>
  <li>Add: <code>YAHOO_REFRESH_TOKEN</code> = token above</li>
  <li>Save → service redeploys automatically</li>
</ol>
<p><a href="/">→ Continue to app</a></p>
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
```

- [ ] **Step 2: Test /api/today locally**

```bash
uvicorn main:app --reload
```

Visit http://localhost:8000/api/today

Expected: JSON array with two league objects, each containing players with scores and recommendations. The first call will be slow (~30-60s) due to sequential MLB lookups. That's acceptable for now.

- [ ] **Step 3: Commit**

```bash
git add main.py
git commit -m "feat: /api/today endpoint wiring Yahoo + MLB + advisor"
git push
```

---

## Task 8: Full UI

**Files:**
- Modify: `templates/index.html` — replace placeholder with full UI

- [ ] **Step 1: Write the complete index.html**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fantasy Baseball Advisor</title>
<style>
  :root {
    --green: #639922;
    --amber: #BA7517;
    --red: #A32D2D;
    --blue: #1a56a0;
    --bg: #0f1923;
    --surface: #1a2535;
    --surface2: #243040;
    --text: #e8edf2;
    --text-muted: #8a9ab0;
    --border: #2d3f55;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; font-size: 14px; min-height: 100vh; display: flex; flex-direction: column; }

  /* Header */
  header { background: var(--surface); border-bottom: 1px solid var(--border); padding: 12px 20px; display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 18px; font-weight: 700; }
  .header-date { color: var(--text-muted); font-size: 13px; margin-left: auto; }

  /* Main layout */
  main { flex: 1; padding: 16px 20px; max-width: 1100px; width: 100%; margin: 0 auto; }

  /* League tabs */
  .league-tabs { display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 2px solid var(--border); padding-bottom: 0; }
  .tab-btn { background: none; border: none; color: var(--text-muted); padding: 8px 20px; cursor: pointer; font-size: 14px; font-weight: 500; border-bottom: 2px solid transparent; margin-bottom: -2px; transition: color 0.15s; }
  .tab-btn.active { color: var(--text); border-bottom-color: var(--green); }
  .tab-btn:hover:not(.active) { color: var(--text); }

  /* Summary cards */
  .summary-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; padding: 14px 16px; }
  .card-label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 6px; }
  .card-value { font-size: 28px; font-weight: 700; }
  .card-value.green { color: var(--green); }
  .card-value.amber { color: var(--amber); }
  .card-value.red { color: var(--red); }
  .card-value.muted { color: var(--text-muted); }

  /* Filter row */
  .filter-row { display: flex; gap: 6px; margin-bottom: 12px; align-items: center; }
  .filter-btn { background: var(--surface); border: 1px solid var(--border); color: var(--text-muted); padding: 5px 14px; border-radius: 20px; cursor: pointer; font-size: 13px; transition: all 0.15s; }
  .filter-btn.active { background: var(--surface2); color: var(--text); border-color: var(--text-muted); }
  .filter-btn.active.f-start { background: rgba(99,153,34,0.15); border-color: var(--green); color: var(--green); }
  .filter-btn.active.f-sit { background: rgba(163,45,45,0.15); border-color: var(--red); color: var(--red); }
  .filter-btn.active.f-flex { background: rgba(186,117,23,0.15); border-color: var(--amber); color: var(--amber); }
  .filter-count { color: var(--text-muted); font-size: 12px; margin-left: 4px; }

  /* Table */
  .table-wrap { background: var(--surface); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  table { width: 100%; border-collapse: collapse; }
  thead th { background: var(--surface2); padding: 10px 12px; text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); font-weight: 600; white-space: nowrap; }
  tbody tr { border-top: 1px solid var(--border); transition: background 0.1s; }
  tbody tr:hover { background: var(--surface2); }
  tbody tr.bench-row { opacity: 0.5; }
  td { padding: 10px 12px; vertical-align: middle; }

  /* Badges */
  .badge { display: inline-block; padding: 1px 6px; border-radius: 4px; font-size: 11px; font-weight: 600; text-transform: uppercase; }
  .badge-lhp { background: rgba(26,86,160,0.25); color: #5b9bd5; border: 1px solid rgba(26,86,160,0.4); }
  .badge-rhp { background: rgba(186,117,23,0.25); color: #e8a030; border: 1px solid rgba(186,117,23,0.4); }
  .badge-bats { background: var(--surface2); color: var(--text-muted); border: 1px solid var(--border); font-size: 10px; padding: 1px 5px; }

  /* Position tag */
  .pos-tag { display: inline-block; min-width: 28px; text-align: center; background: var(--surface2); color: var(--text-muted); border-radius: 4px; padding: 2px 6px; font-size: 12px; font-weight: 600; }

  /* Matchup pill */
  .matchup-pill { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; font-weight: 600; }
  .matchup-good { background: rgba(99,153,34,0.15); color: var(--green); border: 1px solid rgba(99,153,34,0.3); }
  .matchup-ok { background: rgba(138,154,176,0.1); color: var(--text-muted); border: 1px solid var(--border); }
  .matchup-bad { background: rgba(163,45,45,0.15); color: var(--red); border: 1px solid rgba(163,45,45,0.3); }
  .ace-flag { color: #f5c842; font-size: 13px; margin-left: 4px; title: "Ace pitcher"; }

  /* Pitcher info line */
  .pitcher-line { font-size: 12px; color: var(--text-muted); margin-top: 2px; }
  .pitcher-stat { color: var(--text); }

  /* Split bars */
  .splits-col { min-width: 120px; }
  .split-row { display: flex; align-items: center; gap: 6px; margin-bottom: 3px; }
  .split-label { font-size: 10px; color: var(--text-muted); width: 16px; text-align: right; flex-shrink: 0; }
  .split-bar-bg { flex: 1; height: 6px; background: var(--surface2); border-radius: 3px; overflow: hidden; }
  .split-bar-fill { height: 100%; border-radius: 3px; transition: width 0.3s; }
  .split-bar-fill.good { background: var(--green); }
  .split-bar-fill.ok { background: var(--amber); }
  .split-bar-fill.low { background: var(--red); }
  .split-avg { font-size: 11px; width: 34px; text-align: right; flex-shrink: 0; }

  /* Recommendation tag */
  .rec-tag { display: inline-block; padding: 3px 10px; border-radius: 4px; font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.04em; }
  .rec-start { background: rgba(99,153,34,0.2); color: var(--green); border: 1px solid rgba(99,153,34,0.4); }
  .rec-sit { background: rgba(163,45,45,0.2); color: var(--red); border: 1px solid rgba(163,45,45,0.4); }
  .rec-flex { background: rgba(186,117,23,0.2); color: var(--amber); border: 1px solid rgba(186,117,23,0.4); }
  .rec-nogame { background: var(--surface2); color: var(--text-muted); border: 1px solid var(--border); font-size: 11px; }

  /* Active toggle */
  .toggle { position: relative; display: inline-block; width: 34px; height: 18px; cursor: pointer; }
  .toggle input { opacity: 0; width: 0; height: 0; }
  .toggle-slider { position: absolute; inset: 0; background: var(--surface2); border: 1px solid var(--border); border-radius: 18px; transition: 0.2s; }
  .toggle-slider:before { content: ""; position: absolute; width: 12px; height: 12px; left: 2px; top: 2px; background: var(--text-muted); border-radius: 50%; transition: 0.2s; }
  .toggle input:checked + .toggle-slider { background: rgba(99,153,34,0.2); border-color: var(--green); }
  .toggle input:checked + .toggle-slider:before { transform: translateX(16px); background: var(--green); }

  /* Loading / error states */
  .loading { text-align: center; padding: 40px; color: var(--text-muted); }
  .error-msg { background: rgba(163,45,45,0.1); border: 1px solid rgba(163,45,45,0.3); color: var(--red); padding: 12px 16px; border-radius: 6px; margin-bottom: 16px; }

  /* Footer */
  footer { background: var(--surface); border-top: 1px solid var(--border); padding: 10px 20px; display: flex; gap: 10px; align-items: center; }
  .footer-btn { background: var(--surface2); border: 1px solid var(--border); color: var(--text); padding: 7px 16px; border-radius: 6px; cursor: pointer; font-size: 13px; transition: background 0.15s; }
  .footer-btn:hover:not(:disabled) { background: #2d3f55; }
  .footer-btn:disabled { opacity: 0.4; cursor: not-allowed; }

  @media (max-width: 700px) {
    .summary-cards { grid-template-columns: repeat(2, 1fr); }
    .splits-col { display: none; }
  }
</style>
</head>
<body>

<header>
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#639922" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/><line x1="2" y1="12" x2="22" y2="12"/></svg>
  <h1>Fantasy Baseball Advisor</h1>
  <span class="header-date" id="header-date"></span>
</header>

<main>
  <div id="error-container"></div>

  <div class="league-tabs" id="league-tabs"></div>

  <div class="summary-cards">
    <div class="card">
      <div class="card-label">Active Hitters</div>
      <div class="card-value" id="stat-active">—</div>
    </div>
    <div class="card">
      <div class="card-label">Strong Matchups</div>
      <div class="card-value green" id="stat-strong">—</div>
    </div>
    <div class="card">
      <div class="card-label">Tough Matchups</div>
      <div class="card-value red" id="stat-tough">—</div>
    </div>
    <div class="card">
      <div class="card-label">No Game Today</div>
      <div class="card-value muted" id="stat-nogame">—</div>
    </div>
  </div>

  <div class="filter-row">
    <button class="filter-btn active" data-filter="all">All</button>
    <button class="filter-btn f-start" data-filter="Start">Start</button>
    <button class="filter-btn f-sit" data-filter="Sit">Sit</button>
    <button class="filter-btn f-flex" data-filter="Flex">Flex</button>
    <span class="filter-count" id="filter-count"></span>
  </div>

  <div class="table-wrap">
    <div class="loading" id="loading">Loading roster data…</div>
    <table id="main-table" style="display:none">
      <thead>
        <tr>
          <th>Pos</th>
          <th>Player</th>
          <th>Matchup</th>
          <th class="splits-col">Splits</th>
          <th>Rec</th>
          <th>Active</th>
        </tr>
      </thead>
      <tbody id="table-body"></tbody>
    </table>
  </div>
</main>

<footer>
  <button class="footer-btn">Waiver Suggestions</button>
  <button class="footer-btn" disabled title="Coming in v2">Push Lineup to Yahoo</button>
</footer>

<script>
let allData = [];       // full API response array
let activeLeagueId = null;
let activeFilter = 'all';

// ── Helpers ──────────────────────────────────────────────────

function fmtAvg(v) {
  if (v == null) return '—';
  return v.toFixed(3).replace(/^0/, '');  // ".285"
}

function splitBarPct(avg) {
  if (avg == null) return 0;
  const pct = (avg - 0.150) / (0.400 - 0.150) * 100;
  return Math.min(100, Math.max(0, pct));
}

function splitBarClass(avg) {
  if (avg == null) return 'ok';
  if (avg >= 0.270) return 'good';
  if (avg <= 0.230) return 'low';
  return 'ok';
}

function recClass(rec) {
  if (rec === 'Start') return 'rec-start';
  if (rec === 'Sit') return 'rec-sit';
  if (rec === 'Flex') return 'rec-flex';
  return 'rec-nogame';
}

function handBadge(hand, type) {
  if (!hand) return '';
  if (type === 'pitcher') {
    const cls = hand === 'L' ? 'badge-lhp' : 'badge-rhp';
    return `<span class="badge ${cls}">${hand}HP</span>`;
  }
  return `<span class="badge badge-bats">${hand}</span>`;
}

function matchupClass(q) {
  if (q === 'good') return 'matchup-good';
  if (q === 'bad') return 'matchup-bad';
  return 'matchup-ok';
}

function matchupLabel(q) {
  if (q === 'good') return 'Good';
  if (q === 'bad') return 'Tough';
  return 'OK';
}

// ── Render ────────────────────────────────────────────────────

function getLeagueData() {
  return allData.find(l => l.league_id === activeLeagueId) || null;
}

function renderTabs() {
  const container = document.getElementById('league-tabs');
  container.innerHTML = allData.map(l => `
    <button class="tab-btn ${l.league_id === activeLeagueId ? 'active' : ''}"
            data-id="${l.league_id}">${l.league_name}</button>
  `).join('');
  container.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      activeLeagueId = btn.dataset.id;
      renderTabs();
      renderAll();
    });
  });
}

function renderStats(league) {
  if (!league || league.error) {
    ['active', 'strong', 'tough', 'nogame'].forEach(k =>
      document.getElementById(`stat-${k}`).textContent = '—'
    );
    return;
  }
  const s = league.stats;
  document.getElementById('stat-active').textContent = s.active_hitters;
  document.getElementById('stat-strong').textContent = s.strong_matchups;
  document.getElementById('stat-tough').textContent = s.tough_matchups;
  document.getElementById('stat-nogame').textContent = s.no_game;
}

function buildSplitBars(splits, pitcherThrows) {
  const vL = splits?.vL;
  const vR = splits?.vR;
  const highlight = pitcherThrows; // the relevant side

  function row(label, avg) {
    const isActive = label === highlight;
    const pct = splitBarPct(avg);
    const cls = splitBarClass(avg);
    const style = isActive ? '' : 'opacity:0.5';
    return `
      <div class="split-row" style="${style}">
        <span class="split-label">${label}</span>
        <div class="split-bar-bg">
          <div class="split-bar-fill ${cls}" style="width:${pct}%"></div>
        </div>
        <span class="split-avg">${fmtAvg(avg)}</span>
      </div>`;
  }

  return row('vL', vL) + row('vR', vR);
}

function buildRow(player) {
  const p = player.pitcher;
  const throws = p?.throws;
  const matchupHtml = player.has_game ? `
    <span class="matchup-pill ${matchupClass(player.matchup_quality)}">${matchupLabel(player.matchup_quality)}</span>
    ${p ? `<div class="pitcher-line">${p.name} ${handBadge(throws, 'pitcher')}
      ${p.is_ace ? '<span class="ace-flag" title="Ace pitcher">★</span>' : ''}
      <span class="pitcher-stat">${p.era != null ? p.era.toFixed(2) + ' ERA' : '—'}</span>
      <span class="pitcher-stat"> ${p.whip != null ? p.whip.toFixed(2) + ' WHIP' : ''}</span>
    </div>` : ''}
  ` : `<span style="color:var(--text-muted);font-size:12px">No game</span>`;

  const recHtml = player.has_game
    ? `<span class="rec-tag ${recClass(player.recommendation)}">${player.recommendation}</span>`
    : `<span class="rec-tag rec-nogame">No Game</span>`;

  const benchClass = player.selected_position === 'BN' ? ' bench-row' : '';

  return `<tr class="${benchClass}" data-rec="${player.recommendation}" data-has-game="${player.has_game}">
    <td><span class="pos-tag">${player.selected_position || player.position}</span></td>
    <td>
      <div><strong>${player.full_name}</strong> ${handBadge(player.bats, 'batter')}</div>
      <div style="font-size:11px;color:var(--text-muted)">${player.team_abbr} · ${player.position}</div>
    </td>
    <td>${matchupHtml}</td>
    <td class="splits-col">${buildSplitBars(player.splits, throws)}</td>
    <td>${recHtml}</td>
    <td>
      <label class="toggle">
        <input type="checkbox" ${player.selected_position !== 'BN' ? 'checked' : ''} disabled>
        <span class="toggle-slider"></span>
      </label>
    </td>
  </tr>`;
}

function renderTable(league) {
  const table = document.getElementById('main-table');
  const loading = document.getElementById('loading');
  const tbody = document.getElementById('table-body');
  const countEl = document.getElementById('filter-count');

  if (!league || league.error) {
    loading.style.display = 'block';
    loading.textContent = league?.error || 'No data';
    table.style.display = 'none';
    return;
  }

  let players = league.players || [];
  if (activeFilter !== 'all') {
    if (activeFilter === 'Start' || activeFilter === 'Sit' || activeFilter === 'Flex') {
      players = players.filter(p => p.recommendation === activeFilter);
    }
  }

  tbody.innerHTML = players.map(buildRow).join('');
  countEl.textContent = `${players.length} players`;
  loading.style.display = 'none';
  table.style.display = '';
}

function renderAll() {
  const league = getLeagueData();
  renderStats(league);
  renderTable(league);
}

// ── Filters ───────────────────────────────────────────────────

document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeFilter = btn.dataset.filter;
    renderAll();
  });
});

// ── Boot ──────────────────────────────────────────────────────

async function init() {
  // Show today's date
  const today = new Date();
  document.getElementById('header-date').textContent =
    today.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' });

  try {
    const resp = await fetch('/api/today');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    allData = await resp.json();
  } catch (err) {
    document.getElementById('error-container').innerHTML =
      `<div class="error-msg">Failed to load data: ${err.message}. Check that Yahoo auth is configured.</div>`;
    document.getElementById('loading').textContent = 'Error loading data.';
    return;
  }

  if (!allData.length) {
    document.getElementById('loading').textContent = 'No leagues found.';
    return;
  }

  activeLeagueId = allData[0].league_id;
  renderTabs();
  renderAll();
}

init();
</script>
</body>
</html>
```

- [ ] **Step 2: Test UI locally**

```bash
uvicorn main:app --reload
```

Visit http://localhost:8000 — you should see:
- League tabs at top
- Summary stat cards
- Table with hitters, split bars, matchup pills, rec tags
- Filter buttons work
- League tab switching works

- [ ] **Step 3: Commit and push**

```bash
git add templates/index.html
git commit -m "feat: full interactive UI with league tabs, split bars, matchup pills"
git push
```

---

## Task 9: Render Deploy + Smoke Test

**Files:** none — this is all configuration and verification.

- [ ] **Step 1: Set all Render env vars**

In Render dashboard → fantasy-advisor → Environment, confirm these are set:
- `YAHOO_CLIENT_ID` — your Yahoo app client ID
- `YAHOO_CLIENT_SECRET` — your Yahoo app client secret
- `YAHOO_REDIRECT_URI` — `https://fantasy-advisor.onrender.com/auth/callback`
- `YAHOO_REFRESH_TOKEN` — obtained from the `/auth/callback` page during first OAuth

- [ ] **Step 2: Trigger deploy**

Render auto-deploys on push. If not yet deployed:

```bash
git push  # triggers Render redeploy
```

Or in Render dashboard → Manual Deploy.

- [ ] **Step 3: Initial OAuth (if YAHOO_REFRESH_TOKEN not yet set)**

If `YAHOO_REFRESH_TOKEN` is not yet set:
1. Visit https://fantasy-advisor.onrender.com/login
2. Complete Yahoo login
3. Copy the refresh token from the success page
4. Set it as `YAHOO_REFRESH_TOKEN` in Render → redeploy

- [ ] **Step 4: Smoke test production**

```bash
# 1. Health check
curl https://fantasy-advisor.onrender.com/health
# Expected: {"status":"ok","authenticated":true}

# 2. Leagues list
curl https://fantasy-advisor.onrender.com/api/leagues
# Expected: [{"id":"4594","name":"The Gold Members"},{"id":"20959","name":"YCQ League"}]

# 3. Raw roster (check parsing works)
curl https://fantasy-advisor.onrender.com/api/roster/4594
# Expected: JSON array of player objects with full_name, team_abbr, position, etc.

# 4. Full today view (may take 30-60s first call)
curl https://fantasy-advisor.onrender.com/api/today
# Expected: array of two league objects with players, stats, recommendations
```

- [ ] **Step 5: Visit the app**

Open https://fantasy-advisor.onrender.com in your browser.

Verify:
- League tabs show "The Gold Members" and "YCQ League"
- Summary cards show counts
- Table shows hitters with matchup pills, split bars, and Start/Sit/Flex tags
- Filter buttons filter the table correctly
- Tab switching shows different rosters

- [ ] **Step 6: Debug Yahoo parsing if needed**

If roster is empty or errors, visit:
https://fantasy-advisor.onrender.com/api/debug/yahoo/4594

Inspect the raw JSON structure and update `_parse_roster_response()` in `yahoo_api.py` to match. The path through the nesting may differ slightly from what's in the plan — this debug endpoint is specifically for diagnosing this.

---

## Self-Review

**Spec coverage:**
- ✅ Two leagues (4594, 20959) — LEAGUES dict in yahoo_api.py
- ✅ Yahoo OAuth web flow — Task 2
- ✅ Roster pull both leagues — Task 3/4
- ✅ MLB splits (vL/vR AVG) — Task 5/6
- ✅ Probable pitcher + handedness — Task 5
- ✅ ERA/WHIP display — get_pitcher_details
- ✅ Ace flag (ERA < 3.00 or WHIP < 1.00) — is_ace()
- ✅ Scoring formula (split_avg * 3.0 + 1/ERA * 2.0 + 1/WHIP * 1.5) — score_hitter()
- ✅ Start/Sit/Flex thresholds (top 60%, bottom 25%) — recommend()
- ✅ Ace auto-downgrade — recommend()
- ✅ League tabs — UI
- ✅ Summary cards (4 stats) — UI
- ✅ Hitter table with all specified columns — UI
- ✅ Matchup pill (good/ok/bad) + pitcher name + hand badge + ERA + WHIP + ace — UI
- ✅ L/R split bars with colored fill — UI
- ✅ Recommendation tag — UI
- ✅ Active toggle (display only, read-only) — UI
- ✅ Filter buttons All/Start/Sit/Flex — UI
- ✅ Green #639922 / Amber #BA7517 / Red #A32D2D — CSS variables
- ✅ Blue badge LHP, amber badge RHP — handBadge()
- ✅ Player handedness badge — handBadge(bats, 'batter')
- ✅ Bottom bar with waiver + push buttons (push disabled) — footer
- ✅ Render deploy via render.yaml — Task 1
- ✅ Token stored server-side as Render env var — Task 2

**Note on Yahoo parsing path:** `_parse_roster_response()` contains a best-estimate navigation path. The debug endpoint `/api/debug/yahoo/{league_id}` is included specifically to diagnose and fix the path if it doesn't match the actual API response. This is expected to need one iteration.
