from types import SimpleNamespace

from history_restore import (
    apply_leaderboard_snapshot_to_players,
    event_type,
    latest_leaderboard_event_data,
    latest_leaderboard_snapshot,
    restore_container_metadata_from_events,
)


def _player(**overrides):
    defaults = {
        "score": 0,
        "attack_score": 0,
        "defense_score": 0,
        "sla_score": 0,
        "flags_captured": 0,
        "flags_lost": 0,
        "sla_up": False,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_event_type_supports_current_and_legacy_keys():
    assert event_type({"type": "MATCH_FINISHED"}) == "MATCH_FINISHED"
    assert event_type({"event_type": "MATCH_STARTED"}) == "MATCH_STARTED"
    assert event_type({"type": 123, "event_type": None}) is None


def test_restore_container_metadata_uses_latest_valid_rows_and_defaults_missing_fields():
    events = [
        {"type": "CONTAINERS_CREATED", "data": {"players": {"bad": {"target_ip": "ignored"}, "2": "bad"}}},
        {
            "event_type": "CONTAINERS_CREATED",
            "data": {
                "players": {
                    "1": {"target_ip": "10.1.1.2", "target_container": "target-custom"},
                    2: {"agent_container": "agent-two", "network": "net-two"},
                }
            },
        },
    ]

    metadata = restore_container_metadata_from_events(events, "match_abc")

    assert metadata[1] == {
        "target_ip": "10.1.1.2",
        "target_container": "target-custom",
        "agent_container": "claw_match_abc_1",
        "network": "awd_match_abc_player_1",
    }
    assert metadata[2] == {
        "agent_container": "agent-two",
        "network": "net-two",
        "target_container": "target_match_abc_2",
    }


def test_latest_leaderboard_event_data_returns_most_recent_payload_with_rows():
    first = {"leaderboard": {"1": {"score": 100}}, "phase": "attack"}
    latest = {"leaderboard": {"1": {"score": 150}}, "phase": "finished"}

    data = latest_leaderboard_event_data([
        {"type": "STATUS", "data": first},
        {"type": "HEARTBEAT", "data": {"leaderboard": {}}},
        {"type": "MATCH_FINISHED", "data": latest},
    ])

    assert data is latest


def test_latest_leaderboard_snapshot_prefers_recent_non_zero_snapshot_over_later_zero_rows():
    old_non_zero = {"1": {"player_id": 1, "total_score": 200}}
    late_zero = {"1": {"player_id": 1, "total_score": 0}}
    events = [
        {"type": "STATUS", "data": {"leaderboard": old_non_zero}},
        {"type": "MATCH_FINISHED", "data": {"leaderboard": late_zero}},
    ]

    assert latest_leaderboard_snapshot(events) is old_non_zero
    assert latest_leaderboard_snapshot(events, prefer_non_zero=False) is late_zero


def test_apply_leaderboard_snapshot_updates_players_and_ignores_bad_rows():
    match = SimpleNamespace(players={1: _player(score=5, sla_up=False), 2: _player(score=7, sla_up=True)})

    apply_leaderboard_snapshot_to_players(
        match,
        {
            "1": {
                "player_id": 1,
                "total_score": 120,
                "attack_score": 80,
                "defense_score": 30,
                "sla_score": 10,
                "flags_captured": 4,
                "flags_lost": 1,
                "sla_up": True,
            },
            "2": {"score": 25},
            "bad": {"player_id": "nope", "total_score": 999},
            "3": {"total_score": 999},
            "4": "not a row",
        },
    )

    assert match.players[1].score == 120
    assert match.players[1].attack_score == 80
    assert match.players[1].defense_score == 30
    assert match.players[1].sla_score == 10
    assert match.players[1].flags_captured == 4
    assert match.players[1].flags_lost == 1
    assert match.players[1].sla_up is True
    assert match.players[2].score == 25
    assert match.players[2].sla_up is True
