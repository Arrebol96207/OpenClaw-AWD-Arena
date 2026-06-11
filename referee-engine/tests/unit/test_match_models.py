import pytest

from match_models import (
    MAX_PLAYERS,
    AttackContext,
    LeaderboardSummary,
    MatchConfig,
    MatchPhaseConfig,
    MatchDetails,
    PlayerBackendConfig,
    PlayerConfig,
    ScoreChangesSinceLastQuery,
    WerewolfConfig,
    WerewolfRoleConfig,
)


def test_match_config_defaults_and_old_payload_compatibility():
    config = MatchConfig(players=[PlayerConfig(id=1, name="P1")])

    assert config.mode == "awd"
    assert config.llm.baseUrl == "https://api.findmini.top/gpt"
    assert config.llm.model == "gpt-5.5"
    assert config.agent_image == "openclaw/local-agent:ssh"
    assert config.players[0].backend_type == "openclaw"
    assert config.players[0].backend_config.extra_env == {}


def test_match_config_rejects_duplicate_too_many_and_invalid_phase_duration():
    with pytest.raises(ValueError, match="player ids must be unique"):
        MatchConfig(players=[PlayerConfig(id=1, name="P1"), PlayerConfig(id=1, name="P1b")])

    with pytest.raises(ValueError, match="players must contain at most"):
        MatchConfig(players=[PlayerConfig(id=i, name=f"P{i}") for i in range(1, MAX_PLAYERS + 2)])

    with pytest.raises(ValueError, match="defense \\+ attack phases cannot exceed match duration"):
        MatchConfig(
            match=MatchDetails(duration=60, phases=MatchPhaseConfig(defense=45, attack=30)),
            players=[PlayerConfig(id=1, name="P1")],
        )


def test_match_config_rejects_invalid_backend_mode_and_image_references():
    with pytest.raises(ValueError, match="unsupported backend_type"):
        PlayerConfig(id=1, name="P1", backend_type="unknown")

    with pytest.raises(ValueError, match="mode must be"):
        MatchConfig(mode="battle-royale", players=[PlayerConfig(id=1, name="P1")])

    with pytest.raises(ValueError, match="invalid Docker image reference"):
        MatchConfig(players=[PlayerConfig(id=1, name="P1")], target_image="bad image;rm")

    with pytest.raises(ValueError, match="invalid Docker image reference"):
        PlayerBackendConfig(image="bad image;rm")


def test_werewolf_boards_and_roles_are_validated():
    players = [PlayerConfig(id=i, name=f"P{i}") for i in range(1, 13)]

    standard = MatchConfig(mode="werewolf", players=players)
    assert standard.werewolf.board == "standard_guard"

    white_wolf = MatchConfig(
        mode="werewolf",
        players=players,
        werewolf=WerewolfConfig(
            board="white_wolf_king_knight",
            roles=WerewolfRoleConfig(
                werewolf=3,
                white_wolf_king=1,
                villager=4,
                seer=1,
                witch=1,
                hunter=1,
                guard=0,
                knight=1,
            ),
        ),
    )
    assert white_wolf.werewolf.board == "white_wolf_king_knight"

    with pytest.raises(ValueError, match="werewolf roles must match board"):
        WerewolfConfig(board="white_wolf_king_knight", roles=WerewolfRoleConfig())

    with pytest.raises(ValueError, match="werewolf mode requires exactly 12 players"):
        MatchConfig(mode="werewolf", players=[PlayerConfig(id=1, name="P1")])


def test_api_payload_models_do_not_share_default_lists():
    first_summary = LeaderboardSummary(
        rank=1,
        total_players=2,
        my_score=10,
        leader_score=10,
        score_gap_to_leader=0,
    )
    second_summary = LeaderboardSummary(
        rank=2,
        total_players=2,
        my_score=0,
        leader_score=10,
        score_gap_to_leader=10,
    )
    first_summary.top_players.append({"player_id": 1, "total_score": 10})
    assert second_summary.top_players == []

    first_context = AttackContext()
    second_context = AttackContext()
    first_context.enemy_targets.append({"player_id": 2, "ip": "10.0.0.2", "port": 3000})
    assert second_context.enemy_targets == []

    first_delta = ScoreChangesSinceLastQuery(
        has_previous_query=False,
        current_query_at="2026-01-01T00:00:00",
    )
    second_delta = ScoreChangesSinceLastQuery(
        has_previous_query=False,
        current_query_at="2026-01-01T00:00:01",
    )
    first_delta.players.append({
        "player_id": 1,
        "total_delta": 1,
        "attack_delta": 1,
        "defense_delta": 0,
        "sla_delta": 0,
    })
    assert second_delta.players == []
