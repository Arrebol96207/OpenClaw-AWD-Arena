"""Health-check payload helpers."""

import os

from typing import Any, Mapping

from deployment_config import bind_host_is_local


RUNNING_MATCH_STATUSES = {
    "initializing",
    "creating_containers",
    "initializing_agents",
    "defense",
    "attack",
    "creating_werewolf_agents",
    "werewolf_training",
    "werewolf_sheriff",
    "werewolf_night",
    "werewolf_day",
}


def is_running_match_status(status: str) -> bool:
    return status in RUNNING_MATCH_STATUSES


def deployment_exposure_mode() -> str:
    """Summarize the configured host bind exposure without leaking secrets."""
    frontend_host = os.environ.get("FRONTEND_BIND_HOST")
    referee_host = os.environ.get("REFEREE_BIND_HOST")
    if not frontend_host and not referee_host:
        return "unknown"

    frontend_local = bind_host_is_local(frontend_host)
    referee_local = bind_host_is_local(referee_host)
    if frontend_local and referee_local:
        return "local_only"
    if not frontend_local and not referee_local:
        return "shared_network"
    return "mixed"


def build_health_payload(
    matches: Mapping[str, Any],
    *,
    ws_connections: int,
    orchestrator_available: bool,
    auth_mode: str,
    version: str = "2.0.0",
) -> dict[str, Any]:
    loaded_matches = len(matches)
    running_matches = sum(
        1
        for match in matches.values()
        if is_running_match_status(getattr(match, "status", ""))
    )
    return {
        "status": "healthy",
        "version": version,
        "loaded_matches": loaded_matches,
        "active_matches": running_matches,
        "orchestrator_mode": "embedded" if orchestrator_available else "external_container_management",
        "auth_mode": auth_mode,
        "deployment_exposure": deployment_exposure_mode(),
        "ws_connections": ws_connections,
    }
