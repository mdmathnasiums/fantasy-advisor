import asyncio
from datetime import date, timedelta
import httpx

MLB_BASE = "https://statsapi.mlb.com/api/v1"

# Park run-scoring factors by home team abbreviation (>1 = hitter-friendly).
# Range kept tight (±6%) so park doesn't overwhelm splits/pitcher quality.
PARK_FACTORS: dict[str, float] = {
    "COL": 1.06, "CIN": 1.04, "BAL": 1.03, "PHI": 1.03, "BOS": 1.02,
    "TEX": 1.02, "NYY": 1.01, "MIL": 1.01, "TOR": 1.01, "ATL": 1.01,
    "CHC": 1.00, "STL": 1.00, "WSH": 1.00, "DET": 1.00, "MIN": 1.00,
    "CLE": 0.99, "PIT": 0.99, "HOU": 0.99, "ARI": 0.99, "LAA": 0.99,
    "SEA": 0.98, "MIA": 0.98, "NYM": 0.98, "TB":  0.98, "OAK": 0.98,
    "SD":  0.97, "CWS": 0.97, "KC":  0.97, "LAD": 0.97, "SF":  0.94,
}


async def get_probable_pitchers(
    game_date: date | None = None,
) -> tuple[dict[str, dict], set[str], dict[str, str]]:
    """Return (pitchers, teams_with_game, game_venue).

    pitchers:        {batting_team_abbr: opponent_pitcher_info}
    teams_with_game: every team playing today regardless of whether a pitcher is named
    game_venue:      {team_abbr: home_team_abbr} — used for park factor lookup
    """
    date_str = (game_date or date.today()).strftime("%Y-%m-%d")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{MLB_BASE}/schedule",
            params={"sportId": 1, "date": date_str, "hydrate": "probablePitcher,team"},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()

    pitchers: dict[str, dict] = {}
    teams_with_game: set[str] = set()
    game_venue: dict[str, str] = {}  # team → home_team_abbr

    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            home = game.get("teams", {}).get("home", {})
            away = game.get("teams", {}).get("away", {})
            home_abbr = home.get("team", {}).get("abbreviation", "")
            away_abbr = away.get("team", {}).get("abbreviation", "")
            home_pitcher = home.get("probablePitcher")
            away_pitcher = away.get("probablePitcher")

            if home_abbr:
                teams_with_game.add(home_abbr)
                game_venue[home_abbr] = home_abbr  # home team plays in their own park
            if away_abbr:
                teams_with_game.add(away_abbr)
                game_venue[away_abbr] = home_abbr  # away team plays at home park

            # Away hitters face the home team's pitcher
            if home_pitcher and away_abbr:
                pitchers[away_abbr] = {
                    "name": home_pitcher.get("fullName", ""),
                    "mlb_id": home_pitcher.get("id"),
                    "throws": None, "era": None, "whip": None,
                    "recent_era": None, "recent_whip": None,
                    "eff_era": None, "eff_whip": None,
                }
            # Home hitters face the away team's pitcher
            if away_pitcher and home_abbr:
                pitchers[home_abbr] = {
                    "name": away_pitcher.get("fullName", ""),
                    "mlb_id": away_pitcher.get("id"),
                    "throws": None, "era": None, "whip": None,
                    "recent_era": None, "recent_whip": None,
                    "eff_era": None, "eff_whip": None,
                }

    return pitchers, teams_with_game, game_venue


def _parse_ip(ip_str) -> float:
    """Convert MLB innings-pitched string '6.1' → 6.333 actual innings."""
    try:
        parts = str(ip_str).split(".")
        whole = int(parts[0])
        outs = int(parts[1]) if len(parts) > 1 else 0
        return whole + outs / 3
    except (ValueError, TypeError):
        return 0.0


