import pytest
from advisor import (
    PitcherInfo, HitterInfo, score_hitter, is_ace,
    get_matchup_quality, recommend, advise_roster,
)


def pitcher(throws="R", era=4.00, whip=1.30) -> PitcherInfo:
    return PitcherInfo(name="Test Pitcher", throws=throws, era=era, whip=whip)


def hitter(vL=0.250, vR=0.280, pitcher_obj=None, pid="1",
           season_avg=None, proj_hr=None, proj_rbi=None, proj_r=None, proj_sb=None) -> HitterInfo:
    return HitterInfo(
        player_id=pid,
        full_name="Test Hitter",
        team_abbr="NYY",
        position="OF",
        selected_position="OF",
        bats="R",
        splits={"vL": vL, "vR": vR},
        pitcher=pitcher_obj,
        season_avg=season_avg,
        proj_hr=proj_hr,
        proj_rbi=proj_rbi,
        proj_r=proj_r,
        proj_sb=proj_sb,
    )


# --- score_hitter ---

def test_score_zero_no_game():
    h = hitter(pitcher_obj=None)
    score, bd = score_hitter(h)
    assert score == 0.0
    assert bd == {}


def test_score_returns_tuple():
    h = hitter(vR=0.300, pitcher_obj=pitcher(throws="R", era=4.0, whip=1.30))
    result = score_hitter(h)
    assert isinstance(result, tuple)
    assert len(result) == 2
    score, bd = result
    assert isinstance(score, float)
    assert isinstance(bd, dict)


def test_score_above_zero_with_pitcher():
    h = hitter(vR=0.280, pitcher_obj=pitcher(throws="R", era=4.0, whip=1.30))
    score, _ = score_hitter(h)
    assert score > 0


def test_score_uses_fallback_era_when_none():
    h = hitter(vR=0.280, pitcher_obj=PitcherInfo("X", "R", era=None, whip=1.30))
    score, _ = score_hitter(h)
    assert score > 0  # uses default ERA norm


def test_score_uses_fallback_whip_when_none():
    h = hitter(vR=0.280, pitcher_obj=PitcherInfo("X", "R", era=4.0, whip=None))
    score, _ = score_hitter(h)
    assert score > 0  # uses default WHIP norm


def test_score_breakdown_has_expected_keys():
    h = hitter(vR=0.280, pitcher_obj=pitcher(), season_avg=0.270, proj_hr=25.0,
               proj_rbi=80.0, proj_r=75.0, proj_sb=10.0)
    _, bd = score_hitter(h)
    for key in ("hitter_quality", "ba_norm", "hr_norm", "rbi_norm", "r_norm", "sb_norm",
                "matchup_quality", "split_norm", "era_norm", "whip_norm",
                "context", "streak_score", "park_score", "total"):
        assert key in bd, f"Missing breakdown key: {key}"


def test_score_higher_with_better_hitter_stats():
    h_good = hitter(pitcher_obj=pitcher(), season_avg=0.300, proj_hr=40.0,
                    proj_rbi=110.0, proj_r=105.0, proj_sb=5.0)
    h_poor = hitter(pitcher_obj=pitcher(), season_avg=0.230, proj_hr=8.0,
                    proj_rbi=35.0, proj_r=45.0, proj_sb=2.0)
    score_good, _ = score_hitter(h_good)
    score_poor, _ = score_hitter(h_poor)
    assert score_good > score_poor


def test_score_higher_vs_bad_pitcher():
    h_vs_bad = hitter(vR=0.280, pitcher_obj=pitcher(era=5.50, whip=1.60))
    h_vs_ace = hitter(vR=0.280, pitcher_obj=pitcher(era=2.50, whip=0.90))
    score_bad, _ = score_hitter(h_vs_bad)
    score_ace, _ = score_hitter(h_vs_ace)
    assert score_bad > score_ace


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

def test_recommend_high_score_is_start():
    assert recommend(0.60) == "Start"


def test_recommend_low_score_is_sit():
    assert recommend(0.30) == "Sit"


def test_recommend_mid_score_is_flex():
    assert recommend(0.45) == "Flex"


def test_recommend_boundary_start():
    assert recommend(0.55) == "Start"


def test_recommend_boundary_sit():
    assert recommend(0.35) == "Sit"


# --- advise_roster ---

def test_advise_roster_sorted_by_score_descending():
    h1 = hitter(vR=0.320, pitcher_obj=pitcher(era=4.0, whip=1.3), pid="1",
                season_avg=0.300, proj_hr=30.0, proj_rbi=90.0, proj_r=85.0, proj_sb=5.0)
    h2 = hitter(vR=0.200, pitcher_obj=pitcher(era=5.0, whip=1.5), pid="2",
                season_avg=0.235, proj_hr=10.0, proj_rbi=40.0, proj_r=45.0, proj_sb=2.0)
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
