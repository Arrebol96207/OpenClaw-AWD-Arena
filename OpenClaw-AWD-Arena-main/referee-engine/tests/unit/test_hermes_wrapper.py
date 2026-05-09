import importlib.util
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_wrapper_module(module_name: str):
    wrapper_path = ROOT / "runtime" / "hermes" / "openclaw_wrapper.py"
    spec = importlib.util.spec_from_file_location(module_name, wrapper_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_resolve_hermes_cli_prefers_env_override(monkeypatch):
    module = _load_wrapper_module("test_hermes_wrapper_env")

    monkeypatch.setenv("HERMES_CLI", "/custom/hermes")
    monkeypatch.setattr(module.shutil, "which", lambda name: "/usr/bin/hermes")

    assert module._resolve_hermes_cli() == "/custom/hermes"


def test_resolve_hermes_cli_falls_back_to_virtualenv_binary(monkeypatch):
    module = _load_wrapper_module("test_hermes_wrapper_venv")

    monkeypatch.delenv("HERMES_CLI", raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module.Path, "exists", lambda self: str(self) == "/opt/hermes/.venv/bin/hermes")

    assert module._resolve_hermes_cli() == "/opt/hermes/.venv/bin/hermes"


def test_resolve_hermes_cli_falls_back_to_command_name_when_unavailable(monkeypatch):
    module = _load_wrapper_module("test_hermes_wrapper_missing")

    monkeypatch.delenv("HERMES_CLI", raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(module.Path, "exists", lambda self: False)

    assert module._resolve_hermes_cli() == "hermes"


def test_sync_custom_provider_config_writes_custom_model_block(monkeypatch, tmp_path):
    module = _load_wrapper_module("test_hermes_wrapper_config")
    config_path = tmp_path / "config.yaml"

    monkeypatch.setattr(module, "HERMES_CONFIG_PATH", config_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("HERMES_MODEL", "kimi-k2.5")

    module._sync_custom_provider_config()

    assert not config_path.exists()


def test_prepare_subprocess_env_normalizes_proxy_variants(monkeypatch):
    module = _load_wrapper_module("test_hermes_wrapper_proxy")

    monkeypatch.setenv("HTTP_PROXY", "")
    monkeypatch.setenv("HTTPS_PROXY", "")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:7897")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:7897")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7897")
    monkeypatch.setenv("all_proxy", "http://127.0.0.1:7897")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")

    env = module._prepare_subprocess_env()

    for key in module.PROXY_ENV_KEYS:
        assert env[key] == ""
    for key in module.NO_PROXY_ENV_KEYS:
        assert env[key] == ""


def test_handle_agent_timeout_preserves_existing_session_id(monkeypatch):
    module = _load_wrapper_module("test_hermes_wrapper_timeout")

    monkeypatch.setattr(module, "_sync_custom_provider_config", lambda: None)
    monkeypatch.setattr(module, "_resolve_hermes_cli", lambda: "/usr/bin/hermes")
    monkeypatch.setattr(module, "_load_session_id", lambda agent_name: "ses-existing")

    saved = {}

    def fake_save_session_id(agent_name, session_id):
        saved[agent_name] = session_id

    monkeypatch.setattr(module, "_save_session_id", fake_save_session_id)

    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=kwargs.get("args", args[0] if args else []), timeout=190)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    args = module._build_parser().parse_args(["agent", "--agent", "main", "-m", "hello", "--json", "--timeout", "180"])

    import io
    stdout = io.StringIO()
    monkeypatch.setattr(sys, "stdout", stdout)

    return_code = module._handle_agent(args)
    payload = json.loads(stdout.getvalue().strip())

    assert return_code == 124
    assert payload["meta"]["agentMeta"]["sessionId"] == "ses-existing"
    assert saved == {"main": "ses-existing"}
