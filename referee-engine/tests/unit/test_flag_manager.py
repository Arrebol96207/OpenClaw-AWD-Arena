import asyncio
from flag_manager import FLAG_SLOT_SEQUENCE, SLA_PROBES, FlagManager, PlayerState, SLAChecker


def test_flag_slot_sequence_covers_hard_mode_slots():
    assert FLAG_SLOT_SEQUENCE == [
        ("admin_notes", 1),
        ("database_flag", 2),
        ("etc_flag", 3),
        ("credentials_flag", 4),
        ("report_template_flag", 5),
        ("webhook_audit_flag", 6),
    ]


def _build_manager() -> FlagManager:
    manager = FlagManager(scoring_config={"attackSuccess": 100, "defenseFailure": -50})
    manager.all_flags["FLAG{shared}"] = 2
    manager.flag_metadata["FLAG{shared}"] = {
        "owner_id": 2,
        "flag_slot": "database_flag",
        "flag_index": 2,
    }
    return manager


def test_same_attacker_cannot_score_same_flag_twice():
    manager = _build_manager()

    first = asyncio.run(manager.validate_submission(attacker_id=1, flag="FLAG{shared}", declared_target_player_id=2, player_count=3))
    second = asyncio.run(manager.validate_submission(attacker_id=1, flag="FLAG{shared}", declared_target_player_id=2, player_count=3))

    assert first["success"] is True
    assert first["points"] == 100
    assert first["submission_record"]["success"] is True
    assert first["submission_record"]["flag_slot"] == "database_flag"
    assert first["submission_record"]["flag_index"] == 2
    assert second["success"] is False
    assert second["reason"] == "flag_already_claimed_by_attacker"
    assert second["points"] == 0
    assert second["submission_record"]["reason"] == "flag_already_claimed_by_attacker"
    assert second["submission_record"]["flag_index"] == 2


def test_different_attackers_can_each_score_same_flag_once():
    manager = _build_manager()

    first = asyncio.run(manager.validate_submission(attacker_id=1, flag="FLAG{shared}", declared_target_player_id=2, player_count=3))
    second = asyncio.run(manager.validate_submission(attacker_id=3, flag="FLAG{shared}", declared_target_player_id=2, player_count=3))

    assert first["success"] is True
    assert second["success"] is True
    assert second["attacker_id"] == 3
    assert second["victim_id"] == 2
    assert second["points"] == 100
    assert first["submission_record"]["attacker_id"] == 1
    assert second["submission_record"]["attacker_id"] == 3
    assert second["submission_record"]["flag_slot"] == "database_flag"


def test_wrong_target_still_scores_and_records_declared_target_for_audit():
    manager = _build_manager()

    wrong_target = asyncio.run(manager.validate_submission(attacker_id=1, flag="FLAG{shared}", declared_target_player_id=3, player_count=3))

    assert wrong_target["success"] is True
    assert wrong_target["reason"] == "success"
    assert wrong_target["victim_id"] == 2
    assert wrong_target["points"] == 100
    assert wrong_target["submission_record"]["reason"] == "success"
    assert wrong_target["submission_record"]["declared_target_player_id"] == 3
    assert wrong_target["submission_record"]["victim_id"] == 2


def _run_sla_check(monkeypatch, results_by_probe: dict[str, int]):
    calls = []

    async def _fake_docker_exec(container_name, command, **_kwargs):
        calls.append((container_name, command))
        url = command[-1]
        probe_name = next(name for name, probe_url in SLA_PROBES if probe_url == url)
        return results_by_probe.get(probe_name, 1), "", ""

    monkeypatch.setattr("flag_manager.docker_exec_simple", _fake_docker_exec)
    checker = SLAChecker(penalty_per_minute=50)
    player = PlayerState(player_id=1, container_name="agent", target_container="target", target_ip="10.0.0.2")
    results = asyncio.run(checker.check_all({1: player}))
    return player, results, calls


def test_sla_checker_requires_all_business_probes(monkeypatch):
    player, results, calls = _run_sla_check(monkeypatch, {"health": 0, "login": 0, "downloads": 0})

    assert results == {1: True}
    assert player.sla_up is True
    assert player.sla_status == "UP"
    assert player.sla_details == "all checks ok"
    assert [command[-1] for _container, command in calls] == [probe_url for _name, probe_url in SLA_PROBES]


def test_sla_checker_marks_degraded_when_business_probe_fails(monkeypatch):
    player, results, calls = _run_sla_check(monkeypatch, {"health": 0, "login": 1, "downloads": 0})

    assert results == {1: False}
    assert player.sla_up is False
    assert player.sla_status == "DEGRADED"
    assert player.sla_details == "health=ok, login=fail, downloads=ok"
    assert player.sla_down_minutes == 1
    assert player.sla_score == -50
    assert [command[-1] for _container, command in calls] == [probe_url for _name, probe_url in SLA_PROBES]


def test_sla_checker_short_circuits_business_probes_when_health_fails(monkeypatch):
    player, results, calls = _run_sla_check(monkeypatch, {"health": 1, "login": 0, "downloads": 0})

    assert results == {1: False}
    assert player.sla_up is False
    assert player.sla_status == "DOWN"
    assert player.sla_details == "health=fail, login=fail, downloads=fail"
    assert player.sla_down_minutes == 1
    assert player.sla_score == -50
    assert [command[-1] for _container, command in calls] == ["http://localhost:3000/health"]
