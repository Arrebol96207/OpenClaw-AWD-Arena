"""Pure helpers for player status API payloads."""

from datetime import datetime
from typing import Any, Dict, Iterable, Optional, Protocol


class PlayerScoreLike(Protocol):
    score: int
    attack_score: int
    defense_score: int
    sla_score: int
    flags_captured: int
    flags_lost: int
    sla_up: bool
    sla_down_minutes: int


class MatchScoresLike(Protocol):
    players: Dict[int, PlayerScoreLike]


class ScoringEngineLike(Protocol):
    def update_scores(self, players: Dict[int, PlayerScoreLike], submissions: Iterable[Dict[str, Any]]) -> Dict[int, Dict]:
        ...

    def get_leaderboard(self, players: Dict[int, PlayerScoreLike]) -> Dict[int, Dict]:
        ...


class MatchWithPersistedScoresLike(MatchScoresLike, Protocol):
    persisted_submissions: Iterable[Dict[str, Any]]
    persisted_leaderboard: Dict[Any, Dict]
    scoring_engine: ScoringEngineLike


class PlayerIdentityConfigLike(Protocol):
    id: int
    name: Optional[str]
    model: Optional[str]


class MatchConfigWithPlayersLike(Protocol):
    players: Iterable[PlayerIdentityConfigLike]


class MatchIdentityLike(Protocol):
    config: MatchConfigWithPlayersLike


class PlayerNotInLeaderboardError(ValueError):
    pass


def normalize_player_label_value(value: Optional[str]) -> Optional[str]:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def build_player_identity_fields(match: MatchIdentityLike, player_id: int) -> Dict[str, Optional[str]]:
    players = getattr(getattr(match, "config", None), "players", [])
    player_cfg = next((cfg for cfg in players if getattr(cfg, "id", None) == player_id), None)
    name = normalize_player_label_value(getattr(player_cfg, "name", None)) if player_cfg else None
    model = normalize_player_label_value(getattr(player_cfg, "model", None)) if player_cfg else None
    if model:
        display_name = f"{model} (P{player_id})"
    elif name:
        display_name = f"{name} (P{player_id})"
    else:
        display_name = f"Player {player_id}"

    return {
        "name": name,
        "model": model,
        "display_name": display_name,
    }


def enrich_leaderboard(match: MatchIdentityLike, leaderboard: Dict[Any, Any]) -> Dict[Any, Any]:
    enriched: Dict[Any, Any] = {}
    for pid, row in leaderboard.items():
        if not isinstance(row, dict):
            enriched[pid] = row
            continue

        row_player_id = row.get("player_id", pid)
        if isinstance(row_player_id, str) and row_player_id.isdigit():
            player_id = int(row_player_id)
        elif isinstance(row_player_id, int):
            player_id = row_player_id
        elif isinstance(pid, int):
            player_id = pid
        else:
            enriched[pid] = dict(row)
            continue

        enriched[pid] = {
            **row,
            **build_player_identity_fields(match, player_id),
        }

    return enriched


def leaderboard_has_non_zero_scores(leaderboard: Dict[Any, Any]) -> bool:
    values = [entry for entry in leaderboard.values() if isinstance(entry, dict)]
    return any((entry.get("total_score") or 0) != 0 for entry in values)


def apply_leaderboard_snapshot(match: MatchScoresLike, leaderboard: Dict[Any, Any]) -> None:
    for raw_player_id, entry in leaderboard.items():
        if not isinstance(entry, dict):
            continue

        player_id = entry.get("player_id")
        if not isinstance(player_id, int):
            if isinstance(raw_player_id, int):
                player_id = raw_player_id
            elif isinstance(raw_player_id, str) and raw_player_id.isdigit():
                player_id = int(raw_player_id)
            else:
                continue

        player = match.players.get(player_id)
        if player is None:
            continue

        player.score = int(entry.get("total_score") or 0)
        player.attack_score = int(entry.get("attack_score") or 0)
        player.defense_score = int(entry.get("defense_score") or 0)
        player.sla_score = int(entry.get("sla_score") or 0)
        player.flags_captured = int(entry.get("flags_captured") or 0)
        player.flags_lost = int(entry.get("flags_lost") or 0)
        if "sla_up" in entry:
            player.sla_up = bool(entry.get("sla_up"))
        if "sla_down_minutes" in entry:
            player.sla_down_minutes = int(entry.get("sla_down_minutes") or 0)


