import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database  # noqa: E402


def _load_main_module(module_name: str):
    main_path = ROOT / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, main_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_verify_api_key_requires_configured_secret_by_default(monkeypatch):
    monkeypatch.delenv("REFEREE_API_KEY", raising=False)
    monkeypatch.delenv("REFEREE_ALLOW_INSECURE_NO_AUTH", raising=False)
    module = _load_main_module("test_main_auth_default_required")

    with pytest.raises(HTTPException) as exc_info:
        module.verify_api_key(None)

    assert exc_info.value.status_code == 503
    assert "REFEREE_API_KEY is required" in exc_info.value.detail


def test_verify_api_key_allows_explicit_insecure_dev_mode(monkeypatch):
    monkeypatch.delenv("REFEREE_API_KEY", raising=False)
    monkeypatch.setenv("REFEREE_ALLOW_INSECURE_NO_AUTH", "1")
    module = _load_main_module("test_main_auth_dev_mode")

    assert module.verify_api_key(None) is None


def test_verify_api_key_rejects_wrong_secret_and_accepts_right_secret(monkeypatch):
    monkeypatch.setenv("REFEREE_API_KEY", "correct-key")
    monkeypatch.delenv("REFEREE_ALLOW_INSECURE_NO_AUTH", raising=False)
    module = _load_main_module("test_main_auth_configured_key")

    with pytest.raises(HTTPException) as exc_info:
        module.verify_api_key("wrong-key")

    assert exc_info.value.status_code == 403
    assert module.verify_api_key("correct-key") == "correct-key"


@pytest.mark.asyncio
async def test_auth_status_reports_key_state_without_leaking_secret(monkeypatch):
    monkeypatch.setenv("REFEREE_API_KEY", "correct-key")
    monkeypatch.delenv("REFEREE_ALLOW_INSECURE_NO_AUTH", raising=False)
    module = _load_main_module("test_main_auth_status")

    missing = await module.auth_status(None)
    assert missing == {
        "authenticated": False,
        "status_code": 403,
        "detail": "Invalid API Key",
        "api_key_configured": True,
        "insecure_dev_auth": False,
        "no_auth_local_only": True,
    }
    assert "correct-key" not in json.dumps(missing)

    wrong = await module.auth_status("wrong-key")
    assert wrong["authenticated"] is False
    assert wrong["status_code"] == 403
    assert "correct-key" not in json.dumps(wrong)

    valid = await module.auth_status("correct-key")
    assert valid["authenticated"] is True
    assert valid["status_code"] == 200
    assert valid["detail"] == "ok"
    assert "correct-key" not in json.dumps(valid)


@pytest.mark.asyncio
async def test_auth_status_reports_insecure_dev_auth(monkeypatch):
    monkeypatch.delenv("REFEREE_API_KEY", raising=False)
    monkeypatch.setenv("REFEREE_ALLOW_INSECURE_NO_AUTH", "1")
    module = _load_main_module("test_main_auth_status_dev")

    payload = await module.auth_status(None)

    assert payload["authenticated"] is True
    assert payload["detail"] == "insecure dev auth enabled"
    assert payload["api_key_configured"] is False
    assert payload["insecure_dev_auth"] is True
    assert payload["no_auth_local_only"] is True


