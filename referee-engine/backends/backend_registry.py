from typing import Dict, Optional

from .base import AgentBackendAdapter
from .hermes_backend import HermesBackendAdapter
from .openclaw_backend import OpenClawBackendAdapter


class BackendResolutionError(ValueError):
    pass


class UnsupportedBackendError(NotImplementedError):
    pass


class BackendRegistry:
    def __init__(self):
        self._backends: Dict[str, AgentBackendAdapter] = {
            "openclaw": OpenClawBackendAdapter(),
            "hermes": HermesBackendAdapter(),
        }

    @staticmethod
    def normalize_backend_type(backend_type: Optional[str]) -> str:
        if backend_type is None:
            return "openclaw"
        normalized = str(backend_type).strip().lower()
        return normalized or "openclaw"

    def get(self, backend_type: Optional[str]) -> AgentBackendAdapter:
        normalized = self.normalize_backend_type(backend_type)
        adapter = self._backends.get(normalized)
        if adapter is None:
            raise BackendResolutionError(f"Unsupported backend_type: {normalized}")
        return adapter


backend_registry = BackendRegistry()
