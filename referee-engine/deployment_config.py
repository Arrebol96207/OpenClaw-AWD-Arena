"""Deployment-facing configuration helpers."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


DEFAULT_CORS_ORIGINS = (
    "http://localhost:8080",
    "http://127.0.0.1:8080",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
)

LOCAL_BIND_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class FrontendDistPaths:
    dist: Path
    index: Path
    assets: Path

    @property
    def complete(self) -> bool:
        return self.index.exists() and self.assets.is_dir()


def parse_cors_origins(raw: Optional[str], defaults: Iterable[str] = DEFAULT_CORS_ORIGINS) -> list[str]:
    fallback = list(defaults)
    if raw is None or not raw.strip():
        return fallback
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()]
    return origins or fallback


def cors_allow_credentials(origins: Iterable[str]) -> bool:
    return "*" not in set(origins)


def bind_host_is_local(value: Optional[str], *, blank_is_local: bool = False) -> bool:
    normalized = (value or "").strip().lower()
    if not normalized:
        return blank_is_local
    return normalized in LOCAL_BIND_HOSTS


def binds_are_local(*values: Optional[str], blank_is_local: bool = False) -> bool:
    return all(bind_host_is_local(value, blank_is_local=blank_is_local) for value in values)


def resolve_frontend_dist_paths(
    *,
    env_value: Optional[str],
    default_dist: str,
) -> FrontendDistPaths:
    dist = Path(env_value or default_dist)
    return FrontendDistPaths(
        dist=dist,
        index=dist / "index.html",
        assets=dist / "assets",
    )


def frontend_dist_from_env(default_dist: str, env_name: str = "OPENCLAW_FRONTEND_DIST") -> FrontendDistPaths:
    return resolve_frontend_dist_paths(
        env_value=os.environ.get(env_name),
        default_dist=default_dist,
    )


def should_serve_frontend_path(full_path: str, api_prefixes: Iterable[str] = ("api/", "ws")) -> bool:
    normalized = (full_path or "").lstrip("/")
    return not any(normalized.startswith(prefix) for prefix in api_prefixes)
