import secrets
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


DEFAULT_WS_TICKET_TTL_SECONDS = 60
DEFAULT_WS_TICKET_RATE_LIMIT_WINDOW_SECONDS = 30
DEFAULT_WS_TICKET_RATE_LIMIT_MAX_REQUESTS = 20


@dataclass
class WebSocketTicket:
    expires_at: float
    client_host: Optional[str] = None
    user_agent: Optional[str] = None


class WebSocketTicketStore:
    def __init__(
        self,
        *,
        ttl_seconds: int = DEFAULT_WS_TICKET_TTL_SECONDS,
        rate_limit_window_seconds: int = DEFAULT_WS_TICKET_RATE_LIMIT_WINDOW_SECONDS,
        rate_limit_max_requests: int = DEFAULT_WS_TICKET_RATE_LIMIT_MAX_REQUESTS,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.rate_limit_window_seconds = rate_limit_window_seconds
        self.rate_limit_max_requests = rate_limit_max_requests
        self.tickets: Dict[str, WebSocketTicket] = {}
        self.issue_log: Dict[str, List[float]] = {}

    def check_rate_limit(self, *, client_host: Optional[str] = None, now: Optional[float] = None) -> tuple[bool, int]:
        current = time.time() if now is None else now
        window_start = current - self.rate_limit_window_seconds
        key = client_host or "unknown"
        recent = [timestamp for timestamp in self.issue_log.get(key, []) if timestamp > window_start]
        if len(recent) >= self.rate_limit_max_requests:
            retry_after = max(1, int(round(recent[0] + self.rate_limit_window_seconds - current)))
            self.issue_log[key] = recent
            return False, retry_after
        recent.append(current)
        self.issue_log[key] = recent
        return True, 0

    def issue(self, *, client_host: Optional[str] = None, user_agent: Optional[str] = None) -> Dict[str, Any]:
        self.prune()
        ticket = secrets.token_urlsafe(32)
        expires_at = time.time() + self.ttl_seconds
        self.tickets[ticket] = WebSocketTicket(
            expires_at=expires_at,
            client_host=client_host,
            user_agent=user_agent,
        )
        return {
            "ticket": ticket,
            "expires_in": self.ttl_seconds,
            "expires_at": datetime.fromtimestamp(expires_at).isoformat(),
        }

    def prune(self, now: Optional[float] = None) -> None:
        current = time.time() if now is None else now
        expired = [ticket for ticket, record in self.tickets.items() if record.expires_at <= current]
        for ticket in expired:
            self.tickets.pop(ticket, None)

    def consume(
        self,
        ticket: Optional[str],
        *,
        client_host: Optional[str] = None,
        user_agent: Optional[str] = None,
        now: Optional[float] = None,
    ) -> bool:
        if not ticket:
            return False
        current = time.time() if now is None else now
        self.prune(current)
        record = self.tickets.pop(ticket, None)
        if record is None or record.expires_at <= current:
            return False
        if record.client_host and record.client_host != client_host:
            return False
        if record.user_agent and record.user_agent != user_agent:
            return False
        return True
