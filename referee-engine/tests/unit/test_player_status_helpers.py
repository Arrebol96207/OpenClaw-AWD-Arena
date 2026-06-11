from datetime import datetime
from types import SimpleNamespace

import pytest

from player_status import (
    PlayerNotInLeaderboardError,
    apply_leaderboard_snapshot,
    build_player_identity_fields,
    build_leaderboard_summary,
    build_score_changes_since_last_query,
    enrich_leaderboard,
    leaderboard_has_non_zero_scores,
    normalize_player_label_value,
    restore_scores_from_persisted_state,
    snapshot_player_scores,
)


def test_build_leaderboard_summary_preserves_current_leaderboard_order():
    leaderboard = {
        2: {"player_id": 2, "total_score": 180},
        1: {"player_id": 1, "total_score": 150},
        3: {"player_id": 3, "total_score": 90},
    }

    assert build_leaderboard_summary(leaderboard, 1) == {
        "rank": 2,
        "total_players": 3,
        "my_score": 150,
        "leader_score": 180,
        "score_gap_to_leader": 30,
        "score_gap_to_next_above": 30,
        "score_gap_to_next_below": 60,
        "top_players": [
            {"player_id": 2, "total_score": 180},
            {"player_id": 1, "total_score": 150},
            {"player_id": 3, "total_score": 90},
        ],
    }


def test_build_leaderboard_summary_handles_empty_and_missing_player():
    assert build_leaderboard_summary({}, 1) == {
        "rank": 0,
        "total_players": 0,
        "my_score": 0,
        "leader_score": 0,
        "score_gap_to_leader": 0,
        "score_gap_to_next_above": None,
        "score_gap_to_next_below": None,
        "top_players": [],
    }

    with pytest.raises(PlayerNotInLeaderboardError):
        build_leaderboard_summary({2: {"player_id": 2, "total_score": 10}}, 1)


def test_snapshot_player_scores_captures_score_columns_only():
    match = SimpleNamespace(players={
        1: SimpleNamespace(score=10, attack_score=7, defense_score=2, sla_score=1),
        2: SimpleNamespace(score=-5, attack_score=0, defense_score=-5, sla_score=0),
    })

    assert snapshot_player_scores(match) == {
        1: {"total": 10, "attack": 7, "defense": 2, "sla": 1},
        2: {"total": -5, "attack": 0, "defense": -5, "sla": 0},
    }


def test_player_identity_fields_prefer_model_for_display_name():
    match = SimpleNamespace(config=SimpleNamespace(players=[
        SimpleNamespace(id=1, name="  Alice  ", model=" routerss/gpt-5.5 "),
        SimpleNamespace(id=2, name=" Bob ", model=" "),
    ]))

    assert normalize_player_label_value("  gpt-5.5  ") == "gpt-5.5"
    assert normalize_player_label_value("   ") is None
    assert build_player_identity_fields(match, 1) == {
        "name": "Alice",
        "model": "routerss/gpt-5.5",
        "display_name": "routerss/gpt-5.5 (P1)",
    }
    assert build_player_identity_fields(match, 2) == {
        "name": "Bob",
        "model": None,
        "display_name": "Bob (P2)",
    }
    assert build_player_identity_fields(match, 3) == {
        "name": None,
        "model": None,
        "display_name": "Player 3",
    }


def test_enrich_leaderboard_handles_string_player_ids_and_dirty_rows():
    match = SimpleNamespace(config=SimpleNamespace(players=[
        SimpleNamespace(id=1, name="Alice", model="gpt-5.5"),
        SimpleNamespace(id=2, name="Bob", model="gpt-5.4"),
    ]))
    leaderboard = {
        "one": {"player_id": "1", "total_score": 10},
        2: {"total_score": 8},
        "bad": {"player_id": "P3", "total_score": 4},
        4: "not-a-row",
    }

    assert enrich_leaderboard(match, leaderboard) == {
        "one": {"player_id": "1", "total_score": 10, "name": "Alice", "model": "gpt-5.5", "display_name": "gpt-5.5 (P1)"},
        2: {"total_score": 8, "name": "Bob", "model": "gpt-5.4", "display_name": "gpt-5.4 (P2)"},
        "bad": {"player_id": "P3", "total_score": 4},
        4: "not-a-row",
    }


def test_leaderboard_has_non_zero_scores_ignores_dirty_rows():
    assert leaderboard_has_non_zero_scores({
        1: {"total_score": 0},
        2: {"total_score": None},
        3: "not-a-row",
    }) is False
    assert leaderboard_has_non_zero_scores({
        1: {"total_score": 0},
        2: {"total_score": -50},
    }) is True


