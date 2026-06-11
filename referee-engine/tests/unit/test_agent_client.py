import asyncio
from pathlib import Path
import sys
import json
import shlex

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_client import AgentClient, AgentSession, InitResult, PromptRenderer  # noqa: E402
from backends.hermes_backend import HermesAgentClient  # noqa: E402


def test_render_defense_init_uses_metadata_first_maintenance_context():
    rendered = PromptRenderer.render_defense_init(
        player_id=1,
        own_target_ip="10.200.0.1",
        target_port=3000,
        maintenance_auth_mode="ssh_key",
        maintenance_helper_command="target-ssh",
        referee_api_url="http://host.docker.internal:8000",
        scoring={"attackSuccess": 100, "defenseFailure": -50, "slaViolation": -50},
        flag_refresh_interval=300,
        defense_duration=600,
        attack_duration=600,
    )

    assert "target-ssh '<remote command>'" in rendered
    assert "target-ssh 'cat /app/app.py'" in rendered
    assert "target-ssh 'curl -sf http://localhost:3000/health'" in rendered
    assert "维护方式：`ssh_key`" in rendered
    assert "MAINTENANCE_AUTH_MODE" not in rendered
    assert "MAINTENANCE_HELPER_COMMAND" not in rendered
    assert "等待 password prompt" not in rendered
    assert "交互式 passwd 改密码" not in rendered
    assert "ssh root@" not in rendered
    assert "ssh defender@10.200.0.1" not in rendered
    assert "ctf_target_2026" not in rendered
    assert "Flag3 / #3" in rendered
    assert "SSRF 内部接口" in rendered
    assert "获取路径不同、边界不同" in rendered


def test_render_attack_start_includes_player_status_polling_guidance():
    rendered = PromptRenderer.render_attack_start(
        player_id=1,
        enemy_targets=[{"player_id": 2, "ip": "10.200.0.2", "port": 3000}],
        target_port=3000,
        own_target_ip="10.0.0.1",
        maintenance_auth_mode="ssh_key",
        maintenance_helper_command="target-ssh",
        referee_api_url="http://host.docker.internal:8000",
        player_status_url="http://host.docker.internal:8000/api/player/status",
        player_read_token="token-123",
        scoring={"attackSuccess": 100, "defenseFailure": -50, "slaViolation": -50},
        flag_refresh_interval=300,
        attack_duration=6600,
    )

    assert "X-Player-Token: token-123" in rendered
    assert "http://host.docker.internal:8000/api/player/status" in rendered
    assert "target-ssh '<remote command>'" in rendered
    assert "target-ssh 'cat /app/app.py'" in rendered
    assert "X-Player-Token: token-123" in rendered
    assert '"target_player_id": 2' in rendered
    assert '"player_id": 1' not in rendered
    assert '"target_player_id": <target_player_id>' not in rendered
    assert "30-60 秒没有明显进展" in rendered
    assert "自上次查询以来，你自己以及所有选手的分数变化" in rendered
    assert "当前排名、与领先者分差、与前后名的分差" in rendered
    assert "第一阶段攻击面提示" in rendered
    assert "Flag3 倾向" in rendered
    assert "不应再默认“一洞全收”" in rendered


def test_classify_ready_response_rejects_upstream_connection_failures():
    client = AgentClient(llm_api_key="test-key")

    result = client._classify_ready_response(
        "⚠ No auxiliary LLM provider configured\nAPI call failed after 3 retries: Connection error."
    )

    assert result.success is False
    assert result.reason == "INIT_PROMPT_ERROR"


def test_classify_ready_response_accepts_non_error_progress_text():
    client = AgentClient(llm_api_key="test-key")

    result = client._classify_ready_response("已收到，开始侦察并加固目标环境。")

    assert result.success is True
    assert result.reason == "READY_FALLBACK_INTENT"


def test_classify_ready_response_rejects_hermes_timeout_payloads_even_if_prompt_text_contains_ready_phrases():
    client = AgentClient(llm_api_key="test-key")

    result = client._classify_ready_response(
        "[HERMES_TIMEOUT] Command '['/opt/hermes/.venv/bin/hermes', 'chat', '-q', '立即开始加固目标并汇报']' timed out after 130 seconds"
    )

    assert result.success is False
    assert result.reason == "INIT_PROMPT_ERROR"


