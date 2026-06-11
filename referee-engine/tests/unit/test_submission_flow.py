import asyncio
import importlib.util
import json
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database  # noqa: E402
from agent_client import AgentSession  # noqa: E402
from flag_manager import FlagManager  # noqa: E402
from flag_manager import ScoringEngine  # noqa: E402
from flag_manager import PlayerState  # noqa: E402


def _load_main_module(module_name: str):
    main_path = ROOT / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, main_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _build_submission(
    *,
    attacker_id: int,
    victim_id: int,
    success: bool,
    timestamp: str,
    reason: str = "success",
    points: int = 100,
    flag_slot: Optional[str] = None,
    flag_index: Optional[int] = None,
):
    submission = {
        "attacker_id": attacker_id,
        "victim_id": victim_id,
        "declared_target_player_id": victim_id,
        "flag": f"FLAG{{{attacker_id}-{victim_id}}}",
        "success": success,
        "reason": reason,
        "points": points,
        "timestamp": timestamp,
    }
    if flag_slot is not None:
        submission["flag_slot"] = flag_slot
    if flag_index is not None:
        submission["flag_index"] = flag_index
    return submission


def _minimal_config_dict():
    return {
        "match": {"name": "Recovered Match", "duration": 7200, "phases": {"defense": 600, "attack": 6600}},
        "llm": {
            "provider": "openai-completions",
            "baseUrl": "https://example.test/v1",
            "apiKey": "",
            "model": "test-model",
            "proxy": "http://host.docker.internal:7897",
        },
        "players": [
            {"id": 1, "name": "P1", "model": None, "apiKey": None, "gatewayPort": None},
            {"id": 2, "name": "P2", "model": None, "apiKey": None, "gatewayPort": None},
        ],
        "scoring": {"attackSuccess": 100, "defenseFailure": -50, "slaViolation": -50},
        "flags": {"refreshInterval": 300, "format": "flag{{{hash}}}"},
        "network": {"arenaSubnet": "172.20.0.0/16", "mgmtSubnetPrefix": "172.21"},
        "target_image": "openclaw/ctf-target:v1",
        "agent_image": "openclaw/local-agent:ssh",
    }


async def _async_noop():
    return None


async def _async_save_event_noop(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_add_event_background_persistence_failure_is_logged(monkeypatch, caplog):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_add_event_background_failure")

    async def _failing_save_event(*args, **kwargs):
        raise RuntimeError("db writer down")

    monkeypatch.setattr(module.database, "save_event", _failing_save_event)

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_background_event_failure", config)

    with caplog.at_level(logging.WARNING, logger=module.logger.name):
        event = match.add_event("TEST_EVENT", {"secret": "value"})
        await asyncio.sleep(0)

    assert event["type"] == "TEST_EVENT"
    assert match.events[-1]["type"] == "TEST_EVENT"
    assert "background event persistence failed for TEST_EVENT" in caplog.text
    assert "db writer down" in caplog.text


@pytest.mark.asyncio
async def test_end_match_waits_for_background_task_cancellation(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_end_match_waits_cancel")

    monkeypatch.setattr(module.database, "update_match_status", _async_save_event_noop)
    monkeypatch.setattr(module.database, "save_event", _async_save_event_noop)
    monkeypatch.setattr(module.referee, "broadcast", _async_save_event_noop)

    class FakeExportResult:
        def to_event_payload(self):
            return {"status": "ready", "complete": True}

    monkeypatch.setattr(module, "export_match_player_code", lambda _match: FakeExportResult())
    monkeypatch.setattr(module.referee, "destroy_match", _async_save_event_noop)

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_end_waits_cancel", config)
    match.status = "defense"
    match.started_at = datetime.now()
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    module.referee.matches[match.match_id] = match

    flag_task_cancelled = asyncio.Event()

    async def _flag_task():
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            flag_task_cancelled.set()
            raise

    match._flag_task = asyncio.create_task(_flag_task())
    await asyncio.sleep(0)

    await module.referee.end_match(match.match_id)

    assert flag_task_cancelled.is_set()
    assert match._flag_task.done() is True
    assert match.status == "finished"


def _assert_success_feedback(feedback):
    assert feedback["status_query_recommended"] is True
    assert feedback["player_status_endpoint"] == "/api/player/status"
    assert feedback["required_header"] == "X-Player-Token"
    assert "GET /api/player/status" in feedback["status_query_hint"]
    assert "You gained 100 points." in feedback["summary"]
    assert "Flag submission succeeded" in feedback["summary"]




async def _async_empty_matches():
    return []


@pytest.mark.asyncio
async def test_save_and_load_submissions_round_trip(tmp_path, monkeypatch):
    db_path = tmp_path / "roundtrip.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    await database.init_db()

    first = _build_submission(
        attacker_id=1,
        victim_id=2,
        success=True,
        timestamp="2026-03-27T10:00:00",
        points=100,
        flag_slot="database_flag",
        flag_index=2,
    )
    second = _build_submission(
        attacker_id=1,
        victim_id=2,
        success=False,
        timestamp="2026-03-27T10:00:05",
        reason="flag_already_claimed_by_attacker",
        points=0,
        flag_slot="database_flag",
        flag_index=2,
    )

    await database.save_submission("match_roundtrip", first)
    await database.save_submission("match_roundtrip", second)

    loaded = await database.load_submissions("match_roundtrip")

    assert loaded == [
        {**first, "flag": "********"},
        {**second, "flag": "********"},
    ]


@pytest.mark.asyncio
async def test_load_submissions_is_isolated_by_match_and_time_order(tmp_path, monkeypatch):
    db_path = tmp_path / "isolated.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    await database.init_db()

    late = _build_submission(
        attacker_id=3,
        victim_id=1,
        success=True,
        timestamp="2026-03-27T10:00:10",
        flag_slot="credentials_flag",
        flag_index=4,
    )
    early = _build_submission(
        attacker_id=2,
        victim_id=1,
        success=False,
        timestamp="2026-03-27T10:00:01",
        reason="target_mismatch",
        points=0,
    )
    other_match = _build_submission(
        attacker_id=9,
        victim_id=8,
        success=True,
        timestamp="2026-03-27T10:00:02",
    )

    await database.save_submission("match_alpha", late)
    await database.save_submission("match_alpha", early)
    await database.save_submission("match_beta", other_match)

    alpha = await database.load_submissions("match_alpha")
    beta = await database.load_submissions("match_beta")
    missing = await database.load_submissions("match_missing")

    assert alpha == [
        {**early, "flag": "********"},
        {**late, "flag": "********"},
    ]
    assert beta == [{**other_match, "flag": "********"}]
    assert missing == []


def test_get_match_status_excludes_submission_payload(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_status_module")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_status", config)
    match.status = "finished"
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.persisted_submissions = [_build_submission(attacker_id=1, victim_id=2, success=True, timestamp="2026-03-27T10:00:00")]
    match.events = [{"type": "STATUS", "data": {"status": "finished"}, "timestamp": "2026-03-27T10:00:00", "match_id": "match_status"}]
    module.referee.matches[match.match_id] = match

    payload = module.referee.get_match_status(match.match_id)

    assert "submissions" not in payload
    assert "events" not in payload
    assert "agent_logs" not in payload
    assert payload["events_count"] == 1
    assert payload["recent_events"] == match.events[-10:]


@pytest.mark.asyncio
async def test_submissions_endpoint_returns_persisted_records_only_with_redacted_flags(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_submissions_module")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1"), module.PlayerConfig(id=2, name="P2")])
    match = module.MatchState("match_submissions", config)
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.players[2] = PlayerState(player_id=2, container_name="c2", target_container="t2", target_ip="10.0.0.2")
    match.flag_manager.submissions = [_build_submission(attacker_id=1, victim_id=2, success=True, timestamp="2026-03-27T10:00:00")]
    persisted = [
        _build_submission(attacker_id=1, victim_id=2, success=False, timestamp="2026-03-27T10:00:01", reason="target_mismatch", points=0),
        _build_submission(attacker_id=2, victim_id=1, success=True, timestamp="2026-03-27T10:00:02", points=100),
    ]
    match.persisted_submissions = persisted
    module.referee.matches[match.match_id] = match

    response = await module.get_submissions(match.match_id)

    assert match.persisted_submissions == persisted
    assert response["match_id"] == match.match_id
    assert [item["flag"] for item in response["submissions"]] == ["********", "********"]
    assert "FLAG{1-2}" not in json.dumps(response)
    assert "FLAG{2-1}" not in json.dumps(response)


