"""Markdown match report rendering helpers."""

import json
from datetime import datetime
from typing import Any, Dict, List, Protocol

from public_payload import sanitize_public_payload, sanitize_public_text, visible_match_events


class ReportMatchLike(Protocol):
    match_id: str
    config: Any
    status: str
    players: Dict[int, Any]
    persisted_submissions: List[Dict[str, Any]]
    created_at: Any
    finished_at: Any
    resources_destroyed: bool


def markdown_cell(value: Any) -> str:
    text = sanitize_public_text(str(value if value is not None else "-"))
    return text.replace("|", "\\|").replace("\n", " ").strip() or "-"


def format_report_time(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")
    if isinstance(value, str) and value:
        return value
    return "-"


def leaderboard_rows_for_report(leaderboard: Dict[Any, Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows = [row for row in leaderboard.values() if isinstance(row, dict)]
    return sorted(
        rows,
        key=lambda row: (
            -(int(row.get("total_score") or row.get("score") or 0)),
            int(row.get("player_id") or 0),
        ),
    )


def submission_summary_for_report(submissions: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_attacker: Dict[int, Dict[str, int]] = {}
    for item in submissions:
        try:
            attacker_id = int(item.get("attacker_id") or 0)
        except (TypeError, ValueError):
            continue
        if attacker_id <= 0:
            continue
        row = by_attacker.setdefault(attacker_id, {"attempts": 0, "successes": 0, "points": 0})
        row["attempts"] += 1
        if item.get("success"):
            row["successes"] += 1
        row["points"] += int(item.get("points") or 0)
    return {
        "attempts": len(submissions),
        "successes": sum(1 for item in submissions if item.get("success")),
        "by_attacker": by_attacker,
    }


def build_match_report_markdown(match: ReportMatchLike, leaderboard: Dict[Any, Dict[str, Any]]) -> str:
    visible_events = visible_match_events(match)
    submissions = sanitize_public_payload(list(match.persisted_submissions))
    submission_summary = submission_summary_for_report(submissions)
    lines = [
        f"# Match Report: {markdown_cell(match.config.match.name or match.match_id)}",
        "",
        "## Overview",
        "",
        f"- Match ID: `{markdown_cell(match.match_id)}`",
        f"- Mode: {markdown_cell(match.config.mode)}",
        f"- Status: {markdown_cell(match.status)}",
        f"- Players: {len(match.players)}",
        f"- Created At: {format_report_time(match.created_at)}",
        f"- Finished At: {format_report_time(match.finished_at)}",
        f"- Resources Destroyed: {'yes' if match.resources_destroyed else 'no'}",
        "",
        "## Leaderboard",
        "",
        "| Rank | Player | Score | Captured | Lost | SLA |",
        "| ---: | --- | ---: | ---: | ---: | --- |",
    ]

    rows = leaderboard_rows_for_report(leaderboard)
    for rank, row in enumerate(rows, start=1):
        player_id = row.get("player_id") or rank
        name = row.get("name") or row.get("player_name") or f"P{player_id}"
        score = row.get("total_score", row.get("score", 0))
        captured = row.get("flags_captured", 0)
        lost = row.get("flags_lost", 0)
        sla = "up" if row.get("sla_up", True) else "down"
        lines.append(
            f"| {rank} | {markdown_cell(name)} | {markdown_cell(score)} | {markdown_cell(captured)} | {markdown_cell(lost)} | {markdown_cell(sla)} |"
        )
    if not rows:
        lines.append("| - | - | - | - | - | - |")

    lines.extend([
        "",
        "## Submission Summary",
        "",
        f"- Attempts: {submission_summary['attempts']}",
        f"- Successful Captures: {submission_summary['successes']}",
        "",
        "| Player | Attempts | Successes | Points |",
        "| --- | ---: | ---: | ---: |",
    ])

    by_attacker = submission_summary["by_attacker"]
    for attacker_id in sorted(by_attacker):
        row = by_attacker[attacker_id]
        lines.append(f"| P{attacker_id} | {row['attempts']} | {row['successes']} | {row['points']} |")
    if not by_attacker:
        lines.append("| - | - | - | - |")

    key_events = [event for event in visible_events if event.get("type") not in {"HEARTBEAT"}][-20:]
    lines.extend([
        "",
        "## Key Events",
        "",
        "| Time | Type | Details |",
        "| --- | --- | --- |",
    ])
    for event in key_events:
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        detail = data.get("reason") or data.get("status") or data.get("message") or data
        if isinstance(detail, (dict, list)):
            detail = json.dumps(detail, ensure_ascii=False, default=str)
        lines.append(
            f"| {markdown_cell(event.get('timestamp'))} | {markdown_cell(event.get('type'))} | {markdown_cell(detail)} |"
        )
    if not key_events:
        lines.append("| - | - | - |")

    lines.extend([
        "",
        "> Generated by OpenClaw AWD Arena. Sensitive values are redacted from public report data.",
        "",
    ])
    return "\n".join(lines)
