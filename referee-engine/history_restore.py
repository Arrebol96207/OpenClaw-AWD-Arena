"""Helpers for reconstructing historical match state from persisted events."""

from typing import Any, Dict, List, Optional, Protocol


class PlayerStateLike(Protocol):
    score: int
    attack_score: int
    defense_score: int
    sla_score: int
    flags_captured: int
    flags_lost: int
    sla_up: bool


class MatchStateLike(Protocol):
    players: Dict[int, PlayerStateLike]


def event_type(event: Dict[str, Any]) -> Optional[str]:
    raw_type = event.get("type")
    if isinstance(raw_type, str):
        return raw_type
    raw_type = event.get("event_type")
    return raw_type if isinstance(raw_type, str) else None


def restore_container_metadata_from_events(
    events: List[Dict[str, Any]],
    match_id: str,
) -> Dict[int, Dict[str, Any]]:
    metadata: Dict[int, Dict[str, Any]] = {}
    for event in events:
        if event_type(event) != "CONTAINERS_CREATED":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        players_data = data.get("players")
        if not isinstance(players_data, dict):
            continue
        for raw_pid, raw_info in players_data.items():
            try:
                player_id = int(raw_pid)
            except (TypeError, ValueError):
                continue
            if not isinstance(raw_info, dict):
                continue
            metadata[player_id] = dict(raw_info)

    for player_id, info in metadata.items():
        info.setdefault("agent_container", f"claw_{match_id}_{player_id}")
        info.setdefault("target_container", f"target_{match_id}_{player_id}")
        info.setdefault("network", f"awd_{match_id}_player_{player_id}")
    return metadata


def latest_leaderboard_event_data(events: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for event in reversed(events):
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        leaderboard = data.get("leaderboard")
        if isinstance(leaderboard, dict) and leaderboard:
            return data
    return None


def latest_leaderboard_snapshot(events: List[Dict[str, Any]], *, prefer_non_zero: bool = True) -> Dict[Any, Dict[str, Any]]:
    selected: Dict[Any, Dict[str, Any]] = {}
    selected_has_non_zero = False
    for event in reversed(events):
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        leaderboard = data.get("leaderboard")
        if not isinstance(leaderboard, dict) or not leaderboard:
            continue
        if not prefer_non_zero:
            return leaderboard
        rows = [entry for entry in leaderboard.values() if isinstance(entry, dict)]
        has_non_zero = any((entry.get("total_score") or entry.get("score") or 0) != 0 for entry in rows)
        if has_non_zero or not selected_has_non_zero:
            selected = leaderboard
            selected_has_non_zero = has_non_zero
        if has_non_zero:
            break
    return selected


def apply_leaderboard_snapshot_to_players(match: MatchStateLike, leaderboard: Dict[Any, Dict[str, Any]]) -> None:
    for raw_player_id, row in leaderboard.items():
        if not isinstance(row, dict):
            continue
        try:
            player_id = int(row.get("player_id") or raw_player_id)
        except (TypeError, ValueError):
            continue
        player = match.players.get(player_id)
        if player is None:
            continue
        player.score = int(row.get("total_score", row.get("score", player.score)) or 0)
        player.attack_score = int(row.get("attack_score", player.attack_score) or 0)
        player.defense_score = int(row.get("defense_score", player.defense_score) or 0)
        player.sla_score = int(row.get("sla_score", player.sla_score) or 0)
        player.flags_captured = int(row.get("flags_captured", player.flags_captured) or 0)
        player.flags_lost = int(row.get("flags_lost", player.flags_lost) or 0)
        if "sla_up" in row:
            player.sla_up = bool(row.get("sla_up"))
