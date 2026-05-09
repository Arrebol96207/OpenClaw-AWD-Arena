import importlib.util
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


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


class _ImmediateLoop:
    async def run_in_executor(self, _executor, func):
        return func()


class _TempDirFactory:
    def __init__(self, path: Path):
        self.path = path

    def __call__(self, prefix: str = ""):
        return self

    def __enter__(self):
        self.path.mkdir(parents=True, exist_ok=True)
        return str(self.path)

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_generate_player_ssh_keypair_reads_generated_material(tmp_path, monkeypatch):
    main = _load_main_module("test_main_ssh_keygen_success")
    engine = main.RefereeEngine()

    monkeypatch.setattr(main.asyncio, "get_running_loop", lambda: _ImmediateLoop())
    monkeypatch.setattr(main.tempfile, "TemporaryDirectory", _TempDirFactory(tmp_path / "ssh-keygen-success"))

    def fake_run(command, check, capture_output, text):
        assert command[:4] == ["ssh-keygen", "-q", "-t", "ed25519"]
        key_path = Path(command[-1])
        key_path.write_text("PRIVATE-KEY\n", encoding="utf-8")
        key_path.with_suffix(".pub").write_text("PUBLIC-KEY\n", encoding="utf-8")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    material = await engine._generate_player_ssh_keypair("match_1", 7)

    assert material.player_id == 7
    assert material.private_key == "PRIVATE-KEY\n"
    assert material.public_key == "PUBLIC-KEY\n"
    assert material.private_key_path == "/home/node/.ssh/awd_target_key"
    assert material.helper_path is None
    assert material.key_type == "ed25519"


@pytest.mark.asyncio
async def test_generate_player_ssh_keypair_is_distinct_per_player(tmp_path, monkeypatch):
    main = _load_main_module("test_main_ssh_keygen_distinct_players")
    engine = main.RefereeEngine()

    monkeypatch.setattr(main.asyncio, "get_running_loop", lambda: _ImmediateLoop())

    created_dirs = []

    def fake_tempdir(prefix: str = ""):
        path = tmp_path / prefix.rstrip("_")
        created_dirs.append(path)
        return _TempDirFactory(path)

    monkeypatch.setattr(main.tempfile, "TemporaryDirectory", fake_tempdir)

    def fake_run(command, check, capture_output, text):
        comment = command[command.index("-C") + 1]
        key_path = Path(command[-1])
        key_path.write_text(f"PRIVATE::{comment}\n", encoding="utf-8")
        key_path.with_suffix(".pub").write_text(f"PUBLIC::{comment}\n", encoding="utf-8")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    player_one = await engine._generate_player_ssh_keypair("match_distinct", 1)
    player_two = await engine._generate_player_ssh_keypair("match_distinct", 2)

    assert player_one.private_key != player_two.private_key
    assert player_one.public_key != player_two.public_key
    assert player_one.private_key == "PRIVATE::awd:match_distinct:1\n"
    assert player_two.private_key == "PRIVATE::awd:match_distinct:2\n"
    assert len(created_dirs) == 2
    assert created_dirs[0] != created_dirs[1]


@pytest.mark.asyncio
async def test_generate_player_ssh_keypair_raises_runtime_error_on_failure(monkeypatch):
    main = _load_main_module("test_main_ssh_keygen_failure")
    engine = main.RefereeEngine()

    monkeypatch.setattr(main.asyncio, "get_running_loop", lambda: _ImmediateLoop())

    def fake_run(command, check, capture_output, text):
        raise subprocess.CalledProcessError(1, command, stderr="permission denied")

    monkeypatch.setattr(main.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="ssh-keygen failed for player 3: permission denied"):
        await engine._generate_player_ssh_keypair("match_2", 3)


class _FakeContainer:
    def stop(self, timeout=10):
        return None

    def remove(self):
        return None


class _FakeContainers:
    def __init__(self):
        self.run_calls = []

    def run(self, image, **kwargs):
        self.run_calls.append({"image": image, **kwargs})
        return _FakeContainer()

    def get(self, name):
        return _FakeContainer()


class _FakeNetwork:
    def remove(self):
        return None


class _FakeNetworks:
    def __init__(self):
        self.created = []

    def create(self, name, **kwargs):
        self.created.append({"name": name, **kwargs})
        return _FakeNetwork()

    def get(self, name):
        return _FakeNetwork()