async def get_pitcher_details(mlb_id: int, season: int | None = None) -> dict:
    """Get pitcher's throws (L/R), season ERA/WHIP, and recent 3-start blended ERA/WHIP."""
    season = season or date.today().year
    async with httpx.AsyncClient() as client:
        season_resp, gamelog_resp = await asyncio.gather(
            client.get(
                f"{MLB_BASE}/people/{mlb_id}",
                params={"hydrate": f"stats(group=pitching,type=season,season={season})"},
                timeout=10.0,
            ),
            client.get(
                f"{MLB_BASE}/people/{mlb_id}",
                params={"hydrate": f"stats(group=pitching,type=gameLog,season={season})"},
                timeout=10.0,
            ),
        )

    season_resp.raise_for_status()

    # --- Season stats ---
    person = season_resp.json().get("people", [{}])[0]
    throws = person.get("pitchHand", {}).get("code", "R")
    era = whip = None
    for stat_group in person.get("stats", []):
        splits = stat_group.get("splits", [])
        if splits:
            stat = splits[0].get("stat", {})
            try:
                era = float(stat["era"])
            except (KeyError, ValueError, TypeError):
                pass
            try:
                whip = float(stat["whip"])
            except (KeyError, ValueError, TypeError):
                pass
            break

    # --- Recent 3 starts ---
    recent_era = recent_whip = None
    try:
        gamelog_resp.raise_for_status()
        gl_person = gamelog_resp.json().get("people", [{}])[0]
        starts = []
        for stat_group in gl_person.get("stats", []):
            for split in stat_group.get("splits", []):
                stat = split.get("stat", {})
                ip = _parse_ip(stat.get("inningsPitched", "0"))
                if ip >= 2.0:
                    starts.append({"stat": stat, "date": split.get("date", "")})
        # Sort newest first, take 3
        starts.sort(key=lambda s: s["date"], reverse=True)
        recent = [s["stat"] for s in starts[:3]]
        if recent:
            total_er = sum(int(s.get("earnedRuns", 0)) for s in recent)
            total_ip = sum(_parse_ip(s.get("inningsPitched", "0")) for s in recent)
            total_hits = sum(int(s.get("hits", 0)) for s in recent)
            total_bb = sum(int(s.get("baseOnBalls", 0)) for s in recent)
            if total_ip > 0:
                recent_era = round((total_er / total_ip) * 9, 2)
                recent_whip = round((total_hits + total_bb) / total_ip, 2)
    except Exception as exc:
        print(f"[mlb_api] pitcher recent form failed for {mlb_id}: {exc}")

    # Blend: 60% recent + 40% season
    eff_era = era
    eff_whip = whip
    if recent_era is not None and era is not None:
        eff_era = round(recent_era * 0.6 + era * 0.4, 2)
    elif recent_era is not None:
        eff_era = recent_era
    if recent_whip is not None and whip is not None:
        eff_whip = round(recent_whip * 0.6 + whip * 0.4, 2)
    elif recent_whip is not None:
        eff_whip = recent_whip

    return {
        "throws": throws,
        "era": era,
        "whip": whip,
        "recent_era": recent_era,
        "recent_whip": recent_whip,
        "eff_era": eff_era,
        "eff_whip": eff_whip,
    }


async def enrich_pitchers(pitchers: dict[str, dict]) -> dict[str, dict]:
    """Fetch throws/ERA/WHIP/recent form for all pitchers in parallel."""
    async def _enrich_one(abbr: str, info: dict) -> tuple[str, dict]:
        if info.get("mlb_id"):
            try:
                details = await get_pitcher_details(info["mlb_id"])
                return abbr, {**info, **details}
            except Exception as exc:
                print(f"[mlb_api] enrich_pitchers: failed for {abbr}: {exc}")
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
        mlb_id = people[0].get("id") if people else None
        if mlb_id is None:
            print(f"[mlb_api] search_player: no result for '{name}' (status={resp.status_code})")
        return mlb_id
    except Exception as exc:
        print(f"[mlb_api] search_player: exception for '{name}': {exc}")
        return None


def _parse_stat_splits(stat_list: list) -> dict:
    """Parse a MLB /stats direct endpoint response list into vL, vR, home_avg, away_avg."""
    vl_avg = vr_avg = home_avg = away_avg = ops = None
    for stat_group in stat_list:
        for split in stat_group.get("splits", []):
            code = split.get("split", {}).get("code", "")
            s = split.get("stat", {})
            avg_str = s.get("avg")
            if avg_str:
                try:
                    avg = float(avg_str)
                    if code == "vl":
                        vl_avg = avg
                    elif code == "vr":
                        vr_avg = avg
                    elif code == "h":
                        home_avg = avg
                    elif code == "a":
                        away_avg = avg
                except (ValueError, TypeError):
                    pass
            # season OPS lives here when stats=season is used
            ops_str = s.get("ops")
            if ops_str and not split.get("split", {}).get("code"):
                try:
                    ops = float(ops_str)
                except (ValueError, TypeError):
                    pass
    return {"vL": vl_avg, "vR": vr_avg, "home_avg": home_avg, "away_avg": away_avg, "ops": ops}


async def _get_stats_direct(
    client: httpx.AsyncClient, mlb_id: int, stats_type: str, season: int,
    extra_params: dict | None = None,
) -> list:
    """Call /people/{id}/stats directly. Returns the stats list or []."""
    params = {"stats": stats_type, "group": "hitting", "season": season}
    if extra_params:
        params.update(extra_params)
    resp = await client.get(
        f"{MLB_BASE}/people/{mlb_id}/stats",
        params=params,
        timeout=12.0,
    )
    resp.raise_for_status()
    return resp.json().get("stats", [])


