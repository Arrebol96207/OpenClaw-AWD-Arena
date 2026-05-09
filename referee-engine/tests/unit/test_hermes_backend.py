from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from agent_client import AgentSession  # noqa: E402
from backends.hermes_backend import HermesAgentClient, HermesBackendAdapter  # noqa: E402


def _build_match_config():
    return SimpleNamespace(
        agent_image="alpine/openclaw:latest",
        llm=SimpleNamespace(
            apiKey="global-key",
            baseUrl="https://example.test/v1",
            model="global-model",
            proxy="http://host.docker.internal:7897",
        ),
    )


def _build_match_state(match_id="match-test"):
    return SimpleNamespace(match_id=match_id, config=_build_match_config())


def _build_player_config(*, api_key=None, model=None, image=None, extra_env=None):
    return SimpleNamespace(
        id=7,
        apiKey=api_key,
        model=model,
        backend_config=SimpleNamespace(
            image=image,
            extra_env=extra_env or {},
        ),
    )


def test_hermes_backend_container_spec_exposes_wrapper_mounts_and_entrypoint():
    adapter = HermesBackendAdapter()
    match_config = _build_match_state()
    player_config = _build_player_config(model="player-model", extra_env={"CUSTOM_FLAG": "enabled"})

    spec = adapter.build_agent_container_spec(match_config, player_config)

    assert spec.image == "openclaw/hermes-agent:latest"
    assert spec.entrypoint == ["/bin/sh"]
    assert spec.command == ["-lc", "mkdir -p /opt/data/sessions /opt/data/logs /opt/data/home && sleep infinity"]
    assert spec.environment["OPENAI_API_KEY"] == "global-key"
    assert spec.environment["OPENAI_BASE_URL"] == "https://example.test/v1"
    assert spec.environment["OPENAI_MODEL"] == "player-model"
    assert spec.environment["HERMES_HOME"] == "/opt/data"
    assert spec.environment["CUSTOM_FLAG"] == "enabled"
    assert spec.volumes == {
        "openclaw_hermes_runtime_match-test_player_7": {"bind": "/opt/data", "mode": "rw"}
    }


def test_hermes_backend_runtime_volume_name_sanitizes_match_and_player_identity():
    adapter = HermesBackendAdapter()
    match_config = _build_match_state(match_id="match weird/id:42")
    player_config = _build_player_config()
    player_config.id = "player 9/blue"

    volume_name = adapter._resolve_runtime_volume_name(match_config, player_config)

    assert volume_name == "openclaw_hermes_runtime_match-weird-id-42_player_player-9-blue"


def test_hermes_client_build_agent_exec_command_uses_wrapper_runtime():
    client = HermesAgentClient(llm_api_key="test-key", llm_model="model-x")
    session = AgentSession(
        player_id=1,
        container_name="claw-test",
        target_container="target-test",
        target_ip="10.0.0.1",
    )

    command = client.build_agent_exec_command(session, "YWJj", 90)

    assert "openclaw_wrapper.py" in command
    assert "--timeout 90" in command
    assert "base64 -d" in command


def test_hermes_backend_resolves_target_ssh_for_hermes_runtime_home():
    adapter = HermesBackendAdapter()

    ssh_spec = adapter.resolve_target_ssh_spec(_build_match_state(), _build_player_config())

    assert ssh_spec.private_key_path == "/opt/data/home/.ssh/awd_target_key"
    assert ssh_spec.owner_user == "hermes"
    assert ssh_spec.owner_group == "hermes"
    assert ssh_spec.helper_path == "/usr/local/bin/target-ssh"


@pytest.mark.asyncio
async def test_hermes_backend_cleanup_removes_named_runtime_volume(monkeypatch):
    adapter = HermesBackendAdapter()
    commands = []
    fake_client = HermesAgentClient(llm_api_key="test-key")

    class FakeProc:
        returncode = 0

        async def communicate(self):
            return (b"deleted\n", b"")

    async def fake_create_subprocess_shell(command, stdout=None, stderr=None):
        commands.append(command)
        return FakeProc()

    monkeypatch.setattr("backends.hermes_backend.asyncio.create_subprocess_shell", fake_create_subprocess_shell)

    await adapter.cleanup(_build_match_state("match cleanup/42"), 7, None, fake_client)

    assert commands == ["docker volume rm openclaw_hermes_runtime_match-cleanup-42_player_7"]


@pytest.mark.asyncio
async def test_hermes_backend_cleanup_swallows_missing_volume_errors(monkeypatch, caplog):
    adapter = HermesBackendAdapter()
    fake_client = HermesAgentClient(llm_api_key="test-key")

    class FakeProc:
        returncode = 1

        async def communicate(self):
            return (b"", b"Error: No such volume")

    async def fake_create_subprocess_shell(command, stdout=None, stderr=None):
        return FakeProc()

    monkeypatch.setattr("backends.hermes_backend.asyncio.create_subprocess_shell", fake_create_subprocess_shell)

    await adapter.cleanup(_build_match_state("match-test"), 7, None, fake_client)

    assert "Failed to remove Hermes runtime volume" in caplog.text

@pytest.mark.asyncio
async def test_hermes_client_resolve_session_file_prefers_named_session(monkeypatch):
    client = HermesAgentClient(llm_api_key="test-key")
    session = AgentSession(
        player_id=1,
        container_name="claw-test",
        target_container="target-test",
        target_ip="10.0.0.1",
        session_id="session-123",
    )

    async def fake_exec(container_name, command, timeout=60, stream_callback=None, session=None, message_kind=None, message_mode=None):
        if "test -f /opt/data/sessions/session_session-123.json" in command:
            return "ok"
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(client, "_exec", fake_exec)

    resolved = await client._resolve_session_file(session)

    assert resolved == "/opt/data/sessions/session_session-123.json"


@pytest.mark.asyncio
async def test_hermes_client_resolve_session_file_falls_back_to_latest(monkeypatch):
    client = HermesAgentClient(llm_api_key="test-key")
    session = AgentSession(
        player_id=1,
        container_name="claw-test",
        target_container="target-test",
        target_ip="10.0.0.1",
        session_id="missing-session",
    )

    async def fake_exec(container_name, command, timeout=60, stream_callback=None, session=None, message_kind=None, message_mode=None):
        if "test -f /opt/data/sessions/session_missing-session.json" in command:
            return ""
        if "ls -t /opt/data/sessions/session_*.json" in command:
            return "/opt/data/sessions/session_latest.json"
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(client, "_exec", fake_exec)

    resolved = await client._resolve_session_file(session)

    assert resolved == "/opt/data/sessions/session_latest.json"
