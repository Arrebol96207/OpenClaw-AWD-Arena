from http import HTTPStatus

from ws_auth import websocket_auth_is_valid


def api_key_validator(expected_key: str):
    def validate(value):
        if value == expected_key:
            return True, HTTPStatus.OK, "ok"
        if value is None:
            return False, HTTPStatus.UNAUTHORIZED, "Missing API Key"
        return False, HTTPStatus.FORBIDDEN, "Invalid API Key"

    return validate


def test_ticket_auth_takes_precedence_over_header_and_query_keys():
    consumed = []

    result = websocket_auth_is_valid(
        ticket="ticket-1",
        header_key="bad-header",
        query_key="bad-query",
        client_host="127.0.0.1",
        user_agent="arena-ui",
        consume_ticket=lambda ticket, host, ua: consumed.append((ticket, host, ua)) or True,
        api_key_is_valid=api_key_validator("good-key"),
        ws_api_key_query_allowed=lambda: False,
    )

    assert result == (True, HTTPStatus.OK, "ok")
    assert consumed == [("ticket-1", "127.0.0.1", "arena-ui")]


def test_invalid_ticket_rejects_without_falling_back_to_header_key():
    result = websocket_auth_is_valid(
        ticket="expired-ticket",
        header_key="good-key",
        query_key=None,
        client_host=None,
        user_agent=None,
        consume_ticket=lambda ticket, host, ua: False,
        api_key_is_valid=api_key_validator("good-key"),
        ws_api_key_query_allowed=lambda: True,
    )

    assert result == (False, HTTPStatus.FORBIDDEN, "Invalid or expired WebSocket ticket")


def test_header_key_is_allowed_when_no_ticket_is_present():
    result = websocket_auth_is_valid(
        ticket=None,
        header_key="good-key",
        query_key="bad-query",
        client_host=None,
        user_agent=None,
        consume_ticket=lambda ticket, host, ua: False,
        api_key_is_valid=api_key_validator("good-key"),
        ws_api_key_query_allowed=lambda: False,
    )

    assert result == (True, HTTPStatus.OK, "ok")


def test_query_key_requires_explicit_legacy_opt_in():
    disabled = websocket_auth_is_valid(
        ticket=None,
        header_key=None,
        query_key="good-key",
        client_host=None,
        user_agent=None,
        consume_ticket=lambda ticket, host, ua: False,
        api_key_is_valid=api_key_validator("good-key"),
        ws_api_key_query_allowed=lambda: False,
    )
    enabled = websocket_auth_is_valid(
        ticket=None,
        header_key=None,
        query_key="good-key",
        client_host=None,
        user_agent=None,
        consume_ticket=lambda ticket, host, ua: False,
        api_key_is_valid=api_key_validator("good-key"),
        ws_api_key_query_allowed=lambda: True,
    )

    assert disabled == (
        False,
        HTTPStatus.FORBIDDEN,
        "WebSocket api_key query auth is disabled; use /api/ws-ticket",
    )
    assert enabled == (True, HTTPStatus.OK, "ok")


def test_missing_credentials_falls_through_to_api_key_policy():
    result = websocket_auth_is_valid(
        ticket=None,
        header_key=None,
        query_key=None,
        client_host=None,
        user_agent=None,
        consume_ticket=lambda ticket, host, ua: False,
        api_key_is_valid=api_key_validator("good-key"),
        ws_api_key_query_allowed=lambda: False,
    )

    assert result == (False, HTTPStatus.UNAUTHORIZED, "Missing API Key")
