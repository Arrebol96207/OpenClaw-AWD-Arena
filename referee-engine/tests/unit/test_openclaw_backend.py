from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from backends.openclaw_backend import OpenClawBackendAdapter  # noqa: E402


def _build_match_config(agent_image: str = "openclaw/local-agent:ssh"):
    return SimpleNamespace(
        agent_image=agent_image,
        llm=SimpleNamespace(
            apiKey="global-key",
            baseUrl="https://example.test/v1",
            model="global-model",
            proxy="http://host.docker.internal:7897",
        ),
    )


def _build_player_config(
    *,
    api_key=None,
    base_url=None,
    provider=None,
    api=None,
    model=None,
    image=None,
    extra_env=None,
):
    return SimpleNamespace(
        apiKey=api_key,
        baseUrl=base_url,
        provider=provider,
        api=api,
        model=model,
        backend_config=SimpleNamespace(
            image=image,
            extra_env=extra_env or {},
        ),
    )


def test_openclaw_backend_container_spec_uses_local_ssh_default():
    adapter = OpenClawBackendAdapter()
    match_config = _build_match_config()
    player_config = _build_player_config()

    spec = adapter.build_agent_container_spec(match_config, player_config)

    assert spec.image == "openclaw/local-agent:ssh"
    assert spec.environment["OPENAI_API_KEY"] == "global-key"
    assert spec.environment["OPENAI_BASE_URL"] == "https://example.test/v1"
    assert spec.environment["OPENAI_MODEL"] == "global-model"
    assert spec.environment["OPENCLAW_PROVIDER_API"] == "openai-completions"
    assert spec.environment["HTTPS_PROXY"] == "http://host.docker.internal:7897"
    assert spec.environment["HTTP_PROXY"] == "http://host.docker.internal:7897"
    assert spec.environment["TZ"] == "Asia/Shanghai"


def test_openclaw_backend_container_spec_allows_player_overrides():
    adapter = OpenClawBackendAdapter()
    match_config = _build_match_config(agent_image="alpine/openclaw:stable")
    player_config = _build_player_config(
        api_key="player-key",
        base_url="https://player-api.test/v1",
        api="anthropic",
        model="player-model",
        image="custom/openclaw:dev",
        extra_env={"CUSTOM_FLAG": "enabled"},
    )

    spec = adapter.build_agent_container_spec(match_config, player_config)

    assert spec.image == "custom/openclaw:dev"
    assert spec.environment["OPENAI_API_KEY"] == "player-key"
    assert spec.environment["OPENAI_BASE_URL"] == "https://player-api.test/v1"
    assert spec.environment["OPENAI_MODEL"] == "player-model"
    assert spec.environment["OPENCLAW_PROVIDER_API"] == "anthropic"
    assert spec.environment["CUSTOM_FLAG"] == "enabled"


def test_openclaw_backend_container_spec_maps_legacy_alpine_default_to_local_ssh_image():
    adapter = OpenClawBackendAdapter()
    match_config = _build_match_config(agent_image="alpine/openclaw:latest")
    player_config = _build_player_config()

    spec = adapter.build_agent_container_spec(match_config, player_config)

    assert spec.image == "openclaw/local-agent:ssh"


def test_openclaw_backend_create_client_prefers_player_model_and_key():
    adapter = OpenClawBackendAdapter()
    match_config = _build_match_config()
    player_config = _build_player_config(
        api_key="player-key",
        base_url="https://player-api.test/v1",
        provider="Anthropic",
        model="player-model",
    )

    client = adapter.create_client(match_config, player_config)

    assert client.llm_api_key == "player-key"
    assert client.llm_base_url == "https://player-api.test/v1"
    assert client.llm_model == "player-model"
    assert client.provider_api == "anthropic"
    assert client.proxy_url == "http://host.docker.internal:7897"
