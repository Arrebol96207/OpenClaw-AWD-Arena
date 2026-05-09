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


def _loop_config_payload(repeat_count: int = 3):
    return {
        "match": {
            "name": "Loop Match",
            "duration": 1200,
            "phases": {"defense": 600, "attack": 600},
        },
        "loop": {
            "enabled": True,
            "repeatCount": repeat_count,
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
        "agent_image": "alpine/openclaw:latest",
    }


@pytest.mark.asyncio
async def test_start_match_creates_loop_record_and_lists_it(monkeypatch, tmp_path):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    monkeypatch.setenv("OPENCLAW_DB_PATH", str(tmp_path / "loop-start.db"))
    module = _load_main_module("test_main_loop_start")

    async def fake_setup_containers(match):
        player_id = 1
        match.players[player_id] = module.PlayerState(
            player_id=player_id,
            container_name="claw_loop_1",
            target_container="target_loop_1",
            target_ip="10.0.0.8",
            network_name="awd_loop_network",
            maintenance_auth_mode="ssh_key",
            maintenance_helper_command="target-ssh",
        )
        match.agent_sessions[player_id] = module.AgentSession(
            player_id=player_id,
            container_name="claw_loop_1",
            target_container="target_loop_1",
            target_ip="10.0.0.8",
        )
        match.player_ssh_key_materials[player_id] = module.PlayerSSHKeyMaterial(
            player_id=player_id,
            private_key="PRIVATE\n",
            public_key="PUBLIC\n",
            helper_path="/usr/local/bin/target-ssh",
        )

    async def fake_initialize_agents(match):
        match.players[1].ready_status = "READY"
        match.players[1].ready_reason = "TARGET_SSH_READY"
        return 1

    async def fake_generate_and_inject(self, players):
        return {1: {"database_flag": "FLAG{loop}"}}

    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)
    monkeypatch.setattr(module.referee, "broadcast", _async_noop)
    monkeypatch.setattr(module.referee, "_setup_containers", fake_setup_containers)
    monkeypatch.setattr(module.referee, "_initialize_agents", fake_initialize_agents)
    monkeypatch.setattr(module.FlagManager, "generate_and_inject", fake_generate_and_inject)
    monkeypatch.setattr(module.referee, "_flag_refresh_loop", _async_noop)
    monkeypatch.setattr(module.referee, "_match_timer", _async_noop)
    monkeypatch.setattr(module.SLAChecker, "start", lambda self, players, broadcast_callback=None: None)

    transport = httpx.ASGITransport(app=module.app)
    async with module.app.router.lifespan_context(module.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/api/matches/start", json=_loop_config_payload())
            assert response.status_code == 200
            payload = response.json()
            match_id = payload["match_id"]
            loop_id = payload["loop_id"]
            assert loop_id
            assert payload["current_iteration"] == 1
            assert payload["repeat_count"] == 3

            match = module.referee.matches[match_id]
            await match._startup_task

            loops_response = await client.get("/api/loops")
            assert loops_response.status_code == 200
            loops_payload = loops_response.json()["loops"]
            assert len(loops_payload) == 1
            assert loops_payload[0]["loop_id"] == loop_id
            assert loops_payload[0]["repeat_count"] == 3
            assert loops_payload[0]["current_iteration"] == 1
            assert loops_payload[0]["current_match_id"] == match_id
            assert loops_payload[0]["status"] == "running"


@pytest.mark.asyncio
async def test_destroy_match_starts_next_loop_iteration_after_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    monkeypatch.setenv("OPENCLAW_DB_PATH", str(tmp_path / "loop-destroy.db"))
    module = _load_main_module("test_main_loop_destroy")

    class _FakeContainer:
        def stop(self, timeout=10):
            return None

        def remove(self):
            return None

    class _FakeNetwork:
        def remove(self):
            return None

    class _FakeCollection:
        def get(self, name):
            return _FakeContainer()

    class _FakeNetworks:
        def get(self, name):
            return _FakeNetwork()

    class _FakeDockerClient:
        containers = _FakeCollection()
        networks = _FakeNetworks()

    monkeypatch.setattr(module.docker, "from_env", lambda: _FakeDockerClient())
    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)
    monkeypatch.setattr(module.referee, "broadcast", _async_noop)

    async with module.app.router.lifespan_context(module.app):
        loop_id = "loop_test_chain"
        config = module.MatchConfig(**_loop_config_payload(repeat_count=2))
        config.loop.loopId = loop_id
        config.loop.currentIteration = 1

        match = module.MatchState("match_loop_1", config)
        match.status = "finished"
        match.players[1] = module.PlayerState(
            player_id=1,
            container_name="claw_loop_1",
            target_container="target_loop_1",
            target_ip="10.0.0.8",
            network_name="awd_loop_network",
        )
        module.referee.matches[match.match_id] = match

        now = module.datetime.now()
        await module.database.save_loop(
            loop_id=loop_id,
            status="running",
            repeat_count=2,
            current_iteration=1,
            current_match_id=match.match_id,
            last_match_id=None,
            config_dict=config.model_dump(),
            created_at=now,
            updated_at=now,
        )

        started_configs = []

        async def fake_start_match(next_config):
            started_configs.append(next_config)
            return {"match_id": "match_loop_2", "status": "initializing"}

        monkeypatch.setattr(module.referee, "start_match", fake_start_match)

        await module.referee.destroy_match(match.match_id)

        assert len(started_configs) == 1
        assert started_configs[0].loop.loopId == loop_id
        assert started_configs[0].loop.currentIteration == 2
        assert started_configs[0].loop.repeatCount == 2

        loop_row = await module.database.get_loop(loop_id)
        assert loop_row is not None
        assert loop_row["status"] == "running"
        assert loop_row["current_iteration"] == 2
        assert loop_row["current_match_id"] == "match_loop_2"
        assert loop_row["last_match_id"] == match.match_id
