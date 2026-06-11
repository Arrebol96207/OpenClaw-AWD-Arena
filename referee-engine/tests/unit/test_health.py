from types import SimpleNamespace

from health import build_health_payload, deployment_exposure_mode, is_running_match_status


def test_is_running_match_status_covers_awd_and_werewolf_runtime_states():
    assert is_running_match_status("attack") is True
    assert is_running_match_status("defense") is True
    assert is_running_match_status("werewolf_day") is True
    assert is_running_match_status("werewolf_night") is True
    assert is_running_match_status("finished") is False
    assert is_running_match_status("aborted") is False


def test_build_health_payload_distinguishes_loaded_and_active_matches(monkeypatch):
    monkeypatch.setenv("FRONTEND_BIND_HOST", "127.0.0.1")
    monkeypatch.setenv("REFEREE_BIND_HOST", "127.0.0.1")
    matches = {
        "finished": SimpleNamespace(status="finished"),
        "attack": SimpleNamespace(status="attack"),
        "werewolf": SimpleNamespace(status="werewolf_day"),
        "unknown": SimpleNamespace(status="restored_history"),
    }

    assert build_health_payload(
        matches,
        ws_connections=3,
        orchestrator_available=True,
        auth_mode="dev_no_auth",
    ) == {
        "status": "healthy",
        "version": "2.0.0",
        "loaded_matches": 4,
        "active_matches": 2,
        "orchestrator_mode": "embedded",
        "auth_mode": "dev_no_auth",
        "deployment_exposure": "local_only",
        "ws_connections": 3,
    }


def test_build_health_payload_reports_external_orchestrator_mode():
    payload = build_health_payload(
        {},
        ws_connections=0,
        orchestrator_available=False,
        auth_mode="api_key",
        version="test-version",
    )

    assert payload["version"] == "test-version"
    assert payload["loaded_matches"] == 0
    assert payload["active_matches"] == 0
    assert payload["orchestrator_mode"] == "external_container_management"
    assert payload["auth_mode"] == "api_key"


def test_deployment_exposure_mode_reports_safe_and_shared_binds(monkeypatch):
    monkeypatch.setenv("FRONTEND_BIND_HOST", "localhost")
    monkeypatch.setenv("REFEREE_BIND_HOST", "::1")
    assert deployment_exposure_mode() == "local_only"

    monkeypatch.setenv("FRONTEND_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("REFEREE_BIND_HOST", "0.0.0.0")
    assert deployment_exposure_mode() == "shared_network"

    monkeypatch.setenv("FRONTEND_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("REFEREE_BIND_HOST", "127.0.0.1")
    assert deployment_exposure_mode() == "mixed"

    monkeypatch.delenv("FRONTEND_BIND_HOST", raising=False)
    monkeypatch.delenv("REFEREE_BIND_HOST", raising=False)
    assert deployment_exposure_mode() == "unknown"