def test_match_report_markdown_summarizes_public_replay_data(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_match_report_markdown")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="Alpha"), module.PlayerConfig(id=2, name="Beta")])
    match = module.MatchState("match_report", config)
    match.status = "finished"
    match.resources_destroyed = True
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.players[2] = PlayerState(player_id=2, container_name="c2", target_container="t2", target_ip="10.0.0.2")
    match.persisted_submissions = [
        _build_submission(attacker_id=1, victim_id=2, success=True, timestamp="2026-03-27T10:00:00", points=100)
    ]
    match.events = [
        {
            "type": "FLAG_SUBMISSION_ACCEPTED",
            "data": {
                "attacker_id": 1,
                "victim_id": 2,
                "flag": "FLAG{super-secret}",
                "reason": "success",
                "token": "secret-token",
            },
            "timestamp": "2026-03-27T10:00:00",
            "match_id": match.match_id,
        }
    ]
    leaderboard = {
        1: {"player_id": 1, "name": "Alpha", "total_score": 100, "flags_captured": 1, "flags_lost": 0, "sla_up": True},
        2: {"player_id": 2, "name": "Beta", "total_score": -50, "flags_captured": 0, "flags_lost": 1, "sla_up": False},
    }

    report = module.build_match_report_markdown(match, leaderboard)

    assert "# Match Report: AWD Match" in report
    assert "| 1 | Alpha | 100 | 1 | 0 | up |" in report
    assert "- Attempts: 1" in report
    assert "- Successful Captures: 1" in report
    assert "FLAG{super-secret}" not in report
    assert "secret-token" not in report
    assert module._markdown_cell("token=secret-token") == "token=********"


@pytest.mark.asyncio
async def test_match_report_markdown_endpoint_returns_download(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_match_report_endpoint")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_report_endpoint", config)
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    module.referee.matches[match.match_id] = match

    response = await module.get_match_report_markdown(match.match_id)

    assert response.media_type == "text/markdown"
    assert "match_match_report_endpoint_report.md" in response.headers["Content-Disposition"]
    assert b"# Match Report:" in response.body


@pytest.mark.asyncio
async def test_match_report_markdown_endpoint_recovers_historical_match(tmp_path, monkeypatch):
    db_path = tmp_path / "historical-report.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    await database.init_db()

    created_at = datetime(2026, 3, 27, 10, 0, 0)
    finished_at = datetime(2026, 3, 27, 10, 30, 0)
    match_id = "match_historical_report"
    leaderboard = {
        "1": {
            "player_id": 1,
            "name": "Recovered Alpha",
            "total_score": 100,
            "flags_captured": 1,
            "flags_lost": 0,
            "sla_up": True,
        },
        "2": {
            "player_id": 2,
            "name": "Recovered Beta",
            "total_score": -50,
            "flags_captured": 0,
            "flags_lost": 1,
            "sla_up": False,
        },
    }
    saved_submission = _build_submission(
        attacker_id=1,
        victim_id=2,
        success=True,
        timestamp="2026-03-27T10:10:00",
        points=100,
    )

    await database.save_match(match_id, "finished", _minimal_config_dict(), created_at)
    await database.update_match_status(match_id, "finished", finished_at)
    await database.save_submission(match_id, saved_submission)
    await database.save_event(match_id, "MATCH_FINISHED", {"leaderboard": leaderboard}, finished_at)
    await database.save_event(
        match_id,
        "MATCH_RESOURCES_DESTROYED",
        {"containers_removed": 4, "networks_removed": 3, "status": "finished"},
        finished_at + timedelta(minutes=1),
    )

    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_historical_report_endpoint")
    module.referee.matches = {}

    response = await module.get_match_report_markdown(match_id)

    report = response.body.decode("utf-8")
    assert response.media_type == "text/markdown"
    assert "# Match Report: Recovered Match" in report
    assert "| 1 | P1 | 100 | 1 | 0 | up |" in report
    assert "| 2 | P2 | -50 | 0 | 1 | down |" in report
    assert "- Attempts: 1" in report
    assert "- Resources Destroyed: yes" in report
    assert "FLAG{1-2}" not in report


def test_replay_submission_filter_hides_future_and_invalid_rows():
    base = datetime.fromisoformat("2026-03-27T10:00:00")
    submissions = [
        _build_submission(attacker_id=1, victim_id=2, success=True, timestamp=(base + timedelta(seconds=1)).isoformat()),
        _build_submission(attacker_id=2, victim_id=3, success=False, timestamp=(base + timedelta(seconds=3)).isoformat(), reason="invalid_flag", points=0),
        _build_submission(attacker_id=3, victim_id=1, success=True, timestamp="invalid-timestamp"),
    ]
    events = [
        {"timestamp": (base + timedelta(seconds=2)).isoformat()},
        {"timestamp": (base + timedelta(seconds=5)).isoformat()},
    ]

    def _event_time(value: str) -> int:
        try:
            return int(datetime.fromisoformat(value).timestamp() * 1000)
        except Exception:
            return 0

    def _visible(cursor: int):
        replay_cutoff_time = _event_time(events[min(cursor, len(events)) - 1]["timestamp"]) if cursor > 0 else 0
        return [
            item
            for item in submissions
            if cursor > 0
            and isinstance(item.get("timestamp"), str)
            and 0 < _event_time(item["timestamp"]) <= replay_cutoff_time
        ]

    assert _visible(0) == []
    assert _visible(1) == [submissions[0]]
    assert _visible(2) == submissions[:2]


@pytest.mark.asyncio
async def test_lifespan_recovers_finished_match_submissions_into_api(tmp_path, monkeypatch):
    db_path = tmp_path / "lifespan-finished.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    await database.init_db()

    created_at = datetime(2026, 3, 27, 10, 0, 0)
    finished_at = datetime(2026, 3, 27, 12, 0, 0)
    match_id = "match_finished_recovery"
    config_dict = _minimal_config_dict()

    await database.save_match(match_id, "finished", config_dict, created_at)
    await database.update_match_status(match_id, "finished", finished_at)
    await database.save_event(
        match_id,
        "MATCH_FINISHED",
        {"leaderboard": {"1": {"player_id": 1, "total_score": 100}, "2": {"player_id": 2, "total_score": -50}}},
        finished_at,
    )
    saved_submission = _build_submission(
        attacker_id=1,
        victim_id=2,
        success=True,
        timestamp="2026-03-27T11:59:00",
        points=100,
    )
    await database.save_submission(match_id, saved_submission)

    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_lifespan_finished")
    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)

    async with module.lifespan(module.app):
        assert match_id in module.referee.matches
        match = module.referee.matches[match_id]
        assert match.status == "finished"
        assert match.persisted_submissions == [{**saved_submission, "flag": "********"}]
        assert match.players[1].score == 100
        assert match.players[1].attack_score == 100
        assert match.players[1].flags_captured == 1
        assert match.players[2].score == -50
        assert match.players[2].defense_score == -50
        assert match.players[2].flags_lost == 1
        match_status = module.referee.get_match_status(match_id)
        assert match_status["players"]["1"]["score"] == 100
        assert match_status["players"]["1"]["attack_score"] == 100
        assert match_status["players"]["2"]["score"] == -50
        assert match_status["players"]["2"]["defense_score"] == -50
        response = await module.get_submissions(match_id)
        assert response["match_id"] == match_id
        assert response["submissions"][0] == {**saved_submission, "flag": "********"}


@pytest.mark.asyncio
async def test_lifespan_recovers_container_names_from_creation_event(tmp_path, monkeypatch):
    db_path = tmp_path / "lifespan-containers.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    await database.init_db()

    created_at = datetime(2026, 3, 27, 10, 0, 0)
    match_id = "match_container_recovery"
    await database.save_match(match_id, "finished", _minimal_config_dict(), created_at)
    await database.save_event(
        match_id,
        "CONTAINERS_CREATED",
        {
            "players": {
                "1": {
                    "target_ip": "10.196.1.2",
                    "target_container": f"target_{match_id}_1",
                    "network": f"awd_{match_id}_player_1",
                    "isolated": True,
                },
                "2": {
                    "target_ip": "10.196.2.2",
                    "agent_container": f"claw_{match_id}_2",
                    "target_container": f"target_{match_id}_2",
                    "network": f"awd_{match_id}_player_2",
                    "isolated": True,
                },
            }
        },
        created_at,
    )

    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_lifespan_container_names")
    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)

    async with module.lifespan(module.app):
        match = module.referee.matches[match_id]
        assert match.players[1].container_name == f"claw_{match_id}_1"
        assert match.players[1].target_container == f"target_{match_id}_1"
        assert match.players[1].network_name == f"awd_{match_id}_player_1"
        assert match.players[1].target_ip == "10.196.1.2"
        assert match.players[2].container_name == f"claw_{match_id}_2"


