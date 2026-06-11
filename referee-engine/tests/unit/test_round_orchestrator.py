import hashlib
import json
import sys
from pathlib import Path
from typing import Optional

import pytest


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import orchestrator.round_orchestrator as round_orchestrator  # noqa: E402


def _minimal_config() -> dict:
    return {
        "players": [{"id": 1, "name": "P1", "apiKey": "player-key"}],
        "llm": {"apiKey": "global-key", "proxy": "http://proxy.test:7897"},
        "target_image": "openclaw/ctf-target:test",
        "agent_image": "alpine/openclaw:test",
    }


class _FakeNetwork:
    def __init__(self, name: str, *, subnet: Optional[str] = None):
        self.name = name
        self.removed = False
        self.attrs = {"IPAM": {"Config": [{"Subnet": subnet}] if subnet else []}}

    def remove(self):
        self.removed = True


class _FakeNetworks:
    def __init__(self, create_result=None, get_result=None, create_error=None, get_error=None, existing=None):
        self.create_result = create_result
        self.get_result = get_result
        self.create_error = create_error
        self.get_error = get_error
        self.existing = existing or []
        self.create_calls = []
        self.get_calls = []
        self.list_calls = 0

    def create(self, name: str, **kwargs):
        self.create_calls.append({"name": name, "kwargs": kwargs})
        if self.create_error is not None:
            raise self.create_error
        return self.create_result or _FakeNetwork(name)

    def get(self, name: str):
        self.get_calls.append(name)
        if self.get_error is not None:
            raise self.get_error
        return self.get_result or _FakeNetwork(name)

    def list(self):
        self.list_calls += 1
        return self.existing


class _FakeContainer:
    def __init__(self, *, container_id: Optional[str] = "container-id", status="running", stats_payload=None):
        self.id = container_id
        self.status = status
        self._stats_payload = stats_payload or {}
        self.reload_called = False
        self.stop_called_with = None
        self.removed = False

    def reload(self):
        self.reload_called = True

    def stats(self, *, stream=False):
        assert stream is False
        return self._stats_payload

    def stop(self, timeout=10):
        self.stop_called_with = timeout

    def remove(self):
        self.removed = True


class _FakeContainers:
    def __init__(self, *, run_result=None, named=None):
        self.run_result = run_result
        self.named = named or {}
        self.run_calls = []

    def run(self, *args, **kwargs):
        self.run_calls.append({"args": args, "kwargs": kwargs})
        return self.run_result

    def get(self, name: str):
        value = self.named[name]
        if isinstance(value, Exception):
            raise value
        return value


class _FakeDockerClient:
    def __init__(self, *, networks=None, containers=None):
        self.networks = networks or _FakeNetworks()
        self.containers = containers or _FakeContainers()


def _build_orchestrator(monkeypatch, client: _FakeDockerClient) -> round_orchestrator.RoundOrchestrator:
    monkeypatch.setattr(round_orchestrator.docker, "from_env", lambda: client)
    return round_orchestrator.RoundOrchestrator("match_test", _minimal_config())