class _FakeDockerClient:
    def __init__(self):
        self.containers = _FakeContainers()
        self.networks = _FakeNetworks()


@pytest.mark.asyncio
async def test_destroy_match_clears_player_ssh_key_materials(monkeypatch):
    main = _load_main_module("test_main_ssh_keygen_destroy")
    engine = main.RefereeEngine()
    config = main.MatchConfig(players=[main.PlayerConfig(id=1, name="P1")])
    match = main.MatchState("match_destroy", config)
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.player_ssh_key_materials[1] = main.PlayerSSHKeyMaterial(
        player_id=1,
        private_key="PRIVATE",
        public_key="PUBLIC",
    )
    engine.matches[match.match_id] = match

    monkeypatch.setattr(main.docker, "from_env", lambda: _FakeDockerClient())

    await engine.destroy_match(match.match_id)

    assert match.player_ssh_key_materials == {}


def test_player_state_defaults_to_ssh_key_maintenance_metadata():
    player = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")

    assert player.maintenance_auth_mode == "ssh_key"
    assert player.maintenance_helper_command == "target-ssh"


class _FakeProcess:
    def __init__(self, stdout: bytes, returncode: int = 0, stderr: bytes = b"", on_communicate=None):
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._on_communicate = on_communicate

    async def communicate(self, input=None):
        if self._on_communicate is not None:
            self._on_communicate(input)
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_setup_containers_passes_public_key_to_target_env(monkeypatch):
    main = _load_main_module("test_main_ssh_keygen_setup")
    engine = main.RefereeEngine()
    config = main.MatchConfig(players=[main.PlayerConfig(id=1, name="P1")])
    match = main.MatchState("match_setup", config)
    fake_client = _FakeDockerClient()

    async def fake_generate(match_id, player_id):
        return main.PlayerSSHKeyMaterial(
            player_id=player_id,
            private_key="PRIVATE",
            public_key="PUBLIC\n",
        )

    async def fake_create_subprocess_shell(command, stdout=None, stderr=None):
        if command.startswith("docker inspect --format"):
            return _FakeProcess(b"10.0.0.8\n")
        if command.startswith("docker exec"):
            return _FakeProcess(b"ok\n", returncode=0)
        raise AssertionError(command)

    async def fake_create_subprocess_exec(*command, stdin=None, stdout=None, stderr=None):
        if command[:6] == ("docker", "exec", "-i", "-u", "root", "claw_match_setup_1"):
            return _FakeProcess(b"")
        if command[:3] == ("docker", "exec", "claw_match_setup_1"):
            return _FakeProcess(b"ok\n")
        raise AssertionError(command)

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(main.docker, "from_env", lambda: fake_client)
    monkeypatch.setattr(main, "_choose_available_subnet", lambda client, subnets: (subnets[0], "10.100.1.1"))
    monkeypatch.setattr(engine, "_generate_player_ssh_keypair", fake_generate)
    monkeypatch.setattr(main.asyncio, "create_subprocess_shell", fake_create_subprocess_shell)
    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    await engine._setup_containers(match)

    target_run = next(call for call in fake_client.containers.run_calls if call["name"] == "target_match_setup_1")
    assert target_run["environment"]["MAINTENANCE_AUTHORIZED_KEY"] == "PUBLIC"