@pytest.mark.asyncio
async def test_lifespan_backfills_finished_event_from_last_leaderboard(tmp_path, monkeypatch):
    db_path = tmp_path / "lifespan-backfill-finished.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    await database.init_db()

    created_at = datetime(2026, 3, 27, 10, 0, 0)
    heartbeat_at = datetime(2026, 3, 27, 10, 30, 0)
    match_id = "match_finished_backfill"
    leaderboard = {"1": {"player_id": 1, "total_score": 500}, "2": {"player_id": 2, "total_score": -100}}
    await database.save_match(match_id, "finished", _minimal_config_dict(), created_at)
    await database.save_event(match_id, "HEARTBEAT", {"leaderboard": leaderboard, "remaining_seconds": 0}, heartbeat_at)

    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_lifespan_finished_backfill")
    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)

    async with module.lifespan(module.app):
        match = module.referee.matches[match_id]
        finished_events = [event for event in match.events if event["type"] == "MATCH_FINISHED"]
        assert len(finished_events) == 1
        assert finished_events[0]["data"]["leaderboard"] == leaderboard
        assert finished_events[0]["data"]["backfilled"] is True

    loaded = await database.load_all_matches()
    recovered = next(item for item in loaded if item["match_id"] == match_id)
    assert any(event["type"] == "MATCH_FINISHED" for event in recovered["events"])


@pytest.mark.asyncio
async def test_lifespan_recovers_resource_destroyed_state_from_event(tmp_path, monkeypatch):
    db_path = tmp_path / "lifespan-resource-cleanup.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    await database.init_db()

    created_at = datetime(2026, 3, 27, 10, 0, 0)
    finished_at = datetime(2026, 3, 27, 10, 5, 0)
    pending_match_id = "match_finished_pending_cleanup"
    clean_match_id = "match_finished_clean"

    await database.save_match(pending_match_id, "finished", _minimal_config_dict(), created_at)
    await database.update_match_status(pending_match_id, "finished", finished_at)
    await database.save_match(clean_match_id, "finished", _minimal_config_dict(), created_at + timedelta(minutes=1))
    await database.update_match_status(clean_match_id, "finished", finished_at + timedelta(minutes=1))
    await database.save_event(
        clean_match_id,
        "MATCH_RESOURCES_DESTROYED",
        {"containers_removed": 4, "networks_removed": 3, "status": "finished"},
        finished_at + timedelta(minutes=2),
    )

    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_lifespan_resource_cleanup")
    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)

    destroy_calls = []

    async def _fake_destroy(target_match_id: str):
        destroy_calls.append(target_match_id)

    monkeypatch.setattr(module.referee, "destroy_match", _fake_destroy)

    def _run_task_immediately(coro):
        return asyncio.get_running_loop().create_task(coro)

    async def _sleep_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(module.asyncio, "create_task", _run_task_immediately)
    monkeypatch.setattr(module.asyncio, "sleep", _sleep_noop)

    async with module.lifespan(module.app):
        assert module.referee.matches[pending_match_id].resources_destroyed is False
        assert module.referee.matches[clean_match_id].resources_destroyed is True
        await asyncio.sleep(0)

    assert destroy_calls == [pending_match_id]


@pytest.mark.asyncio
async def test_lifespan_marks_running_match_aborted_and_keeps_submissions(tmp_path, monkeypatch):
    db_path = tmp_path / "lifespan-aborted.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    await database.init_db()

    created_at = datetime(2026, 3, 27, 10, 0, 0)
    match_id = "match_attack_recovery"
    config_dict = _minimal_config_dict()
    saved_submission = _build_submission(
        attacker_id=2,
        victim_id=1,
        success=False,
        timestamp="2026-03-27T10:30:00",
        reason="target_mismatch",
        points=0,
    )

    await database.save_match(match_id, "attack", config_dict, created_at)
    await database.save_submission(match_id, saved_submission)

    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_lifespan_aborted")
    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)

    destroy_calls = []

    async def _fake_destroy(target_match_id: str):
        destroy_calls.append(target_match_id)

    monkeypatch.setattr(module.referee, "destroy_match", _fake_destroy)

    def _run_task_immediately(coro):
        return asyncio.get_running_loop().create_task(coro)

    monkeypatch.setattr(module.asyncio, "create_task", _run_task_immediately)

    async with module.lifespan(module.app):
        assert match_id in module.referee.matches
        match = module.referee.matches[match_id]
        assert match.status == "aborted"
        assert match.persisted_submissions == [{**saved_submission, "flag": "********"}]
        assert module.referee.player_match_index[1] == match_id
        assert module.referee.player_match_index[2] == match_id
        await asyncio.sleep(0)

    loaded_matches = await database.load_all_matches()
    recovered = next(item for item in loaded_matches if item["match_id"] == match_id)
    assert recovered["status"] == "aborted"
    assert destroy_calls == [match_id]


@pytest.mark.asyncio
async def test_match_summary_uses_resource_destroyed_event_for_aborted_matches(tmp_path, monkeypatch):
    db_path = tmp_path / "summary-resource-destroyed.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    await database.init_db()

    created_at = datetime(2026, 3, 27, 10, 0, 0)
    finished_at = datetime(2026, 3, 27, 10, 5, 0)
    dirty_match_id = "match_aborted_dirty"
    clean_match_id = "match_aborted_clean"
    finished_dirty_match_id = "match_finished_dirty"
    finished_clean_match_id = "match_finished_clean"

    await database.save_match(dirty_match_id, "aborted", _minimal_config_dict(), created_at)
    await database.update_match_status(dirty_match_id, "aborted", finished_at)
    await database.save_match(clean_match_id, "aborted", _minimal_config_dict(), created_at + timedelta(minutes=1))
    await database.update_match_status(clean_match_id, "aborted", finished_at + timedelta(minutes=1))
    await database.save_match(finished_dirty_match_id, "finished", _minimal_config_dict(), created_at + timedelta(minutes=2))
    await database.update_match_status(finished_dirty_match_id, "finished", finished_at + timedelta(minutes=2))
    await database.save_match(finished_clean_match_id, "finished", _minimal_config_dict(), created_at + timedelta(minutes=3))
    await database.update_match_status(finished_clean_match_id, "finished", finished_at + timedelta(minutes=3))
    await database.save_event(
        clean_match_id,
        "MATCH_RESOURCES_DESTROYED",
        {"containers_removed": 4, "networks_removed": 3, "status": "aborted"},
        finished_at + timedelta(minutes=2),
    )
    await database.save_event(
        finished_clean_match_id,
        "MATCH_RESOURCES_DESTROYED",
        {"containers_removed": 4, "networks_removed": 3, "status": "finished"},
        finished_at + timedelta(minutes=4),
    )

    summaries = await database.list_matches_summary()
    rows = {row["match_id"]: row for row in summaries}

    assert rows[dirty_match_id]["resource_destroyed"] is False
    assert rows[clean_match_id]["resource_destroyed"] is True
    assert rows[finished_dirty_match_id]["resource_destroyed"] is False
    assert rows[finished_clean_match_id]["resource_destroyed"] is True


@pytest.mark.asyncio
async def test_lifespan_handles_match_without_submissions(tmp_path, monkeypatch):
    db_path = tmp_path / "lifespan-empty.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))

    await database.init_db()

    created_at = datetime(2026, 3, 27, 10, 0, 0)
    match_id = "match_empty_recovery"
    await database.save_match(match_id, "finished", _minimal_config_dict(), created_at)

    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_lifespan_empty")
    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)

    async with module.lifespan(module.app):
        assert match_id in module.referee.matches
        response = await module.get_submissions(match_id)
        assert response == {"match_id": match_id, "submissions": []}


@pytest.mark.asyncio
async def test_events_endpoint_respects_limit_and_keeps_tail_order(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_events_limit")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_events_limit", config)
    match.events = [
        {"type": "STATUS", "data": {"seq": 1}, "timestamp": "2026-03-27T10:00:01", "match_id": match.match_id},
        {"type": "FLAG_SUBMISSION", "data": {"seq": 2}, "timestamp": "2026-03-27T10:00:02", "match_id": match.match_id},
        {"type": "FLAG_CAPTURED", "data": {"seq": 3}, "timestamp": "2026-03-27T10:00:03", "match_id": match.match_id},
    ]
    module.referee.matches[match.match_id] = match

    response = await module.get_events(match.match_id, limit=2)

    assert response["events"] == match.events[:2]
    assert response["total"] == 3
    assert response["offset"] == 0
    assert response["limit"] == 2
    assert response["next_offset"] == 2
    assert [event["data"]["seq"] for event in response["events"]] == [1, 2]

    second_page = await module.get_events(match.match_id, limit=2, offset=2)
    assert second_page["events"] == match.events[2:]
    assert second_page["total"] == 3
    assert second_page["next_offset"] is None


