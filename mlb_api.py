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
        return people[0].get("id") if people else None
    except Exception:
        return None


async def _fetch_split_avgs(
    client: httpx.AsyncClient, mlb_id: int, season: int
) -> tuple[float | None, float | None]:
    """Return (vL_avg, vR_avg) for the given season. Used for prior-year fallback."""
    resp = await client.get(
        f"{MLB_BASE}/people/{mlb_id}",
        params={"hydrate": f"stats(group=hitting,type=statSplits,season={season})"},
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    person = data.get("people", [{}])[0]
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
    return vl_avg, vr_avg


def _parse_splits_from_person(person: dict) -> dict:
    """Extract vL, vR, home_avg, away_avg from a MLB statSplits response person object."""
    vl_avg = vr_avg = home_avg = away_avg = None
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
                    elif code == "h":
                        home_avg = avg
                    elif code == "a":
                        away_avg = avg
                except (ValueError, TypeError):
                    pass
    return {"vL": vl_avg, "vR": vr_avg, "home_avg": home_avg, "away_avg": away_avg}


def _parse_ops_from_person(person: dict) -> float | None:
    """Extract season OPS from a MLB season stats response person object."""
    for sg in person.get("stats", []):
        splits = sg.get("splits", [])
        if splits:
            ops_str = splits[0].get("stat", {}).get("ops")
            if ops_str:
                try:
                    return float(ops_str)
                except (ValueError, TypeError):
                    pass
    return None


async def get_hitter_details(mlb_id: int, season: int | None = None) -> dict:
    """Get batter's bat side, L/R splits, home/away splits, season OPS, and recent 7-day avg.

    Before June 1: falls back to prior-season L/R splits and OPS if current season has none.
    """
    today = date.today()
    season = season or today.year
    use_fallback = today < date(today.year, 6, 1)

    # Fetch current season data — three separate calls in parallel
    async with httpx.AsyncClient() as client:
        splits_resp, season_resp, gamelog_resp = await asyncio.gather(
            client.get(
                f"{MLB_BASE}/people/{mlb_id}",
                params={"hydrate": f"stats(group=hitting,type=statSplits,season={season})"},
                timeout=10.0,
            ),
            client.get(
                f"{MLB_BASE}/people/{mlb_id}",
                params={"hydrate": f"stats(group=hitting,type=season,season={season})"},
                timeout=10.0,
            ),
            client.get(
                f"{MLB_BASE}/people/{mlb_id}",
                params={"hydrate": f"stats(group=hitting,type=gameLog,season={season})"},
                timeout=10.0,
            ),
        )

    # --- Current season splits ---
    splits_resp.raise_for_status()
    person = splits_resp.json().get("people", [{}])[0]
    bats = person.get("batSide", {}).get("code", "R")
    split_data = _parse_splits_from_person(person)
    vl_avg = split_data["vL"]
    vr_avg = split_data["vR"]
    home_avg = split_data["home_avg"]
    away_avg = split_data["away_avg"]

    # --- Current season OPS ---
    ops = None
    try:
        season_resp.raise_for_status()
        sp = season_resp.json().get("people", [{}])[0]
        ops = _parse_ops_from_person(sp)
    except Exception:
        pass

    # Prior-season fallback (early in the year — before June 1)
    if use_fallback and (vl_avg is None and vr_avg is None):
        try:
            async with httpx.AsyncClient() as prior_client:
                prior_splits_resp, prior_ops_resp = await asyncio.gather(
                    prior_client.get(
                        f"{MLB_BASE}/people/{mlb_id}",
                        params={"hydrate": f"stats(group=hitting,type=statSplits,season={season - 1})"},
                        timeout=10.0,
                    ),
                    prior_client.get(
                        f"{MLB_BASE}/people/{mlb_id}",
                        params={"hydrate": f"stats(group=hitting,type=season,season={season - 1})"},
                        timeout=10.0,
                    ),
                )
            prior_splits_resp.raise_for_status()
            prior_person = prior_splits_resp.json().get("people", [{}])[0]
            prior_data = _parse_splits_from_person(prior_person)
            vl_avg = prior_data["vL"]
            vr_avg = prior_data["vR"]
            home_avg = prior_data["home_avg"]
            away_avg = prior_data["away_avg"]

            if ops is None:
                try:
                    prior_ops_resp.raise_for_status()
                    prior_sp = prior_ops_resp.json().get("people", [{}])[0]
                    ops = _parse_ops_from_person(prior_sp)
                except Exception:
                    pass
        except Exception as exc:
            print(f"[mlb_api] prior-season fallback failed for {mlb_id}: {exc}")

    # --- Recent 7-day batting average ---
    recent_avg = None
    try:
        gamelog_resp.raise_for_status()
        gl = gamelog_resp.json().get("people", [{}])[0]
        cutoff = today - timedelta(days=7)
        hits = at_bats = 0
        for sg in gl.get("stats", []):
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
        if at_bats >= 5:  # lower threshold since 7-day window is shorter
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
