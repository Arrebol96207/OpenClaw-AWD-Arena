"""WebSocket authentication decision helpers."""

from http import HTTPStatus
from typing import Callable, Optional


ApiKeyValidator = Callable[[Optional[str]], tuple[bool, int, str]]
TicketConsumer = Callable[[str, Optional[str], Optional[str]], bool]
QueryKeyPolicy = Callable[[], bool]


def websocket_auth_is_valid(
    *,
    ticket: Optional[str],
    header_key: Optional[str],
    query_key: Optional[str],
    client_host: Optional[str],
    user_agent: Optional[str],
    consume_ticket: TicketConsumer,
    api_key_is_valid: ApiKeyValidator,
    ws_api_key_query_allowed: QueryKeyPolicy,
) -> tuple[bool, int, str]:
    if ticket:
        if consume_ticket(ticket, client_host, user_agent):
            return True, HTTPStatus.OK, "ok"
        return False, HTTPStatus.FORBIDDEN, "Invalid or expired WebSocket ticket"

    if header_key:
        return api_key_is_valid(header_key)

    if query_key:
        if not ws_api_key_query_allowed():
            return (
                False,
                HTTPStatus.FORBIDDEN,
                "WebSocket api_key query auth is disabled; use /api/ws-ticket",
            )
        return api_key_is_valid(query_key)

    return api_key_is_valid(None)
