# advisor.py
from dataclasses import dataclass, field

POSITION_ORDER = {
    "C": 0, "1B": 1, "2B": 2, "3B": 3, "SS": 4,
    "CI": 5, "MI": 6, "OF": 7, "Util": 8, "UTIL": 8,
    "BN": 99, "IL": 100, "IL+": 101,
}

# League-average stat baselines by position, used when a player has no data
# (rookies, players with tiny samples, first call-up of the season).
# Values represent a typical full-season contribution at that spot.
_POSITION_DEFAULTS: dict[str, dict] = {
    "C":    {"avg": 0.245, "hr": 14, "rbi": 58, "r": 52, "sb": 4},
    "1B":   {"avg": 0.265, "hr": 25, "rbi": 90, "r": 80, "sb": 4},
    "2B":   {"avg": 0.260, "hr": 16, "rbi": 68, "r": 75, "sb": 12},
    "3B":   {"avg": 0.260, "hr": 22, "rbi": 80, "r": 76, "sb": 6},
    "SS":   {"avg": 0.262, "hr": 17, "rbi": 68, "r": 78, "sb": 14},
    "OF":   {"avg": 0.262, "hr": 20, "rbi": 72, "r": 78, "sb": 12},
    "LF":   {"avg": 0.262, "hr": 20, "rbi": 72, "r": 78, "sb": 12},
    "CF":   {"avg": 0.262, "hr": 16, "rbi": 65, "r": 80, "sb": 20},
    "RF":   {"avg": 0.265, "hr": 22, "rbi": 78, "r": 78, "sb": 8},
    "DH":   {"avg": 0.260, "hr": 22, "rbi": 82, "r": 75, "sb": 4},
}
_DEFAULT_STATS = {"avg": 0.258, "hr": 18, "rbi": 70, "r": 72, "sb": 10}  # generic MLB avg


def _pos_defaults(position: str) -> dict:
    """Return baseline stats for a position, falling back to generic MLB average."""
    pos = position.upper().split("/")[0].strip()  # handle "LF/CF" → "LF"
    return _POSITION_DEFAULTS.get(pos, _DEFAULT_STATS)


@dataclass
class PitcherInfo:
    name: str
    throws: str           # "L" or "R"
    era: float | None
    whip: float | None
    recent_era: float | None = None   # last 3 starts
    recent_whip: float | None = None
    eff_era: float | None = None      # 60% recent + 40% season blend
    eff_whip: float | None = None


@dataclass
class HitterInfo:
    player_id: str
    full_name: str
    team_abbr: str
    position: str
    selected_position: str
    bats: str | None          # "L", "R", "S"
    splits: dict              # {"vL", "vR", "home_avg", "away_avg"}
    pitcher: PitcherInfo | None
    ops: float | None = None
    recent_avg: float | None = None
    career_vs_pitcher: dict | None = None   # {"avg": float, "pa": int} or None
    park_factor: float = 1.0
    is_home: bool = False
    confirmed_start: bool | None = None    # True/False when lineup is posted, None when unknown
    confirmed_sit: bool | None = None
    season_avg: float | None = None
    proj_hr: float | None = None
    proj_rbi: float | None = None
    proj_r: float | None = None
    proj_sb: float | None = None


def is_ace(pitcher: PitcherInfo) -> bool:
    eff_era = pitcher.eff_era if pitcher.eff_era is not None else pitcher.era
    eff_whip = pitcher.eff_whip if pitcher.eff_whip is not None else pitcher.whip
    if eff_era is not None and eff_era < 3.00:
        return True
    if eff_whip is not None and eff_whip < 1.00:
        return True
    return False