@pytest.mark.asyncio
async def test_send_message_serializes_single_session(monkeypatch):
    client = AgentClient(llm_api_key="test-key")
    session = AgentSession(
        player_id=1,
        container_name="claw-test",
        target_container="target-test",
        target_ip="10.0.0.1",
    )

    active_calls = 0
    max_active_calls = 0
    observed_kinds = []

    async def fake_exec(container_name, command, timeout=60, stream_callback=None, session=None, message_kind=None, message_mode=None):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        observed_kinds.append(message_kind)
        await asyncio.sleep(0.01)
        active_calls -= 1
        return json.dumps({"content": f"done-{message_kind}"})

    monkeypatch.setattr(client, "_exec", fake_exec)

    first, second = await asyncio.gather(
        client.send_message(session, "first", message_kind="first"),
        client.send_message(session, "second", message_kind="second"),
    )

    assert first == "done-first"
    assert second == "done-second"
    assert observed_kinds == ["first", "second"]
    assert max_active_calls == 1
    assert session.in_flight_message_kind is None


@pytest.mark.asyncio
async def test_buffered_keepalive_merges_and_updates_sent_timestamp(monkeypatch):
    client = AgentClient(llm_api_key="test-key")
    session = AgentSession(
        player_id=1,
        container_name="claw-test",
        target_container="target-test",
        target_ip="10.0.0.1",
    )

    delivered_payloads = []

    async def fake_exec(container_name, command, timeout=60, stream_callback=None, session=None, message_kind=None, message_mode=None):
        delivered_payloads.append((message_kind, message_mode, command))
        return json.dumps({"content": "keepalive-ok"})

    monkeypatch.setattr(client, "_exec", fake_exec)

    client.freeze_buffered_messages(session)
    first_status = await client.enqueue_buffered_message(
        session,
        "keepalive-1",
        message_kind="keepalive",
        dedupe_key="keepalive",
        merge_strategy="replace",
    )
    second_status = await client.enqueue_buffered_message(
        session,
        "keepalive-2",
        message_kind="keepalive",
        dedupe_key="keepalive",
        merge_strategy="replace",
    )

    assert first_status == "queued"
    assert second_status == "merged"
    assert len(session.buffered_messages) == 1
    assert session.buffered_messages[0].message == "keepalive-2"

    client.unfreeze_buffered_messages(session)
    drained = await client.drain_buffered_messages(session)

    assert drained == 1
    assert session.last_keepalive_sent_at is not None
    assert delivered_payloads[0][0] == "keepalive"
    assert delivered_payloads[0][1] == "buffered"


@pytest.mark.asyncio
async def test_send_message_marks_session_ready_when_session_id_is_returned(monkeypatch):
    client = AgentClient(llm_api_key="test-key")
    session = AgentSession(
        player_id=1,
        container_name="claw-test",
        target_container="target-test",
        target_ip="10.0.0.1",
    )

    async def fake_exec(container_name, command, timeout=60, stream_callback=None, session=None, message_kind=None, message_mode=None):
        return json.dumps({
            "content": "hello",
            "meta": {"agentMeta": {"sessionId": "ses-123"}},
        })

    monkeypatch.setattr(client, "_exec", fake_exec)

    response = await client.send_message(session, "ping", message_kind="attack_prompt")

    assert response == "hello"
    assert session.session_id == "ses-123"
    assert session.session_ready is True
    assert session.runtime_ready is True
    assert session.interactive_ready is False