def test_insecure_dev_auth_does_not_apply_to_shared_binds_without_key(monkeypatch):
    monkeypatch.delenv("REFEREE_API_KEY", raising=False)
    monkeypatch.setenv("REFEREE_ALLOW_INSECURE_NO_AUTH", "1")
    monkeypatch.setenv("REFEREE_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("FRONTEND_BIND_HOST", "127.0.0.1")
    module = _load_main_module("test_main_auth_shared_bind_no_key")

    with pytest.raises(HTTPException) as exc_info:
        module.verify_api_key(None)

    assert exc_info.value.status_code == 503
    assert "binds beyond localhost" in exc_info.value.detail


def test_websocket_auth_uses_same_api_key_policy(monkeypatch):
    monkeypatch.setenv("REFEREE_API_KEY", "ws-key")
    module = _load_main_module("test_main_websocket_auth_policy")

    assert module._api_key_is_valid("ws-key")[0] is True
    valid, status_code, detail = module._api_key_is_valid("bad-key")
    assert valid is False
    assert status_code == 403
    assert detail == "Invalid API Key"


@pytest.mark.asyncio
async def test_websocket_endpoint_requires_ticket_for_browser_query_auth(monkeypatch):
    monkeypatch.setenv("REFEREE_API_KEY", "ws-key")
    monkeypatch.delenv("REFEREE_ALLOW_WS_API_KEY_QUERY", raising=False)
    module = _load_main_module("test_main_websocket_endpoint_auth")

    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"user-agent": "arena-ui-test"},
    )

    class FakeWebSocket:
        def __init__(self, *, query_key=None, ticket=None, header_key=None, messages=None, host="127.0.0.1", user_agent="arena-ui-test"):
            self.query_params = {}
            if query_key:
                self.query_params["api_key"] = query_key
            if ticket:
                self.query_params["ticket"] = ticket
            self.headers = {"user-agent": user_agent}
            if header_key:
                self.headers["x-api-key"] = header_key
            self.client = SimpleNamespace(host=host)
            self.messages = list(messages or [])
            self.accepted = False
            self.closed = None
            self.sent_json = []

        async def accept(self):
            self.accepted = True

        async def close(self, code=1000, reason=None):
            self.closed = {"code": code, "reason": reason}

        async def receive_text(self):
            if self.messages:
                return self.messages.pop(0)
            raise module.WebSocketDisconnect(code=1000)

        async def send_json(self, payload):
            self.sent_json.append(payload)

    missing_key = FakeWebSocket()
    await module.websocket_endpoint(missing_key)
    assert missing_key.accepted is False
    assert missing_key.closed["code"] == 1008

    legacy_query_key = FakeWebSocket(query_key="ws-key")
    await module.websocket_endpoint(legacy_query_key)
    assert legacy_query_key.accepted is False
    assert legacy_query_key.closed["code"] == 1008
    assert "ws-ticket" in legacy_query_key.closed["reason"]

    ticket_payload = await module.issue_ws_ticket(request)
    with_ticket = FakeWebSocket(
        ticket=ticket_payload["ticket"],
        messages=[json.dumps({"type": "subscribe", "match_id": "match1"})],
    )
    await module.websocket_endpoint(with_ticket)
    assert with_ticket.accepted is True
    assert with_ticket.sent_json == [{"type": "subscribed", "match_id": "match1"}]
    assert with_ticket not in module.referee.ws_connections
    assert with_ticket not in module.referee.ws_subscriptions

    reused_ticket = FakeWebSocket(ticket=ticket_payload["ticket"])
    await module.websocket_endpoint(reused_ticket)
    assert reused_ticket.accepted is False
    assert reused_ticket.closed["code"] == 1008

    with_header = FakeWebSocket(
        header_key="ws-key",
        messages=[json.dumps({"type": "subscribe", "match_id": "match2"})],
    )
    await module.websocket_endpoint(with_header)
    assert with_header.accepted is True
    assert with_header.sent_json == [{"type": "subscribed", "match_id": "match2"}]


def test_websocket_ticket_rejects_expired_or_mismatched_client_metadata(monkeypatch):
    monkeypatch.setenv("REFEREE_API_KEY", "ws-key")
    module = _load_main_module("test_main_websocket_ticket_metadata")
    engine = module.RefereeEngine()

    payload = engine.issue_ws_ticket(client_host="127.0.0.1", user_agent="arena-ui-test")
    assert engine.consume_ws_ticket(
        payload["ticket"],
        client_host="127.0.0.2",
        user_agent="arena-ui-test",
    ) is False

    payload = engine.issue_ws_ticket(client_host="127.0.0.1", user_agent="arena-ui-test")
    assert engine.consume_ws_ticket(
        payload["ticket"],
        client_host="127.0.0.1",
        user_agent="other-browser",
    ) is False

    payload = engine.issue_ws_ticket(client_host="127.0.0.1", user_agent="arena-ui-test")
    engine.ws_ticket_store.tickets[payload["ticket"]].expires_at = 1
    assert engine.consume_ws_ticket(
        payload["ticket"],
        client_host="127.0.0.1",
        user_agent="arena-ui-test",
    ) is False

    payload = engine.issue_ws_ticket(client_host="127.0.0.1", user_agent="arena-ui-test")
    assert engine.consume_ws_ticket(
        payload["ticket"],
        client_host="127.0.0.1",
        user_agent="arena-ui-test",
    ) is True


