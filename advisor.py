# advisor.py
from dataclasses import dataclass, field

# Hardcoded elite hitters who should never sit unless facing an ace while cold.
# Used as a fallback when OPS data isn't available (e.g. early season).
ELITE_PLAYERS = {
    "freddie freeman", "shohei ohtani", "aaron judge", "juan soto",
    "kyle tucker", "jazz chisholm", "mookie betts", "corey seager",
    "jose ramirez", "rafael devers", "vladimir guerrero jr", "vladimir guerrero",
    "bryce harper", "yordan alvarez", "gunnar henderson", "bobby witt jr",
    "elly de la cruz", "corbin carroll", "julio rodriguez", "steven kwan",
    "trea turner", "pete alonso", "michael harris ii", "michael harris",
    "william contreras", "adley rutschman", "cal raleigh",
}

POSITION_ORDER = {
    "C": 0, "1B": 1, "2B": 2, "3B": 3, "SS": 4,
    "CI": 5, "MI": 6, "OF": 7, "Util": 8, "UTIL": 8,
    "BN": 99, "IL": 100, "IL+": 101,
}


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
    Returns (score, breakdown) where breakdown shows each component's contribution.

    Weights:
      - Split avg (L/R vs pitcher hand, or career vs this pitcher): ×3.0
        → blended 70/30 with home/away avg when available
        → default .250 league-avg if no data
      - 1/effective ERA: ×2.0  (default assumes ERA 4.50)
      - 1/effective WHIP: ×1.5 (default assumes WHIP 1.30)
      - Hot/cold modifier: ±8% if last 7 days ≥.300 or ≤.180
      - Park factor: ±0–6% multiplier on total score
    """
    if hitter.pitcher is None:
        return 0.0, {}
    p = hitter.pitcher

    # Best available split avg
    split_source = "default"
    if hitter.career_vs_pitcher is not None:
        split_avg = hitter.career_vs_pitcher["avg"]
        split_source = f"career vs pitcher ({hitter.career_vs_pitcher['pa']} PA)"
    else:
        split_avg = (
            hitter.splits.get("vL") if p.throws == "L" else hitter.splits.get("vR")
        )
        split_source = ("vL" if p.throws == "L" else "vR") if split_avg is not None else "default (.250)"

    # Blend home/away context (70% L/R, 30% home/away)
    home_away_avg = hitter.splits.get("home_avg" if hitter.is_home else "away_avg")
    if split_avg is not None and home_away_avg is not None:
        split_avg = split_avg * 0.7 + home_away_avg * 0.3
    elif home_away_avg is not None and split_avg is None:
        split_avg = home_away_avg
        split_source = "home/away avg"

    final_split = split_avg if split_avg is not None else 0.250

    # Effective ERA/WHIP (60% recent 3 starts + 40% season), with league-avg fallback
    era_val = p.eff_era if p.eff_era and p.eff_era > 0 else (p.era if p.era and p.era > 0 else None)
    whip_val = p.eff_whip if p.eff_whip and p.eff_whip > 0 else (p.whip if p.whip and p.whip > 0 else None)
    era = era_val if era_val else 4.50
    whip = whip_val if whip_val else 1.30

    split_component = final_split * 3.0
    era_component = (1.0 / era) * 2.0
    whip_component = (1.0 / whip) * 1.5
    base_score = split_component + era_component + whip_component

    # Hot/cold modifier (±8% based on last 7 days)
    streak_modifier = 1.0
    if hitter.recent_avg is not None:
        if hitter.recent_avg >= 0.300:
            streak_modifier = 1.08
        elif hitter.recent_avg <= 0.180:
            streak_modifier = 0.92

    score = base_score * streak_modifier * hitter.park_factor

    breakdown = {
        "split_avg_used": round(final_split, 3),
        "split_source": split_source,
        "split_component": round(split_component, 3),
        "era_used": round(era, 2),
        "era_source": "effective (blended)" if era_val and p.recent_era else ("season" if era_val else "default (4.50)"),
        "era_component": round(era_component, 3),
        "whip_used": round(whip, 2),
        "whip_source": "effective (blended)" if whip_val and p.recent_whip else ("season" if whip_val else "default (1.30)"),
        "whip_component": round(whip_component, 3),
        "streak_modifier": round(streak_modifier, 2),
        "park_factor": round(hitter.park_factor, 2),
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


def recommend(score: float, all_scores: list[float], is_ace_pitcher: bool) -> str:
    """
    Start = score >= 60th percentile of active hitters
    Sit   = score <= 25th percentile of active hitters
    Flex  = middle
    Ace pitcher (effective ERA<3 or WHIP<1) downgrades by one level.
    """
    if not all_scores:
        return "Flex"
    sorted_scores = sorted(all_scores)
    n = len(sorted_scores)
    bottom_threshold = sorted_scores[max(0, int(n * 0.25) - 1)]
    top_threshold = sorted_scores[min(n - 1, int(n * 0.60))]

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


def _apply_star_protection(rec: str, hitter: HitterInfo, ace: bool) -> str:
    """Stars should rarely sit.
    - OPS ≥.950 OR known elite player → floor Flex always
    - OPS ≥.850 → floor Flex unless facing ace AND cold (slumping last 7 days)
    Works even when OPS data is unavailable by checking the ELITE_PLAYERS list.
    """
    if rec != "Sit":
        return rec
    cold = hitter.recent_avg is not None and hitter.recent_avg <= 0.180
    is_known_elite = hitter.full_name.lower() in ELITE_PLAYERS

    if is_known_elite or (hitter.ops is not None and hitter.ops >= 0.950):
        return "Flex"
    if hitter.ops is not None and hitter.ops >= 0.850 and not (ace and cold):
        return "Flex"
    return rec


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

    # Star note
    if hitter.ops is not None and hitter.ops >= 0.850:
        ops_str = f".{int(round(hitter.ops * 1000)):03d}"
        parts.append(f"star ({ops_str} OPS)")

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
    active_scores = [scores[h.player_id] for h in hitters if h.pitcher is not None]

    result = []
    for h in hitters:
        score = scores[h.player_id]
        p = h.pitcher
        ace = is_ace(p) if p else False
        rec = recommend(score, active_scores, ace) if p else "Sit"
        rec = _apply_star_protection(rec, h, ace)
        # Confirmed lineup scratch overrides everything — no star protection exception
        if h.confirmed_sit:
            rec = "Sit"

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
