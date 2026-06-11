import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flag_manager import PlayerState  # noqa: E402


def _load_main_module(module_name: str):
    main_path = ROOT / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, main_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _build_match(module):
    config = module.MatchConfig(
        players=[
            module.PlayerConfig(id=1, name="P1"),
            module.PlayerConfig(id=2, name="P2"),
            module.PlayerConfig(id=3, name="P3"),
        ]
    )
    match = module.MatchState("match_player_status", config)
    match.status = "attack"
    now = datetime.now()
    match.started_at = now - timedelta(seconds=120)
    match.attack_started_at = now - timedelta(seconds=15)

    match.players[1] = PlayerState(
        player_id=1,
        container_name="c1",
        target_container="t1",
        target_ip="10.0.0.1",
        ready_status="AGENT_READY",
        score=150,
        attack_score=200,
        defense_score=-50,
        sla_score=0,
        sla_up=True,
        sla_down_minutes=0,
        flags_captured=2,
        flags_lost=1,
    )
    match.players[2] = PlayerState(
        player_id=2,
        container_name="c2",
        target_container="t2",
        target_ip="10.0.0.2",
        ready_status="AGENT_READY",
        score=180,
        attack_score=200,
        defense_score=-20,
        sla_score=0,
        sla_up=True,
        sla_down_minutes=0,
        flags_captured=2,
        flags_lost=0,
    )
    match.players[3] = PlayerState(
        player_id=3,
        container_name="c3",
        target_container="t3",
        target_ip="10.0.0.3",
        ready_status="AGENT_READY",
        score=90,
        attack_score=100,
        defense_score=-10,
        sla_score=0,
        sla_up=True,
        sla_down_minutes=0,
        flags_captured=1,
        flags_lost=0,
    )
    match.attack_targets_by_player[1] = [
        {"player_id": 2, "ip": "10.200.0.2", "port": 3000},
        {"player_id": 3, "ip": "10.200.0.3", "port": 3000},
    ]
    return match


async def _async_noop():
    return None


async def _async_empty_matches():
    return []


def test_player_token_issue_and_revoke_updates_indexes(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_player_tokens")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_tokens", config)

    token = module.referee._issue_player_read_token(match, 1)

    assert match.player_read_tokens[1] == token
    assert module.referee.player_token_index[token] == (match.match_id, 1)

    module.referee._revoke_player_read_token(match, 1)

    assert 1 not in match.player_read_tokens
    assert token not in module.referee.player_token_index


