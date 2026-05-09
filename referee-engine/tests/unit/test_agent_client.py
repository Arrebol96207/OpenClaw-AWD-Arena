import asyncio
from pathlib import Path
import sys
import json

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_client import AgentClient, AgentSession, PromptRenderer  # noqa: E402
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
        referee_api_url="http://host.docker.internal:8000",
        player_status_url="http://host.docker.internal:8000/api/player/status",
        player_read_token="token-123",
        scoring={"attackSuccess": 100, "defenseFailure": -50, "slaViolation": -50},
        flag_refresh_interval=300,
        attack_duration=6600,
    )

    assert "X-Player-Token: token-123" in rendered
    assert "http://host.docker.internal:8000/api/player/status" in rendered
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
