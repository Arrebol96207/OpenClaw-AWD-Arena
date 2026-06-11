from datetime import datetime
from types import SimpleNamespace

from match_report import (
    build_match_report_markdown,
    leaderboard_rows_for_report,
    markdown_cell,
    submission_summary_for_report,
)


def _match(**overrides):
    defaults = {
        "match_id": "match_report",
        "config": SimpleNamespace(match=SimpleNamespace(name="AWD Match"), mode="awd"),
        "status": "finished",
        "players": {1: object(), 2: object()},
        "persisted_submissions": [],
        "created_at": datetime(2026, 1, 1, 12, 0, 0),
        "finished_at": datetime(2026, 1, 1, 12, 20, 0),
        "resources_destroyed": True,
        "events": [],
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_markdown_cell_redacts_and_escapes_table_content():
    assert markdown_cell("name|token=secret-token\nnext") == "name\\|token=******** next"
    assert markdown_cell("") == "-"
    assert markdown_cell(None) == "-"


def test_leaderboard_rows_sort_by_score_then_player_id():
    rows = leaderboard_rows_for_report({
        "2": {"player_id": 2, "name": "B", "total_score": 100},
        "1": {"player_id": 1, "name": "A", "total_score": 100},
        "3": {"player_id": 3, "name": "C", "total_score": 50},
    })

    assert [row["player_id"] for row in rows] == [1, 2, 3]


def test_submission_summary_counts_attempts_successes_and_points():
    summary = submission_summary_for_report([
        {"attacker_id": 1, "success": True, "points": 100},
        {"attacker_id": 1, "success": False, "points": -10},
        {"attacker_id": 2, "success": True, "points": 50},
        {"attacker_id": "bad", "success": True, "points": 999},
    ])

    assert summary["attempts"] == 4
    assert summary["successes"] == 3
    assert summary["by_attacker"] == {
        1: {"attempts": 2, "successes": 1, "points": 90},
        2: {"attempts": 1, "successes": 1, "points": 50},
    }


def test_build_match_report_markdown_redacts_sensitive_replay_data():
    match = _match(
        persisted_submissions=[
            {
                "attacker_id": 1,
                "target_id": 2,
                "flag": "FLAG{super-secret}",
                "success": True,
                "points": 100,
                "token": "secret-token",
            }
        ],
        events=[
            {"type": "HEARTBEAT", "data": {"message": "ignored"}},
            {"type": "SECRET", "audience": "hidden", "data": {"token": "hidden-token"}},
            {"type": "MATCH_FINISHED", "timestamp": "2026-01-01T12:20:00", "data": {"status": "finished"}},
            {"type": "AI_COMMENTARY", "timestamp": "2026-01-01T12:20:01", "data": {"message": "token=commentary-secret"}},
        ],
    )
    leaderboard = {
        1: {"player_id": 1, "name": "Alpha", "total_score": 100, "flags_captured": 1, "flags_lost": 0, "sla_up": True},
        2: {"player_id": 2, "name": "Beta", "total_score": -50, "flags_captured": 0, "flags_lost": 1, "sla_up": False},
    }

    report = build_match_report_markdown(match, leaderboard)

    assert "# Match Report: AWD Match" in report
    assert "| 1 | Alpha | 100 | 1 | 0 | up |" in report
    assert "| 2 | Beta | -50 | 0 | 1 | down |" in report
    assert "- Attempts: 1" in report
    assert "- Successful Captures: 1" in report
    assert "- Resources Destroyed: yes" in report
    assert "FLAG{super-secret}" not in report
    assert "secret-token" not in report
    assert "hidden-token" not in report
    assert "commentary-secret" not in report
