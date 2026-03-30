from yahoo_api import parse_player_info, parse_player, _parse_eligible_positions


def test_parse_player_info_extracts_flat_fields():
    info_list = [
        {"player_id": "8967"},
        {"name": {"full": "Mike Trout", "first": "Mike", "last": "Trout"}},
        {"editorial_team_abbr": "LAA"},
        {"display_position": "OF"},
        {"eligible_positions": {"position": ["OF", "Util"]}},
    ]
    result = parse_player_info(info_list)
    assert result["player_id"] == "8967"
    assert result["name"]["full"] == "Mike Trout"
    assert result["editorial_team_abbr"] == "LAA"
    assert result["display_position"] == "OF"


def test_parse_player_info_ignores_non_dicts():
    info_list = ["some_string", {"player_id": "123"}, 42]
    result = parse_player_info(info_list)
    assert result["player_id"] == "123"
    assert len(result) == 1


def test_parse_player_extracts_full_name():
    player_data = [
        [
            {"player_id": "8967"},
            {"name": {"full": "Mike Trout"}},
            {"editorial_team_abbr": "LAA"},
            {"display_position": "OF"},
            {"eligible_positions": {"position": ["OF", "Util"]}},
        ],
        {"selected_position": {"position": "OF"}},
    ]
    result = parse_player(player_data)
    assert result["full_name"] == "Mike Trout"
    assert result["team_abbr"] == "LAA"
    assert result["position"] == "OF"
    assert result["selected_position"] == "OF"
    assert "OF" in result["eligible_positions"]
    assert result["player_id"] == "8967"
    assert result["bats"] is None
    assert result["mlb_id"] is None


def test_parse_player_handles_missing_selected_position():
    player_data = [
        [{"player_id": "1"}, {"name": {"full": "Test Player"}},
         {"editorial_team_abbr": "NYY"}, {"display_position": "1B"}],
    ]
    result = parse_player(player_data)
    assert result["selected_position"] == ""


def test_parse_eligible_positions_string():
    assert _parse_eligible_positions({"position": "1B"}) == ["1B"]


def test_parse_eligible_positions_list():
    assert _parse_eligible_positions({"position": ["1B", "Util"]}) == ["1B", "Util"]


def test_parse_eligible_positions_empty():
    assert _parse_eligible_positions({}) == []