@pytest.mark.asyncio
async def test_events_endpoint_returns_all_events_when_limit_exceeds_count(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_events_large_limit")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_events_large_limit", config)
    match.events = [
        {"type": "MATCH_CREATED", "data": {}, "timestamp": "2026-03-27T10:00:00", "match_id": match.match_id},
        {"type": "MATCH_STARTED", "data": {}, "timestamp": "2026-03-27T10:00:01", "match_id": match.match_id},
    ]
    module.referee.matches[match.match_id] = match

    response = await module.get_events(match.match_id, limit=99)

    assert response["events"] == match.events
    assert response["total"] == 2
    assert response["next_offset"] is None


@pytest.mark.asyncio
async def test_events_endpoint_returns_empty_list_for_match_without_events(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_events_empty")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_events_empty", config)
    module.referee.matches[match.match_id] = match

    response = await module.get_events(match.match_id, limit=50)

    assert response["events"] == []
    assert response["total"] == 0
    assert response["next_offset"] is None


def test_scoring_engine_uses_passed_submission_source_only():
    players = {
        1: PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1"),
        2: PlayerState(player_id=2, container_name="c2", target_container="t2", target_ip="10.0.0.2"),
    }
    scoring = ScoringEngine({"attackSuccess": 100, "defenseFailure": -50, "slaViolation": -50})
    submissions = [
        _build_submission(attacker_id=1, victim_id=2, success=True, timestamp="2026-03-27T10:00:00", points=100),
        _build_submission(attacker_id=1, victim_id=2, success=False, timestamp="2026-03-27T10:00:01", reason="target_mismatch", points=0),
    ]

    leaderboard = scoring.update_scores(players, submissions)

    assert players[1].attack_score == 100
    assert players[1].flags_captured == 1
    assert players[2].defense_score == -50
    assert players[2].flags_lost == 1
    assert leaderboard[1]["total_score"] == 100
    assert leaderboard[2]["total_score"] == -50


@pytest.mark.asyncio
async def test_success_submission_updates_score_from_persisted_submissions_not_runtime_buffer(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_scoring_persisted_source")

    async def _fake_save_submission(match_id: str, submission: dict):
        return None

    monkeypatch.setattr(module.database, "save_submission", _fake_save_submission)
    monkeypatch.setattr(module.database, "save_event", _async_save_event_noop)

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1"), module.PlayerConfig(id=2, name="P2")])
    match = module.MatchState("match_scoring_source", config)
    match.status = "attack"
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.players[2] = PlayerState(player_id=2, container_name="c2", target_container="t2", target_ip="10.0.0.2")
    match.flag_manager.all_flags["FLAG{score}"] = 2
    module.referee.matches[match.match_id] = match

    result = await module.referee.submit_flag(
        match.match_id,
        module.FlagSubmission(player_id=1, flag="FLAG{score}", target_player_id=2),
    )

    assert result["success"] is True
    assert len(match.persisted_submissions) == 1
    assert match.players[1].attack_score == 100
    assert match.players[2].defense_score == -50

    match.flag_manager.submissions.clear()
    leaderboard = match.scoring_engine.update_scores(match.players, match.persisted_submissions)

    assert leaderboard[1]["attack_score"] == 100
    assert leaderboard[2]["defense_score"] == -50


def test_validate_submission_returns_explicit_submission_record():
    import asyncio
    manager = FlagManager(scoring_config={"attackSuccess": 100, "defenseFailure": -50})
    manager.all_flags["FLAG{explicit}"] = 2

    result = asyncio.run(manager.validate_submission(
        attacker_id=1,
        flag="FLAG{explicit}",
        declared_target_player_id=2,
        player_count=3,
    ))

    assert result["success"] is True
    assert result["submission_record"] == manager.submissions[-1]
    assert result["submission_record"]["attacker_id"] == 1
    assert result["submission_record"]["victim_id"] == 2
    assert result["submission_record"]["success"] is True


@pytest.mark.asyncio
async def test_submit_flag_persists_returned_submission_record_even_if_runtime_tail_changes(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_submission_record_source")

    saved_records = []

    async def _fake_save_submission(match_id: str, submission: dict):
        saved_records.append((match_id, dict(submission)))

    monkeypatch.setattr(module.database, "save_submission", _fake_save_submission)
    monkeypatch.setattr(module.database, "save_event", _async_save_event_noop)

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1"), module.PlayerConfig(id=2, name="P2")])
    match = module.MatchState("match_submission_record_source", config)
    match.status = "attack"
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.players[2] = PlayerState(player_id=2, container_name="c2", target_container="t2", target_ip="10.0.0.2")
    module.referee.matches[match.match_id] = match

    original_validate = match.flag_manager.validate_submission

    async def _wrapped_validate(*args, **kwargs):
        result = await original_validate(*args, **kwargs)
        match.flag_manager.submissions.append({
            "attacker_id": 999,
            "victim_id": 999,
            "flag": "FLAG{noise}",
            "success": False,
            "reason": "noise",
            "timestamp": "2026-03-27T10:00:09",
        })
        return result

    match.flag_manager.validate_submission = _wrapped_validate
    match.flag_manager.all_flags["FLAG{real}"] = 2

    result = await module.referee.submit_flag(
        match.match_id,
        module.FlagSubmission(player_id=1, flag="FLAG{real}", target_player_id=2),
    )

    assert result["success"] is True
    assert len(saved_records) == 1
    persisted = saved_records[0][1]
    assert persisted["attacker_id"] == 1
    assert persisted["victim_id"] == 2
    assert persisted["success"] is True
    assert persisted["reason"] == "success"


@pytest.mark.asyncio
async def test_submit_flag_returns_player_feedback_with_status_query_hint(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_submission_feedback")

    async def _fake_save_submission(match_id: str, submission: dict):
        return None

    monkeypatch.setattr(module.database, "save_submission", _fake_save_submission)
    monkeypatch.setattr(module.database, "save_event", _async_save_event_noop)

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1"), module.PlayerConfig(id=2, name="P2")])
    match = module.MatchState("match_submission_feedback", config)
    match.status = "attack"
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.players[2] = PlayerState(player_id=2, container_name="c2", target_container="t2", target_ip="10.0.0.2")
    match.flag_manager.all_flags["FLAG{feedback}"] = 2
    module.referee.matches[match.match_id] = match

    result = await module.referee.submit_flag(
        match.match_id,
        module.FlagSubmission(player_id=1, flag="FLAG{feedback}", target_player_id=2),
    )

    assert result["success"] is True
    _assert_success_feedback(result["player_feedback"])


@pytest.mark.asyncio
async def test_wait_for_all_players_ready_retries_pending_players(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_wait_ready_retries")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_wait_ready_retries", config)
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")

    retry_calls = []

    async def _fake_retry_not_ready_agents(match_obj, player_ids):
        retry_calls.append(list(player_ids))
        match_obj.players[1].ready_status = "AGENT_READY"
        return 1

    monkeypatch.setattr(module.referee, "_retry_not_ready_agents", _fake_retry_not_ready_agents)

    await module.referee._wait_for_all_players_ready(match)

    assert retry_calls == [[1]]


@pytest.mark.asyncio
async def test_send_defense_keepalive_enqueues_buffered_message(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_keepalive_buffered")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_keepalive_buffered", config)
    match.status = "defense"
    match.started_at = datetime.now()
    match.defense_started_at = datetime.now()
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    session = AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1] = session

    enqueue_calls = []

    class DummyClient:
        async def enqueue_buffered_message(self, session, message, **kwargs):
            enqueue_calls.append({"session": session, "message": message, **kwargs})
            return "queued"

    match.player_clients[1] = DummyClient()

    await module.referee._send_defense_keepalive(match, 1, session)

    assert len(enqueue_calls) == 1
    call = enqueue_calls[0]
    assert call["session"] is session
    assert call["message_kind"] == "keepalive"
    assert call["dedupe_key"] == "keepalive"
    assert call["merge_strategy"] == "replace"
    assert session.last_keepalive_sent_at is not None
    assert any(event["type"] == "DEFENSE_KEEPALIVE_BUFFERED" for event in match.events)


@pytest.mark.asyncio
async def test_send_attack_keepalive_enqueues_buffered_message_without_advancing_checkpoint(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_attack_keepalive_buffered")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1"), module.PlayerConfig(id=2, name="P2")])
    match = module.MatchState("match_attack_keepalive_buffered", config)
    match.status = "attack"
    now = datetime.now()
    match.started_at = now
    match.attack_started_at = now
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
        flags_captured=2,
        flags_lost=0,
    )
    match.attack_targets_by_player[1] = [{"player_id": 2, "ip": "10.200.0.2", "port": 3000}]
    match.player_status_checkpoints[1] = {
        "queried_at": "baseline-query",
        "scores_by_player": {
            1: {"total": 90, "attack": 120, "defense": -30, "sla": 0},
            2: {"total": 110, "attack": 150, "defense": -40, "sla": 0},
        },
    }
    session = AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1] = session
    module.referee.matches[match.match_id] = match

    enqueue_calls = []

    class DummyClient:
        async def enqueue_buffered_message(self, session, message, **kwargs):
            enqueue_calls.append({"session": session, "message": message, **kwargs})
            return "queued"

    match.player_clients[1] = DummyClient()

    await module.referee._send_attack_keepalive(match, 1, session)

    assert len(enqueue_calls) == 1
    call = enqueue_calls[0]
    assert call["session"] is session
    assert call["message_kind"] == "attack_keepalive"
    assert call["dedupe_key"] == "attack_keepalive"
    assert call["merge_strategy"] == "replace"
    assert '"phase": "attack"' in call["message"]
    assert "Continue attacking opponents while keeping your own target patched." in call["message"]
    assert "target-ssh '<remote command>'" in call["message"]
    assert "target-ssh 'curl -sf http://localhost:3000/health'" in call["message"]
    assert session.last_keepalive_sent_at is not None
    assert match.player_status_checkpoints[1] == {
        "queried_at": "baseline-query",
        "scores_by_player": {
            1: {"total": 90, "attack": 120, "defense": -30, "sla": 0},
            2: {"total": 110, "attack": 150, "defense": -40, "sla": 0},
        },
    }
    assert any(event["type"] == "ATTACK_KEEPALIVE_BUFFERED" for event in match.events)