@pytest.mark.asyncio
async def test_write_openclaw_config_streams_json_without_docker_cp(monkeypatch):
    client = _FakeDockerClient()
    orchestrator = _build_orchestrator(monkeypatch, client)
    observed = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self, input=None):
            observed["stdin"] = input
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["args"] = args
        observed["stdin_pipe"] = kwargs.get("stdin")
        return FakeProcess()

    monkeypatch.setattr(round_orchestrator.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    ok = await orchestrator._write_openclaw_config_to_container(
        "claw-test",
        {"models": {"providers": {"routerss": {"apiKey": "secret-key"}}}},
    )

    assert ok is True
    assert observed["args"][:6] == ("docker", "exec", "-u", "root", "-i", "claw-test")
    assert "cp" not in observed["args"]
    assert observed["stdin_pipe"] == round_orchestrator.asyncio.subprocess.PIPE
    payload = json.loads(observed["stdin"].decode("utf-8"))
    assert payload["models"]["providers"]["routerss"]["apiKey"] == "secret-key"


def test_create_network_builds_ipam_config_with_explicit_helpers(monkeypatch):
    pool_calls = []
    config_calls = []

    def _fake_ipam_pool(*, subnet: str, gateway: str):
        pool = {"subnet": subnet, "gateway": gateway}
        pool_calls.append(pool)
        return pool

    def _fake_ipam_config(*, pool_configs):
        config = {"pool_configs": pool_configs}
        config_calls.append(config)
        return config

    created_network = _FakeNetwork("awd_match_test")
    client = _FakeDockerClient(networks=_FakeNetworks(create_result=created_network))
    orchestrator = _build_orchestrator(monkeypatch, client)

    monkeypatch.setattr(round_orchestrator, "IPAMPool", _fake_ipam_pool)
    monkeypatch.setattr(round_orchestrator, "IPAMConfig", _fake_ipam_config)

    network = orchestrator._create_network()

    match_hash = int(hashlib.md5(orchestrator.match_id.encode()).hexdigest()[:4], 16) % 256
    expected_pool = {"subnet": f"10.201.{match_hash}.0/24", "gateway": f"10.201.{match_hash}.1"}

    assert pool_calls == [expected_pool]
    assert config_calls == [{"pool_configs": [expected_pool]}]
    assert client.networks.create_calls == [
        {
            "name": orchestrator.topology.network_name,
            "kwargs": {
                "driver": "bridge",
                "check_duplicate": True,
                "ipam": {"pool_configs": [expected_pool]},
            },
        }
    ]
    assert network is created_network


def test_create_network_skips_existing_overlapping_subnets(monkeypatch):
    pool_calls = []

    def _fake_ipam_pool(*, subnet: str, gateway: str):
        pool = {"subnet": subnet, "gateway": gateway}
        pool_calls.append(pool)
        return pool

    def _fake_ipam_config(*, pool_configs):
        return {"pool_configs": pool_configs}

    client = _FakeDockerClient(networks=_FakeNetworks())
    orchestrator = _build_orchestrator(monkeypatch, client)
    match_hash = int(hashlib.md5(orchestrator.match_id.encode()).hexdigest()[:4], 16) % 256
    occupied = _FakeNetwork("occupied", subnet=f"10.201.{match_hash}.0/24")
    client.networks.existing = [occupied]

    monkeypatch.setattr(round_orchestrator, "IPAMPool", _fake_ipam_pool)
    monkeypatch.setattr(round_orchestrator, "IPAMConfig", _fake_ipam_config)

    orchestrator._create_network()

    expected_next = (match_hash + 1) % 256
    assert client.networks.list_calls == 1
    assert pool_calls == [{"subnet": f"10.201.{expected_next}.0/24", "gateway": f"10.201.{expected_next}.1"}]


def test_create_network_raises_when_candidate_pool_is_exhausted(monkeypatch):
    occupied = [_FakeNetwork(f"occupied-{i}", subnet=f"10.201.{i}.0/24") for i in range(256)]
    client = _FakeDockerClient(networks=_FakeNetworks(existing=occupied))
    orchestrator = _build_orchestrator(monkeypatch, client)

    with pytest.raises(RuntimeError, match="No available Docker subnet"):
        orchestrator._create_network()

    assert client.networks.create_calls == []


def test_create_network_reuses_existing_network_on_already_exists(monkeypatch):
    existing_network = _FakeNetwork("awd_match_test")
    client = _FakeDockerClient(
        networks=_FakeNetworks(
            create_error=round_orchestrator.APIError("network already exists"),
            get_result=existing_network,
        )
    )
    orchestrator = _build_orchestrator(monkeypatch, client)

    network = orchestrator._create_network()

    assert network is existing_network
    assert client.networks.get_calls == [orchestrator.topology.network_name]


def test_create_target_container_passes_restart_policy_and_records_container_id(monkeypatch):
    fake_container = _FakeContainer(container_id="target-cid")
    client = _FakeDockerClient(containers=_FakeContainers(run_result=fake_container))
    orchestrator = _build_orchestrator(monkeypatch, client)

    info = orchestrator._create_target_container(7, _FakeNetwork("arena-net"))

    run_call = client.containers.run_calls[0]
    assert run_call["args"] == ("openclaw/ctf-target:test",)
    assert run_call["kwargs"]["network"] == "arena-net"
    assert run_call["kwargs"]["restart_policy"] == round_orchestrator.CONTAINER_RESTART_POLICY
    assert run_call["kwargs"]["mem_limit"] == "1g"
    assert run_call["kwargs"]["environment"]["TZ"] == round_orchestrator.CONTAINER_TIMEZONE
    assert len([key for key in run_call["kwargs"]["environment"] if key.startswith("FLAG_")]) == 6
    assert info.container_id == "target-cid"
    assert info.role == "target"
    assert info.player_id == 7


def test_create_agent_container_raises_when_container_id_missing(monkeypatch):
    fake_container = _FakeContainer(container_id=None)
    client = _FakeDockerClient(containers=_FakeContainers(run_result=fake_container))
    orchestrator = _build_orchestrator(monkeypatch, client)

    with pytest.raises(RuntimeError, match="returned no container id"):
        orchestrator._create_agent_container(
            9,
            {"apiKey": "player-specific-key"},
            {"apiKey": "global-key"},
            "http://proxy.test:7897",
            _FakeNetwork("arena-net"),
        )

    run_call = client.containers.run_calls[0]
    assert run_call["args"] == ("alpine/openclaw:test",)
    assert run_call["kwargs"]["restart_policy"] == round_orchestrator.CONTAINER_RESTART_POLICY
    assert run_call["kwargs"]["environment"]["OPENAI_API_KEY"] == "player-specific-key"
    assert run_call["kwargs"]["environment"]["HTTPS_PROXY"] == "http://proxy.test:7897"


def test_create_agent_container_uses_local_ssh_image_by_default(monkeypatch):
    fake_container = _FakeContainer(container_id="agent-cid")
    client = _FakeDockerClient(containers=_FakeContainers(run_result=fake_container))
    orchestrator = _build_orchestrator(monkeypatch, client)
    orchestrator.config.pop("agent_image")

    orchestrator._create_agent_container(
        4,
        {"apiKey": "player-key"},
        {"apiKey": "global-key"},
        "http://proxy.test:7897",
        _FakeNetwork("arena-net"),
    )

    run_call = client.containers.run_calls[0]
    assert run_call["args"] == ("openclaw/local-agent:ssh",)


def test_create_agent_container_passes_player_llm_endpoint_overrides(monkeypatch):
    fake_container = _FakeContainer(container_id="agent-cid")
    client = _FakeDockerClient(containers=_FakeContainers(run_result=fake_container))
    orchestrator = _build_orchestrator(monkeypatch, client)

    info = orchestrator._create_agent_container(
        3,
        {
            "apiKey": "player-key",
            "baseUrl": "https://player-api.test/v1",
            "model": "player-model",
            "api": "anthropic",
        },
        {
            "apiKey": "global-key",
            "baseUrl": "https://global-api.test/v1",
            "model": "global-model",
            "provider": "OpenAI",
        },
        "http://proxy.test:7897",
        _FakeNetwork("arena-net"),
    )

    env = client.containers.run_calls[0]["kwargs"]["environment"]
    assert info.container_id == "agent-cid"
    assert env["OPENAI_API_KEY"] == "player-key"
    assert env["OPENAI_BASE_URL"] == "https://player-api.test/v1"
    assert env["OPENAI_MODEL"] == "player-model"
    assert env["OPENCLAW_PROVIDER_API"] == "anthropic"


def test_get_container_stats_computes_metrics_from_non_streaming_stats(monkeypatch):
    stats_payload = {
        "cpu_stats": {"cpu_usage": {"total_usage": 300}, "system_cpu_usage": 2_000},
        "precpu_stats": {"cpu_usage": {"total_usage": 100}, "system_cpu_usage": 1_000},
        "memory_stats": {"usage": 50 * 1024 * 1024, "limit": 200 * 1024 * 1024},
    }
    agent_container = _FakeContainer(container_id="agent-cid", status="running", stats_payload=stats_payload)
    target_container = _FakeContainer(container_id="target-cid", status="running", stats_payload={})
    client = _FakeDockerClient(
        containers=_FakeContainers(
            named={
                "claw_match_test_1": agent_container,
                "target_match_test_1": target_container,
            }
        )
    )
    orchestrator = _build_orchestrator(monkeypatch, client)
    orchestrator.topology.containers = {
        "claw_match_test_1": round_orchestrator.ContainerInfo(
            name="claw_match_test_1",
            container_id="agent-cid",
            ip_address="10.0.0.10",
            role="agent",
            player_id=1,
        ),
        "target_match_test_1": round_orchestrator.ContainerInfo(
            name="target_match_test_1",
            container_id="target-cid",
            ip_address="10.0.0.20",
            role="target",
            player_id=1,
        ),
    }

    stats = orchestrator.get_container_stats()

    assert agent_container.reload_called is True
    assert stats == {
        1: {
            "status": "running",
            "cpu_percent": 20.0,
            "memory_mb": 50.0,
            "memory_limit_mb": 200.0,
            "ip_address": "10.0.0.10",
        }
    }
