from flag_manager import FlagManager


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

    first = manager.validate_submission(attacker_id=1, flag="FLAG{shared}", declared_target_player_id=2, player_count=3)
    second = manager.validate_submission(attacker_id=1, flag="FLAG{shared}", declared_target_player_id=2, player_count=3)

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

    first = manager.validate_submission(attacker_id=1, flag="FLAG{shared}", declared_target_player_id=2, player_count=3)
    second = manager.validate_submission(attacker_id=3, flag="FLAG{shared}", declared_target_player_id=2, player_count=3)

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

    wrong_target = manager.validate_submission(attacker_id=1, flag="FLAG{shared}", declared_target_player_id=3, player_count=3)

    assert wrong_target["success"] is True
    assert wrong_target["reason"] == "success"
    assert wrong_target["victim_id"] == 2
    assert wrong_target["points"] == 100
    assert wrong_target["submission_record"]["reason"] == "success"
    assert wrong_target["submission_record"]["declared_target_player_id"] == 3
    assert wrong_target["submission_record"]["victim_id"] == 2