def test_websocket_ticket_rate_limit_is_per_client_window(monkeypatch):
    monkeypatch.setenv("REFEREE_API_KEY", "ws-key")
    module = _load_main_module("test_main_websocket_ticket_rate_limit")
    engine = module.RefereeEngine()
    now = 1000.0

    for _ in range(module.WS_TICKET_RATE_LIMIT_MAX_REQUESTS):
        allowed, retry_after = engine.check_ws_ticket_rate_limit(client_host="127.0.0.1", now=now)
        assert allowed is True
        assert retry_after == 0

    allowed, retry_after = engine.check_ws_ticket_rate_limit(client_host="127.0.0.1", now=now + 1)
    assert allowed is False
    assert retry_after > 0

    allowed, retry_after = engine.check_ws_ticket_rate_limit(client_host="127.0.0.2", now=now + 1)
    assert allowed is True
    assert retry_after == 0

    allowed, retry_after = engine.check_ws_ticket_rate_limit(
        client_host="127.0.0.1",
        now=now + module.WS_TICKET_RATE_LIMIT_WINDOW_SECONDS + 1,
    )
    assert allowed is True
    assert retry_after == 0


@pytest.mark.asyncio
async def test_websocket_ticket_route_returns_429_when_rate_limited(monkeypatch):
    monkeypatch.setenv("REFEREE_API_KEY", "ws-key")
    module = _load_main_module("test_main_websocket_ticket_route_rate_limit")
    request = SimpleNamespace(
        client=SimpleNamespace(host="127.0.0.1"),
        headers={"user-agent": "arena-ui-test"},
    )

    for _ in range(module.WS_TICKET_RATE_LIMIT_MAX_REQUESTS):
        payload = await module.issue_ws_ticket(request)
        assert payload["ticket"]

    with pytest.raises(HTTPException) as exc_info:
        await module.issue_ws_ticket(request)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == "Too many WebSocket ticket requests"
    assert int(exc_info.value.headers["Retry-After"]) > 0


@pytest.mark.asyncio
async def test_websocket_query_api_key_requires_explicit_compat_flag(monkeypatch):
    monkeypatch.setenv("REFEREE_API_KEY", "ws-key")
    monkeypatch.setenv("REFEREE_ALLOW_WS_API_KEY_QUERY", "1")
    module = _load_main_module("test_main_websocket_legacy_query_auth")

    class FakeWebSocket:
        def __init__(self):
            self.query_params = {"api_key": "ws-key"}
            self.headers = {}
            self.messages = []
            self.accepted = False
            self.closed = None
            self.sent_json = []

        async def accept(self):
            self.accepted = True

        async def close(self, code=1000, reason=None):
            self.closed = {"code": code, "reason": reason}

        async def receive_text(self):
            raise module.WebSocketDisconnect(code=1000)

        async def send_json(self, payload):
            self.sent_json.append(payload)

    websocket = FakeWebSocket()
    await module.websocket_endpoint(websocket)
    assert websocket.accepted is True
    assert websocket.closed is None


