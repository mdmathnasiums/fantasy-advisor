# advisor.py
from dataclasses import dataclass, field

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


def is_ace(pitcher: PitcherInfo) -> bool:
    eff_era = pitcher.eff_era if pitcher.eff_era is not None else pitcher.era
    eff_whip = pitcher.eff_whip if pitcher.eff_whip is not None else pitcher.whip
    if eff_era is not None and eff_era < 3.00:
        return True
    if eff_whip is not None and eff_whip < 1.00:
        return True
    return False


def score_hitter(hitter: HitterInfo) -> float:
    """
    Score components:
      - Split avg vs pitcher handedness (or career vs pitcher if ≥10 PA): weight 3.0
      - Blended with home/away split (70% L/R, 30% home/away) when available
      - Opponent effective ERA (60% recent + 40% season): weight 2.0
      - Opponent effective WHIP: weight 1.5
      - Hot/cold modifier: ±10% based on last 14 days
      - Park factor multiplier
    """
    if hitter.pitcher is None:
        return 0.0
    p = hitter.pitcher

    # Best available split avg
    if hitter.career_vs_pitcher is not None:
        split_avg = hitter.career_vs_pitcher["avg"]
    else:
        split_avg = (
            hitter.splits.get("vL") if p.throws == "L" else hitter.splits.get("vR")
        )

    # Blend home/away context into the split avg
    home_away_avg = hitter.splits.get("home_avg" if hitter.is_home else "away_avg")
    if split_avg is not None and home_away_avg is not None:
        split_avg = split_avg * 0.7 + home_away_avg * 0.3
    elif home_away_avg is not None and split_avg is None:
        split_avg = home_away_avg

    # Use effective (blended) ERA/WHIP, fall back to season, then league average
    era_val = p.eff_era if p.eff_era and p.eff_era > 0 else (p.era if p.era and p.era > 0 else None)
    whip_val = p.eff_whip if p.eff_whip and p.eff_whip > 0 else (p.whip if p.whip and p.whip > 0 else None)
    era = era_val if era_val else 4.50
    whip = whip_val if whip_val else 1.30

    score = (split_avg if split_avg is not None else 0.250) * 3.0
    score += (1.0 / era) * 2.0
    score += (1.0 / whip) * 1.5

    # Hot/cold streak modifier
    if hitter.recent_avg is not None:
        if hitter.recent_avg >= 0.300:
            score *= 1.10
        elif hitter.recent_avg <= 0.180:
            score *= 0.90

    # Park factor
    score *= hitter.park_factor

    return score


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
    """Stars should rarely sit. OPS ≥.950 → floor Flex. OPS ≥.850 → floor Flex unless ace + cold."""
    if hitter.ops is None:
        return rec
    if rec != "Sit":
        return rec
    cold = hitter.recent_avg is not None and hitter.recent_avg <= 0.180
    if hitter.ops >= 0.950:
        return "Flex"  # elite stars always at least Flex
    if hitter.ops >= 0.850 and not (ace and cold):
        return "Flex"  # good stars sit only when facing ace AND slumping
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
            parts.append(f"hot streak ({avg_str} L14)")
        elif hitter.recent_avg <= 0.180:
            parts.append(f"slumping ({avg_str} L14)")

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
    scores = {h.player_id: score_hitter(h) for h in hitters}
    active_scores = [scores[h.player_id] for h in hitters if h.pitcher is not None]

    result = []
    for h in hitters:
        score = scores[h.player_id]
        p = h.pitcher
        ace = is_ace(p) if p else False
        rec = recommend(score, active_scores, ace) if p else "Sit"
        rec = _apply_star_protection(rec, h, ace)

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
            "recent_avg": h.recent_avg,
            "ops": h.ops,
            "career_vs_pitcher": h.career_vs_pitcher,
            "park_factor": round(h.park_factor, 2),
            "is_home": h.is_home,
        })

    result.sort(key=lambda x: (
        POSITION_ORDER.get(x["selected_position"], 9),
        -x["score"],
    ))
    return result
