"""Shared pytest setup for local developer environments."""

from __future__ import annotations

import sys
from types import ModuleType


def _ensure_docker_sdk_import_stub() -> None:
    """Let tests import Docker SDK symbols even when local Python has a broken namespace package."""
    try:
        import docker.errors  # noqa: F401
        import docker.types  # noqa: F401
        return
    except Exception:
        pass

    try:
        import docker as docker_module
    except Exception:
        docker_module = ModuleType("docker")
        sys.modules["docker"] = docker_module

    errors_module = sys.modules.get("docker.errors") or ModuleType("docker.errors")
    types_module = sys.modules.get("docker.types") or ModuleType("docker.types")

    class DockerException(Exception):
        pass

    class APIError(DockerException):
        pass

    class NotFound(APIError):
        pass

    class ImageNotFound(NotFound):
        pass

    class ContainerError(DockerException):
        pass

    class IPAMConfig:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    class IPAMPool:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    def from_env():
        raise RuntimeError("docker SDK is not available in this test environment")

    for name, value in {
        "DockerException": DockerException,
        "APIError": APIError,
        "NotFound": NotFound,
        "ImageNotFound": ImageNotFound,
        "ContainerError": ContainerError,
    }.items():
        if not hasattr(errors_module, name):
            setattr(errors_module, name, value)

    for name, value in {"IPAMConfig": IPAMConfig, "IPAMPool": IPAMPool}.items():
        if not hasattr(types_module, name):
            setattr(types_module, name, value)

    docker_module.errors = errors_module
    docker_module.types = types_module
    docker_module.__openclaw_test_stub__ = True
    if not hasattr(docker_module, "from_env"):
        docker_module.from_env = from_env

    sys.modules["docker"] = docker_module
    sys.modules["docker.errors"] = errors_module
    sys.modules["docker.types"] = types_module


_ensure_docker_sdk_import_stub()
