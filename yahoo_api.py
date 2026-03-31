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
    # Yahoo returns a list of {"position": "X"} dicts
    if isinstance(ep_obj, list):
        return [item["position"] for item in ep_obj if isinstance(item, dict) and "position" in item]
    # Fallback: old assumed format {"position": "X"} or {"position": ["X", "Y"]}
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


def _parse_roster_response(data: dict) -> list[dict]:
    """Navigate Yahoo's deeply nested response to find roster players.

    Yahoo's compound query nests:
    fantasy_content → users → 0 → user →
    [1] → games → 0 → game →
    [1] → leagues → 0 → league →
    [1] → teams → 0 → team → [1] → roster
    """
    try:
        fc = data["fantasy_content"]
        user = fc["users"]["0"]["user"]
        game = user[1]["games"]["0"]["game"]
        league = game[1]["leagues"]["0"]["league"]
        team = league[1]["teams"]["0"]["team"]
        roster = team[1]["roster"]
        return _extract_players_from_roster(roster)
    except (KeyError, IndexError, TypeError) as e:
        raise ValueError(
            f"Failed to parse Yahoo roster response. "
            f"Use /api/debug/yahoo/{{league_id}} to inspect raw response. Error: {e}"
        )


async def fetch_raw_roster(league_id: str) -> dict:
    """Fetch raw Yahoo roster JSON for a given league (no parsing)."""
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
        return resp.json()


async def get_roster(league_id: str) -> list[dict]:
    """Fetch and parse the authenticated user's roster for a given league."""
    data = await fetch_raw_roster(league_id)
    return _parse_roster_response(data)