@pytest.mark.asyncio
async def test_attack_keepalive_loop_triggers_after_stream_idle_threshold(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_attack_keepalive_loop")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_attack_keepalive_loop", config)
    match.status = "attack"
    now = datetime.now()
    match.started_at = now
    match.attack_started_at = now
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    session = AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    session.last_stream_output_at = asyncio.get_running_loop().time() - 301
    match.agent_sessions[1] = session

    class DummyClient:
        @staticmethod
        def is_session_busy(session):
            return False

        @staticmethod
        def has_buffered_message_kind(session, message_kind):
            return False

    match.player_clients[1] = DummyClient()

    captured_calls = []

    async def _fake_send_attack_keepalive(match_obj, player_id, session_obj):
        captured_calls.append((match_obj.match_id, player_id, session_obj.player_id))
        match_obj.status = "finished"

    async def _fake_sleep(seconds):
        return None

    monkeypatch.setattr(module.referee, "_send_attack_keepalive", _fake_send_attack_keepalive)
    monkeypatch.setattr(module.asyncio, "sleep", _fake_sleep)

    await module.referee._attack_keepalive_loop(match)

    assert captured_calls == [(match.match_id, 1, 1)]


@pytest.mark.asyncio
async def test_defense_keepalive_loop_triggers_for_inactive_session_with_client(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_defense_keepalive_inactive")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_defense_keepalive_inactive", config)
    match.status = "defense"
    now = datetime.now()
    match.started_at = now
    match.defense_started_at = now
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    session = AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1] = session

    class DummyBackend:
        @staticmethod
        def is_session_busy(agent_client, session):
            return False

        @staticmethod
        def has_buffered_message_kind(agent_client, session, message_kind):
            return False

    class DummyClient:
        pass

    match.player_clients[1] = DummyClient()
    match.player_backends[1] = DummyBackend()

    captured_calls = []

    async def _fake_send_defense_keepalive(match_obj, player_id, session_obj):
        captured_calls.append((match_obj.match_id, player_id, session_obj.player_id))
        match_obj.status = "finished"

    async def _fake_sleep(seconds):
        return None

    monkeypatch.setattr(module.referee, "_send_defense_keepalive", _fake_send_defense_keepalive)
    monkeypatch.setattr(module.asyncio, "sleep", _fake_sleep)

    await module.referee._defense_keepalive_loop(match)

    assert captured_calls == [(match.match_id, 1, 1)]


@pytest.mark.asyncio
async def test_match_timer_cancellation_drains_heartbeat_and_keepalive_tasks(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_match_timer_cancel_drains_children")

    config = module.MatchConfig(
        match=module.MatchDetails(
            name="Cancel Timer",
            duration=120,
            phases=module.MatchPhaseConfig(defense=120, attack=0),
        ),
        players=[module.PlayerConfig(id=1, name="P1")],
    )
    match = module.MatchState("match_timer_cancel_drains", config)
    match.status = "defense"

    heartbeat_cancelled = asyncio.Event()
    keepalive_cancelled = asyncio.Event()

    async def _fake_heartbeat_loop(match_obj, total_seconds):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            heartbeat_cancelled.set()
            raise

    async def _fake_defense_keepalive_loop(match_obj):
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            keepalive_cancelled.set()
            raise

    monkeypatch.setattr(module.referee, "_heartbeat_loop", _fake_heartbeat_loop)
    monkeypatch.setattr(module.referee, "_defense_keepalive_loop", _fake_defense_keepalive_loop)

    timer_task = asyncio.create_task(module.referee._match_timer(match))
    await asyncio.sleep(0)

    timer_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await timer_task

    assert heartbeat_cancelled.is_set()
    assert keepalive_cancelled.is_set()


@pytest.mark.asyncio
async def test_submit_flag_enqueues_victim_alert_instead_of_fire_and_forget(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_submission_buffered_alert")

    async def _fake_save_submission(match_id: str, submission: dict):
        return None

    monkeypatch.setattr(module.database, "save_submission", _fake_save_submission)
    monkeypatch.setattr(module.database, "save_event", _async_save_event_noop)

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1"), module.PlayerConfig(id=2, name="P2")])
    match = module.MatchState("match_submission_buffered_alert", config)
    match.status = "attack"
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.players[2] = PlayerState(player_id=2, container_name="c2", target_container="t2", target_ip="10.0.0.2")
    match.agent_sessions[2] = AgentSession(
        player_id=2,
        container_name="c2",
        target_container="t2",
        target_ip="10.0.0.2",
    )
    match.flag_manager.all_flags["FLAG{buffered-alert}"] = 2
    module.referee.matches[match.match_id] = match

    captured_enqueue_calls = []

    class DummyVictimClient:
        async def enqueue_buffered_message(self, session, message, **kwargs):
            captured_enqueue_calls.append({
                "session": session,
                "message": message,
                **kwargs,
            })
            return "queued"

    match.player_clients[2] = DummyVictimClient()

    result = await module.referee.submit_flag(
        match.match_id,
        module.FlagSubmission(player_id=1, flag="FLAG{buffered-alert}", target_player_id=2),
    )

    assert result["success"] is True
    assert len(captured_enqueue_calls) == 1
    call = captured_enqueue_calls[0]
    assert call["session"] is match.agent_sessions[2]
    assert call["message_kind"] == "flag_alert"
    assert call["dedupe_key"] == "flag_alert"
    assert call["merge_strategy"] == "append"
    assert call["timeout"] == 120
    assert call["message"].startswith("[ALERT] Your flag")


@pytest.mark.asyncio
async def test_match_timer_dispatches_attack_prompt_as_interrupt(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_attack_prompt_interrupt")

    async def _fake_update_match_status(*args, **kwargs):
        return None

    async def _fake_open_arena_network(match):
        return None

    end_match_calls = []

    async def _fake_end_match(match_id: str):
        end_match_calls.append(match_id)
        return {"match_id": match_id, "status": "finished"}

    monkeypatch.setattr(module.database, "update_match_status", _fake_update_match_status)
    monkeypatch.setattr(module.referee, "_open_arena_network", _fake_open_arena_network)
    monkeypatch.setattr(module.referee, "end_match", _fake_end_match)

    class DummyContainer:
        def __init__(self, ip: str):
            self.attrs = {"NetworkSettings": {"Networks": {"awd_match_attack_prompt_interrupt_arena": {"IPAddress": ip}}}}

        def reload(self):
            return None

    class DummyDockerClient:
        class Containers:
            @staticmethod
            def get(name: str):
                return DummyContainer("10.10.0.2")

        containers = Containers()

    monkeypatch.setattr(module.docker, "from_env", lambda: DummyDockerClient())

    config = module.MatchConfig(
        match=module.MatchDetails(name="Attack Prompt", duration=0, phases=module.MatchPhaseConfig(defense=0, attack=0)),
        players=[module.PlayerConfig(id=1, name="P1")],
    )
    match = module.MatchState("match_attack_prompt_interrupt", config)
    match.status = "defense"
    match.started_at = datetime.now()
    match.defense_started_at = datetime.now()
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1] = AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1].last_activity_at = asyncio.get_running_loop().time()
    match.player_read_tokens[1] = "token-1"

    send_calls = []
    freeze_calls = []
    unfreeze_calls = []

    class DummyPlayerClient:
        def freeze_buffered_messages(self, session):
            freeze_calls.append(session.player_id)

        def unfreeze_buffered_messages(self, session):
            unfreeze_calls.append(session.player_id)

        async def send_message(self, session, message, **kwargs):
            send_calls.append({"session": session, "message": message, **kwargs})
            return "ok"

        async def check_session_contains(self, session, keyword, tail_lines=50):
            return True

        async def drain_buffered_messages(self, session):
            return 0

    match.player_clients[1] = DummyPlayerClient()

    await module.referee._match_timer(match)

    assert freeze_calls == [1]
    assert unfreeze_calls == [1]
    assert len(send_calls) == 1
    assert send_calls[0]["message_kind"] == "attack_prompt"
    assert send_calls[0]["message_mode"] == module.MESSAGE_MODE_INTERRUPT
    assert end_match_calls == [match.match_id]


@pytest.mark.asyncio
async def test_match_timer_starts_attack_keepalive_loop(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_attack_keepalive_task")

    async def _fake_update_match_status(*args, **kwargs):
        return None

    async def _fake_open_arena_network(match):
        return None

    end_match_calls = []

    async def _fake_end_match(match_id: str):
        end_match_calls.append(match_id)
        return {"match_id": match_id, "status": "finished"}

    monkeypatch.setattr(module.database, "update_match_status", _fake_update_match_status)
    monkeypatch.setattr(module.referee, "_open_arena_network", _fake_open_arena_network)
    monkeypatch.setattr(module.referee, "end_match", _fake_end_match)

    class DummyContainer:
        def __init__(self, ip: str):
            self.attrs = {"NetworkSettings": {"Networks": {"awd_match_attack_keepalive_task_arena": {"IPAddress": ip}}}}

        def reload(self):
            return None

    class DummyDockerClient:
        class Containers:
            @staticmethod
            def get(name: str):
                return DummyContainer("10.10.0.2")

        containers = Containers()

    monkeypatch.setattr(module.docker, "from_env", lambda: DummyDockerClient())

    config = module.MatchConfig(
        match=module.MatchDetails(name="Attack Keepalive Task", duration=0, phases=module.MatchPhaseConfig(defense=0, attack=0)),
        players=[module.PlayerConfig(id=1, name="P1")],
    )
    match = module.MatchState("match_attack_keepalive_task", config)
    match.status = "defense"
    match.started_at = datetime.now()
    match.defense_started_at = datetime.now()
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1] = AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1].last_activity_at = asyncio.get_running_loop().time()
    match.player_read_tokens[1] = "token-1"

    attack_keepalive_calls = []

    class DummyPlayerClient:
        def freeze_buffered_messages(self, session):
            return None

        def unfreeze_buffered_messages(self, session):
            return None

        async def send_message(self, session, message, **kwargs):
            return "ok"

        async def check_session_contains(self, session, keyword, tail_lines=50):
            return True

        async def drain_buffered_messages(self, session):
            return 0

    async def _fake_attack_keepalive_loop(match_obj):
        attack_keepalive_calls.append(match_obj.match_id)
        await asyncio.sleep(0)

    monkeypatch.setattr(module.referee, "_attack_keepalive_loop", _fake_attack_keepalive_loop)
    match.player_clients[1] = DummyPlayerClient()

    await module.referee._match_timer(match)

    assert attack_keepalive_calls == [match.match_id]
    assert end_match_calls == [match.match_id]