async def get_hitter_details(mlb_id: int, season: int | None = None) -> dict:
    """Get batter's bat side, L/R splits, home/away splits, season OPS, and recent 7-day avg.

    Uses /people/{id}/stats (direct endpoint) instead of hydrate — more reliable.
    Before June 1: falls back to prior-season data when current season has none yet.
    """
    today = date.today()
    season = season or today.year
    use_fallback = today < date(today.year, 6, 1)

    async with httpx.AsyncClient() as client:
        # bat side comes from the person record (not a stat)
        # statSplits requires sitCodes — without them the API returns empty splits
        person_resp, splits_stats, ha_stats, season_stats, gamelog_stats = await asyncio.gather(
            client.get(f"{MLB_BASE}/people/{mlb_id}", timeout=10.0),
            _get_stats_direct(client, mlb_id, "statSplits", season, {"sitCodes": "vl,vr"}),
            _get_stats_direct(client, mlb_id, "statSplits", season, {"sitCodes": "h,a"}),
            _get_stats_direct(client, mlb_id, "season", season),
            _get_stats_direct(client, mlb_id, "gameLog", season),
            return_exceptions=True,
        )

    # --- Bat side ---
    bats = "R"
    try:
        person_resp.raise_for_status()
        bats = person_resp.json().get("people", [{}])[0].get("batSide", {}).get("code", "R")
    except Exception:
        pass

    # --- Current season splits ---
    splits_data = _parse_stat_splits(splits_stats if not isinstance(splits_stats, Exception) else [])
    ha_data = _parse_stat_splits(ha_stats if not isinstance(ha_stats, Exception) else [])
    vl_avg = splits_data["vL"]
    vr_avg = splits_data["vR"]
    home_avg = ha_data["home_avg"]
    away_avg = ha_data["away_avg"]

    # --- OPS from season stats ---
    ops = None
    if not isinstance(season_stats, Exception):
        for sg in season_stats:
            for split in sg.get("splits", []):
                ops_str = split.get("stat", {}).get("ops")
                if ops_str:
                    try:
                        ops = float(ops_str)
                    except (ValueError, TypeError):
                        pass
                    break
            if ops is not None:
                break

    # --- Prior-season fallback (early in the year — before June 1) ---
    if use_fallback and vl_avg is None and vr_avg is None:
        try:
            async with httpx.AsyncClient() as prior_client:
                prior_splits, prior_ha, prior_season = await asyncio.gather(
                    _get_stats_direct(prior_client, mlb_id, "statSplits", season - 1, {"sitCodes": "vl,vr"}),
                    _get_stats_direct(prior_client, mlb_id, "statSplits", season - 1, {"sitCodes": "h,a"}),
                    _get_stats_direct(prior_client, mlb_id, "season", season - 1),
                    return_exceptions=True,
                )
            if not isinstance(prior_splits, Exception):
                prior_data = _parse_stat_splits(prior_splits)
                prior_ha_data = _parse_stat_splits(prior_ha if not isinstance(prior_ha, Exception) else [])
                vl_avg = prior_data["vL"]
                vr_avg = prior_data["vR"]
                home_avg = prior_ha_data["home_avg"]
                away_avg = prior_ha_data["away_avg"]
                print(f"[mlb_api] using {season - 1} splits for player {mlb_id}: vL={vl_avg} vR={vr_avg}")
            if ops is None and not isinstance(prior_season, Exception):
                for sg in prior_season:
                    for split in sg.get("splits", []):
                        ops_str = split.get("stat", {}).get("ops")
                        if ops_str:
                            try:
                                ops = float(ops_str)
                            except (ValueError, TypeError):
                                pass
                            break
                    if ops is not None:
                        break
        except Exception as exc:
            print(f"[mlb_api] prior-season fallback failed for {mlb_id}: {exc}")
    else:
        print(f"[mlb_api] {season} splits for player {mlb_id}: vL={vl_avg} vR={vr_avg} OPS={ops}")

    # --- Recent 7-day batting average ---
    recent_avg = None
    try:
        if not isinstance(gamelog_stats, Exception):
            cutoff = today - timedelta(days=7)
            hits = at_bats = 0
            for sg in gamelog_stats:
                for split in sg.get("splits", []):
                    game_date_str = split.get("date", "")
                    try:
                        game_dt = date.fromisoformat(game_date_str)
                    except (ValueError, TypeError):
                        continue
                    if game_dt >= cutoff:
                        stat = split.get("stat", {})
                        hits += int(stat.get("hits", 0))
                        at_bats += int(stat.get("atBats", 0))
            if at_bats >= 5:
                recent_avg = round(hits / at_bats, 3)
    except Exception:
        pass

    return {
        "bats": bats,
        "vL": vl_avg,
        "vR": vr_avg,
        "home_avg": home_avg,
        "away_avg": away_avg,
        "ops": ops,
        "recent_avg": recent_avg,
    }


async def get_career_vs_pitcher(hitter_id: int, pitcher_id: int) -> dict | None:
    """Return {avg, pa} for hitter's career stats vs this pitcher. None if < 10 PA."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{MLB_BASE}/people/{hitter_id}/stats",
                params={
                    "stats": "vsPlayer",
                    "opposingPlayerId": pitcher_id,
                    "group": "hitting",
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
        for sg in data.get("stats", []):
            for split in sg.get("splits", []):
                stat = split.get("stat", {})
                pa = int(stat.get("plateAppearances", 0))
                avg_str = stat.get("avg")
                if pa >= 10 and avg_str:
                    return {"avg": float(avg_str), "pa": pa}
        return None
    except Exception:
        return None
