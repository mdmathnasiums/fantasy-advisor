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


async def get_pitcher_details(mlb_id: int, season: int | None = None) -> dict:
    """Get pitcher's throws (L/R), current-season ERA and WHIP."""
    season = season or date.today().year
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{MLB_BASE}/people/{mlb_id}",
            params={"hydrate": f"stats(group=pitching,type=season,season={season})"},
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
            except Exception as exc:
                print(f"[mlb_api] enrich_pitchers: failed to enrich {abbr}: {exc}")
        return abbr, info

    pairs = await asyncio.gather(*[_enrich_one(a, i) for a, i in pitchers.items()])
    return dict(pairs)


async def search_player(name: str) -> int | None:
    """Look up MLB player ID by full name. Returns first match or None."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{MLB_BASE}/people/search",
                params={"names": name, "sportId": 1},
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        people = data.get("people", [])
        return people[0].get("id") if people else None
    except Exception:
        return None


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