@pytest.mark.asyncio
async def test_configure_container_restarts_gateway_before_waiting_for_live_model(monkeypatch):
    client = AgentClient(llm_api_key="test-key", llm_base_url="https://example.test/v1", llm_model="model-a")
    calls = []

    async def fake_exec(container_name, command, timeout=60, stream_callback=None, session=None, message_kind=None, message_mode=None):
        calls.append(command)
        if command.startswith("cat "):
            return json.dumps({
                "gateway": {"auth": {"token": "tok"}},
                "agents": {"defaults": {"model": "routerss/model-a"}},
                "models": {"providers": {"routerss": {"api": "openai-completions"}}},
            })
        if command.startswith("test -f"):
            return "ok"
        return ""

    async def fake_exec_as_root(container_name, command, timeout=30):
        calls.append(command)
        return ""

    async def fake_bootstrap(container_name):
        return InitResult(True)

    async def fake_config_wait(container_name):
        return InitResult(True)

    async def fake_restart(container_name):
        calls.append("restart")
        return InitResult(True)

    async def fake_wait_model(container_name, timeout=None):
        calls.append("wait_model")
        return client.qualified_model, "agent model: routerss/model-a"

    class FakeCopyProcess:
        returncode = 0

        async def communicate(self, input=None):
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append(args[0])
        return FakeCopyProcess()

    monkeypatch.setattr(client, "_exec", fake_exec)
    monkeypatch.setattr(client, "_exec_as_root", fake_exec_as_root)
    monkeypatch.setattr(client, "_wait_for_gateway_bootstrap", fake_bootstrap)
    monkeypatch.setattr(client, "_wait_for_gateway_config", fake_config_wait)
    monkeypatch.setattr(client, "_restart_gateway_container", fake_restart)
    monkeypatch.setattr(client, "_wait_gateway_model_applied", fake_wait_model)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await client.configure_container("claw-test")

    assert result.success is True
    assert calls.index("restart") < calls.index("wait_model")


@pytest.mark.asyncio
async def test_configure_container_accepts_gateway_model_runtime_suffix(monkeypatch):
    client = AgentClient(llm_api_key="test-key", llm_base_url="https://example.test/v1", llm_model="gpt-5.5")

    async def fake_exec(container_name, command, timeout=60, stream_callback=None, session=None, message_kind=None, message_mode=None):
        if command.startswith("cat "):
            return json.dumps({
                "gateway": {"auth": {"token": "tok"}},
                "agents": {"defaults": {"model": "routerss/gpt-5.5"}},
                "models": {"providers": {"routerss": {"api": "openai-completions"}}},
            })
        if command.startswith("test -f"):
            return "ok"
        return ""

    async def fake_exec_as_root(container_name, command, timeout=30):
        return ""

    async def fake_bootstrap(container_name):
        return InitResult(True)

    async def fake_config_wait(container_name):
        return InitResult(True)

    async def fake_restart(container_name):
        return InitResult(True)

    async def fake_wait_model(container_name, timeout=None):
        return "routerss/gpt-5.5 (thinking=medium, fast=off)", "agent model: routerss/gpt-5.5 (thinking=medium, fast=off)"

    class FakeCopyProcess:
        returncode = 0

        async def communicate(self, input=None):
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeCopyProcess()

    monkeypatch.setattr(client, "_exec", fake_exec)
    monkeypatch.setattr(client, "_exec_as_root", fake_exec_as_root)
    monkeypatch.setattr(client, "_wait_for_gateway_bootstrap", fake_bootstrap)
    monkeypatch.setattr(client, "_wait_for_gateway_config", fake_config_wait)
    monkeypatch.setattr(client, "_restart_gateway_container", fake_restart)
    monkeypatch.setattr(client, "_wait_gateway_model_applied", fake_wait_model)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    result = await client.configure_container("claw-test")

    assert result.success is True


def test_normalize_gateway_model_value_strips_runtime_suffix():
    assert (
        AgentClient._normalize_gateway_model_value("routerss/gpt-5.5 (thinking=medium, fast=off)")
        == "routerss/gpt-5.5"
    )


@pytest.mark.asyncio
async def test_write_config_streams_json_to_container_without_docker_cp(monkeypatch):
    client = AgentClient(llm_api_key="test-key", llm_model="model-a")
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

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    await client._write_config_to_container(
        "claw-test",
        {"models": {"providers": {"routerss": {"apiKey": "secret-key", "models": [{"id": "model-a"}]}}}},
    )

    assert observed["args"][:6] == ("docker", "exec", "-u", "root", "-i", "claw-test")
    assert "cp" not in observed["args"]
    assert observed["stdin_pipe"] == asyncio.subprocess.PIPE
    payload = json.loads(observed["stdin"].decode("utf-8"))
    assert payload["models"]["providers"]["routerss"]["apiKey"] == "secret-key"


