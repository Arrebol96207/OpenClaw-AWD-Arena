import pytest
from fastapi import HTTPException

import api_auth


def test_api_key_policy_requires_configured_key_by_default(monkeypatch):
    monkeypatch.delenv("REFEREE_API_KEY", raising=False)
    monkeypatch.delenv("REFEREE_ALLOW_INSECURE_NO_AUTH", raising=False)

    valid, status_code, detail = api_auth.api_key_is_valid(None)

    assert valid is False
    assert status_code == 503
    assert "REFEREE_API_KEY is required" in detail
    assert api_auth.auth_mode_label() == "unconfigured"


def test_api_key_policy_accepts_only_exact_configured_key(monkeypatch):
    monkeypatch.setenv("REFEREE_API_KEY", "correct-key")
    monkeypatch.delenv("REFEREE_ALLOW_INSECURE_NO_AUTH", raising=False)

    assert api_auth.api_key_is_valid("wrong-key") == (False, 403, "Invalid API Key")
    assert api_auth.api_key_is_valid("correct-key") == (True, 200, "ok")
    assert api_auth.auth_mode_label() == "api_key"


def test_verify_api_key_raises_http_exception_for_invalid_key(monkeypatch):
    monkeypatch.setenv("REFEREE_API_KEY", "correct-key")

    with pytest.raises(HTTPException) as exc_info:
        api_auth.verify_api_key("wrong-key")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Invalid API Key"


def test_auth_status_payload_does_not_include_secret(monkeypatch):
    monkeypatch.setenv("REFEREE_API_KEY", "correct-key")
    monkeypatch.delenv("REFEREE_ALLOW_INSECURE_NO_AUTH", raising=False)

    payload = api_auth.auth_status_payload("wrong-key")

    assert payload == {
        "authenticated": False,
        "status_code": 403,
        "detail": "Invalid API Key",
        "api_key_configured": True,
        "insecure_dev_auth": False,
        "no_auth_local_only": True,
    }
    assert "correct-key" not in repr(payload)


def test_insecure_dev_auth_is_explicit(monkeypatch):
    monkeypatch.delenv("REFEREE_API_KEY", raising=False)
    monkeypatch.setenv("REFEREE_ALLOW_INSECURE_NO_AUTH", "true")

    payload = api_auth.auth_status_payload(None)

    assert payload["authenticated"] is True
    assert payload["status_code"] == 200
    assert payload["detail"] == "insecure dev auth enabled"
    assert payload["api_key_configured"] is False
    assert payload["insecure_dev_auth"] is True
    assert payload["no_auth_local_only"] is True
    assert api_auth.auth_mode_label() == "dev_no_auth"


def test_insecure_dev_auth_is_blocked_for_shared_binds_without_key(monkeypatch):
    monkeypatch.delenv("REFEREE_API_KEY", raising=False)
    monkeypatch.setenv("REFEREE_ALLOW_INSECURE_NO_AUTH", "1")
    monkeypatch.setenv("REFEREE_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("FRONTEND_BIND_HOST", "127.0.0.1")

    valid, status_code, detail = api_auth.api_key_is_valid(None)
    payload = api_auth.auth_status_payload(None)

    assert valid is False
    assert status_code == 503
    assert "binds beyond localhost" in detail
    assert payload["authenticated"] is False
    assert payload["insecure_dev_auth"] is False
    assert payload["no_auth_local_only"] is False
    assert api_auth.auth_mode_label() == "unconfigured"


def test_shared_no_auth_escape_hatch_is_explicit(monkeypatch):
    monkeypatch.delenv("REFEREE_API_KEY", raising=False)
    monkeypatch.setenv("REFEREE_ALLOW_INSECURE_NO_AUTH", "1")
    monkeypatch.setenv("REFEREE_ALLOW_SHARED_NO_AUTH", "1")
    monkeypatch.setenv("REFEREE_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("FRONTEND_BIND_HOST", "0.0.0.0")

    payload = api_auth.auth_status_payload(None)

    assert payload["authenticated"] is True
    assert payload["insecure_dev_auth"] is True
    assert payload["no_auth_local_only"] is False
    assert api_auth.auth_mode_label() == "dev_no_auth"


def test_configured_key_takes_precedence_over_dev_auth_flag(monkeypatch):
    monkeypatch.setenv("REFEREE_API_KEY", "correct-key")
    monkeypatch.setenv("REFEREE_ALLOW_INSECURE_NO_AUTH", "1")

    assert api_auth.api_key_is_valid(None) == (False, 403, "Invalid API Key")
    assert api_auth.api_key_is_valid("wrong-key") == (False, 403, "Invalid API Key")
    assert api_auth.api_key_is_valid("correct-key") == (True, 200, "ok")

    payload = api_auth.auth_status_payload(None)
    assert payload["authenticated"] is False
    assert payload["api_key_configured"] is True
    assert payload["insecure_dev_auth"] is False
    assert api_auth.auth_mode_label() == "api_key"


def test_websocket_query_key_compat_flag_is_explicit(monkeypatch):
    monkeypatch.delenv("REFEREE_ALLOW_WS_API_KEY_QUERY", raising=False)
    assert api_auth.ws_api_key_query_allowed() is False

    monkeypatch.setenv("REFEREE_ALLOW_WS_API_KEY_QUERY", "1")
    assert api_auth.ws_api_key_query_allowed() is True
