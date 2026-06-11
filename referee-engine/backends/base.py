from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Protocol


StreamCallback = Callable[[str], object]


@dataclass
class BackendContainerSpec:
    image: str
    environment: Dict[str, str]
    entrypoint: Optional[Any] = None
    command: Optional[Any] = None
    volumes: Dict[str, Dict[str, str]] = field(default_factory=dict)


@dataclass
class BackendTargetSSHSpec:
    private_key_path: str = "/home/node/.ssh/awd_target_key"
    owner_user: str = "node"
    owner_group: str = "node"
    helper_path: str = "/usr/local/bin/target-ssh"


def normalize_provider_api(value: Optional[str]) -> str:
    raw = (value or "").strip()
    if not raw:
        return "openai-completions"

    normalized = raw.lower().replace("_", "-").replace(" ", "-")
    aliases = {
        "openai": "openai-completions",
        "custom": "openai-completions",
        "openai-compatible": "openai-completions",
        "openai-completion": "openai-completions",
        "openai-completions": "openai-completions",
        "anthropic": "anthropic",
        "claude": "anthropic",
    }
    return aliases.get(normalized, raw)


class AgentBackendAdapter(Protocol):
    backend_type: str

    def build_agent_container_spec(self, match_config: Any, player_config: Any) -> BackendContainerSpec:
        ...

    def create_client(self, match_config: Any, player_config: Any) -> Any:
        ...

    def resolve_target_ssh_spec(self, match_config: Any, player_config: Any) -> BackendTargetSSHSpec:
        ...

    async def initialize_agent(
        self,
        client: Any,
        session: Any,
        system_prompt: str,
        stream_callback: Optional[StreamCallback] = None,
    ) -> Any:
        ...

    async def send_message(
        self,
        client: Any,
        session: Any,
        message: str,
        *,
        timeout: Optional[int] = None,
        stream_callback: Optional[StreamCallback] = None,
        message_kind: str = "message",
        message_mode: str = "normal",
        drain_buffered_after: bool = True,
    ) -> Optional[str]:
        ...

    async def enqueue_buffered_message(
        self,
        client: Any,
        session: Any,
        message: str,
        *,
        message_kind: str,
        timeout: Optional[int] = None,
        stream_callback: Optional[StreamCallback] = None,
        dedupe_key: Optional[str] = None,
        merge_strategy: str = "replace",
        auto_drain: bool = True,
    ) -> str:
        ...

    async def drain_buffered_messages(self, client: Any, session: Any) -> int:
        ...

    def freeze_buffered_messages(self, client: Any, session: Any) -> None:
        ...

    def unfreeze_buffered_messages(self, client: Any, session: Any) -> None:
        ...

    def is_session_busy(self, client: Any, session: Any) -> bool:
        ...

    def has_buffered_message_kind(self, client: Any, session: Any, message_kind: str) -> bool:
        ...

    async def observe_session_activity(self, client: Any, session: Any) -> bool:
        ...

    async def observe_code_activity(self, client: Any, session: Any) -> bool:
        ...

    async def check_session_contains(self, client: Any, session: Any, keyword: str, tail_lines: int = 50) -> bool:
        ...

    async def collect_session_log(self, client: Any, session: Any) -> Optional[str]:
        ...

    async def cleanup(self, match: Any, player_id: int, session: Any, client: Any) -> None:
        ...
