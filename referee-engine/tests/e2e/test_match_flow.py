import importlib.util
import sys
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_main_module(module_name: str):
    main_path = ROOT / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, main_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


async def _async_noop(*args, **kwargs):
    return None


async def _async_empty_matches():
    return []


def _phase8_config_payload():
    return {
        "match": {
            "name": "Phase 8 HTTP Startup",
            "duration": 7200,
            "phases": {"defense": 600, "attack": 6600},
        },
        "llm": {
            "provider": "openai-completions",
            "baseUrl": "https://example.test/v1",
            "apiKey": "",
            "model": "test-model",
            "proxy": "http://host.docker.internal:7897",
        },
        "players": [
            {"id": 1, "name": "P1", "model": None, "apiKey": None, "gatewayPort": None},
        ],
        "scoring": {"attackSuccess": 100, "defenseFailure": -50, "slaViolation": -50},
        "flags": {"refreshInterval": 300, "format": "flag{{{hash}}}"},
        "network": {"arenaSubnet": "172.20.0.0/16", "mgmtSubnetPrefix": "172.21"},
        "target_image": "openclaw/ctf-target:v1",
        "agent_image": "openclaw/local-agent:ssh",
    }


@pytest.mark.asyncio
async def test_start_match_http_runs_phase8_ssh_key_startup_flow(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    monkeypatch.setenv("REFEREE_API_KEY", "test-api-key")
    module = _load_main_module("test_main_phase8_http_start_match")

    async def fake_setup_containers(match):
        player_id = 1
        match.players[player_id] = module.PlayerState(
            player_id=player_id,
            container_name="claw_match_phase8_http_1",
            target_container="target_match_phase8_http_1",
            target_ip="10.0.0.8",
            maintenance_auth_mode="ssh_key",
            maintenance_helper_command="target-ssh",
        )
        match.agent_sessions[player_id] = module.AgentSession(
            player_id=player_id,
            container_name="claw_match_phase8_http_1",
            target_container="target_match_phase8_http_1",
            target_ip="10.0.0.8",
        )
        match.player_ssh_key_materials[player_id] = module.PlayerSSHKeyMaterial(
            player_id=player_id,
            private_key="PRIVATE\n",
            public_key="PUBLIC\n",
            helper_path="/usr/local/bin/target-ssh",
        )

    async def fake_initialize_agents(match):
        player = match.players[1]
        ssh_key_material = match.player_ssh_key_materials[1]

        assert player.maintenance_auth_mode == "ssh_key"
        assert player.maintenance_helper_command == "target-ssh"
        assert ssh_key_material.helper_path == "/usr/local/bin/target-ssh"
        assert ssh_key_material.private_key_path == "/home/node/.ssh/awd_target_key"

        player.ready_status = "READY"
        player.ready_reason = "TARGET_SSH_READY"
        return 1

    async def fake_generate_and_inject(self, players):
        assert 1 in players
        return {1: {"database_flag": "FLAG{phase8}"}}

    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)
    monkeypatch.setattr(module.database, "init_db", _async_noop)
    monkeypatch.setattr(module.database, "load_all_matches", _async_empty_matches)
    monkeypatch.setattr(module.database, "save_match", _async_noop)
    monkeypatch.setattr(module.database, "update_match_status", _async_noop)
    monkeypatch.setattr(module.database, "save_event", _async_noop)
    monkeypatch.setattr(module.referee, "broadcast", _async_noop)
    monkeypatch.setattr(module.referee, "_setup_containers", fake_setup_containers)
    monkeypatch.setattr(module.referee, "_initialize_agents", fake_initialize_agents)
    monkeypatch.setattr(module.FlagManager, "generate_and_inject", fake_generate_and_inject)
    monkeypatch.setattr(module.referee, "_flag_refresh_loop", _async_noop)
    monkeypatch.setattr(module.referee, "_match_timer", _async_noop)
    monkeypatch.setattr(module.SLAChecker, "start", lambda self, players, broadcast_callback=None: None)

    config_payload = _phase8_config_payload()

    transport = httpx.ASGITransport(app=module.app)
    async with module.app.router.lifespan_context(module.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/matches/start", json=config_payload, headers={"X-API-Key": "test-api-key"})

            assert response.status_code == 200
            payload = response.json()
            assert payload["status"] == "initializing"

            match_id = payload["match_id"]
            match = module.referee.matches[match_id]
            await match._startup_task

            assert match.status == "defense"
            assert match.players[1].maintenance_auth_mode == "ssh_key"
            assert match.players[1].maintenance_helper_command == "target-ssh"
            assert match.players[1].ready_status == "READY"
            assert match.player_ssh_key_materials[1].helper_path == "/usr/local/bin/target-ssh"
            assert match.player_read_tokens[1]
            assert any(event["type"] == "MATCH_STARTED" for event in match.events)

            status_response = await client.get(
                "/api/player/status",
                headers={"X-Player-Token": match.player_read_tokens[1]},
            )

            assert status_response.status_code == 200
            status_payload = status_response.json()
            assert status_payload["phase"] == "defense"
            assert status_payload["can_submit_flags"] is False
            assert status_payload["self"]["player_id"] == 1
            assert status_payload["self"]["ready_status"] == "READY"


@pytest.mark.asyncio
async def test_start_match_http_waits_for_all_players_ready_before_entering_defense(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    monkeypatch.setenv("REFEREE_API_KEY", "test-api-key")
    module = _load_main_module("test_main_phase8_http_waits_all_ready_start_match")

    async def fake_setup_containers(match):
        player_id = 1
        match.players[player_id] = module.PlayerState(
            player_id=player_id,
            container_name="claw_match_phase8_http_1",
            target_container="target_match_phase8_http_1",
            target_ip="10.0.0.8",
            maintenance_auth_mode="ssh_key",
            maintenance_helper_command="target-ssh",
        )
        match.agent_sessions[player_id] = module.AgentSession(
            player_id=player_id,
            container_name="claw_match_phase8_http_1",
            target_container="target_match_phase8_http_1",
            target_ip="10.0.0.8",
        )
        match.player_ssh_key_materials[player_id] = module.PlayerSSHKeyMaterial(
            player_id=player_id,
            private_key="PRIVATE\n",
            public_key="PUBLIC\n",
            helper_path="/usr/local/bin/target-ssh",
        )

    async def fake_initialize_agents(match):
        match.players[1].ready_status = "AGENT_NOT_READY"
        match.players[1].ready_reason = "INIT_PROMPT_NO_RESPONSE"
        match.add_event(
            "AGENT_NOT_READY",
            {
                "player_id": 1,
                "ready_status": "AGENT_NOT_READY",
                "ready_reason": "INIT_PROMPT_NO_RESPONSE",
                "reason": "INIT_PROMPT_NO_RESPONSE",
                "details": "Agent returned no response to the initialization prompt",
            },
        )
        return 0

    wait_calls = []

    async def fake_wait_for_all_players_ready(match):
        wait_calls.append(match.match_id)
        assert match.status == "initializing_agents"
        match.players[1].ready_status = "AGENT_READY"
        match.players[1].ready_reason = "READY_RETRY_RESPONSE"

    async def fake_generate_and_inject(self, players):
        assert 1 in players
        assert players[1].ready_status == "AGENT_READY"
        return {1: {"database_flag": "FLAG{phase8}"}}

    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)
    monkeypatch.setattr(module.database, "init_db", _async_noop)
    monkeypatch.setattr(module.database, "load_all_matches", _async_empty_matches)
    monkeypatch.setattr(module.database, "save_match", _async_noop)
    monkeypatch.setattr(module.database, "update_match_status", _async_noop)
    monkeypatch.setattr(module.database, "save_event", _async_noop)
    monkeypatch.setattr(module.referee, "broadcast", _async_noop)
    monkeypatch.setattr(module.referee, "_setup_containers", fake_setup_containers)
    monkeypatch.setattr(module.referee, "_initialize_agents", fake_initialize_agents)
    monkeypatch.setattr(module.referee, "_wait_for_all_players_ready", fake_wait_for_all_players_ready)
    monkeypatch.setattr(module.FlagManager, "generate_and_inject", fake_generate_and_inject)
    monkeypatch.setattr(module.referee, "_flag_refresh_loop", _async_noop)
    monkeypatch.setattr(module.referee, "_match_timer", _async_noop)
    monkeypatch.setattr(module.SLAChecker, "start", lambda self, players, broadcast_callback=None: None)

    transport = httpx.ASGITransport(app=module.app)
    async with module.app.router.lifespan_context(module.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/matches/start", json=_phase8_config_payload(), headers={"X-API-Key": "test-api-key"})

            assert response.status_code == 200
            match_id = response.json()["match_id"]
            match = module.referee.matches[match_id]
            await match._startup_task

            assert wait_calls == [match_id]
            assert match.status == "defense"
            assert match.players[1].ready_status == "AGENT_READY"
            assert match.players[1].ready_reason == "READY_RETRY_RESPONSE"
            assert any(event["type"] == "AGENT_NOT_READY" for event in match.events)

            status_response = await client.get(
                "/api/player/status",
                headers={"X-Player-Token": match.player_read_tokens[1]},
            )

            assert status_response.status_code == 200
            status_payload = status_response.json()
            assert status_payload["phase"] == "defense"
            assert status_payload["self"]["ready_status"] == "AGENT_READY"


@pytest.mark.asyncio
async def test_start_match_http_records_match_error_when_phase8_container_setup_fails(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    monkeypatch.setenv("REFEREE_API_KEY", "test-api-key")
    module = _load_main_module("test_main_phase8_http_match_error")

    async def fake_setup_containers(_match):
        raise RuntimeError("phase8 setup failed")

    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)
    monkeypatch.setattr(module.database, "init_db", _async_noop)
    monkeypatch.setattr(module.database, "load_all_matches", _async_empty_matches)
    monkeypatch.setattr(module.database, "save_match", _async_noop)
    monkeypatch.setattr(module.database, "update_match_status", _async_noop)
    monkeypatch.setattr(module.database, "save_event", _async_noop)
    monkeypatch.setattr(module.referee, "broadcast", _async_noop)
    monkeypatch.setattr(module.referee, "_setup_containers", fake_setup_containers)

    transport = httpx.ASGITransport(app=module.app)
    async with module.app.router.lifespan_context(module.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/matches/start", json=_phase8_config_payload(), headers={"X-API-Key": "test-api-key"})

            assert response.status_code == 200
            match_id = response.json()["match_id"]
            match = module.referee.matches[match_id]
            match.player_ssh_key_materials[1] = module.PlayerSSHKeyMaterial(
                player_id=1,
                private_key="PRIVATE\n",
                public_key="PUBLIC\n",
                helper_path="/usr/local/bin/target-ssh",
            )

            await match._startup_task

            assert match.status == "error"
            assert match.player_ssh_key_materials == {}
            match_error_events = [event for event in match.events if event["type"] == "MATCH_ERROR"]
            assert match_error_events
            assert match_error_events[-1]["data"] == {"error": "phase8 setup failed"}

            detail_response = await client.get(f"/api/matches/{match_id}", headers={"X-API-Key": "test-api-key"})
            assert detail_response.status_code == 200
            detail_payload = detail_response.json()
            assert detail_payload["status"] == "error"
