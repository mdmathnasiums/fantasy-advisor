import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from mlb_api import get_probable_pitchers, get_pitcher_details


MOCK_SCHEDULE = {
    "dates": [
        {
            "games": [
                {
                    "teams": {
                        "home": {
                            "team": {"abbreviation": "NYY"},
                            "probablePitcher": {"fullName": "Gerrit Cole", "id": 543037},
                        },
                        "away": {
                            "team": {"abbreviation": "BOS"},
                            "probablePitcher": {"fullName": "Chris Sale", "id": 519242},
                        },
                    }
                }
            ]
        }
    ]
}

MOCK_PITCHER_PERSON = {
    "people": [
        {
            "pitchHand": {"code": "R"},
            "stats": [
                {
                    "splits": [
                        {
                            "stat": {"era": "2.85", "whip": "0.95"}
                        }
                    ]
                }
            ],
        }
    ]
}


def make_mock_client(json_data):
    mock_resp = MagicMock()
    mock_resp.json.return_value = json_data
    mock_resp.raise_for_status.return_value = None
    mock_get = AsyncMock(return_value=mock_resp)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock(get=mock_get))
    mock_ctx.__aexit__ = AsyncMock(return_value=False)
    return mock_ctx


@pytest.mark.asyncio
async def test_get_probable_pitchers_parses_home_and_away():
    with patch("mlb_api.httpx.AsyncClient", return_value=make_mock_client(MOCK_SCHEDULE)):
        result = await get_probable_pitchers()
    assert "NYY" in result
    assert result["NYY"]["name"] == "Gerrit Cole"
    assert result["NYY"]["mlb_id"] == 543037
    assert "BOS" in result
    assert result["BOS"]["name"] == "Chris Sale"


@pytest.mark.asyncio
async def test_get_probable_pitchers_skips_missing_pitcher():
    data = {
        "dates": [{"games": [{"teams": {
            "home": {"team": {"abbreviation": "NYY"}, "probablePitcher": None},
            "away": {"team": {"abbreviation": "BOS"}},
        }}]}]
    }
    with patch("mlb_api.httpx.AsyncClient", return_value=make_mock_client(data)):
        result = await get_probable_pitchers()
    assert "NYY" not in result
    assert "BOS" not in result


@pytest.mark.asyncio
async def test_get_pitcher_details_extracts_throws_era_whip():
    with patch("mlb_api.httpx.AsyncClient", return_value=make_mock_client(MOCK_PITCHER_PERSON)):
        result = await get_pitcher_details(543037)
    assert result["throws"] == "R"
    assert result["era"] == 2.85
    assert result["whip"] == 0.95
