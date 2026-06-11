"""Public response sanitization helpers for referee-facing API and WebSocket data."""

from typing import Any, Dict, List, Protocol

from redaction import DEFAULT_REDACTED_VALUE, is_sensitive_key, redact_text, redact_value


REDACTED_VALUE = DEFAULT_REDACTED_VALUE


class MatchLike(Protocol):
    config: Any
    events: List[Dict[str, Any]]


def is_sensitive_public_key(key: Any) -> bool:
    return is_sensitive_key(key)


def sanitize_public_text(value: str) -> str:
    return redact_text(value, redacted=REDACTED_VALUE)


def sanitize_public_payload(value: Any) -> Any:
    return redact_value(value, redacted=REDACTED_VALUE)


def sanitize_public_event(event: Dict[str, Any]) -> Dict[str, Any]:
    safe_event = dict(event)
    data = safe_event.get("data")
    if isinstance(data, dict):
        safe_event["data"] = sanitize_public_payload(data)
    return safe_event


def sanitize_public_agent_logs(agent_logs: Dict[Any, Any]) -> Dict[int, str]:
    safe_logs: Dict[int, str] = {}
    for pid, content in agent_logs.items():
        try:
            player_id = int(pid)
        except (TypeError, ValueError):
            continue
        safe_logs[player_id] = sanitize_public_text(str(content))
    return safe_logs


def visible_match_events(match: MatchLike) -> List[Dict[str, Any]]:
    events = [event for event in match.events if event.get("audience") != "hidden"]
    if getattr(match.config, "mode", None) != "werewolf":
        return [sanitize_public_event(event) for event in events]

    visible = [
        event for event in events
        if (
            str(event.get("type", "")).startswith("WEREWOLF_")
            or event.get("type") in {"STATUS", "MATCH_FINISHED", "MATCH_ERROR", "AI_COMMENTARY"}
        )
    ]
    return [sanitize_public_event(event) for event in visible]


def visible_recent_match_events(match: MatchLike, limit: int = 10) -> List[Dict[str, Any]]:
    return visible_match_events(match)[-limit:]


def paginated_visible_match_events(match: MatchLike, *, limit: int, offset: int = 0) -> Dict[str, Any]:
    visible_events = visible_match_events(match)
    total = len(visible_events)
    page = visible_events[offset:offset + limit]
    next_offset = offset + len(page) if offset + len(page) < total else None
    return {
        "events": page,
        "total": total,
        "offset": offset,
        "limit": limit,
        "next_offset": next_offset,
    }
