import asyncio
from datetime import date, timedelta
import httpx

MLB_BASE = "https://statsapi.mlb.com/api/v1"

# In-memory cache: Yahoo player name → MLB player ID (or None if not found).
# Persists for the lifetime of the server process — player name/ID mappings
# don't change, so this is always safe to cache indefinitely.
_player_id_cache: dict[str, int | None] = {}

# Semaphore to cap concurrent outbound MLB Stats API calls.
# Without this, 16 players × 5 stat calls = 80 simultaneous requests → API throttles.
# 15 concurrent calls keeps load manageable while still being fast.
_mlb_semaphore: asyncio.Semaphore | None = None

def _get_semaphore() -> asyncio.Semaphore:
    global _mlb_semaphore
    if _mlb_semaphore is None:
        _mlb_semaphore = asyncio.Semaphore(15)
    return _mlb_semaphore


async def _mlb_get(client: httpx.AsyncClient, url: str, **kwargs) -> httpx.Response:
    """Thin wrapper around client.get that respects the MLB API concurrency limit."""
    async with _get_semaphore():
        return await client.get(url, **kwargs)

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
        resp = await _mlb_get(client,
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


async def get_confirmed_lineups(game_date: date | None = None) -> dict[str, set[int]]:
    """Fetch confirmed starting lineups from MLB schedule API.

    Returns {team_abbr: {mlb_player_id, ...}} for teams whose lineups have been posted.
    Teams with no lineup posted are absent from the dict (not an empty set).
    """
    date_str = (game_date or date.today()).strftime("%Y-%m-%d")
    try:
        async with httpx.AsyncClient() as client:
            resp = await _mlb_get(client,
                f"{MLB_BASE}/schedule",
                params={"sportId": 1, "date": date_str, "hydrate": "lineups,team"},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        print(f"[mlb_api] get_confirmed_lineups failed: {exc}")
        return {}

    lineups: dict[str, set[int]] = {}
    for date_entry in data.get("dates", []):
        for game in date_entry.get("games", []):
            game_lineups = game.get("lineups")
            if not game_lineups:
                continue
            home_abbr = game.get("teams", {}).get("home", {}).get("team", {}).get("abbreviation", "")
            away_abbr = game.get("teams", {}).get("away", {}).get("team", {}).get("abbreviation", "")
            home_players = game_lineups.get("homePlayers", [])
            away_players = game_lineups.get("awayPlayers", [])
            if home_abbr and home_players:
                lineups[home_abbr] = {p.get("id") for p in home_players if p.get("id")}
            if away_abbr and away_players:
                lineups[away_abbr] = {p.get("id") for p in away_players if p.get("id")}
    return lineups


def _parse_ip(ip_str) -> float:
    """Convert MLB innings-pitched string '6.1' → 6.333 actual innings."""
    try:
        parts = str(ip_str).split(".")
        whole = int(parts[0])
        outs = int(parts[1]) if len(parts) > 1 else 0
        return whole + outs / 3
    except (ValueError, TypeError):
        return 0.0


MIN_PITCHER_STARTS = 4          # below this we don't trust current-season ERA/WHIP
LEAGUE_AVG_ERA    = 4.20        # fallback for pitchers with no usable history
LEAGUE_AVG_WHIP   = 1.28


async def get_pitcher_details(mlb_id: int, season: int | None = None) -> dict:
    """Get pitcher's throws (L/R), season ERA/WHIP, and recent 3-start blended ERA/WHIP.

    Small-sample guard: if the pitcher has fewer than MIN_PITCHER_STARTS (4) this
    season, fall back to prior-year full-season ERA/WHIP.  If no prior-year data
    exists either (true rookie or no MLB history), use league-average values so a
    0.71 ERA from one April start doesn't make every hitter look like they're
    facing Sandy Koufax.
    """
    season = season or date.today().year
    async with httpx.AsyncClient() as client:
        season_resp, gamelog_resp = await asyncio.gather(
            _mlb_get(client,
                f"{MLB_BASE}/people/{mlb_id}",
                params={"hydrate": f"stats(group=pitching,type=season,season={season})"},
                timeout=10.0,
            ),
            _mlb_get(client,
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
    season_gs = 0   # games started this season
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
            try:
                season_gs = int(stat.get("gamesStarted", 0))
            except (ValueError, TypeError):
                pass
            break

    # --- Small-sample guard: < 4 starts → fall back to prior year ---
    if season_gs < MIN_PITCHER_STARTS:
        try:
            async with httpx.AsyncClient() as prior_client:
                prior_resp = await _mlb_get(prior_client,
                    f"{MLB_BASE}/people/{mlb_id}",
                    params={"hydrate": f"stats(group=pitching,type=season,season={season - 1})"},
                    timeout=10.0,
                )
                prior_resp.raise_for_status()
                prior_person = prior_resp.json().get("people", [{}])[0]
                prior_era = prior_whip = None
                for sg in prior_person.get("stats", []):
                    sp = sg.get("splits", [])
                    if sp:
                        st = sp[0].get("stat", {})
                        try:
                            prior_era = float(st["era"])
                        except (KeyError, ValueError, TypeError):
                            pass
                        try:
                            prior_whip = float(st["whip"])
                        except (KeyError, ValueError, TypeError):
                            pass
                        break
                if prior_era is not None:
                    era = prior_era
                    print(f"[mlb_api] pitcher {mlb_id}: {season_gs} starts, using {season-1} ERA {era}")
                if prior_whip is not None:
                    whip = prior_whip
        except Exception as exc:
            print(f"[mlb_api] pitcher {mlb_id} prior-year fallback failed: {exc}")

        # If still no data (true rookie / no MLB history) use league averages
        if era is None:
            era = LEAGUE_AVG_ERA
            print(f"[mlb_api] pitcher {mlb_id}: no history, using league-avg ERA {era}")
        if whip is None:
            whip = LEAGUE_AVG_WHIP

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
        # Only use recent-start blend if pitcher has enough starts to be meaningful
        if recent and season_gs >= MIN_PITCHER_STARTS:
            total_er = sum(int(s.get("earnedRuns", 0)) for s in recent)
            total_ip = sum(_parse_ip(s.get("inningsPitched", "0")) for s in recent)
            total_hits = sum(int(s.get("hits", 0)) for s in recent)
            total_bb = sum(int(s.get("baseOnBalls", 0)) for s in recent)
            if total_ip > 0:
                recent_era = round((total_er / total_ip) * 9, 2)
                recent_whip = round((total_hits + total_bb) / total_ip, 2)
    except Exception as exc:
        print(f"[mlb_api] pitcher recent form failed for {mlb_id}: {exc}")

    # Blend: 60% recent + 40% season (only when recent is available)
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


def _normalize_name(name: str) -> str:
    """Strip accents, suffixes, and extra whitespace for loose name matching."""
    import unicodedata
    # Normalize unicode → ASCII (strips accents)
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = nfkd.encode("ascii", "ignore").decode("ascii")
    # Remove generational suffixes
    import re
    ascii_name = re.sub(r"\b(Jr\.?|Sr\.?|II|III|IV)\b", "", ascii_name, flags=re.IGNORECASE)
    return " ".join(ascii_name.split()).strip()


async def _search_mlb(client: httpx.AsyncClient, query: str) -> list:
    resp = await _mlb_get(client,
        f"{MLB_BASE}/people/search",
        params={"names": query, "sportId": 1},
        timeout=10.0,
    )
    resp.raise_for_status()
    return resp.json().get("people", [])


async def search_player(name: str, team_abbr: str | None = None) -> int | None:
    """Look up MLB player ID by full name.

    Results are cached in _player_id_cache for the lifetime of the process —
    name→ID mappings never change, so subsequent loads are instant.

    Search strategy (at most 2 parallel HTTP calls):
    1. Exact name
    2. Normalized name (accents stripped, suffixes removed) — only if different
    Logs all misses so they show up in Render logs.
    """
    cache_key = name.lower().strip()
    if cache_key in _player_id_cache:
        return _player_id_cache[cache_key]

    try:
        normalized = _normalize_name(name)
        queries: list[str] = [name]
        if normalized.lower() != name.lower():
            queries.append(normalized)

        async with httpx.AsyncClient() as client:
            results = await asyncio.gather(
                *[_search_mlb(client, q) for q in queries],
                return_exceptions=True,
            )

        mlb_id: int | None = None

        r0 = results[0] if not isinstance(results[0], Exception) else []
        if r0:
            mlb_id = r0[0].get("id")
        elif len(results) > 1:
            r1 = results[1] if not isinstance(results[1], Exception) else []
            if r1:
                mlb_id = r1[0].get("id")
                print(f"[mlb_api] search_player: matched '{name}' via normalized '{normalized}'")

        if mlb_id is None:
            print(f"[mlb_api] search_player: NO MATCH for '{name}' (team={team_abbr})")

        _player_id_cache[cache_key] = mlb_id
        return mlb_id
    except Exception as exc:
        print(f"[mlb_api] search_player: exception for '{name}': {exc}")
        return None


def _parse_stat_splits(stat_list: list) -> dict:
    """Parse a MLB /stats direct endpoint response list into vL, vR, home_avg, away_avg.

    Also returns vL_ab / vR_ab so callers can enforce a minimum sample size.
    """
    vl_avg = vr_avg = home_avg = away_avg = ops = None
    vl_ab = vr_ab = 0
    for stat_group in stat_list:
        for split in stat_group.get("splits", []):
            code = split.get("split", {}).get("code", "")
            s = split.get("stat", {})
            avg_str = s.get("avg")
            if avg_str:
                try:
                    avg = float(avg_str)
                    ab = int(s.get("atBats", 0))
                    if code == "vl":
                        vl_avg = avg
                        vl_ab = ab
                    elif code == "vr":
                        vr_avg = avg
                        vr_ab = ab
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
    return {
        "vL": vl_avg, "vR": vr_avg,
        "home_avg": home_avg, "away_avg": away_avg,
        "ops": ops,
        "vL_ab": vl_ab, "vR_ab": vr_ab,
    }


async def _get_stats_direct(
    client: httpx.AsyncClient, mlb_id: int, stats_type: str, season: int,
    extra_params: dict | None = None,
) -> list:
    """Call /people/{id}/stats directly. Returns the stats list or []."""
    params = {"stats": stats_type, "group": "hitting", "season": season}
    if extra_params:
        params.update(extra_params)
    resp = await _mlb_get(client,
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
            _mlb_get(client, f"{MLB_BASE}/people/{mlb_id}", timeout=10.0),
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

    # --- OPS and counting stats from season stats ---
    ops = None
    season_avg = None
    season_hr = None
    season_rbi = None
    season_r = None
    season_sb = None
    games_played = 0
    if not isinstance(season_stats, Exception):
        for sg in season_stats:
            for split in sg.get("splits", []):
                stat = split.get("stat", {})
                ops_str = stat.get("ops")
                if ops_str:
                    try:
                        ops = float(ops_str)
                    except (ValueError, TypeError):
                        pass
                avg_str = stat.get("avg")
                if avg_str:
                    try:
                        season_avg = float(avg_str)
                    except (ValueError, TypeError):
                        pass
                try:
                    season_hr = int(stat["homeRuns"])
                except (KeyError, ValueError, TypeError):
                    pass
                try:
                    season_rbi = int(stat["rbi"])
                except (KeyError, ValueError, TypeError):
                    pass
                try:
                    season_r = int(stat["runs"])
                except (KeyError, ValueError, TypeError):
                    pass
                try:
                    season_sb = int(stat["stolenBases"])
                except (KeyError, ValueError, TypeError):
                    pass
                try:
                    games_played = int(stat["gamesPlayed"])
                except (KeyError, ValueError, TypeError):
                    pass
                break
            if ops is not None or season_avg is not None:
                break

    # Projected 162-game pace (only if games_played >= 10)
    proj_hr = round(season_hr * 162.0 / games_played, 1) if games_played >= 10 and season_hr is not None else None
    proj_rbi = round(season_rbi * 162.0 / games_played, 1) if games_played >= 10 and season_rbi is not None else None
    proj_r = round(season_r * 162.0 / games_played, 1) if games_played >= 10 and season_r is not None else None
    proj_sb = round(season_sb * 162.0 / games_played, 1) if games_played >= 10 and season_sb is not None else None

    # --- Prior-season fallback (early in the year — before June 1) ---
    # Also fall back when current-year sample is too small to trust (< 15 AB vs either hand).
    MIN_SPLIT_AB = 15
    thin_sample = (
        splits_data.get("vL_ab", 0) < MIN_SPLIT_AB or
        splits_data.get("vR_ab", 0) < MIN_SPLIT_AB
    )
    prior_hr = prior_rbi = prior_r = prior_sb = prior_season_avg = None
    if use_fallback and (vl_avg is None or vr_avg is None or thin_sample):
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
            if not isinstance(prior_season, Exception):
                for sg in prior_season:
                    for split in sg.get("splits", []):
                        stat = split.get("stat", {})
                        ops_str = stat.get("ops")
                        if ops_str and ops is None:
                            try:
                                ops = float(ops_str)
                            except (ValueError, TypeError):
                                pass
                        avg_str = stat.get("avg")
                        if avg_str:
                            try:
                                prior_season_avg = float(avg_str)
                            except (ValueError, TypeError):
                                pass
                        try:
                            prior_hr = int(stat["homeRuns"])
                        except (KeyError, ValueError, TypeError):
                            pass
                        try:
                            prior_rbi = int(stat["rbi"])
                        except (KeyError, ValueError, TypeError):
                            pass
                        try:
                            prior_r = int(stat["runs"])
                        except (KeyError, ValueError, TypeError):
                            pass
                        try:
                            prior_sb = int(stat["stolenBases"])
                        except (KeyError, ValueError, TypeError):
                            pass
                        break
        except Exception as exc:
            print(f"[mlb_api] prior-season fallback failed for {mlb_id}: {exc}")

        # If early season and games_played < 30, use prior year actuals as projection base
        if games_played < 30:
            if prior_hr is not None:
                proj_hr = float(prior_hr)
            if prior_rbi is not None:
                proj_rbi = float(prior_rbi)
            if prior_r is not None:
                proj_r = float(prior_r)
            if prior_sb is not None:
                proj_sb = float(prior_sb)
            if prior_season_avg is not None and season_avg is None:
                season_avg = prior_season_avg
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
        "season_avg": season_avg,
        "proj_hr": proj_hr,
        "proj_rbi": proj_rbi,
        "proj_r": proj_r,
        "proj_sb": proj_sb,
    }


async def get_career_vs_pitcher(hitter_id: int, pitcher_id: int) -> dict | None:
    """Return {avg, pa} for hitter's career stats vs this pitcher. None if < 10 PA."""
    try:
        async with httpx.AsyncClient() as client:
            resp = await _mlb_get(client,
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