def test_template_store_persists_user_templates_without_secrets(monkeypatch, tmp_path):
    template_path = tmp_path / "templates.json"
    monkeypatch.setenv("OPENCLAW_TEMPLATES_PATH", str(template_path))
    module = _load_main_module("test_main_template_store_security")

    template = module.template_store.create(
        module.ConfigTemplate(
            name="Secure Template",
            description="No secrets on disk",
            tags=["security"],
            saveOptions={"includeAPIKeys": True},
            config={
                "llm": {"apiKey": "global-secret", "baseUrl": "https://example.test/v1"},
                "players": [
                    {
                        "id": 1,
                        "name": "P1",
                        "apiKey": "player-secret",
                        "backend_config": {
                            "extra_env": {
                                "CUSTOM_FLAG": "enabled",
                                "SECRET_TOKEN": "token-secret",
                            }
                        },
                    }
                ],
            },
        )
    )

    assert template_path.exists()
    persisted = json.loads(template_path.read_text(encoding="utf-8"))
    assert len(persisted) == 1
    stored_config = persisted[0]["config"]
    assert "apiKey" not in stored_config["llm"]
    assert "apiKey" not in stored_config["players"][0]
    assert "SECRET_TOKEN" not in stored_config["players"][0]["backend_config"]["extra_env"]
    assert stored_config["players"][0]["backend_config"]["extra_env"]["CUSTOM_FLAG"] == "enabled"
    assert template["config"] == stored_config

    updated = module.template_store.update(
        template["id"],
        module.ConfigTemplate(
            name="Updated Template",
            description="Still no secrets",
            tags=["updated"],
            config={
                "llm": {"apiKey": "updated-secret"},
                "players": [{"id": 1, "name": "P1", "token": "player-token"}],
            },
        ),
    )
    persisted = json.loads(template_path.read_text(encoding="utf-8"))
    assert len(persisted) == 1
    assert persisted[0]["name"] == "Updated Template"
    assert "apiKey" not in persisted[0]["config"]["llm"]
    assert "token" not in persisted[0]["config"]["players"][0]
    assert updated["config"] == persisted[0]["config"]

    module.template_store.delete(template["id"])
    assert json.loads(template_path.read_text(encoding="utf-8")) == []


@pytest.mark.asyncio
async def test_match_and_loop_config_persistence_redacts_secrets(monkeypatch, tmp_path):
    db_path = tmp_path / "config-redaction.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    await database.init_db()

    config = {
        "match": {"name": "Sensitive Match", "duration": 1200},
        "llm": {"apiKey": "global-secret", "baseUrl": "https://example.test/v1"},
        "players": [
            {
                "id": 1,
                "name": "P1",
                "apiKey": "player-secret",
                "backend_config": {
                    "extra_env": {
                        "CUSTOM_FLAG": "enabled",
                        "SECRET_TOKEN": "loop-secret",
                        "PASSWORD": "env-password",
                    }
                },
            }
        ],
    }
    created_at = database.datetime(2026, 3, 27, 10, 0, 0)

    await database.save_match("match_sensitive", "initializing", config, created_at)
    await database.save_loop(
        loop_id="loop_sensitive",
        status="running",
        repeat_count=2,
        current_iteration=1,
        config_dict=config,
        created_at=created_at,
        updated_at=created_at,
    )

    loaded_match = (await database.load_all_matches())[0]["config"]
    loaded_loop = await database.get_loop("loop_sensitive")
    serialized = json.dumps({"match": loaded_match, "loop": loaded_loop}, ensure_ascii=False)

    assert "global-secret" not in serialized
    assert "player-secret" not in serialized
    assert "loop-secret" not in serialized
    assert "env-password" not in serialized
    assert loaded_match["llm"]["apiKey"] == "********"
    assert loaded_match["players"][0]["apiKey"] == "********"
    assert loaded_match["players"][0]["backend_config"]["extra_env"]["SECRET_TOKEN"] == "********"
    assert loaded_match["players"][0]["backend_config"]["extra_env"]["CUSTOM_FLAG"] == "enabled"
    assert loaded_loop["config"]["llm"]["apiKey"] == "********"


