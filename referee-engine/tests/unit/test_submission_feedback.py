from submission_feedback import build_submission_feedback


def test_build_submission_feedback_for_success_includes_points_and_status_hint():
    feedback = build_submission_feedback({"success": True, "reason": "success", "points": 100})

    assert feedback["status_query_recommended"] is True
    assert feedback["player_status_endpoint"] == "/api/player/status"
    assert feedback["required_header"] == "X-Player-Token"
    assert "GET /api/player/status" in feedback["status_query_hint"]
    assert "Flag submission succeeded" in feedback["summary"]
    assert "You gained 100 points." in feedback["summary"]


def test_build_submission_feedback_for_known_rejection_reason():
    feedback = build_submission_feedback({"success": False, "reason": "own_flag", "points": 0})

    assert feedback["summary"] == "Flag rejected: you cannot submit your own team's flag."
    assert "You gained" not in feedback["summary"]


def test_build_submission_feedback_for_unknown_reason_is_still_actionable():
    feedback = build_submission_feedback({"success": False, "reason": "rate_limited"})

    assert feedback["summary"] == "Flag submission processed with result: rate_limited."
    assert feedback["status_query_recommended"] is True