def test_build_agent_exec_command_quotes_base64_payload():
    client = AgentClient(llm_api_key="test-key")
    session = AgentSession(
        player_id=1,
        container_name="claw-test",
        target_container="target-test",
        target_ip="10.0.0.1",
    )

    payload = "abc'; touch /tmp/pwned #"
    command = client.build_agent_exec_command(session, payload, 30)

    assert shlex.quote(payload) in command
    assert "printf %s" in command


@pytest.mark.asyncio
async def test_session_file_paths_are_shell_quoted(monkeypatch):
    client = AgentClient(llm_api_key="test-key")
    session = AgentSession(
        player_id=1,
        container_name="claw-test",
        target_container="target-test",
        target_ip="10.0.0.1",
        session_id="sid'; touch /tmp/pwned #",
    )
    commands = []

    async def fake_exec(container_name, command, timeout=60, stream_callback=None, session=None, message_kind=None, message_mode=None):
        commands.append(command)
        return ""

    monkeypatch.setattr(client, "_exec", fake_exec)

    await client._resolve_session_file(session)

    assert commands
    expected_path = f"{client.OPENCLAW_SESSION_DIR}/{session.session_id}.jsonl"
    assert shlex.quote(expected_path) in commands[0]


@pytest.mark.asyncio
async def test_exec_uses_large_stream_limit_for_long_stdout_lines(monkeypatch):
    client = AgentClient(llm_api_key="test-key")
    observed = {}

    class FakeReader:
        def __init__(self, chunks):
            self.chunks = list(chunks)

        async def readline(self):
            if self.chunks:
                return self.chunks.pop(0)
            return b""

    class FakeProcess:
        returncode = 0

        def __init__(self):
            self.stdout = FakeReader([(b"x" * 70000) + b"\n"])
            self.stderr = FakeReader([])

        async def wait(self):
            return 0

        def kill(self):
            pass

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["limit"] = kwargs.get("limit")
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    output = await client._exec("claw-test", "printf long-line")

    assert output == "x" * 70000
    assert observed["limit"] == client.SUBPROCESS_STREAM_LIMIT


@pytest.mark.asyncio
async def test_exec_invokes_docker_without_host_shell(monkeypatch):
    client = AgentClient(llm_api_key="test-key")
    observed = {}

    class FakeReader:
        async def readline(self):
            return b""

    class FakeProcess:
        returncode = 0

        def __init__(self):
            self.stdout = FakeReader()
            self.stderr = FakeReader()

        async def wait(self):
            return 0

        def kill(self):
            pass

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["args"] = args
        observed["limit"] = kwargs.get("limit")
        return FakeProcess()

    async def forbidden_shell(*args, **kwargs):
        raise AssertionError("host shell must not be used")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden_shell)

    container_name = "claw-test; touch /tmp/host-pwned"
    command = "printf ok; touch /tmp/container-only"
    await client._exec(container_name, command)

    assert observed["args"] == (
        "docker",
        "exec",
        container_name,
        "sh",
        "-lc",
        command,
    )
    assert observed["limit"] == client.SUBPROCESS_STREAM_LIMIT


@pytest.mark.asyncio
async def test_exec_as_root_invokes_docker_without_host_shell(monkeypatch):
    client = AgentClient(llm_api_key="test-key")
    observed = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b"", b""

    async def fake_create_subprocess_exec(*args, **kwargs):
        observed["args"] = args
        observed["limit"] = kwargs.get("limit")
        return FakeProcess()

    async def forbidden_shell(*args, **kwargs):
        raise AssertionError("host shell must not be used")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(asyncio, "create_subprocess_shell", forbidden_shell)

    container_name = "claw-test; touch /tmp/host-pwned"
    command = "chown node:node /home/node/.openclaw/openclaw.json"
    await client._exec_as_root(container_name, command)

    assert observed["args"] == (
        "docker",
        "exec",
        "-u",
        "root",
        container_name,
        "sh",
        "-lc",
        command,
    )
    assert observed["limit"] == client.SUBPROCESS_STREAM_LIMIT