@pytest.mark.asyncio
async def test_match_timer_cancels_slow_attack_prompt_tasks(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_attack_prompt_cancel_drains")

    async def _fake_update_match_status(*args, **kwargs):
        return None

    async def _fake_open_arena_network(match):
        return None

    monkeypatch.setattr(module.database, "update_match_status", _fake_update_match_status)
    monkeypatch.setattr(module.referee, "_open_arena_network", _fake_open_arena_network)
    monkeypatch.setattr(module.referee, "end_match", _async_save_event_noop)
    monkeypatch.setattr(module.referee, "_attack_prompt_delivery_timeout", lambda *_args: 1)

    class DummyContainer:
        def __init__(self, ip: str):
            self.attrs = {"NetworkSettings": {"Networks": {"awd_match_attack_prompt_cancel_drains_arena": {"IPAddress": ip}}}}

        def reload(self):
            return None

    class DummyDockerClient:
        class Containers:
            @staticmethod
            def get(name: str):
                return DummyContainer("10.10.0.2")

        containers = Containers()

    monkeypatch.setattr(module.docker, "from_env", lambda: DummyDockerClient())

    config = module.MatchConfig(
        match=module.MatchDetails(name="Attack Prompt Cancel", duration=1, phases=module.MatchPhaseConfig(defense=0, attack=1)),
        players=[module.PlayerConfig(id=1, name="P1")],
    )
    match = module.MatchState("match_attack_prompt_cancel_drains", config)
    match.status = "defense"
    match.started_at = datetime.now()
    match.defense_started_at = datetime.now()
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1] = AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1].last_activity_at = asyncio.get_running_loop().time()
    match.player_read_tokens[1] = "token-1"

    attack_prompt_started = asyncio.Event()
    attack_prompt_cancelled = asyncio.Event()

    class DummyPlayerClient:
        def freeze_buffered_messages(self, session):
            return None

        def unfreeze_buffered_messages(self, session):
            return None

        async def send_message(self, session, message, **kwargs):
            attack_prompt_started.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                attack_prompt_cancelled.set()
                raise

        async def check_session_contains(self, session, keyword, tail_lines=50):
            return True

        async def drain_buffered_messages(self, session):
            return 0

    match.player_clients[1] = DummyPlayerClient()

    await module.referee._match_timer(match)

    assert attack_prompt_started.is_set()
    assert attack_prompt_cancelled.is_set()


@pytest.mark.asyncio
async def test_initialize_agents_retains_client_for_false_negative_result(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_initialize_agents_retains_client")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_initialize_agents_retains_client", config)
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1] = AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")

    retained_client = object()

    async def _fake_initialize_single_agent(match_obj, pid, session):
        assert match_obj is match
        assert pid == 1
        assert session is match.agent_sessions[1]
        return module.AgentInitializationResult(
            player_id=1,
            success=False,
            reason="INIT_PROMPT_NO_RESPONSE",
            details="agent returned no init reply yet",
            client=retained_client,
        )

    monkeypatch.setattr(module.referee, "_initialize_single_agent", _fake_initialize_single_agent)

    ready_count = await module.referee._initialize_agents(match)

    assert ready_count == 0
    assert match.player_clients[1] is retained_client
    assert match.players[1].ready_status == "AGENT_NOT_READY"
    assert match.players[1].ready_reason == "INIT_PROMPT_NO_RESPONSE"
    not_ready_events = [event for event in match.events if event["type"] == "AGENT_NOT_READY"]
    assert not_ready_events
    assert not_ready_events[-1]["data"] == {
        "player_id": 1,
        "ready_status": "AGENT_NOT_READY",
        "ready_reason": "INIT_PROMPT_NO_RESPONSE",
        "readiness_details": {
            "runtime_ready": True,
            "session_ready": False,
            "interactive_ready": False,
            "init_ready": False,
            "session_id": None,
        },
        "reason": "INIT_PROMPT_NO_RESPONSE",
        "details": "agent returned no init reply yet",
    }


@pytest.mark.asyncio
async def test_initialize_agents_records_target_ssh_probe_failure_event(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_initialize_agents_target_ssh_probe_failure")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_initialize_agents_target_ssh_probe_failure", config)
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1] = AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")

    async def _fake_initialize_single_agent(match_obj, pid, session):
        assert match_obj is match
        assert pid == 1
        assert session is match.agent_sessions[1]
        return module.AgentInitializationResult(
            player_id=1,
            success=False,
            reason="TARGET_SSH_AUTHORIZED_KEYS_MISSING",
            details="Permission denied (publickey,password).",
            client=None,
        )

    monkeypatch.setattr(module.referee, "_initialize_single_agent", _fake_initialize_single_agent)

    ready_count = await module.referee._initialize_agents(match)

    assert ready_count == 0
    assert 1 not in match.player_clients
    assert match.players[1].ready_status == "AGENT_NOT_READY"
    assert match.players[1].ready_reason == "TARGET_SSH_AUTHORIZED_KEYS_MISSING"
    not_ready_events = [event for event in match.events if event["type"] == "AGENT_NOT_READY"]
    assert not_ready_events
    assert not_ready_events[-1]["data"] == {
        "player_id": 1,
        "ready_status": "AGENT_NOT_READY",
        "ready_reason": "TARGET_SSH_AUTHORIZED_KEYS_MISSING",
        "readiness_details": {
            "runtime_ready": False,
            "session_ready": False,
            "interactive_ready": False,
            "init_ready": False,
            "session_id": None,
        },
        "reason": "TARGET_SSH_AUTHORIZED_KEYS_MISSING",
        "details": "Permission denied (publickey,password).",
    }