def score_hitter(hitter: HitterInfo) -> tuple[float, dict]:
    """
    Score = 65% hitter quality + 25% matchup quality + 10% context.
    All components normalized to 0–1 before weighting so weights are honest.
    Returns (score, breakdown).
    """
    if hitter.pitcher is None:
        return 0.0, {}
    p = hitter.pitcher

    def norm(val, floor, ceiling, default=0.5):
        if val is None:
            return default
        return max(0.0, min(1.0, (val - floor) / (ceiling - floor)))

    # ── Hitter quality (65%) ────────────────────────────────────────────────
    # 5-category weights: BA 1.0, HR 1.0, RBI 1.0, R 1.0, SB 0.5 → total 4.5
    # Ranges calibrated to realistic MLB player distribution.
    # When a player has no data (rookie, no prior year), fall back to
    # position-based league averages rather than a neutral 0.5.
    pos_def = _pos_defaults(hitter.position)
    ba_norm  = norm(hitter.season_avg if hitter.season_avg is not None else pos_def["avg"], 0.220, 0.340)
    hr_norm  = norm(hitter.proj_hr    if hitter.proj_hr    is not None else pos_def["hr"],  5,     45)
    rbi_norm = norm(hitter.proj_rbi   if hitter.proj_rbi   is not None else pos_def["rbi"], 30,    115)
    r_norm   = norm(hitter.proj_r     if hitter.proj_r     is not None else pos_def["r"],   40,    115)
    sb_norm  = norm(hitter.proj_sb    if hitter.proj_sb    is not None else pos_def["sb"],  0,     55)
    hitter_quality = (ba_norm + hr_norm + rbi_norm + r_norm + sb_norm * 0.5) / 4.5

    # ── Matchup quality (25%) ───────────────────────────────────────────────
    # Split avg vs this pitcher's handedness — use career vs pitcher if available
    if hitter.career_vs_pitcher is not None:
        split_avg = hitter.career_vs_pitcher["avg"]
        split_source = f"career vs pitcher ({hitter.career_vs_pitcher['pa']} PA)"
    else:
        throws = p.throws
        split_avg = hitter.splits.get("vL") if throws == "L" else hitter.splits.get("vR")
        split_source = ("vL" if throws == "L" else "vR") if split_avg is not None else "default"

    # Blend home/away context into split (70/30)
    home_away_avg = hitter.splits.get("home_avg" if hitter.is_home else "away_avg")
    if split_avg is not None and home_away_avg is not None:
        split_avg = split_avg * 0.7 + home_away_avg * 0.3
    elif home_away_avg is not None and split_avg is None:
        split_avg = home_away_avg
        split_source = "home/away avg"

    split_norm = norm(split_avg, 0.200, 0.320)

    # Pitcher ERA/WHIP: higher value = worse pitcher = BETTER for hitter → higher norm
    # (direction is now correct: ace ERA 2.50 → 0.0, bad pitcher ERA 6.00 → 1.0)
    era_val  = p.eff_era  if p.eff_era  and p.eff_era  > 0 else (p.era  if p.era  and p.era  > 0 else None)
    whip_val = p.eff_whip if p.eff_whip and p.eff_whip > 0 else (p.whip if p.whip and p.whip > 0 else None)
    era_norm  = norm(era_val,  2.50, 6.00, default=0.55)   # ~league-avg default
    whip_norm = norm(whip_val, 0.85, 1.65, default=0.55)

    matchup_quality = split_norm * 0.60 + era_norm * 0.20 + whip_norm * 0.20

    # ── Context (10%) ───────────────────────────────────────────────────────
    if hitter.recent_avg is not None:
        if hitter.recent_avg >= 0.300:
            streak_score = 0.75
        elif hitter.recent_avg <= 0.180:
            streak_score = 0.25
        else:
            streak_score = 0.50
    else:
        streak_score = 0.50

    # Park factor already in 0.92–1.08 range; normalize to 0–1
    park_score = norm(hitter.park_factor, 0.92, 1.08)
    context = streak_score * 0.60 + park_score * 0.40

    # ── Final score ─────────────────────────────────────────────────────────
    score = hitter_quality * 0.65 + matchup_quality * 0.25 + context * 0.10

    breakdown = {
        "hitter_quality": round(hitter_quality, 3),
        "ba_norm": round(ba_norm, 3),
        "hr_norm": round(hr_norm, 3),
        "rbi_norm": round(rbi_norm, 3),
        "r_norm": round(r_norm, 3),
        "sb_norm": round(sb_norm, 3),
        "matchup_quality": round(matchup_quality, 3),
        "split_avg_used": round(split_avg, 3) if split_avg is not None else None,
        "split_source": split_source,
        "split_norm": round(split_norm, 3),
        "era_used": round(era_val, 2) if era_val is not None else None,
        "era_norm": round(era_norm, 3),
        "whip_used": round(whip_val, 2) if whip_val is not None else None,
        "whip_norm": round(whip_norm, 3),
        "context": round(context, 3),
        "streak_score": round(streak_score, 2),
        "park_score": round(park_score, 3),
        "total": round(score, 4),
    }

    return score, breakdown


