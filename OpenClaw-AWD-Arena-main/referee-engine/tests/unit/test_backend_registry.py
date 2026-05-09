from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from backends.backend_registry import BackendRegistry, BackendResolutionError  # noqa: E402


def test_backend_registry_defaults_missing_backend_to_openclaw():
    registry = BackendRegistry()

    assert registry.normalize_backend_type(None) == "openclaw"
    assert registry.normalize_backend_type("") == "openclaw"
    assert registry.get(None).backend_type == "openclaw"
    assert registry.get("  OPENCLAW  ").backend_type == "openclaw"


def test_backend_registry_resolves_hermes_backend():
    registry = BackendRegistry()

    assert registry.get("hermes").backend_type == "hermes"


def test_backend_registry_rejects_unknown_backend():
    registry = BackendRegistry()

    with pytest.raises(BackendResolutionError, match="Unsupported backend_type: mystery"):
        registry.get("mystery")
