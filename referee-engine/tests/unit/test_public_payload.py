from types import SimpleNamespace

from public_payload import (
    paginated_visible_match_events,
    sanitize_public_agent_logs,
    sanitize_public_event,
    sanitize_public_payload,
    sanitize_public_text,
    visible_match_events,
    visible_recent_match_events,
)


def _match(mode, events):
    return SimpleNamespace(config=SimpleNamespace(mode=mode), events=events)


def test_sanitize_public_payload_redacts_nested_secrets():
    payload = {
        "message": "Authorization: Bearer live-secret-token",
        "config": {
            "apiKey": "sk-secret",
            "players": [{"id": 1, "token": "player-token", "name": "P1"}],
        },
    }

    redacted = sanitize_public_payload(payload)

    assert "live-secret-token" not in str(redacted)
    assert redacted["config"]["apiKey"] == "********"
    assert redacted["config"]["players"][0]["token"] == "********"
    assert redacted["config"]["players"][0]["name"] == "P1"


def test_sanitize_public_event_redacts_data_but_preserves_metadata():
    event = {
        "type": "AGENT_LOG",
        "timestamp": "2026-01-01T00:00:00",
        "data": {"token": "secret-token", "status": "ok"},
    }

    redacted = sanitize_public_event(event)

    assert redacted["type"] == "AGENT_LOG"
    assert redacted["timestamp"] == "2026-01-01T00:00:00"
    assert redacted["data"] == {"token": "********", "status": "ok"}


def test_sanitize_public_agent_logs_redacts_sensitive_text_and_ignores_bad_player_ids():
    logs = {
        1: "Authorization: Bearer stream-secret-token\nFLAG{runtime-secret}",
        "2": "cookie=session-secret; token=log-secret",
        "bad": "token=ignored-secret",
    }

    redacted = sanitize_public_agent_logs(logs)

    assert sorted(redacted) == [1, 2]
    assert "stream-secret-token" not in redacted[1]
    assert "FLAG{runtime-secret}" not in redacted[1]
    assert "session-secret" not in redacted[2]
    assert "log-secret" not in redacted[2]


def test_visible_match_events_hides_hidden_events_for_awd_matches():
    match = _match(
        "awd",
        [
            {"type": "STATUS", "data": {"status": "attack"}},
            {"type": "SECRET", "audience": "hidden", "data": {"token": "secret-token"}},
            {"type": "SUBMISSION", "data": {"flag": "FLAG{secret}"}},
        ],
    )

    events = visible_match_events(match)

    assert [event["type"] for event in events] == ["STATUS", "SUBMISSION"]
    assert "FLAG{secret}" not in str(events)
    assert visible_recent_match_events(match, limit=1)[0]["type"] == "SUBMISSION"


def test_visible_match_events_limits_werewolf_public_surface():
    match = _match(
        "werewolf",
        [
            {"type": "STATUS", "data": {"status": "werewolf_night"}},
            {"type": "AGENT_STREAM", "data": {"content": "private reasoning token=secret"}},
            {"type": "WEREWOLF_NIGHT_RESULT", "data": {"summary": "night passed"}},
            {"type": "AI_COMMENTARY", "data": {"content": "safe"}},
        ],
    )

    events = visible_match_events(match)

    assert [event["type"] for event in events] == ["STATUS", "WEREWOLF_NIGHT_RESULT", "AI_COMMENTARY"]
    assert "private reasoning" not in str(events)
    assert "secret" not in str(events)


def test_paginated_visible_match_events_uses_visible_sanitized_event_stream():
    match = _match(
        "awd",
        [
            {"type": "STATUS", "data": {"seq": 1}},
            {"type": "SECRET", "audience": "hidden", "data": {"seq": 999, "token": "hidden-secret"}},
            {"type": "FLAG_SUBMISSION", "data": {"seq": 2, "flag": "FLAG{secret}"}},
            {"type": "FLAG_CAPTURED", "data": {"seq": 3}},
        ],
    )

    first_page = paginated_visible_match_events(match, limit=2)
    second_page = paginated_visible_match_events(match, limit=2, offset=2)
    empty_page = paginated_visible_match_events(match, limit=2, offset=99)

    assert [event["data"]["seq"] for event in first_page["events"]] == [1, 2]
    assert first_page["total"] == 3
    assert first_page["offset"] == 0
    assert first_page["limit"] == 2
    assert first_page["next_offset"] == 2
    assert "FLAG{secret}" not in str(first_page)
    assert "hidden-secret" not in str(first_page)

    assert [event["data"]["seq"] for event in second_page["events"]] == [3]
    assert second_page["next_offset"] is None
    assert empty_page["events"] == []
    assert empty_page["total"] == 3
    assert empty_page["next_offset"] is None


def test_sanitize_public_text_redacts_inline_tokens():
    assert "secret-token" not in sanitize_public_text("X-Player-Token: secret-token")