def test_verify_player_token_rejects_unknown_token(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_verify_bad_player_token")

    with pytest.raises(HTTPException) as exc_info:
        module.verify_player_token("bad-token")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Invalid player token"


@pytest.mark.asyncio
async def test_build_player_status_includes_leaderboard_summary_and_attack_context(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_player_status_payload")

    match = _build_match(module)
    module.referee.matches[match.match_id] = match
    token = module.referee._issue_player_read_token(match, 1)

    ctx = module.verify_player_token(token)
    payload = await module.referee.build_player_status(ctx.match_id, ctx.player_id)

    assert payload["match_id"] == match.match_id
    assert payload["phase"] == "attack"
    assert payload["can_submit_flags"] is True
    assert payload["poll_after_seconds"] == 30
    assert payload["remaining_seconds"] >= 0

    assert payload["self"] == {
        "player_id": 1,
        "name": "P1",
        "model": None,
        "display_name": "P1 (P1)",
        "ready_status": "AGENT_READY",
        "ready_reason": None,
        "readiness_details": {
            "runtime_ready": False,
            "session_ready": False,
            "interactive_ready": False,
            "init_ready": False,
            "session_id": None,
        },
        "score": 150,
        "attack_score": 200,
        "defense_score": -50,
        "sla_score": 0,
        "sla_up": True,
        "sla_down_minutes": 0,
        "flags_captured": 2,
        "flags_lost": 1,
    }

    assert payload["leaderboard_summary"] == {
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
    assert payload["score_changes_since_last_query"] == {
        "has_previous_query": False,
        "previous_query_at": None,
        "current_query_at": payload["score_changes_since_last_query"]["current_query_at"],
        "players": [
            {
                "player_id": 1,
                "is_self": True,
                "total_delta": 0,
                "attack_delta": 0,
                "defense_delta": 0,
                "sla_delta": 0,
            },
            {
                "player_id": 2,
                "is_self": False,
                "total_delta": 0,
                "attack_delta": 0,
                "defense_delta": 0,
                "sla_delta": 0,
            },
            {
                "player_id": 3,
                "is_self": False,
                "total_delta": 0,
                "attack_delta": 0,
                "defense_delta": 0,
                "sla_delta": 0,
            },
        ],
    }
    assert payload["attack_context"] == {
        "enemy_targets": [
            {"player_id": 2, "ip": "10.200.0.2", "port": 3000},
            {"player_id": 3, "ip": "10.200.0.3", "port": 3000},
        ]
    }


@pytest.mark.asyncio
async def test_get_player_status_route_returns_payload_for_valid_token(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_player_status_route")

    match = _build_match(module)
    module.referee.matches[match.match_id] = match
    token = module.referee._issue_player_read_token(match, 1)

    payload = await module.get_player_status(module.verify_player_token(token))

    assert payload["match_id"] == match.match_id
    assert payload["schema_version"] == 2
    assert payload["leaderboard_summary"]["rank"] == 2
    assert payload["score_changes_since_last_query"]["has_previous_query"] is False
    assert payload["attack_context"]["enemy_targets"][0]["player_id"] == 2
    assert payload["self"]["readiness_details"] == {
        "runtime_ready": False,
        "session_ready": False,
        "interactive_ready": False,
        "init_ready": False,
        "session_id": None,
    }


@pytest.mark.asyncio
async def test_player_status_endpoint_returns_payload_via_http(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_player_status_http")

    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)
    monkeypatch.setattr(module.database, "init_db", _async_noop)
    monkeypatch.setattr(module.database, "load_all_matches", _async_empty_matches)

    match = _build_match(module)
    module.referee.matches[match.match_id] = match
    token = module.referee._issue_player_read_token(match, 1)

    transport = httpx.ASGITransport(app=module.app)
    async with module.app.router.lifespan_context(module.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.get("/api/player/status", headers={"X-Player-Token": token})

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 2
    assert payload["match_id"] == match.match_id
    assert payload["phase"] == "attack"
    assert payload["leaderboard_summary"]["rank"] == 2
    assert payload["score_changes_since_last_query"]["has_previous_query"] is False
    assert payload["self"]["player_id"] == 1
    assert payload["self"]["readiness_details"] == {
        "runtime_ready": False,
        "session_ready": False,
        "interactive_ready": False,
        "init_ready": False,
        "session_id": None,
    }
    assert payload["attack_context"]["enemy_targets"][0]["player_id"] == 2


@pytest.mark.asyncio
async def test_build_player_status_reports_score_deltas_since_previous_query(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_player_status_deltas")

    match = _build_match(module)
    module.referee.matches[match.match_id] = match
    token = module.referee._issue_player_read_token(match, 1)

    ctx = module.verify_player_token(token)
    first_payload = await module.referee.build_player_status(ctx.match_id, ctx.player_id)

    match.players[1].score += 100
    match.players[1].attack_score += 100
    match.players[1].flags_captured += 1
    match.players[2].score -= 50
    match.players[2].defense_score -= 50
    match.players[2].flags_lost += 1
    match.players[3].score -= 50
    match.players[3].sla_score -= 50
    match.players[3].sla_down_minutes += 1

    second_payload = await module.referee.build_player_status(ctx.match_id, ctx.player_id)

    assert first_payload["score_changes_since_last_query"]["has_previous_query"] is False
    assert second_payload["score_changes_since_last_query"] == {
        "has_previous_query": True,
        "previous_query_at": first_payload["score_changes_since_last_query"]["current_query_at"],
        "current_query_at": second_payload["score_changes_since_last_query"]["current_query_at"],
        "players": [
            {
                "player_id": 1,
                "is_self": True,
                "total_delta": 100,
                "attack_delta": 100,
                "defense_delta": 0,
                "sla_delta": 0,
            },
            {
                "player_id": 2,
                "is_self": False,
                "total_delta": -50,
                "attack_delta": 0,
                "defense_delta": -50,
                "sla_delta": 0,
            },
            {
                "player_id": 3,
                "is_self": False,
                "total_delta": -50,
                "attack_delta": 0,
                "defense_delta": 0,
                "sla_delta": -50,
            },
        ],
    }