@pytest.mark.asyncio
async def test_init_db_scrubs_legacy_persisted_config_secrets(monkeypatch, tmp_path):
    db_path = tmp_path / "legacy-config-redaction.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    await database.init_db()

    legacy_config = {
        "match": {"name": "Legacy Sensitive Match"},
        "llm": {"apiKey": "legacy-global-secret"},
        "players": [{"id": 1, "apiKey": "legacy-player-secret"}],
    }
    created_at = database.datetime(2026, 3, 27, 10, 0, 0).isoformat()
    conn = database.sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE matches SET config_json = ? WHERE match_id = ?",
            (json.dumps(legacy_config), "missing"),
        )
        conn.execute(
            "INSERT INTO matches(match_id, status, config_json, created_at) VALUES(?, ?, ?, ?)",
            ("legacy_match", "finished", json.dumps(legacy_config), created_at),
        )
        conn.execute(
            """
            INSERT INTO loops(loop_id, status, repeat_count, current_iteration, config_json, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            """,
            ("legacy_loop", "stopped", 1, 1, json.dumps(legacy_config), created_at, created_at),
        )
        conn.commit()
    finally:
        conn.close()

    await database.init_db()
    loaded_match = (await database.load_all_matches())[0]["config"]
    loaded_loop = await database.get_loop("legacy_loop")
    serialized = json.dumps({"match": loaded_match, "loop": loaded_loop}, ensure_ascii=False)

    assert "legacy-global-secret" not in serialized
    assert "legacy-player-secret" not in serialized
    assert loaded_match["llm"]["apiKey"] == "********"
    assert loaded_loop["config"]["players"][0]["apiKey"] == "********"


