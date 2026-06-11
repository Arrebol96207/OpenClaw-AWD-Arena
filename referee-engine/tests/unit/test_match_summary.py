from datetime import datetime
from types import SimpleNamespace

from match_summary import db_match_summary_row, merge_match_summaries


def test_db_match_summary_defaults_cleanup_state_for_legacy_rows():
    finished = db_match_summary_row({
        "match_id": "match_finished",
        "status": "finished",
        "player_count": 2,
        "created_at": "2026-03-27T10:00:00",
        "finished_at": "2026-03-27T10:20:00",
    })
    aborted = db_match_summary_row({
        "match_id": "match_aborted",
        "status": "aborted",
        "player_count": 2,
        "created_at": "2026-03-27T09:00:00",
        "finished_at": "2026-03-27T09:05:00",
    })

    assert finished["name"] == "match_finished"
    assert finished["mode"] == "awd"
    assert finished["resource_destroyed"] is True
    assert finished["can_end"] is False
    assert aborted["resource_destroyed"] is False
    assert aborted["can_end"] is True


def test_db_match_summary_preserves_werewolf_enriched_fields():
    row = db_match_summary_row({
        "match_id": "match_werewolf",
        "name": "Werewolf Final",
        "mode": "werewolf",
        "status": "finished",
        "player_count": 12,
        "duration": 5400,
        "created_at": "2026-03-27T10:00:00",
        "finished_at": "2026-03-27T11:00:00",
        "resource_destroyed": False,
        "werewolf_board": "standard_guard",
        "werewolf_winner": "good",
        "werewolf_final_day": 3,
    })

    assert row["resource_destroyed"] is False
    assert row["can_end"] is True
    assert row["werewolf_board"] == "standard_guard"
    assert row["werewolf_winner"] == "good"
    assert row["werewolf_final_day"] == 3


def test_db_match_summary_exposes_safe_player_code_export_status_without_local_path():
    row = db_match_summary_row(
        {
            "match_id": "match_export_ready",
            "mode": "awd",
            "status": "finished",
            "player_count": 2,
            "created_at": "2026-03-27T10:00:00",
            "finished_at": "2026-03-27T10:20:00",
            "resource_destroyed": True,
            "player_code_export": {
                "status": "ready",
                "result_status": "partial",
                "bundle_available": True,
                "bundle_path": "C:/private/exports/match_export_ready.zip",
                "bundle_filename": "match_export_ready.zip",
                "generated_at": "2026-03-27T10:21:00",
                "complete": False,
                "partial": True,
                "export_profile": "replay",
                "incomplete_player_count": 1,
            },
        },
        player_code_export_exists=lambda match_id: match_id == "match_export_ready",
    )

    assert row["player_code_export_status"] == "partial"
    assert row["player_code_export_available"] is True
    assert row["player_code_export_downloadable"] is True
    assert row["player_code_export_partial"] is True
    assert row["player_code_export_generated_at"] == "2026-03-27T10:21:00"
    assert row["player_code_export_profile"] == "replay"
    assert row["player_code_export_result_status"] == "partial"
    assert row["player_code_export_incomplete_player_count"] == 1
    assert "player_code_export" not in row
    assert "bundle_path" not in row


def test_db_match_summary_marks_failed_player_code_export_not_downloadable():
    row = db_match_summary_row({
        "match_id": "match_export_failed",
        "mode": "awd",
        "status": "finished",
        "player_count": 2,
        "created_at": "2026-03-27T10:00:00",
        "finished_at": "2026-03-27T10:20:00",
        "resource_destroyed": True,
        "player_code_export": {
            "status": "failed",
            "bundle_available": False,
            "error": "agent export crashed",
        },
    })

    assert row["player_code_export_status"] == "failed"
    assert row["player_code_export_available"] is False
    assert row["player_code_export_downloadable"] is False
    assert row["player_code_export_error"] == "agent export crashed"


def test_merge_match_summaries_active_rows_override_database_rows_and_sort_descending():
    db_rows = [
        {
            "match_id": "match_active",
            "name": "Old Name",
            "mode": "awd",
            "status": "finished",
            "player_count": 2,
            "duration": 1200,
            "created_at": "2026-03-27T09:00:00",
            "finished_at": "2026-03-27T09:20:00",
            "resource_destroyed": True,
        },
        {
            "match_id": "match_old",
            "name": "Old Match",
            "mode": "awd",
            "status": "finished",
            "player_count": 2,
            "duration": 1200,
            "created_at": "2026-03-27T08:00:00",
            "finished_at": "2026-03-27T08:20:00",
            "resource_destroyed": True,
        },
    ]
    active_match = SimpleNamespace(
        config=SimpleNamespace(
            mode="awd",
            match=SimpleNamespace(name="Live Match", duration=1800),
        ),
        status="attack",
        players={1: object(), 2: object(), 3: object()},
        created_at=datetime(2026, 3, 27, 10, 0, 0),
        finished_at=None,
        resources_destroyed=False,
        werewolf_state=None,
    )

    rows = merge_match_summaries(db_rows, {"match_active": active_match})

    assert [row["match_id"] for row in rows] == ["match_active", "match_old"]
    assert rows[0]["name"] == "Live Match"
    assert rows[0]["status"] == "attack"
    assert rows[0]["player_count"] == 3
    assert rows[0]["resource_destroyed"] is False
    assert rows[0]["can_end"] is True


def test_merge_match_summaries_adds_active_werewolf_state_fields():
    werewolf_match = SimpleNamespace(
        config=SimpleNamespace(
            mode="werewolf",
            match=SimpleNamespace(name="Wolf Table", duration=5400),
            werewolf=SimpleNamespace(board="white_wolf_king_knight"),
        ),
        status="werewolf_day",
        players={pid: object() for pid in range(1, 13)},
        created_at=datetime(2026, 3, 27, 10, 0, 0),
        finished_at=datetime(2026, 3, 27, 11, 0, 0),
        resources_destroyed=False,
        werewolf_state=SimpleNamespace(
            winner="werewolf",
            day=4,
            finished_reason="wolves_equal_good",
            sheriff_id=7,
        ),
    )

    rows = merge_match_summaries(
        [],
        {"match_wolf": werewolf_match},
        board_label=lambda board: f"label:{board}",
    )

    assert rows == [{
        "match_id": "match_wolf",
        "name": "Wolf Table",
        "mode": "werewolf",
        "status": "werewolf_day",
        "player_count": 12,
        "duration": 5400,
        "created_at": "2026-03-27T10:00:00",
        "finished_at": "2026-03-27T11:00:00",
        "resource_destroyed": False,
        "can_end": True,
        "werewolf_board": "white_wolf_king_knight",
        "werewolf_board_label": "label:white_wolf_king_knight",
        "werewolf_winner": "werewolf",
        "werewolf_final_day": 4,
        "werewolf_finished_reason": "wolves_equal_good",
        "werewolf_final_sheriff_id": 7,
    }]