def get_matchup_quality(hitter: HitterInfo) -> str:
    if hitter.pitcher is None:
        return "ok"
    p = hitter.pitcher
    # Prefer career vs pitcher, then L/R split
    if hitter.career_vs_pitcher is not None:
        avg = hitter.career_vs_pitcher["avg"]
    else:
        avg = hitter.splits.get("vL") if p.throws == "L" else hitter.splits.get("vR")
    if avg is None:
        return "ok"
    if avg >= 0.270:
        return "good"
    if avg <= 0.230:
        return "bad"
    return "ok"


def recommend(score: float) -> str:
    """
    Absolute thresholds calibrated against 0–1 normalized score:
      Start: >= 0.55  (above-average player or good matchup)
      Sit:   <= 0.35  (below-average player with tough matchup)
      Flex:  everything between
    """
    if score >= 0.55:
        return "Start"
    if score <= 0.35:
        return "Sit"
    return "Flex"


def build_reason(hitter: HitterInfo, rec: str, ace: bool) -> str:
    """One-line explanation of the recommendation."""
    if hitter.pitcher is None:
        return "No game scheduled today"
    p = hitter.pitcher

    parts = []

    # Hot/cold form
    if hitter.recent_avg is not None:
        avg_str = f".{int(round(hitter.recent_avg * 1000)):03d}"
        if hitter.recent_avg >= 0.300:
            parts.append(f"hot streak ({avg_str} L7)")
        elif hitter.recent_avg <= 0.180:
            parts.append(f"slumping ({avg_str} L7)")

    # Best available split
    if hitter.career_vs_pitcher is not None:
        avg_str = f".{int(round(hitter.career_vs_pitcher['avg'] * 1000)):03d}"
        parts.append(f"{avg_str} career vs pitcher ({hitter.career_vs_pitcher['pa']} PA)")
    else:
        throws = p.throws
        split_avg = hitter.splits.get("vL") if throws == "L" else hitter.splits.get("vR")
        side = "vL" if throws == "L" else "vR"
        if split_avg is not None:
            avg_str = f".{int(round(split_avg * 1000)):03d}"
            parts.append(f"{avg_str} {side}")

    # Pitcher quality
    if ace:
        parts.append("facing ace")
    else:
        eff = p.eff_era or p.era
        if eff is not None:
            parts.append(f"opp ERA {eff:.2f}")

    # Park factor
    if hitter.park_factor >= 1.05:
        parts.append(f"hitter-friendly park (+{int(round((hitter.park_factor - 1) * 100))}%)")
    elif hitter.park_factor <= 0.95:
        parts.append(f"pitcher-friendly park ({int(round((hitter.park_factor - 1) * 100))}%)")

    if not parts:
        parts.append("no split data available")
    return ", ".join(parts)


def advise_roster(hitters: list[HitterInfo]) -> list[dict]:
    score_results = {h.player_id: score_hitter(h) for h in hitters}
    scores = {pid: sr[0] for pid, sr in score_results.items()}
    breakdowns = {pid: sr[1] for pid, sr in score_results.items()}

    result = []
    for h in hitters:
        p = h.pitcher
        ace = is_ace(p) if p else False

        # No game → always Sit immediately, before any scoring
        if p is None:
            rec = "Sit"
        else:
            score = scores[h.player_id]
            rec = recommend(score)

        # Confirmed lineup scratch overrides everything
        if h.confirmed_sit:
            rec = "Sit"

        score = scores[h.player_id]
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
                "eff_era": p.eff_era,
                "eff_whip": p.eff_whip,
                "is_ace": ace,
            } if p else None,
            "splits": h.splits,
            "matchup_quality": get_matchup_quality(h),
            "score": round(score, 4),
            "recommendation": rec,
            "reason": build_reason(h, rec, ace),
            "score_breakdown": breakdowns[h.player_id],
            "recent_avg": h.recent_avg,
            "ops": h.ops,
            "career_vs_pitcher": h.career_vs_pitcher,
            "park_factor": round(h.park_factor, 2),
            "is_home": h.is_home,
            "confirmed_start": h.confirmed_start,
            "confirmed_sit": h.confirmed_sit,
        })

    result.sort(key=lambda x: (
        POSITION_ORDER.get(x["selected_position"], 9),
        -x["score"],
    ))
    return result
