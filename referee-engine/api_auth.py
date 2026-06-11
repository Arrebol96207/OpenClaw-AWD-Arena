"""API key policy helpers for the referee HTTP and WebSocket surfaces."""

import os
import secrets
from typing import Optional

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from deployment_config import binds_are_local


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
player_token_header = APIKeyHeader(name="X-Player-Token", auto_error=True)


def insecure_no_auth_allowed() -> bool:
    return os.environ.get("REFEREE_ALLOW_INSECURE_NO_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}


def unsafe_shared_no_auth_allowed() -> bool:
    return os.environ.get("REFEREE_ALLOW_SHARED_NO_AUTH", "").strip().lower() in {"1", "true", "yes", "on"}


def no_auth_limited_to_local_binds() -> bool:
    return binds_are_local(
        os.environ.get("REFEREE_BIND_HOST"),
        os.environ.get("FRONTEND_BIND_HOST"),
        blank_is_local=True,
    )


def dev_no_auth_effective() -> bool:
    if not insecure_no_auth_allowed():
        return False
    return no_auth_limited_to_local_binds() or unsafe_shared_no_auth_allowed()


def ws_api_key_query_allowed() -> bool:
    return os.environ.get("REFEREE_ALLOW_WS_API_KEY_QUERY", "").strip().lower() in {"1", "true", "yes", "on"}


def configured_api_key() -> Optional[str]:
    configured = os.environ.get("REFEREE_API_KEY")
    if configured is None:
        return None
    configured = configured.strip()
    return configured or None


def auth_mode_label() -> str:
    if configured_api_key() is not None:
        return "api_key"
    if dev_no_auth_effective():
        return "dev_no_auth"
    return "unconfigured"


def api_key_is_valid(api_key: Optional[str]) -> tuple[bool, int, str]:
    expected = configured_api_key()
    if expected is None:
        if dev_no_auth_effective():
            return True, status.HTTP_200_OK, "insecure dev auth enabled"
        if insecure_no_auth_allowed() and not no_auth_limited_to_local_binds():
            return (
                False,
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "REFEREE_API_KEY is required when frontend or referee binds beyond localhost",
            )
        return (
            False,
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "REFEREE_API_KEY is required unless REFEREE_ALLOW_INSECURE_NO_AUTH=1",
        )
    if not api_key or not secrets.compare_digest(api_key, expected):
        return False, status.HTTP_403_FORBIDDEN, "Invalid API Key"
    return True, status.HTTP_200_OK, "ok"


def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> Optional[str]:
    valid, status_code, detail = api_key_is_valid(api_key)
    if not valid:
        raise HTTPException(
            status_code=status_code,
            detail=detail,
        )
    return api_key


def auth_status_payload(api_key: Optional[str]) -> dict:
    valid, status_code, detail = api_key_is_valid(api_key)
    has_configured_key = configured_api_key() is not None
    return {
        "authenticated": valid,
        "status_code": status_code,
        "detail": detail,
        "api_key_configured": has_configured_key,
        "insecure_dev_auth": not has_configured_key and dev_no_auth_effective(),
        "no_auth_local_only": no_auth_limited_to_local_binds(),
    }
