import pytest
from advisor import (
    PitcherInfo, HitterInfo, score_hitter, is_ace,
    get_matchup_quality, recommend, advise_roster,
)


def pitcher(throws="R", era=4.00, whip=1.30) -> PitcherInfo:
    return PitcherInfo(name="Test Pitcher", throws=throws, era=era, whip=whip)


def hitter(vL=0.250, vR=0.280, pitcher_obj=None, pid="1") -> HitterInfo:
    return HitterInfo(
        player_id=pid,
        full_name="Test Hitter",
        team_abbr="NYY",
        position="OF",
        selected_position="OF",
        bats="R",
        splits={"vL": vL, "vR": vR},
        pitcher=pitcher_obj,
    )


# --- score_hitter ---

def test_score_uses_vR_vs_righty():
    h = hitter(vR=0.300, pitcher_obj=pitcher(throws="R", era=4.0, whip=1.30))
    expected = 0.300 * 3.0 + (1 / 4.0) * 2.0 + (1 / 1.30) * 1.5
    assert abs(score_hitter(h) - expected) < 0.001


def test_score_uses_vL_vs_lefty():
    h = hitter(vL=0.250, pitcher_obj=pitcher(throws="L", era=4.0, whip=1.30))
    expected = 0.250 * 3.0 + (1 / 4.0) * 2.0 + (1 / 1.30) * 1.5
    assert abs(score_hitter(h) - expected) < 0.001


def test_score_zero_no_game():
    h = hitter(pitcher_obj=None)
    assert score_hitter(h) == 0.0


def test_score_uses_fallback_era_when_none():
    h = hitter(vR=0.280, pitcher_obj=PitcherInfo("X", "R", era=None, whip=1.30))
    score = score_hitter(h)
    assert score > 0  # uses fallback ERA of 4.50


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

def test_recommend_top_score_is_start():
    scores = [1.0, 1.5, 2.0, 2.5, 3.0]
    assert recommend(3.0, scores, False) == "Start"


def test_recommend_bottom_score_is_sit():
    scores = [1.0, 1.5, 2.0, 2.5, 3.0]
    assert recommend(1.0, scores, False) == "Sit"


def test_recommend_ace_downgrades_start_to_flex():
    scores = [1.0, 1.5, 2.0, 2.5, 3.0]
    assert recommend(3.0, scores, True) == "Flex"


def test_recommend_ace_downgrades_flex_to_sit():
    scores = [1.0, 1.5, 2.0, 2.5, 3.0]
    # Middle score → Flex without ace, → Sit with ace
    assert recommend(2.0, scores, True) == "Sit"


def test_recommend_empty_scores_returns_flex():
    assert recommend(1.0, [], False) == "Flex"


# --- advise_roster ---

def test_advise_roster_sorted_by_score_descending():
    h1 = hitter(vR=0.320, pitcher_obj=pitcher(era=4.0, whip=1.3), pid="1")
    h2 = hitter(vR=0.200, pitcher_obj=pitcher(era=5.0, whip=1.5), pid="2")
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
