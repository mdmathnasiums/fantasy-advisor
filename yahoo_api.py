API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"

LEAGUES = {
    "4594": {"name": "The Gold Members", "key": "mlb.l.4594"},
    "20959": {"name": "YCQ League", "key": "mlb.l.20959"},
}


def parse_player_info(info_list: list) -> dict:
    """Collapse Yahoo's array-of-single-key-dicts into a flat dict."""
    player = {}
    for item in info_list:
        if isinstance(item, dict):
            for k, v in item.items():
                player[k] = v
    return player


def _parse_eligible_positions(ep_obj) -> list[str]:
    if not isinstance(ep_obj, dict):
        return []
    pos = ep_obj.get("position", [])
    if isinstance(pos, str):
        return [pos]
    return list(pos) if isinstance(pos, list) else []


def parse_player(player_data: list) -> dict:
    """Parse a Yahoo player entry [info_array, meta_dict] into a clean dict."""
    info_list = player_data[0] if player_data else []
    meta = player_data[1] if len(player_data) > 1 else {}
    raw = parse_player_info(info_list)

    name_obj = raw.get("name", {})
    full_name = name_obj.get("full", "") if isinstance(name_obj, dict) else str(name_obj)

    selected_pos = ""
    if isinstance(meta, dict):
        sp = meta.get("selected_position", {})
        if isinstance(sp, dict):
            selected_pos = sp.get("position", "")
        elif isinstance(sp, list):
            for item in sp:
                if isinstance(item, dict) and "position" in item:
                    selected_pos = item["position"]
                    break

    return {
        "player_id": str(raw.get("player_id", "")),
        "full_name": full_name,
        "team_abbr": raw.get("editorial_team_abbr", ""),
        "position": raw.get("display_position", ""),
        "selected_position": selected_pos,
        "eligible_positions": _parse_eligible_positions(raw.get("eligible_positions", {})),
        "bats": None,
        "mlb_id": None,
    }