def test_apply_leaderboard_snapshot_restores_score_columns():
    player = SimpleNamespace(
        score=0,
        attack_score=0,
        defense_score=0,
        sla_score=0,
        flags_captured=0,
        flags_lost=0,
        sla_up=True,
        sla_down_minutes=0,
    )
    match = SimpleNamespace(players={1: player})

    apply_leaderboard_snapshot(match, {
        "1": {
            "total_score": 120,
            "attack_score": 200,
            "defense_score": -50,
            "sla_score": -30,
            "flags_captured": 2,
            "flags_lost": 1,
            "sla_up": False,
            "sla_down_minutes": 3,
        },
        "bad": {"player_id": "P2", "total_score": 999},
        3: "not-a-row",
    })

    assert player.score == 120
    assert player.attack_score == 200
    assert player.defense_score == -50
    assert player.sla_score == -30
    assert player.flags_captured == 2
    assert player.flags_lost == 1
    assert player.sla_up is False
    assert player.sla_down_minutes == 3


def test_restore_scores_keeps_recomputed_non_zero_leaderboard():
    recomputed = {1: {"player_id": 1, "total_score": 80}}
    scoring_engine = SimpleNamespace(
        update_scores=lambda players, submissions: recomputed,
        get_leaderboard=lambda players: {1: {"player_id": 1, "total_score": 999}},
    )
    match = SimpleNamespace(
        players={1: SimpleNamespace(score=0, attack_score=0, defense_score=0, sla_score=0, flags_captured=0, flags_lost=0, sla_up=True, sla_down_minutes=0)},
        persisted_submissions=[{"flag": "FLAG{one}"}],
        persisted_leaderboard={1: {"player_id": 1, "total_score": 120}},
        scoring_engine=scoring_engine,
    )

    assert restore_scores_from_persisted_state(match) is recomputed


def test_restore_scores_falls_back_to_persisted_snapshot_when_recomputed_zero():
    player = SimpleNamespace(
        score=0,
        attack_score=0,
        defense_score=0,
        sla_score=0,
        flags_captured=0,
        flags_lost=0,
        sla_up=True,
        sla_down_minutes=0,
    )

    def get_leaderboard(players):
        return {
            1: {
                "player_id": 1,
                "total_score": players[1].score,
                "attack_score": players[1].attack_score,
                "defense_score": players[1].defense_score,
                "sla_score": players[1].sla_score,
                "flags_captured": players[1].flags_captured,
                "flags_lost": players[1].flags_lost,
            }
        }

    scoring_engine = SimpleNamespace(
        update_scores=lambda players, submissions: {1: {"player_id": 1, "total_score": 0}},
        get_leaderboard=get_leaderboard,
    )
    match = SimpleNamespace(
        players={1: player},
        persisted_submissions=[],
        persisted_leaderboard={
            "1": {
                "player_id": 1,
                "total_score": 130,
                "attack_score": 150,
                "defense_score": -20,
                "sla_score": 0,
                "flags_captured": 3,
                "flags_lost": 1,
            }
        },
        scoring_engine=scoring_engine,
    )

    assert restore_scores_from_persisted_state(match) == {
        1: {
            "player_id": 1,
            "total_score": 130,
            "attack_score": 150,
            "defense_score": -20,
            "sla_score": 0,
            "flags_captured": 3,
            "flags_lost": 1,
        }
    }


def test_build_score_changes_reports_zero_without_previous_checkpoint():
    now = datetime(2026, 1, 1, 12, 0, 0)
    current_scores = {
        1: {"total": 10, "attack": 10, "defense": 0, "sla": 0},
        2: {"total": 5, "attack": 5, "defense": 0, "sla": 0},
    }

    assert build_score_changes_since_last_query(None, 1, now, current_scores) == {
        "has_previous_query": False,
        "previous_query_at": None,
        "current_query_at": "2026-01-01T12:00:00",
        "players": [
            {"player_id": 1, "is_self": True, "total_delta": 0, "attack_delta": 0, "defense_delta": 0, "sla_delta": 0},
            {"player_id": 2, "is_self": False, "total_delta": 0, "attack_delta": 0, "defense_delta": 0, "sla_delta": 0},
        ],
    }


def test_build_score_changes_reports_deltas_since_checkpoint_with_viewer_first():
    now = datetime(2026, 1, 1, 12, 1, 0)
    checkpoint = {
        "queried_at": "2026-01-01T12:00:00",
        "scores_by_player": {
            1: {"total": 10, "attack": 5, "defense": 3, "sla": 2},
            2: {"total": 20, "attack": 20, "defense": 0, "sla": 0},
        },
    }
    current_scores = {
        1: {"total": 25, "attack": 15, "defense": 8, "sla": 2},
        2: {"total": 15, "attack": 20, "defense": -5, "sla": 0},
        3: {"total": 7, "attack": 7, "defense": 0, "sla": 0},
    }

    assert build_score_changes_since_last_query(checkpoint, 2, now, current_scores) == {
        "has_previous_query": True,
        "previous_query_at": "2026-01-01T12:00:00",
        "current_query_at": "2026-01-01T12:01:00",
        "players": [
            {"player_id": 2, "is_self": True, "total_delta": -5, "attack_delta": 0, "defense_delta": -5, "sla_delta": 0},
            {"player_id": 1, "is_self": False, "total_delta": 15, "attack_delta": 10, "defense_delta": 5, "sla_delta": 0},
            {"player_id": 3, "is_self": False, "total_delta": 7, "attack_delta": 7, "defense_delta": 0, "sla_delta": 0},
        ],
    }