@pytest.mark.asyncio
async def test_setup_containers_installs_private_key_and_target_ssh_helper(monkeypatch):
    main = _load_main_module("test_main_ssh_keygen_agent_install")
    engine = main.RefereeEngine()
    config = main.MatchConfig(players=[main.PlayerConfig(id=1, name="P1")])
    match = main.MatchState("match_setup", config)
    fake_client = _FakeDockerClient()
    exec_calls = []

    async def fake_generate(match_id, player_id):
        return main.PlayerSSHKeyMaterial(
            player_id=player_id,
            private_key="PRIVATE\n",
            public_key="PUBLIC\n",
        )

    async def fake_create_subprocess_shell(command, stdout=None, stderr=None):
        if command.startswith("docker inspect --format"):
            return _FakeProcess(b"10.0.0.8\n")
        if command.startswith("docker exec"):
            return _FakeProcess(b"ok\n", returncode=0)
        raise AssertionError(command)

    async def fake_create_subprocess_exec(*command, stdin=None, stdout=None, stderr=None):
        def on_communicate(input_bytes):
            exec_calls.append({
                "command": command,
                "stdin": input_bytes.decode("utf-8") if input_bytes else None,
            })

        if command[:6] == ("docker", "exec", "-i", "-u", "root", "claw_match_setup_1"):
            return _FakeProcess(b"", on_communicate=on_communicate)
        if command[:3] == ("docker", "exec", "claw_match_setup_1"):
            return _FakeProcess(b"ok\n", on_communicate=on_communicate)
        raise AssertionError(command)

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(main.docker, "from_env", lambda: fake_client)
    monkeypatch.setattr(main, "_choose_available_subnet", lambda client, subnets: (subnets[0], "10.100.1.1"))
    monkeypatch.setattr(engine, "_generate_player_ssh_keypair", fake_generate)
    monkeypatch.setattr(main.asyncio, "create_subprocess_shell", fake_create_subprocess_shell)
    monkeypatch.setattr(main.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    await engine._setup_containers(match)

    private_key_write = next(
        call for call in exec_calls
        if call["stdin"] == "PRIVATE\n"
    )
    assert private_key_write["command"][:6] == (
        "docker", "exec", "-i", "-u", "root", "claw_match_setup_1"
    )
    assert private_key_write["command"][-1].startswith(
        "mkdir -p /home/node/.ssh && chmod 700 /home/node/.ssh && cat > /home/node/.ssh/awd_target_key"
    )

    helper_write = next(
        call for call in exec_calls
        if call["stdin"] and call["stdin"].startswith("#!/bin/sh\nset -eu\n")
    )
    assert "exec ssh -i /home/node/.ssh/awd_target_key" in helper_write["stdin"]
    assert "defender@10.0.0.8 \"$@\"" in helper_write["stdin"]
    assert match.player_ssh_key_materials[1].helper_path == "/usr/local/bin/target-ssh"
    assert match.players[1].maintenance_auth_mode == "ssh_key"
    assert match.players[1].maintenance_helper_command == "target-ssh"


@pytest.mark.asyncio
async def test_verify_agent_target_ssh_uses_ready_probe(monkeypatch):
    main = _load_main_module("test_main_verify_agent_target_ssh_ready_probe")
    engine = main.RefereeEngine()
    docker_exec_calls = []

    async def fake_docker_exec(container_name, command, **kwargs):
        docker_exec_calls.append({
            "container_name": container_name,
            "command": command,
            "kwargs": kwargs,
        })
        return "ready\n"

    monkeypatch.setattr(engine, "_docker_exec", fake_docker_exec)

    await engine._verify_agent_target_ssh(1, "claw_match_setup_1", "/usr/local/bin/target-ssh", retries=1)

    assert docker_exec_calls == [{
        "container_name": "claw_match_setup_1",
        "command": ["sh", "-lc", "/usr/local/bin/target-ssh 'echo ready'"],
        "kwargs": {"timeout": 15},
    }]


@pytest.mark.asyncio
async def test_verify_agent_target_ssh_retries_until_ready(monkeypatch):
    main = _load_main_module("test_main_verify_agent_target_ssh_retries")
    engine = main.RefereeEngine()
    probe_commands = []

    async def fake_docker_exec(container_name, command, **kwargs):
        probe_commands.append((container_name, command, kwargs))
        if len(probe_commands) == 1:
            raise RuntimeError("docker exec failed for claw: ssh: connect to host 10.0.0.8 port 22: Connection refused")
        return "ready\n"

    sleep_calls = []

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        return None

    monkeypatch.setattr(engine, "_docker_exec", fake_docker_exec)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    await engine._verify_agent_target_ssh(1, "claw_match_setup_1", "/usr/local/bin/target-ssh", retries=3, delay_seconds=2)

    assert len(probe_commands) == 2
    assert probe_commands[0][1] == ["sh", "-lc", "/usr/local/bin/target-ssh 'echo ready'"]
    assert probe_commands[1][1] == ["sh", "-lc", "/usr/local/bin/target-ssh 'echo ready'"]
    assert sleep_calls == [2]


@pytest.mark.asyncio
async def test_verify_agent_target_ssh_raises_classified_error_after_retry_exhaustion(monkeypatch):
    main = _load_main_module("test_main_verify_agent_target_ssh_retry_exhaustion")
    engine = main.RefereeEngine()
    sleep_calls = []

    async def fake_docker_exec(container_name, command, **kwargs):
        assert container_name == "claw_match_setup_1"
        assert command == ["sh", "-lc", "/usr/local/bin/target-ssh 'echo ready'"]
        assert kwargs == {"timeout": 15}
        raise RuntimeError(
            "docker exec failed for claw_match_setup_1: "
            "defender@10.0.0.8: Permission denied (publickey,password)."
        )

    async def fake_sleep(seconds):
        sleep_calls.append(seconds)
        return None

    monkeypatch.setattr(engine, "_docker_exec", fake_docker_exec)
    monkeypatch.setattr(main.asyncio, "sleep", fake_sleep)

    with pytest.raises(main.TargetSSHProbeError) as exc_info:
        await engine._verify_agent_target_ssh(
            1,
            "claw_match_setup_1",
            "/usr/local/bin/target-ssh",
            retries=3,
            delay_seconds=2,
        )

    assert exc_info.value.reason == "TARGET_SSH_AUTHORIZED_KEYS_MISSING"
    assert exc_info.value.details == (
        "docker exec failed for claw_match_setup_1: "
        "defender@10.0.0.8: Permission denied (publickey,password)."
    )
    assert sleep_calls == [2, 2]


@pytest.mark.asyncio
async def test_verify_agent_target_ssh_rejects_unexpected_probe_output(monkeypatch):
    main = _load_main_module("test_main_verify_agent_target_ssh_unexpected_output")
    engine = main.RefereeEngine()

    async def fake_docker_exec(container_name, command, **kwargs):
        assert container_name == "claw_match_setup_1"
        assert command == ["sh", "-lc", "/usr/local/bin/target-ssh 'echo ready'"]
        assert kwargs == {"timeout": 15}
        return "almost-ready\n"

    monkeypatch.setattr(engine, "_docker_exec", fake_docker_exec)

    with pytest.raises(main.TargetSSHProbeError) as exc_info:
        await engine._verify_agent_target_ssh(
            1,
            "claw_match_setup_1",
            "/usr/local/bin/target-ssh",
            retries=1,
        )

    assert exc_info.value.reason == "TARGET_SSH_UNEXPECTED_OUTPUT"
    assert exc_info.value.details == "target-ssh probe returned unexpected output: almost-ready"


def test_classify_target_ssh_probe_failure_maps_known_causes():
    main = _load_main_module("test_main_classify_target_ssh_probe_failure")

    assert main.RefereeEngine._classify_target_ssh_probe_failure(
        RuntimeError("docker exec failed for claw: sh: target-ssh: not found")
    ) == (
        "TARGET_SSH_CLIENT_MISSING",
        "docker exec failed for claw: sh: target-ssh: not found",
    )

    assert main.RefereeEngine._classify_target_ssh_probe_failure(
        RuntimeError("docker exec failed for claw: Warning: Identity file /home/node/.ssh/awd_target_key not accessible: No such file or directory")
    ) == (
        "TARGET_SSH_KEY_MISSING",
        "docker exec failed for claw: Warning: Identity file /home/node/.ssh/awd_target_key not accessible: No such file or directory",
    )

    assert main.RefereeEngine._classify_target_ssh_probe_failure(
        RuntimeError("docker exec failed for claw: defender@10.0.0.8: Permission denied (publickey,password).")
    ) == (
        "TARGET_SSH_AUTHORIZED_KEYS_MISSING",
        "docker exec failed for claw: defender@10.0.0.8: Permission denied (publickey,password).",
    )

    assert main.RefereeEngine._classify_target_ssh_probe_failure(
        RuntimeError("docker exec failed for claw: ssh: connect to host 10.0.0.8 port 22: Connection refused")
    ) == (
        "TARGET_SSHD_NOT_READY",
        "docker exec failed for claw: ssh: connect to host 10.0.0.8 port 22: Connection refused",
    )

    assert main.RefereeEngine._classify_target_ssh_probe_failure(
        RuntimeError("docker exec failed for claw: ssh: connect to host 10.0.0.8 port 22: Network is unreachable")
    ) == (
        "TARGET_SSH_NETWORK_UNREACHABLE",
        "docker exec failed for claw: ssh: connect to host 10.0.0.8 port 22: Network is unreachable",
    )


@pytest.mark.asyncio
async def test_initialize_single_agent_returns_target_ssh_probe_failure(monkeypatch):
    main = _load_main_module("test_main_initialize_single_agent_probe_failure")
    config = main.MatchConfig(players=[main.PlayerConfig(id=1, name="P1")])
    match = main.MatchState("match_phase7_initialize_single_agent", config)
    match.players[1] = PlayerState(
        player_id=1,
        container_name="c1",
        target_container="t1",
        target_ip="10.0.0.1",
        maintenance_auth_mode="ssh_key",
        maintenance_helper_command="target-ssh",
    )
    match.agent_sessions[1] = main.AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.player_ssh_key_materials[1] = main.PlayerSSHKeyMaterial(
        player_id=1,
        private_key="PRIVATE\n",
        public_key="PUBLIC\n",
        helper_path="/usr/local/bin/target-ssh",
    )
    session = match.agent_sessions[1]

    async def fake_verify_agent_target_ssh(player_id, agent_container, helper_path):
        assert player_id == 1
        assert agent_container == "c1"
        assert helper_path == "/usr/local/bin/target-ssh"
        raise main.TargetSSHProbeError(
            "TARGET_SSH_AUTHORIZED_KEYS_MISSING",
            "Permission denied (publickey,password).",
        )

    def fail_render_defense_init(**kwargs):
        raise AssertionError(f"render_defense_init should not be called: {kwargs}")

    class DummyAgentClient:
        def __init__(self, **kwargs):
            raise AssertionError(f"AgentClient should not be constructed: {kwargs}")

    monkeypatch.setattr(main.referee, "_verify_agent_target_ssh", fake_verify_agent_target_ssh)
    monkeypatch.setattr(main.PromptRenderer, "render_defense_init", fail_render_defense_init)
    monkeypatch.setattr("backends.openclaw_backend.AgentClient", DummyAgentClient)

    result = await main.referee._initialize_single_agent(match, 1, session)

    assert result.success is False
    assert result.reason == "TARGET_SSH_AUTHORIZED_KEYS_MISSING"
    assert result.details == "Permission denied (publickey,password)."
    assert session.init_error_reason == "TARGET_SSH_AUTHORIZED_KEYS_MISSING"
    assert session.init_error_details == "Permission denied (publickey,password)."


@pytest.mark.asyncio
async def test_initialize_single_agent_renders_defense_prompt_with_maintenance_metadata(monkeypatch):
    main = _load_main_module("test_main_phase5_initialize_single_agent")
    config = main.MatchConfig(players=[main.PlayerConfig(id=1, name="P1")])
    match = main.MatchState("match_phase5_initialize_single_agent", config)
    match.players[1] = PlayerState(
        player_id=1,
        container_name="c1",
        target_container="t1",
        target_ip="10.0.0.1",
        maintenance_auth_mode="ssh_key",
        maintenance_helper_command="target-ssh",
        maintenance_password="legacy-password",
    )
    session = main.AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")

    captured = {}

    def fake_render_defense_init(**kwargs):
        captured.update(kwargs)
        return "rendered-prompt"

    class DummyAgentClient:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def initialize_agent(self, session_arg, prompt, stream_callback=None):
            assert session_arg is session
            assert prompt == "rendered-prompt"
            return SimpleNamespace(success=True, reason="READY", details="ok")

    async def fake_stream_callback(_match, _player_id):
        return None

    async def fake_verify_agent_target_ssh(player_id, agent_container, helper_path):
        assert player_id == 1
        assert agent_container == "c1"
        assert helper_path == "/usr/local/bin/target-ssh"
        return None

    monkeypatch.setattr("backends.openclaw_backend.AgentClient", DummyAgentClient)
    monkeypatch.setattr(main.PromptRenderer, "render_defense_init", fake_render_defense_init)
    monkeypatch.setattr(main.referee, "_make_agent_stream_callback", fake_stream_callback)
    monkeypatch.setattr(main.referee, "_verify_agent_target_ssh", fake_verify_agent_target_ssh)

    result = await main.referee._initialize_single_agent(match, 1, session)

    assert result.success is True
    assert captured["maintenance_auth_mode"] == "ssh_key"
    assert captured["maintenance_helper_command"] == "target-ssh"
    assert "maintenance_password" not in captured
