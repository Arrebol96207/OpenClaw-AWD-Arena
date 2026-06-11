"""Player-facing feedback helpers for flag submission results."""

from typing import Any, Dict


STATUS_QUERY_HINT = (
    "Before choosing your next move, query GET /api/player/status with your existing "
    "X-Player-Token to refresh score gaps, ranking, remaining time, current target context, "
    "and score changes since your last status query."
)

SUMMARY_BY_REASON = {
    "success": (
        "Flag submission succeeded and the score has been recorded. "
        "If you are mainly scoring low-value flags, prioritize the higher-value slots next. "
        "Check SSRF, internal APIs, admin surfaces, credentials, privileged files, and escalation paths."
    ),
    "invalid_flag": "Flag rejected: the submitted value is not a valid live flag.",
    "own_flag": "Flag rejected: you cannot submit your own team's flag.",
    "target_mismatch": "Flag rejected: the declared target player does not match the real owner of this flag.",
    "flag_already_claimed_by_attacker": "Flag rejected: you already scored this exact flag earlier.",
}


def build_submission_feedback(result: Dict[str, Any]) -> Dict[str, Any]:
    reason = str(result.get("reason") or "unknown")
    summary = SUMMARY_BY_REASON.get(reason, f"Flag submission processed with result: {reason}.")
    if result.get("success"):
        summary = f"{summary} You gained {int(result.get('points') or 0)} points."

    return {
        "summary": summary,
        "status_query_recommended": True,
        "status_query_hint": STATUS_QUERY_HINT,
        "player_status_endpoint": "/api/player/status",
        "required_header": "X-Player-Token",
    }