@pytest.mark.asyncio
async def test_sync_and_emit_readiness_layers_emits_runtime_transition_event(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_readiness_layer_runtime_transition")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_readiness_layer_runtime_transition", config)
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1] = AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1].runtime_ready = True

    await module.referee._sync_and_emit_readiness_layers(
        match,
        1,
        phase="defense",
        reason="RUNTIME_CLIENT_READY",
        details="runtime client retained",
    )

    readiness_events = [event for event in match.events if event["type"] == "AGENT_READINESS_LAYER"]
    assert readiness_events
    assert readiness_events[-1]["data"] == {
        "player_id": 1,
        "phase": "defense",
        "layer": "runtime_ready",
        "enabled": True,
        "reason": "RUNTIME_CLIENT_READY",
        "readiness_details": {
            "runtime_ready": True,
            "session_ready": False,
            "interactive_ready": False,
            "init_ready": False,
            "session_id": None,
        },
        "previous_value": False,
        "details": "runtime client retained",
    }


@pytest.mark.asyncio
async def test_sync_and_emit_readiness_layers_emits_session_metadata_refresh_event(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_readiness_layer_session_metadata_refresh")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_readiness_layer_session_metadata_refresh", config)
    match.players[1] = PlayerState(
        player_id=1,
        container_name="c1",
        target_container="t1",
        target_ip="10.0.0.1",
        readiness_details={
            "runtime_ready": True,
            "session_ready": True,
            "interactive_ready": False,
            "init_ready": False,
            "session_id": None,
        },
    )
    match.agent_sessions[1] = AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1].runtime_ready = True
    match.agent_sessions[1].session_ready = True
    match.agent_sessions[1].session_id = "ses-123"

    await module.referee._sync_and_emit_readiness_layers(
        match,
        1,
        phase="defense",
        reason="SESSION_METADATA_REFRESHED",
        details="session id captured after initial readiness",
    )

    readiness_events = [event for event in match.events if event["type"] == "AGENT_READINESS_LAYER"]
    assert readiness_events
    assert readiness_events[-1]["data"] == {
        "player_id": 1,
        "phase": "defense",
        "layer": "session_ready",
        "enabled": True,
        "reason": "SESSION_METADATA_REFRESHED",
        "readiness_details": {
            "runtime_ready": True,
            "session_ready": True,
            "interactive_ready": False,
            "init_ready": False,
            "session_id": "ses-123",
        },
        "previous_value": True,
        "details": "session id captured after initial readiness",
    }


