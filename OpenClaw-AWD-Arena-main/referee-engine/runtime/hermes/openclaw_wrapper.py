import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


DEFAULT_HERMES_HOME = os.environ.get("HERMES_HOME", "/opt/data")
HERMES_INSTALL_DIR = Path("/opt/hermes")
HERMES_CONFIG_PATH = Path(DEFAULT_HERMES_HOME) / "config.yaml"
HERMES_CLI_CANDIDATES = (
    HERMES_INSTALL_DIR / ".venv/bin/hermes",
    HERMES_INSTALL_DIR / "hermes",
)
PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
)
NO_PROXY_ENV_KEYS = (
    "NO_PROXY",
    "no_proxy",
)
SESSION_ID_PATTERNS = (
    re.compile(r"session_id:\s*([^\s]+)", re.IGNORECASE),
    re.compile(r"Session ended\. ID:\s*([^\s]+)", re.IGNORECASE),
)


def _state_file(agent_name: str) -> Path:
    safe_agent = re.sub(r"[^a-zA-Z0-9_.-]", "_", agent_name or "main")
    return Path(DEFAULT_HERMES_HOME) / f"openclaw_{safe_agent}.session"


def _load_session_id(agent_name: str) -> Optional[str]:
    state_file = _state_file(agent_name)
    if not state_file.exists():
        return None
    content = state_file.read_text(encoding="utf-8").strip()
    return content or None


def _save_session_id(agent_name: str, session_id: str) -> None:
    state_file = _state_file(agent_name)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(session_id, encoding="utf-8")


def _extract_session_id(*chunks: str) -> Optional[str]:
    for chunk in chunks:
        if not chunk:
            continue
        for pattern in SESSION_ID_PATTERNS:
            match = pattern.search(chunk)
            if match:
                return match.group(1).strip()
    return None


def _strip_session_markers(text: str) -> str:
    cleaned_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if any(pattern.search(line) for pattern in SESSION_ID_PATTERNS):
            continue
        cleaned_lines.append(raw_line)
    return "\n".join(cleaned_lines).strip()


def _resolve_hermes_cli() -> str:
    env_override = os.environ.get("HERMES_CLI")
    if env_override:
        return env_override

    hermes_from_path = shutil.which("hermes")
    if hermes_from_path:
        return hermes_from_path

    for candidate in HERMES_CLI_CANDIDATES:
        if candidate.exists():
            return str(candidate)

    return "hermes"


def _sync_custom_provider_config() -> None:
    base_url = os.environ.get("OPENAI_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model_name = (os.environ.get("HERMES_MODEL") or os.environ.get("OPENAI_MODEL") or "").strip()
    if not base_url:
        return

    HERMES_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    HERMES_CONFIG_PATH.write_text(
        json.dumps(
            {
                "model": {
                    "provider": "custom",
                    "base_url": base_url,
                    "api_key": api_key,
                    "default": model_name,
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _prepare_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()

    proxy_value = None
    for key in PROXY_ENV_KEYS:
        if key in env:
            proxy_value = env.get(key, "")
            break

    if proxy_value is not None:
        for key in PROXY_ENV_KEYS:
            env[key] = proxy_value

    no_proxy_value = None
    for key in NO_PROXY_ENV_KEYS:
        if key in env:
            no_proxy_value = env.get(key, "")
            break

    if no_proxy_value is not None:
        for key in NO_PROXY_ENV_KEYS:
            env[key] = no_proxy_value

    return env


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openclaw")
    subparsers = parser.add_subparsers(dest="command")

    agent_parser = subparsers.add_parser("agent")
    agent_parser.add_argument("--agent", default="main")
    agent_parser.add_argument("-m", "--message", required=True)
    agent_parser.add_argument("--json", action="store_true")
    agent_parser.add_argument("--timeout", type=int, default=600)

    return parser


def _handle_agent(args: argparse.Namespace) -> int:
    persisted_session_id = _load_session_id(args.agent)
    _sync_custom_provider_config()
    hermes_cmd = [_resolve_hermes_cli(), "chat", "-q", args.message, "-Q"]

    preferred_model = os.environ.get("HERMES_MODEL") or os.environ.get("OPENAI_MODEL")
    if preferred_model:
        hermes_cmd.extend(["--model", preferred_model])
    if persisted_session_id:
        hermes_cmd.extend(["--resume", persisted_session_id])

    try:
        completed = subprocess.run(
            hermes_cmd,
            capture_output=True,
            text=True,
            timeout=max(args.timeout, 1) + 10,
            env=_prepare_subprocess_env(),
        )
    except subprocess.TimeoutExpired as exc:
        timeout_stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        timeout_stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        timeout_session_id = _extract_session_id(timeout_stdout, timeout_stderr) or persisted_session_id
        if timeout_session_id:
            _save_session_id(args.agent, timeout_session_id)
        timeout_content = _strip_session_markers(timeout_stdout or timeout_stderr)
        payload = {
            "content": timeout_content or f"[HERMES_TIMEOUT] {exc}",
            "meta": {"agentMeta": {"sessionId": timeout_session_id}},
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(payload["content"])
        return 124

    session_id = _extract_session_id(completed.stdout, completed.stderr) or persisted_session_id
    if session_id:
        _save_session_id(args.agent, session_id)

    stdout_text = _strip_session_markers(completed.stdout or "")
    stderr_text = _strip_session_markers(completed.stderr or "")
    content = stdout_text or stderr_text or ""

    payload = {
        "content": content,
        "meta": {"agentMeta": {"sessionId": session_id}},
    }
    if completed.returncode != 0 and not content:
        payload["content"] = f"[HERMES_ERROR] exit={completed.returncode}"

    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(payload["content"])

    return completed.returncode


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    if args.command != "agent":
        parser.print_help(sys.stderr)
        return 2
    return _handle_agent(args)


if __name__ == "__main__":
    raise SystemExit(main())
