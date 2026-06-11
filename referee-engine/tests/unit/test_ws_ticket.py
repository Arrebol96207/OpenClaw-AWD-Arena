from ws_ticket import WebSocketTicketStore


def test_ticket_store_issues_binds_consumes_and_rejects_reuse():
    store = WebSocketTicketStore(ttl_seconds=60)

    payload = store.issue(client_host="127.0.0.1", user_agent="arena-ui-test")

    assert payload["ticket"]
    assert payload["expires_in"] == 60
    assert store.consume(payload["ticket"], client_host="127.0.0.2", user_agent="arena-ui-test") is False

    payload = store.issue(client_host="127.0.0.1", user_agent="arena-ui-test")
    assert store.consume(payload["ticket"], client_host="127.0.0.1", user_agent="other-browser") is False

    payload = store.issue(client_host="127.0.0.1", user_agent="arena-ui-test")
    assert store.consume(payload["ticket"], client_host="127.0.0.1", user_agent="arena-ui-test") is True
    assert store.consume(payload["ticket"], client_host="127.0.0.1", user_agent="arena-ui-test") is False


def test_ticket_store_rejects_expired_ticket():
    store = WebSocketTicketStore(ttl_seconds=60)

    payload = store.issue(client_host="127.0.0.1", user_agent="arena-ui-test")
    store.tickets[payload["ticket"]].expires_at = 10

    assert store.consume(payload["ticket"], client_host="127.0.0.1", user_agent="arena-ui-test", now=11) is False


def test_ticket_store_rate_limit_is_per_client_window():
    store = WebSocketTicketStore(rate_limit_window_seconds=30, rate_limit_max_requests=2)

    assert store.check_rate_limit(client_host="127.0.0.1", now=100)[0] is True
    assert store.check_rate_limit(client_host="127.0.0.1", now=101)[0] is True

    allowed, retry_after = store.check_rate_limit(client_host="127.0.0.1", now=102)
    assert allowed is False
    assert retry_after > 0

    assert store.check_rate_limit(client_host="127.0.0.2", now=102) == (True, 0)
    assert store.check_rate_limit(client_host="127.0.0.1", now=131) == (True, 0)