def restore_scores_from_persisted_state(match: MatchWithPersistedScoresLike) -> Dict[int, Dict]:
    leaderboard = match.scoring_engine.update_scores(match.players, match.persisted_submissions)
    if leaderboard_has_non_zero_scores(leaderboard) or not match.persisted_leaderboard:
        return leaderboard

    if not leaderboard_has_non_zero_scores(match.persisted_leaderboard):
        return leaderboard

    apply_leaderboard_snapshot(match, match.persisted_leaderboard)
    return match.scoring_engine.get_leaderboard(match.players)


def build_leaderboard_summary(leaderboard: Dict[int, Dict], player_id: int) -> Dict[str, Any]:
    rows = [row for row in leaderboard.values() if isinstance(row, dict)]
    if not rows:
        return {
            "rank": 0,
            "total_players": 0,
            "my_score": 0,
            "leader_score": 0,
            "score_gap_to_leader": 0,
            "score_gap_to_next_above": None,
            "score_gap_to_next_below": None,
            "top_players": [],
        }

    my_index = next((index for index, row in enumerate(rows) if row.get("player_id") == player_id), None)
    if my_index is None:
        raise PlayerNotInLeaderboardError("Player not found in leaderboard")

    my_row = rows[my_index]
    leader_score = int(rows[0].get("total_score") or 0)
    my_score = int(my_row.get("total_score") or 0)
    above = rows[my_index - 1] if my_index > 0 else None
    below = rows[my_index + 1] if my_index + 1 < len(rows) else None

    return {
        "rank": my_index + 1,
        "total_players": len(rows),
        "my_score": my_score,
        "leader_score": leader_score,
        "score_gap_to_leader": leader_score - my_score,
        "score_gap_to_next_above": None if above is None else int(above.get("total_score") or 0) - my_score,
        "score_gap_to_next_below": None if below is None else my_score - int(below.get("total_score") or 0),
        "top_players": [
            {
                "player_id": int(row.get("player_id") or 0),
                "total_score": int(row.get("total_score") or 0),
            }
            for row in rows[:3]
        ],
    }


def snapshot_player_scores(match: MatchScoresLike) -> Dict[int, Dict[str, int]]:
    return {
        pid: {
            "total": int(player.score),
            "attack": int(player.attack_score),
            "defense": int(player.defense_score),
            "sla": int(player.sla_score),
        }
        for pid, player in match.players.items()
    }


def build_score_changes_since_last_query(
    checkpoint: Optional[Dict[str, Any]],
    viewer_player_id: int,
    now: datetime,
    current_scores: Dict[int, Dict[str, int]],
) -> Dict[str, Any]:
    checkpoint = checkpoint or {}
    has_previous_query = bool(checkpoint)
    previous_scores = checkpoint.get("scores_by_player") if isinstance(checkpoint, dict) else None
    if not isinstance(previous_scores, dict):
        previous_scores = {}

    ordered_player_ids = [viewer_player_id] + sorted(
        pid for pid in current_scores.keys() if pid != viewer_player_id
    )
    players = []

    for pid in ordered_player_ids:
        current = current_scores.get(pid) or {}
        previous_raw = previous_scores.get(pid)
        previous = previous_raw if isinstance(previous_raw, dict) else {}

        if has_previous_query:
            total_delta = int(current.get("total", 0)) - int(previous.get("total", 0))
            attack_delta = int(current.get("attack", 0)) - int(previous.get("attack", 0))
            defense_delta = int(current.get("defense", 0)) - int(previous.get("defense", 0))
            sla_delta = int(current.get("sla", 0)) - int(previous.get("sla", 0))
        else:
            total_delta = 0
            attack_delta = 0
            defense_delta = 0
            sla_delta = 0

        players.append({
            "player_id": pid,
            "is_self": pid == viewer_player_id,
            "total_delta": total_delta,
            "attack_delta": attack_delta,
            "defense_delta": defense_delta,
            "sla_delta": sla_delta,
        })

    return {
        "has_previous_query": has_previous_query,
        "previous_query_at": checkpoint.get("queried_at") if has_previous_query else None,
        "current_query_at": now.isoformat(),
        "players": players,
    }