@pytest.mark.asyncio
async def test_init_db_scrubs_legacy_submission_flags(monkeypatch, tmp_path):
    db_path = tmp_path / "legacy-submission-redaction.db"
    monkeypatch.setattr(database, "DB_PATH", str(db_path))
    await database.init_db()

    conn = database.sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO submissions(
              match_id, attacker_id, victim_id, declared_target_player_id, flag,
              success, reason, points, timestamp
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "legacy_match",
                1,
                2,
                2,
                "FLAG{legacy-secret}",
                1,
                "success",
                100,
                "2026-03-27T10:00:00",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    await database.init_db()
    loaded = await database.load_submissions("legacy_match")
    serialized = json.dumps(loaded, ensure_ascii=False)

    assert "FLAG{legacy-secret}" not in serialized
    assert loaded[0]["flag"] == "********"


def test_template_store_scrubs_legacy_template_file_on_load(monkeypatch, tmp_path):
    template_path = tmp_path / "templates.json"
    template_path.write_text(
        json.dumps(
            [
                {
                    "id": "tpl-legacy",
                    "name": "Legacy",
                    "description": "Old template with secrets",
                    "tags": [],
                    "isSystem": False,
                    "usageCount": 0,
                    "playerCount": 1,
                    "duration": 20,
                    "createdAt": "2026-01-01T00:00:00",
                    "lastUsedAt": None,
                    "config": {
                        "llm": {"apiKey": "legacy-global-secret"},
                        "players": [{"id": 1, "token": "legacy-player-token"}],
                    },
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_TEMPLATES_PATH", str(template_path))

    module = _load_main_module("test_main_template_store_legacy_scrub")
    template = module.template_store.get("tpl-legacy")
    persisted = json.loads(template_path.read_text(encoding="utf-8"))
    serialized = json.dumps({"api": template, "disk": persisted}, ensure_ascii=False)

    assert "legacy-global-secret" not in serialized
    assert "legacy-player-token" not in serialized
    assert "apiKey" not in template["config"]["llm"]
    assert "token" not in template["config"]["players"][0]


def test_template_store_recovers_empty_template_file(monkeypatch, tmp_path):
    template_path = tmp_path / "templates.json"
    template_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_TEMPLATES_PATH", str(template_path))

    module = _load_main_module("test_main_template_store_empty_file")

    assert module.template_store.list()
    assert json.loads(template_path.read_text(encoding="utf-8")) == []


@pytest.mark.asyncio
async def test_database_retry_recovers_from_transient_locked_error(monkeypatch):
    calls = {"count": 0}
    sleeps = []

    def flaky_write():
        calls["count"] += 1
        if calls["count"] == 1:
            raise database.sqlite3.OperationalError("database is locked")
        return "ok"

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(database.asyncio, "sleep", fake_sleep)

    result = await database._run_db_sync(flaky_write, attempts=2)

    assert result == "ok"
    assert calls["count"] == 2
    assert sleeps == [database.DB_RETRY_BASE_DELAY_SECONDS]


@pytest.mark.asyncio
async def test_database_retry_does_not_retry_non_lock_operational_error(monkeypatch):
    calls = {"count": 0}

    def invalid_sql():
        calls["count"] += 1
        raise database.sqlite3.OperationalError("no such table: missing")

    async def forbidden_sleep(_delay):
        raise AssertionError("non-lock sqlite errors should not sleep")

    monkeypatch.setattr(database.asyncio, "sleep", forbidden_sleep)

    with pytest.raises(database.sqlite3.OperationalError, match="no such table"):
        await database._run_db_sync(invalid_sql, attempts=3)

    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_database_retry_raises_after_exhausting_locked_attempts(monkeypatch):
    calls = {"count": 0}
    sleeps = []

    def locked_write():
        calls["count"] += 1
        raise database.sqlite3.OperationalError("database table is locked")

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(database.asyncio, "sleep", fake_sleep)

    with pytest.raises(database.sqlite3.OperationalError, match="locked"):
        await database._run_db_sync(locked_write, attempts=3)

    assert calls["count"] == 3
    assert sleeps == [
        database.DB_RETRY_BASE_DELAY_SECONDS,
        database.DB_RETRY_BASE_DELAY_SECONDS * 2,
    ]


@pytest.mark.asyncio
async def test_template_import_filters_secrets_before_persisting(monkeypatch, tmp_path):
    template_path = tmp_path / "templates.json"
    monkeypatch.setenv("OPENCLAW_TEMPLATES_PATH", str(template_path))
    module = _load_main_module("test_main_template_import_filters_secrets")

    class Upload:
        async def read(self, size=-1):
            return json.dumps(
                {
                    "name": "Imported",
                    "description": "Imported template",
                    "tags": ["imported"],
                    "config": {
                        "llm": {"apiKey": "global-secret"},
                        "players": [{"id": 1, "name": "P1", "secret": "player-secret"}],
                    },
                }
            ).encode("utf-8")

    result = await module.import_template(Upload())

    persisted = json.loads(template_path.read_text(encoding="utf-8"))
    stored_config = persisted[0]["config"]
    assert result["success"] is True
    assert "apiKey" not in stored_config["llm"]
    assert "secret" not in stored_config["players"][0]


@pytest.mark.asyncio
async def test_template_import_rejects_oversized_payload(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_TEMPLATES_PATH", str(tmp_path / "templates.json"))
    module = _load_main_module("test_main_template_import_limit")

    class OversizedUpload:
        async def read(self, size=-1):
            return b"x" * (module.MAX_TEMPLATE_IMPORT_BYTES + 1)

    with pytest.raises(HTTPException) as exc_info:
        await module.import_template(OversizedUpload())

    assert exc_info.value.status_code == 413


def test_outbound_url_validator_blocks_private_llm_targets_by_default(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    monkeypatch.delenv("REFEREE_ALLOW_PRIVATE_OUTBOUND_URLS", raising=False)
    module = _load_main_module("test_main_outbound_url_private_block")

    blocked_urls = [
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://10.0.0.2/v1",
        "http://169.254.169.254/latest/meta-data",
        "https://user:pass@example.test/v1",
        "file:///tmp/model",
    ]

    for url in blocked_urls:
        with pytest.raises(HTTPException):
            module._validate_outbound_url(url, field_name="baseUrl")


def test_outbound_url_validator_allows_public_llm_targets(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_outbound_url_public_allow")

    assert module._validate_outbound_url(
        "https://api.findmini.top/gpt/",
        field_name="baseUrl",
    ) == "https://api.findmini.top/gpt"


def test_outbound_url_validator_can_allow_private_for_explicit_local_testing(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    monkeypatch.setenv("REFEREE_ALLOW_PRIVATE_OUTBOUND_URLS", "1")
    module = _load_main_module("test_main_outbound_url_private_opt_in")

    assert module._validate_outbound_url(
        "http://127.0.0.1:8000/v1",
        field_name="baseUrl",
    ) == "http://127.0.0.1:8000/v1"


def test_incomplete_frontend_dist_does_not_break_main_import(monkeypatch, tmp_path):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    frontend_dist = tmp_path / "dist"
    frontend_dist.mkdir()
    (frontend_dist / "index.html").write_text("<!doctype html><title>partial dist</title>", encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_FRONTEND_DIST", str(frontend_dist))

    module = _load_main_module("test_main_incomplete_frontend_dist")

    mounted_paths = {getattr(route, "path", None) for route in module.app.routes}
    assert "/assets" not in mounted_paths


@pytest.mark.asyncio
async def test_health_check_distinguishes_loaded_and_active_matches(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_health_match_counts")

    finished = module.MatchState(
        "match_finished",
        module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")]),
    )
    finished.status = "finished"
    attack = module.MatchState(
        "match_attack",
        module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")]),
    )
    attack.status = "attack"
    werewolf_day = module.MatchState(
        "match_werewolf_day",
        module.MatchConfig(
            mode="werewolf",
            players=[module.PlayerConfig(id=i, name=f"P{i}") for i in range(1, 13)],
        ),
    )
    werewolf_day.status = "werewolf_day"
    module.referee.matches = {
        finished.match_id: finished,
        attack.match_id: attack,
        werewolf_day.match_id: werewolf_day,
    }

    payload = await module.health_check()

    assert payload["status"] == "healthy"
    assert payload["loaded_matches"] == 3
    assert payload["active_matches"] == 2
    assert payload["orchestrator_mode"] in {"embedded", "external_container_management"}


@pytest.mark.asyncio
async def test_match_summary_marks_aborted_database_rows_as_cleanup_available(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_match_summary_cleanup_state")

    async def _fake_summary():
        return [
            {
                "match_id": "match_aborted_after_restart",
                "name": "Interrupted Match",
                "mode": "awd",
                "status": "aborted",
                "player_count": 2,
                "duration": 1200,
                "created_at": "2026-03-27T10:00:00",
                "finished_at": "2026-03-27T10:05:00",
            },
            {
                "match_id": "match_finished_pending_cleanup",
                "name": "Finished Match Pending Cleanup",
                "mode": "awd",
                "status": "finished",
                "player_count": 2,
                "duration": 1200,
                "created_at": "2026-03-27T09:00:00",
                "finished_at": "2026-03-27T09:20:00",
                "resource_destroyed": False,
            },
            {
                "match_id": "match_finished_clean",
                "name": "Finished Match",
                "mode": "awd",
                "status": "finished",
                "player_count": 2,
                "duration": 1200,
                "created_at": "2026-03-27T08:30:00",
                "finished_at": "2026-03-27T08:50:00",
                "resource_destroyed": True,
            },
            {
                "match_id": "match_aborted_clean",
                "name": "Clean Interrupted Match",
                "mode": "awd",
                "status": "aborted",
                "player_count": 2,
                "duration": 1200,
                "created_at": "2026-03-27T08:00:00",
                "finished_at": "2026-03-27T08:05:00",
                "resource_destroyed": True,
            },
        ]

    monkeypatch.setattr(module.database, "list_matches_summary", _fake_summary)
    module.referee.matches = {}

    payload = await module.list_matches()
    rows = {row["match_id"]: row for row in payload["matches"]}

    assert rows["match_aborted_after_restart"]["resource_destroyed"] is False
    assert rows["match_aborted_after_restart"]["can_end"] is True
    assert rows["match_finished_pending_cleanup"]["resource_destroyed"] is False
    assert rows["match_finished_pending_cleanup"]["can_end"] is True
    assert rows["match_finished_clean"]["resource_destroyed"] is True
    assert rows["match_finished_clean"]["can_end"] is False
    assert rows["match_aborted_clean"]["resource_destroyed"] is True
    assert rows["match_aborted_clean"]["can_end"] is False