@pytest.mark.asyncio
async def test_check_session_contains_matches_markdown_formatted_prompt(monkeypatch):
    client = AgentClient(llm_api_key="test-key")
    session = AgentSession(
        player_id=1,
        container_name="claw-test",
        target_container="target-test",
        target_ip="10.0.0.1",
    )

    async def fake_resolve_session_file(_session):
        return "/tmp/session.json"

    async def fake_exec(container_name, command, timeout=60, stream_callback=None, session=None, message_kind=None, message_mode=None):
        return '{"messages":[{"role":"user","content":"【阶段变更】**攻击阶段** 已经开始！网络现已开放。"}]}'

    monkeypatch.setattr(client, "_resolve_session_file", fake_resolve_session_file)
    monkeypatch.setattr(client, "_exec", fake_exec)

    assert await client.check_session_contains(session, "【阶段变更】攻击阶段", tail_lines=10) is True


@pytest.mark.asyncio
async def test_check_session_contains_falls_back_to_full_session_content(monkeypatch):
    client = AgentClient(llm_api_key="test-key")
    session = AgentSession(
        player_id=1,
        container_name="claw-test",
        target_container="target-test",
        target_ip="10.0.0.1",
    )

    async def fake_resolve_session_file(_session):
        return "/tmp/session.json"

    async def fake_exec(container_name, command, timeout=60, stream_callback=None, session=None, message_kind=None, message_mode=None):
        if command.startswith("tail -n"):
            return '{"messages":[{"role":"assistant","content":"正在分析目标..."}]}'
        return '{"messages":[{"role":"user","content":"【阶段变更】**攻击阶段** 已经开始！网络现已开放。"}]}'

    monkeypatch.setattr(client, "_resolve_session_file", fake_resolve_session_file)
    monkeypatch.setattr(client, "_exec", fake_exec)

    assert await client.check_session_contains(session, "【阶段变更】攻击阶段", tail_lines=10) is True


@pytest.mark.asyncio
async def test_hermes_initialize_agent_uses_extended_init_timeout(monkeypatch):
    client = HermesAgentClient(llm_api_key="test-key")
    session = AgentSession(
        player_id=1,
        container_name="claw-test",
        target_container="target-test",
        target_ip="10.0.0.1",
    )
    observed = {}

    async def fake_send_message(session_obj, message, timeout=None, stream_callback=None, message_kind="message", message_mode="normal", drain_buffered_after=True):
        observed["timeout"] = timeout
        observed["message_kind"] = message_kind
        return "已收到，开始防御。"

    monkeypatch.setattr(client, "send_message", fake_send_message)

    result = await client.initialize_agent(session, "prompt")

    assert result.success is True
    assert observed == {
        "timeout": 180,
        "message_kind": "init",
    }


@pytest.mark.asyncio
async def test_hermes_initialize_agent_accepts_timeout_when_session_activity_exists(monkeypatch):
    client = HermesAgentClient(llm_api_key="test-key")
    session = AgentSession(
        player_id=1,
        container_name="claw-test",
        target_container="target-test",
        target_ip="10.0.0.1",
    )

    async def fake_send_message(session_obj, message, timeout=None, stream_callback=None, message_kind="message", message_mode="normal", drain_buffered_after=True):
        return "[HERMES_TIMEOUT] timed out after 190 seconds"

    async def fake_observe_session_activity(session_obj, tail_lines=8):
        session_obj.last_session_activity_signature = "snapshot"
        return True

    async def fake_observe_code_activity(session_obj):
        raise AssertionError("code activity probe should not run when session activity already succeeded")

    monkeypatch.setattr(client, "send_message", fake_send_message)
    monkeypatch.setattr(client, "observe_session_activity", fake_observe_session_activity)
    monkeypatch.setattr(client, "observe_code_activity", fake_observe_code_activity)

    result = await client.initialize_agent(session, "prompt")

    assert result.success is True
    assert result.reason == "READY_INIT_SESSION_ACTIVITY"
    assert session.ready is True
    assert session.init_ready is True
    assert session.interactive_ready is True
