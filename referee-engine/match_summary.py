"""Helpers for /api/matches summary rows."""

from typing import Any, Callable, Mapping, Optional


WEREWOLF_SUMMARY_KEYS = (
    "werewolf_board",
    "werewolf_board_label",
    "werewolf_winner",
    "werewolf_winner_label",
    "werewolf_finished_reason",
    "werewolf_final_day",
    "werewolf_final_sheriff_id",
)


def _payload_is_partial(payload: Mapping[str, Any]) -> bool:
    return bool(payload.get("partial")) or payload.get("complete") is False


def _player_code_export_summary(
    match_id: str,
    *,
    mode: str,
    status: str,
    resource_destroyed: bool,
    payload: Any,
    bundle_exists: Optional[bool] = None,
) -> dict[str, Any]:
    if mode != "awd":
        return {}

    payload_dict = payload if isinstance(payload, Mapping) else {}
    payload_status = str(payload_dict.get("status") or "").lower()
    payload_bundle_available = payload_dict.get("bundle_available")
    if bundle_exists is None:
        bundle_available = bool(payload_bundle_available)
    else:
        bundle_available = bundle_exists

    if status != "finished":
        export_status = "pending"
        downloadable = False
    elif bundle_available:
        export_status = "partial" if _payload_is_partial(payload_dict) else "ready"
        downloadable = True
    elif payload_status == "failed":
        export_status = "failed"
        downloadable = False
    else:
        export_status = "generatable"
        downloadable = True

    summary: dict[str, Any] = {
        "player_code_export_status": export_status,
        "player_code_export_available": bundle_available,
        "player_code_export_downloadable": downloadable,
        "player_code_export_partial": export_status == "partial",
    }
    if payload_status == "failed" and payload_dict.get("error"):
        summary["player_code_export_error"] = str(payload_dict.get("error"))
    if payload_dict.get("generated_at"):
        summary["player_code_export_generated_at"] = payload_dict.get("generated_at")
    if payload_dict.get("export_profile"):
        summary["player_code_export_profile"] = payload_dict.get("export_profile")
    if payload_dict.get("result_status"):
        summary["player_code_export_result_status"] = payload_dict.get("result_status")
    if payload_dict.get("incomplete_player_count") is not None:
        summary["player_code_export_incomplete_player_count"] = payload_dict.get("incomplete_player_count")

    return summary


def _bundle_exists(
    match_id: str,
    player_code_export_exists: Optional[Callable[[str], bool]],
) -> Optional[bool]:
    if player_code_export_exists is None:
        return None
    try:
        return bool(player_code_export_exists(match_id))
    except Exception:
        return False


def db_match_summary_row(
    row: Mapping[str, Any],
    *,
    player_code_export_exists: Optional[Callable[[str], bool]] = None,
) -> dict[str, Any]:
    status = row["status"]
    resource_destroyed = (
        bool(row.get("resource_destroyed"))
        if "resource_destroyed" in row
        else status == "finished"
    )
    mode = row.get("mode") or "awd"
    entry = {
        "match_id": row["match_id"],
        "name": row.get("name") or row["match_id"],
        "mode": mode,
        "status": status,
        "player_count": row["player_count"],
        "duration": row.get("duration"),
        "created_at": row["created_at"],
        "finished_at": row["finished_at"],
        "resource_destroyed": resource_destroyed,
        "can_end": not resource_destroyed,
    }
    entry.update(_player_code_export_summary(
        row["match_id"],
        mode=mode,
        status=status,
        resource_destroyed=resource_destroyed,
        payload=row.get("player_code_export"),
        bundle_exists=_bundle_exists(row["match_id"], player_code_export_exists),
    ))
    for key in WEREWOLF_SUMMARY_KEYS:
        if key in row:
            entry[key] = row[key]
    return entry


def active_match_summary_row(
    match_id: str,
    match: Any,
    *,
    board_label: Optional[Callable[[str], str]] = None,
    player_code_export_exists: Optional[Callable[[str], bool]] = None,
) -> dict[str, Any]:
    entry = {
        "match_id": match_id,
        "name": match.config.match.name,
        "mode": match.config.mode,
        "status": match.status,
        "player_count": len(match.players),
        "duration": match.config.match.duration,
        "created_at": match.created_at.isoformat(),
        "finished_at": match.finished_at.isoformat() if match.finished_at else None,
        "resource_destroyed": match.resources_destroyed,
        "can_end": not match.resources_destroyed,
    }
    entry.update(_player_code_export_summary(
        match_id,
        mode=match.config.mode,
        status=match.status,
        resource_destroyed=bool(match.resources_destroyed),
        payload=getattr(match, "player_code_export", None),
        bundle_exists=_bundle_exists(match_id, player_code_export_exists),
    ))

    if match.config.mode == "werewolf":
        entry["werewolf_board"] = match.config.werewolf.board
        if board_label is not None:
            entry["werewolf_board_label"] = board_label(match.config.werewolf.board)

    if match.config.mode == "werewolf" and match.werewolf_state is not None:
        entry["werewolf_winner"] = match.werewolf_state.winner
        entry["werewolf_final_day"] = match.werewolf_state.day
        entry["werewolf_finished_reason"] = match.werewolf_state.finished_reason
        entry["werewolf_final_sheriff_id"] = match.werewolf_state.sheriff_id

    return entry


def merge_match_summaries(
    db_rows: list[Mapping[str, Any]],
    active_matches: Mapping[str, Any],
    *,
    board_label: Optional[Callable[[str], str]] = None,
    player_code_export_exists: Optional[Callable[[str], bool]] = None,
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {
        row["match_id"]: db_match_summary_row(row, player_code_export_exists=player_code_export_exists)
        for row in db_rows
    }

    for match_id, match in active_matches.items():
        merged[match_id] = active_match_summary_row(
            match_id,
            match,
            board_label=board_label,
            player_code_export_exists=player_code_export_exists,
        )

    return sorted(merged.values(), key=lambda row: row["created_at"], reverse=True)
