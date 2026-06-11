import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from commentator import (  # noqa: E402
    CommentaryConfig,
    CommentatorService,
    build_commentary_context,
    render_commentary_prompts,
)


def _load_main_module(module_name: str):
    main_path = ROOT / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, main_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


class FakeCommentaryClient:
    def __init__(self, text: str = "P1 just changed the pace with a clean capture."):
        self.text = text
        self.calls = []

    async def generate_commentary(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.text


class FailingCommentaryClient:
    def __init__(self):
        self.calls = 0

    async def generate_commentary(self, system_prompt: str, user_prompt: str) -> str:
        self.calls += 1
        raise RuntimeError("llm unavailable")


def _config(**overrides):
    values = {
        "enabled": True,
        "provider": "openai-completions",
        "model": "commentator-test",
        "api_key": "commentator-key",
        "base_url": "https://llm.test/v1",
        "interval_seconds": 1,
        "max_log_chars": 200,
    }
    values.update(overrides)
    return CommentaryConfig(**values)


def _match():
    players = {
        1: SimpleNamespace(
            score=100,
            attack_score=100,
            defense_score=0,
            sla_score=0,
            flags_captured=1,
            flags_lost=0,
            sla_up=True,
            sla_down_minutes=0,
            ready_status="READY",
        ),
        2: SimpleNamespace(
            score=-50,
            attack_score=0,
            defense_score=-50,
            sla_score=0,
            flags_captured=0,
            flags_lost=1,
            sla_up=True,
            sla_down_minutes=0,
            ready_status="READY",
        ),
    }
    return SimpleNamespace(
        match_id="match_ai",
        status="attack",
        players=players,
        agent_logs={},
    )


async def _emit(emitted, match, payload):
    emitted.append((match, payload))


@pytest.mark.asyncio
async def test_commentator_disabled_does_not_call_llm():
    client = FakeCommentaryClient()
    service = CommentatorService(_config(enabled=False), client=client)
    emitted = []

    await service.observe_event(
        _match(),
        {"type": "FLAG_CAPTURED", "match_id": "match_ai", "attacker_id": 1, "victim_id": 2},
        lambda match, payload: _emit(emitted, match, payload),
    )
    await service.drain()

    assert client.calls == []
    assert emitted == []


@pytest.mark.asyncio
async def test_commentator_success_persists_payload_and_sanitizes_prompt_and_output():
    client = FakeCommentaryClient("Great swing, but FLAG{model-leaked-flag} token=abc123456789 should stay hidden.")
    service = CommentatorService(_config(), client=client)
    emitted = []

    await service.observe_event(
        _match(),
        {
            "type": "FLAG_CAPTURED",
            "match_id": "match_ai",
            "attacker_id": 1,
            "victim_id": 2,
            "flag": "FLAG{live-secret-flag}",
            "authorization": "Bearer live-secret-token",
        },
        lambda match, payload: _emit(emitted, match, payload),
    )
    await service.drain()

    assert len(client.calls) == 1
    user_prompt = client.calls[0][1]
    assert "FLAG{live-secret-flag}" not in user_prompt
    assert "live-secret-token" not in user_prompt
    assert "[REDACTED]" in user_prompt

    assert len(emitted) == 1
    payload = emitted[0][1]
    assert payload["trigger"] == "flag_captured"
    assert payload["style"] == "live_tactical_zh"
    assert payload["covered_events"][0]["type"] == "FLAG_CAPTURED"
    assert "FLAG{model-leaked-flag}" not in payload["text"]
    assert "abc123456789" not in payload["text"]


@pytest.mark.asyncio
async def test_commentator_llm_failure_is_non_fatal():
    client = FailingCommentaryClient()
    service = CommentatorService(_config(), client=client)
    emitted = []

    await service.observe_event(
        _match(),
        {"type": "FLAG_CAPTURED", "match_id": "match_ai", "attacker_id": 1, "victim_id": 2},
        lambda match, payload: _emit(emitted, match, payload),
    )
    await service.drain()

    assert client.calls == 1
    assert emitted == []


@pytest.mark.asyncio
async def test_commentator_ignores_recursive_commentary_events():
    client = FakeCommentaryClient()
    service = CommentatorService(_config(), client=client)

    await service.observe_event(
        _match(),
        {"type": "AI_COMMENTARY", "match_id": "match_ai", "text": "already generated"},
        lambda match, payload: _emit([], match, payload),
    )
    await service.drain()

    assert client.calls == []


@pytest.mark.asyncio
async def test_commentator_ignores_hidden_events_before_prompting_llm():
    client = FakeCommentaryClient()
    service = CommentatorService(_config(), client=client)
    emitted = []

    await service.observe_event(
        _match(),
        {
            "type": "WEREWOLF_NIGHT_RESOLUTION_PRIVATE",
            "match_id": "match_ai",
            "_audience": "hidden",
            "seer_target_role": "werewolf",
            "witch_poison_target": 7,
        },
        lambda match, payload: _emit(emitted, match, payload),
    )
    await service.drain()

    assert client.calls == []
    assert emitted == []


def test_commentary_context_redacts_secrets_and_limits_agent_logs():
    match = _match()
    events = [
        {
            "type": "AGENT_STREAM",
            "timestamp": "2026-05-15T10:00:00",
            "data": {
                "player_id": 1,
                "content": (
                    "Authorization: Bearer super-secret-token\n"
                    "curl -H 'X-Player-Token: player-secret-token' http://target\n"
                    "found FLAG{live-secret-flag}\n"
                    + "x" * 200
                ),
            },
        },
        {
            "type": "FLAG_SUBMISSION",
            "timestamp": "2026-05-15T10:00:01",
            "data": {"attacker_id": 1, "flag": "FLAG{submitted-secret}"},
        },
    ]

    context = build_commentary_context(match, events, max_log_chars=80)
    serialized = json.dumps(context, ensure_ascii=False)

    assert "super-secret-token" not in serialized
    assert "player-secret-token" not in serialized
    assert "FLAG{live-secret-flag}" not in serialized
    assert "FLAG{submitted-secret}" not in serialized
    assert "[REDACTED]" in serialized
    assert sum(len(value) for value in context["agent_log_summary"].values()) <= 80


def test_commentary_context_highlights_phase_specific_focus():
    match = _match()
    match.status = "defense"
    events = [
        {
            "type": "AGENT_STREAM",
            "timestamp": "2026-05-15T10:00:00",
            "data": {
                "player_id": 1,
                "content": "patching auth and template render, tightening redirect and ssrf paths",
            },
        },
        {
            "type": "AGENT_STREAM",
            "timestamp": "2026-05-15T10:00:01",
            "data": {
                "player_id": 2,
                "content": "closing sql query surface and checking token headers",
            },
        },
    ]

    context = build_commentary_context(match, events, max_log_chars=200)

    assert "鉴权" in context["topic_labels"]
    assert "SSRF/转发" in context["topic_labels"]
    assert context["phase_focus"].startswith("防御阶段要讲清楚他们在修补什么")
    assert "不要说“没有积分变化”" in context["phase_focus"]


def test_werewolf_commentator_handles_white_wolf_king_and_knight_events():
    match = _match()
    match.status = "werewolf_day"
    events = [
        {
            "type": "WEREWOLF_WHITE_WOLF_KING_REVEALED",
            "timestamp": "2026-05-15T10:00:00",
            "data": {"day": 2, "player_id": 6, "target_player_id": 3},
        },
        {
            "type": "WEREWOLF_KNIGHT_DUEL",
            "timestamp": "2026-05-15T10:00:01",
            "data": {"day": 3, "knight_id": 4, "target_player_id": 8, "hit_wolf": True},
        },
    ]

    context = build_commentary_context(match, events, max_log_chars=200)
    system_prompt, user_prompt = render_commentary_prompts(context)

    assert "白狼王自爆" in user_prompt
    assert "骑士决斗" in user_prompt
    assert "structured spectator-visible wolf chat" in system_prompt
    assert "do not infer or leak hidden roles" in system_prompt


@pytest.mark.asyncio
async def test_referee_broadcast_emits_persisted_ai_commentary(monkeypatch):
    monkeypatch.setenv("REFEREE_ALLOW_INSECURE_NO_AUTH", "1")
    module = _load_main_module("test_main_ai_commentary_broadcast")

    async def save_event_noop(*args, **kwargs):
        return None

    monkeypatch.setattr(module.database, "save_event", save_event_noop)

    config = module.MatchConfig(
        players=[
            module.PlayerConfig(id=1, name="P1"),
            module.PlayerConfig(id=2, name="P2"),
        ]
    )
    match = module.MatchState("match_ai_broadcast", config)
    match.status = "attack"
    match.players = {
        1: module.PlayerState(1, "agent1", "target1", "10.0.0.1"),
        2: module.PlayerState(2, "agent2", "target2", "10.0.0.2"),
    }
    module.referee.matches[match.match_id] = match
    module.referee.commentator = CommentatorService(_config(), client=FakeCommentaryClient("P1 is building momentum."))

    class FakeWebSocket:
        def __init__(self):
            self.sent = []

        async def send_json(self, payload):
            self.sent.append(payload)

    websocket = FakeWebSocket()
    module.referee.ws_connections = [websocket]
    module.referee.ws_subscriptions = {}

    await module.referee.broadcast(
        {
            "type": "FLAG_CAPTURED",
            "match_id": match.match_id,
            "attacker_id": 1,
            "victim_id": 2,
            "flag": "FLAG{do-not-leak}",
        }
    )
    await module.referee.commentator.drain()

    commentary_events = [event for event in match.events if event["type"] == "AI_COMMENTARY"]
    assert len(commentary_events) == 1
    assert commentary_events[0]["data"]["text"] == "P1 is building momentum."
    assert any(payload.get("type") == "AI_COMMENTARY" for payload in websocket.sent)
