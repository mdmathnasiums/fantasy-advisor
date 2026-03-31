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
