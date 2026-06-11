from typing import Any, Optional

from agent_client import AgentClient

from .base import AgentBackendAdapter, BackendContainerSpec, BackendTargetSSHSpec, StreamCallback, normalize_provider_api


CONTAINER_TIMEZONE = "Asia/Shanghai"
DEFAULT_AGENT_IMAGE = "openclaw/local-agent:ssh"


class OpenClawBackendAdapter(AgentBackendAdapter):
    backend_type = "openclaw"

    @staticmethod
    def _resolve_player_llm(match_config: Any, player_config: Any) -> tuple[str, str, str, str]:
        config = getattr(match_config, "config", match_config)
        llm_config = getattr(config, "llm", None)
        api_key = getattr(player_config, "apiKey", None) or getattr(llm_config, "apiKey", "")
        base_url = getattr(player_config, "baseUrl", None) or getattr(llm_config, "baseUrl", "")
        model = getattr(player_config, "model", None) or getattr(llm_config, "model", "gpt-5.5")
        provider_api = getattr(player_config, "api", None) or getattr(player_config, "provider", None) or getattr(
            llm_config, "provider", "openai-completions"
        )
        return api_key, base_url, model, normalize_provider_api(provider_api)

    def build_agent_container_spec(self, match_config: Any, player_config: Any) -> BackendContainerSpec:
        config = getattr(match_config, "config", match_config)
        backend_config = getattr(player_config, "backend_config", None)
        image_override = getattr(backend_config, "image", None) if backend_config is not None else None
        extra_env = getattr(backend_config, "extra_env", None) if backend_config is not None else None
        llm_api_key, llm_base_url, llm_model, llm_provider_api = self._resolve_player_llm(config, player_config)

        environment = {
            "OPENAI_API_KEY": llm_api_key,
            "OPENAI_BASE_URL": llm_base_url,
            "OPENAI_MODEL": llm_model,
            "OPENCLAW_PROVIDER_API": llm_provider_api,
            "HTTPS_PROXY": config.llm.proxy,
            "HTTP_PROXY": config.llm.proxy,
            "NO_PROXY": "localhost,127.0.0.1,172.16.0.0/12,10.0.0.0/8,host.docker.internal,.local",
            "TZ": CONTAINER_TIMEZONE,
        }
        if isinstance(extra_env, dict):
            environment.update({str(key): str(value) for key, value in extra_env.items()})

        selected_image = image_override or config.agent_image or DEFAULT_AGENT_IMAGE
        if selected_image == "alpine/openclaw:latest":
            selected_image = DEFAULT_AGENT_IMAGE

        return BackendContainerSpec(
            image=selected_image,
            environment=environment,
        )

    def create_client(self, match_config: Any, player_config: Any) -> AgentClient:
        llm_api_key, llm_base_url, llm_model, llm_provider_api = self._resolve_player_llm(match_config, player_config)
        return AgentClient(
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url,
            llm_model=llm_model,
            proxy_url=match_config.llm.proxy,
            provider_api=llm_provider_api,
        )

    def resolve_target_ssh_spec(self, match_config: Any, player_config: Any) -> BackendTargetSSHSpec:
        return BackendTargetSSHSpec()

    async def initialize_agent(
        self,
        client: AgentClient,
        session: Any,
        system_prompt: str,
        stream_callback: Optional[StreamCallback] = None,
    ) -> Any:
        return await client.initialize_agent(session, system_prompt, stream_callback=stream_callback)

    async def send_message(
        self,
        client: AgentClient,
        session: Any,
        message: str,
        *,
        timeout: Optional[int] = None,
        stream_callback: Optional[StreamCallback] = None,
        message_kind: str = "message",
        message_mode: str = "normal",
        drain_buffered_after: bool = True,
    ) -> Optional[str]:
        return await client.send_message(
            session,
            message,
            timeout=timeout,
            stream_callback=stream_callback,
            message_kind=message_kind,
            message_mode=message_mode,
            drain_buffered_after=drain_buffered_after,
        )

    async def enqueue_buffered_message(
        self,
        client: AgentClient,
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
        return await client.enqueue_buffered_message(
            session,
            message,
            message_kind=message_kind,
            timeout=timeout,
            stream_callback=stream_callback,
            dedupe_key=dedupe_key,
            merge_strategy=merge_strategy,
            auto_drain=auto_drain,
        )

    async def drain_buffered_messages(self, client: AgentClient, session: Any) -> int:
        return await client.drain_buffered_messages(session)

    def freeze_buffered_messages(self, client: AgentClient, session: Any) -> None:
        client.freeze_buffered_messages(session)

    def unfreeze_buffered_messages(self, client: AgentClient, session: Any) -> None:
        client.unfreeze_buffered_messages(session)

    def is_session_busy(self, client: AgentClient, session: Any) -> bool:
        return client.is_session_busy(session)

    def has_buffered_message_kind(self, client: AgentClient, session: Any, message_kind: str) -> bool:
        return client.has_buffered_message_kind(session, message_kind)

    async def observe_session_activity(self, client: AgentClient, session: Any) -> bool:
        return await client.observe_session_activity(session)

    async def observe_code_activity(self, client: AgentClient, session: Any) -> bool:
        return await client.observe_code_activity(session)

    async def check_session_contains(self, client: AgentClient, session: Any, keyword: str, tail_lines: int = 50) -> bool:
        return await client.check_session_contains(session, keyword, tail_lines=tail_lines)

    async def collect_session_log(self, client: AgentClient, session: Any) -> Optional[str]:
        return await client.get_session_log(session)

    async def cleanup(self, match: Any, player_id: int, session: Any, client: AgentClient) -> None:
        return None