@pytest.mark.asyncio
async def test_match_timer_promotes_false_negative_player_when_attack_prompt_returns(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_attack_prompt_late_ready")

    async def _fake_update_match_status(*args, **kwargs):
        return None

    async def _fake_open_arena_network(match):
        return None

    end_match_calls = []

    async def _fake_end_match(match_id: str):
        end_match_calls.append(match_id)
        return {"match_id": match_id, "status": "finished"}

    monkeypatch.setattr(module.database, "update_match_status", _fake_update_match_status)
    monkeypatch.setattr(module.referee, "_open_arena_network", _fake_open_arena_network)
    monkeypatch.setattr(module.referee, "end_match", _fake_end_match)

    class DummyContainer:
        def __init__(self, ip: str):
            self.attrs = {"NetworkSettings": {"Networks": {"awd_match_attack_prompt_late_ready_arena": {"IPAddress": ip}}}}

        def reload(self):
            return None

    class DummyDockerClient:
        class Containers:
            @staticmethod
            def get(name: str):
                return DummyContainer("10.10.0.2")

        containers = Containers()

    monkeypatch.setattr(module.docker, "from_env", lambda: DummyDockerClient())

    config = module.MatchConfig(
        match=module.MatchDetails(name="Attack Prompt Late Ready", duration=0, phases=module.MatchPhaseConfig(defense=0, attack=0)),
        players=[module.PlayerConfig(id=1, name="P1")],
    )
    match = module.MatchState("match_attack_prompt_late_ready", config)
    match.status = "defense"
    match.started_at = datetime.now()
    match.defense_started_at = datetime.now()
    match.players[1] = PlayerState(
        player_id=1,
        container_name="c1",
        target_container="t1",
        target_ip="10.0.0.1",
        ready_status="AGENT_NOT_READY",
        ready_reason="INIT_PROMPT_NO_RESPONSE",
    )
    match.agent_sessions[1] = AgentSession(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.agent_sessions[1].last_activity_at = asyncio.get_running_loop().time()
    match.player_read_tokens[1] = "token-1"

    player_client_calls = []
    fallback_client_calls = []

    class DummyPlayerClient:
        def freeze_buffered_messages(self, session):
            player_client_calls.append(("freeze", session.player_id))

        def unfreeze_buffered_messages(self, session):
            player_client_calls.append(("unfreeze", session.player_id))

        async def send_message(self, session, message, **kwargs):
            player_client_calls.append(("send", session.player_id, kwargs.get("message_kind")))
            return "ok"

        async def check_session_contains(self, session, keyword, tail_lines=50):
            player_client_calls.append(("verify", session.player_id, keyword))
            return True

        async def drain_buffered_messages(self, session):
            player_client_calls.append(("drain", session.player_id))
            return 0

    class DummyFallbackClient:
        def freeze_buffered_messages(self, session):
            fallback_client_calls.append(("freeze", session.player_id))

        def unfreeze_buffered_messages(self, session):
            fallback_client_calls.append(("unfreeze", session.player_id))

        async def send_message(self, session, message, **kwargs):
            fallback_client_calls.append(("send", session.player_id, kwargs.get("message_kind")))
            return "ok"

        async def check_session_contains(self, session, keyword, tail_lines=50):
            fallback_client_calls.append(("verify", session.player_id, keyword))
            return True

        async def drain_buffered_messages(self, session):
            fallback_client_calls.append(("drain", session.player_id))
            return 0

    match.player_clients[1] = DummyPlayerClient()
    match.agent_client = DummyFallbackClient()

    await module.referee._match_timer(match)

    assert ("freeze", 1) in player_client_calls
    assert ("send", 1, "attack_prompt") in player_client_calls
    assert ("unfreeze", 1) in player_client_calls
    assert not fallback_client_calls
    assert match.players[1].ready_status == "AGENT_READY"
    assert match.players[1].ready_reason == "READY_ATTACK_PROMPT_RESPONSE"
    assert match.players[1].readiness_details == {
        "runtime_ready": False,
        "session_ready": False,
        "interactive_ready": True,
        "init_ready": False,
        "session_id": None,
    }
    ready_events = [event for event in match.events if event["type"] == "AGENT_READY"]
    assert ready_events
    assert ready_events[-1]["data"]["player_id"] == 1
    assert ready_events[-1]["data"]["previous_ready_status"] == "AGENT_NOT_READY"
    assert ready_events[-1]["data"]["previous_ready_reason"] == "INIT_PROMPT_NO_RESPONSE"
    assert ready_events[-1]["data"]["readiness_details"] == {
        "runtime_ready": False,
        "session_ready": False,
        "interactive_ready": True,
        "init_ready": False,
        "session_id": None,
    }
    assert end_match_calls == [match.match_id]


@pytest.mark.asyncio
async def test_submit_flag_scores_even_when_declared_target_mismatches(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_submission_feedback_target_audit_only")

    saved_submissions = []

    async def _fake_save_submission(match_id: str, submission: dict):
        saved_submissions.append((match_id, dict(submission)))

    monkeypatch.setattr(module.database, "save_submission", _fake_save_submission)
    monkeypatch.setattr(module.database, "save_event", _async_save_event_noop)

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1"), module.PlayerConfig(id=2, name="P2")])
    match = module.MatchState("match_submission_feedback_rejected", config)
    match.status = "attack"
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.players[2] = PlayerState(player_id=2, container_name="c2", target_container="t2", target_ip="10.0.0.2")
    match.flag_manager.all_flags["FLAG{feedback}"] = 2
    module.referee.matches[match.match_id] = match

    result = await module.referee.submit_flag(
        match.match_id,
        module.FlagSubmission(player_id=1, flag="FLAG{feedback}", target_player_id=1),
    )

    assert result["success"] is True
    assert result["reason"] == "success"
    assert result["victim_id"] == 2
    assert match.players[1].attack_score == 100
    assert match.players[2].defense_score == -50
    _assert_success_feedback(result["player_feedback"])
    assert len(saved_submissions) == 1
    persisted = saved_submissions[0][1]
    assert persisted["success"] is True
    assert persisted["reason"] == "success"
    assert persisted["victim_id"] == 2
    assert persisted["declared_target_player_id"] == 1


@pytest.mark.asyncio
async def test_submit_endpoint_returns_player_feedback_via_http(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_submit_http")

    async def _fake_save_submission(match_id: str, submission: dict):
        return None

    async def _fake_save_event(match_id: str, event_type: str, data: dict, timestamp):
        return None

    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)
    monkeypatch.setattr(module.database, "init_db", _async_noop)
    monkeypatch.setattr(module.database, "load_all_matches", _async_empty_matches)
    monkeypatch.setattr(module.database, "save_submission", _fake_save_submission)
    monkeypatch.setattr(module.database, "save_event", _async_save_event_noop)
    monkeypatch.setattr(module.database, "save_event", _fake_save_event)

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1"), module.PlayerConfig(id=2, name="P2")])
    match = module.MatchState("match_submit_http", config)
    match.status = "attack"
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.players[2] = PlayerState(player_id=2, container_name="c2", target_container="t2", target_ip="10.0.0.2")
    match.flag_manager.all_flags["FLAG{http}"] = 2
    module.referee.matches[match.match_id] = match
    module.referee.player_match_index[1] = match.match_id
    token = module.referee._issue_player_read_token(match, 1)

    transport = httpx.ASGITransport(app=module.app)
    async with module.app.router.lifespan_context(module.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/submit",
                json={"target_player_id": 2, "flag": "FLAG{http}"},
                headers={"X-Player-Token": token},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["reason"] == "success"
    _assert_success_feedback(payload["player_feedback"])


@pytest.mark.asyncio
async def test_submit_endpoint_rejects_missing_player_token(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_submit_http_missing_token")

    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)
    monkeypatch.setattr(module.database, "init_db", _async_noop)
    monkeypatch.setattr(module.database, "load_all_matches", _async_empty_matches)

    transport = httpx.ASGITransport(app=module.app)
    async with module.app.router.lifespan_context(module.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/submit",
                json={"player_id": 1, "target_player_id": 2, "flag": "FLAG{http}"},
            )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_submit_endpoint_rejects_spoofed_player_id(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_submit_http_spoofed_player")

    monkeypatch.setattr(module.referee, "validate_docker_api_compatibility", _async_noop)
    monkeypatch.setattr(module.database, "init_db", _async_noop)
    monkeypatch.setattr(module.database, "load_all_matches", _async_empty_matches)

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1"), module.PlayerConfig(id=2, name="P2")])
    match = module.MatchState("match_submit_http_spoofed", config)
    match.status = "attack"
    match.players[1] = PlayerState(player_id=1, container_name="c1", target_container="t1", target_ip="10.0.0.1")
    match.players[2] = PlayerState(player_id=2, container_name="c2", target_container="t2", target_ip="10.0.0.2")
    module.referee.matches[match.match_id] = match
    token = module.referee._issue_player_read_token(match, 1)

    transport = httpx.ASGITransport(app=module.app)
    async with module.app.router.lifespan_context(module.app):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post(
                "/api/submit",
                json={"player_id": 2, "target_player_id": 1, "flag": "FLAG{http}"},
                headers={"X-Player-Token": token},
            )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_detail_endpoint_keeps_recent_summary_while_events_endpoint_keeps_full_feed(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_detail_vs_events")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_detail_vs_events", config)
    match.events = [
        {"type": "E1", "data": {"seq": 1}, "timestamp": "2026-03-27T10:00:01", "match_id": match.match_id},
        {"type": "E2", "data": {"seq": 2}, "timestamp": "2026-03-27T10:00:02", "match_id": match.match_id},
        {"type": "E3", "data": {"seq": 3}, "timestamp": "2026-03-27T10:00:03", "match_id": match.match_id},
    ]
    match.agent_logs = {1: "internal-only"}
    module.referee.matches[match.match_id] = match

    detail = module.referee.get_match_status(match.match_id)
    feed = await module.get_events(match.match_id, limit=100)

    assert detail["recent_events"] == match.events
    assert detail["events_count"] == 3
    assert "events" not in detail
    assert "agent_logs" not in detail
    assert feed["events"] == match.events
    assert feed["total"] == 3


def test_werewolf_detail_recent_events_uses_same_public_filter_as_events_endpoint(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_werewolf_detail_event_filter")

    players = [module.PlayerConfig(id=i, name=f"P{i}") for i in range(1, 13)]
    config = module.MatchConfig(mode="werewolf", players=players)
    match = module.MatchState("match_werewolf_detail_filter", config)
    match.events = [
        {"type": "AGENT_STREAM", "data": {"player_id": 1, "content": "private reasoning"}, "timestamp": "2026-03-27T10:00:01", "match_id": match.match_id},
        {"type": "WEREWOLF_PUBLIC_SPEECH", "data": {"player_id": 1, "text": "public"}, "timestamp": "2026-03-27T10:00:02", "match_id": match.match_id},
        {"type": "WEREWOLF_PLAYER_TURN_STARTED_PRIVATE", "data": {"role": "seer"}, "timestamp": "2026-03-27T10:00:03", "match_id": match.match_id, "audience": "hidden"},
        {"type": "MATCH_FINISHED", "data": {"mode": "werewolf"}, "timestamp": "2026-03-27T10:00:04", "match_id": match.match_id},
    ]
    module.referee.matches[match.match_id] = match

    detail = module.referee.get_match_status(match.match_id)

    assert [event["type"] for event in detail["recent_events"]] == [
        "WEREWOLF_PUBLIC_SPEECH",
        "MATCH_FINISHED",
    ]
    assert detail["events_count"] == 2


@pytest.mark.asyncio
async def test_public_events_redact_agent_stream_logs_and_flags(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_public_event_redaction")

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1"), module.PlayerConfig(id=2, name="P2")])
    match = module.MatchState("match_public_event_redaction", config)
    match.events = [
        {
            "type": "AGENT_STREAM",
            "data": {
                "player_id": 1,
                "content": (
                    "Authorization: Bearer stream-secret-token\n"
                    "X-Player-Token: player-secret-token\n"
                    "api_key=sk-streamsecret\n"
                    "found FLAG{stream-secret}"
                ),
            },
            "timestamp": "2026-03-27T10:00:01",
            "match_id": match.match_id,
        },
        {
            "type": "AGENT_LOGS_COLLECTED",
            "data": {
                "players": {1: 128},
                "logs": {
                    1: (
                        "cookie=session-secret; token=log-secret\n"
                        "Authorization: Bearer log-secret-token\n"
                        "FLAG{log-secret}"
                    )
                },
            },
            "timestamp": "2026-03-27T10:00:02",
            "match_id": match.match_id,
        },
        {
            "type": "FLAG_SUBMISSION",
            "data": _build_submission(
                attacker_id=1,
                victim_id=2,
                success=True,
                timestamp="2026-03-27T10:00:03",
                flag_slot="database_flag",
                flag_index=2,
            ),
            "timestamp": "2026-03-27T10:00:03",
            "match_id": match.match_id,
        },
    ]
    module.referee.matches[match.match_id] = match

    detail = module.referee.get_match_status(match.match_id)
    feed = await module.get_events(match.match_id, limit=100)
    serialized = json.dumps({"detail": detail, "feed": feed}, ensure_ascii=False)

    assert "stream-secret-token" not in serialized
    assert "player-secret-token" not in serialized
    assert "sk-streamsecret" not in serialized
    assert "log-secret-token" not in serialized
    assert "session-secret" not in serialized
    assert "log-secret" not in serialized
    assert "FLAG{stream-secret}" not in serialized
    assert "FLAG{log-secret}" not in serialized
    assert "FLAG{1-2}" not in serialized
    assert "FLAG{********}" in serialized


def test_public_agent_log_redaction_preserves_runtime_raw_logs(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_public_log_redaction_helper")

    raw_logs = {
        1: "Authorization: Bearer runtime-secret-token\nFLAG{runtime-secret}\n",
    }
    public_logs = module.sanitize_public_agent_logs(raw_logs)

    assert raw_logs[1].startswith("Authorization: Bearer runtime-secret-token")
    assert "runtime-secret-token" not in public_logs[1]
    assert "FLAG{runtime-secret}" not in public_logs[1]
