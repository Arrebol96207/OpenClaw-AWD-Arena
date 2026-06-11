import asyncio
import importlib.util
import shlex
import subprocess
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PHASE8_TARGET_TEST_IMAGE = "openclaw/ctf-target:phase8-live-tests"
PHASE8_AGENT_TEST_IMAGE = "openclaw/local-agent:ssh"
_PHASE8_TARGET_IMAGE_READY = False


def _load_main_module(module_name: str):
    main_path = ROOT / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, main_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _require_live_phase8_docker() -> None:
    try:
        import docker
    except Exception as exc:
        pytest.skip(f"docker SDK is unavailable for live Phase 8 integration tests: {exc}")

    if getattr(docker, "__openclaw_test_stub__", False) or not callable(getattr(docker, "from_env", None)):
        pytest.skip("docker SDK is incomplete for live Phase 8 integration tests")

    commands = [
        ["docker", "info"],
        ["docker", "image", "inspect", PHASE8_AGENT_TEST_IMAGE],
    ]
    for command in commands:
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
        except FileNotFoundError:
            pytest.skip("docker CLI is unavailable for live Phase 8 integration tests")
        except subprocess.CalledProcessError as exc:
            pytest.skip(
                f"live Phase 8 integration prerequisite failed: {' '.join(command)} -> {exc.stderr.strip() or exc.stdout.strip() or exc}"
            )


async def _async_noop(*args, **kwargs):
    return None


def _ensure_live_phase8_target_image() -> None:
    global _PHASE8_TARGET_IMAGE_READY

    if _PHASE8_TARGET_IMAGE_READY:
        return

    target_context = ROOT.parent / "target-image" / "ctf"
    try:
        subprocess.run(
            ["docker", "build", "-t", PHASE8_TARGET_TEST_IMAGE, "."],
            cwd=target_context,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "failed to build live Phase 8 target image: "
            f"{exc.stderr.strip() or exc.stdout.strip() or exc}"
        ) from exc

    _PHASE8_TARGET_IMAGE_READY = True


def _build_live_config(module):
    return module.MatchConfig(
        match=module.MatchDetails(
            name="Phase 8 Live SSH Integration",
            duration=900,
            phases=module.MatchPhaseConfig(defense=300, attack=600),
        ),
        llm=module.LLMConfig(
            provider="openai-completions",
            baseUrl="https://example.test/v1",
            apiKey="",
            model="test-model",
            proxy="",
        ),
        players=[module.PlayerConfig(id=1, name="P1")],
        target_image=PHASE8_TARGET_TEST_IMAGE,
        agent_image=PHASE8_AGENT_TEST_IMAGE,
    )


@asynccontextmanager
async def _live_phase8_match(module_name: str):
    _require_live_phase8_docker()
    _ensure_live_phase8_target_image()
    module = _load_main_module(module_name)
    setattr(module.database, "save_event", _async_noop)

    engine = module.RefereeEngine()
    match = module.MatchState(
        f"phase8_live_{uuid.uuid4().hex[:10]}",
        _build_live_config(module),
    )
    engine.matches[match.match_id] = match

    try:
        await engine._setup_containers(match)
        yield module, engine, match
    finally:
        await engine.destroy_match(match.match_id)


def _phase8_player(match):
    player = match.players[1]
    ssh_key_material = match.player_ssh_key_materials[1]
    return player, ssh_key_material


def _maintenance_authorized_keys_command(command: str) -> str:
    return (
        'MAINTENANCE_HOME="$(awk -F: -v user=defender \'$1 == user {print $6}\' /etc/passwd)" && '
        f'{command} "$MAINTENANCE_HOME/.ssh/authorized_keys"'
    )


async def _exec_target_ssh(engine, player, helper_path: str, remote_command: str) -> str:
    return await engine._docker_exec(
        player.container_name,
        ["sh", "-lc", f"{helper_path} {shlex.quote(remote_command)}"],
        timeout=20,
    )


async def _write_agent_private_key(engine, player, ssh_key_material, private_key: str) -> None:
    ssh_dir = Path(ssh_key_material.private_key_path).parent.as_posix()
    await engine._docker_exec(
        player.container_name,
        [
            "sh",
            "-lc",
            (
                f"mkdir -p {ssh_dir} && "
                f"chmod 700 {ssh_dir} && "
                f"cat > {ssh_key_material.private_key_path} && "
                f"chmod 600 {ssh_key_material.private_key_path}"
            ),
        ],
        timeout=20,
        user=ssh_key_material.owner_user or "node",
        stdin_text=private_key,
    )


async def _wait_for_health_200(engine, player, helper_path: str, retries: int = 20, delay_seconds: float = 1.0) -> None:
    last_output = ""
    last_error = None
    for attempt in range(retries):
        try:
            last_output = await _exec_target_ssh(
                engine,
                player,
                helper_path,
                'curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/health',
            )
            if last_output.strip() == "200":
                return
        except Exception as exc:
            last_error = exc
        if attempt < retries - 1:
            await asyncio.sleep(delay_seconds)
    if last_error is not None:
        raise AssertionError(f"target health never reached 200 after restart: {last_error}") from last_error
    raise AssertionError(f"target health never reached 200 after restart, last output={last_output!r}")


@pytest.mark.asyncio
async def test_phase8_live_ssh_key_flow_covers_real_target_maintenance_path():
    async with _live_phase8_match("test_main_phase8_live_ssh_flow") as (module, engine, match):
        player, ssh_key_material = _phase8_player(match)
        helper_path = ssh_key_material.helper_path or "/usr/local/bin/target-ssh"

        assert player.maintenance_auth_mode == "ssh_key"
        assert player.maintenance_helper_command == "target-ssh"

        authorized_keys = await engine._docker_exec(
            player.target_container,
            ["sh", "-lc", _maintenance_authorized_keys_command("cat")],
            timeout=10,
        )
        assert authorized_keys.strip() == ssh_key_material.public_key.strip()

        private_key = await engine._docker_exec(
            player.container_name,
            ["sh", "-lc", f"cat {ssh_key_material.private_key_path}"],
            timeout=10,
        )
        assert private_key == ssh_key_material.private_key

        helper_probe = await engine._docker_exec(
            player.container_name,
            ["sh", "-lc", f"test -x {helper_path} && echo helper-ready"],
            timeout=10,
        )
        assert helper_probe.strip() == "helper-ready"

        await engine._verify_agent_target_ssh(player.player_id, player.container_name, helper_path, retries=3, delay_seconds=1)

        file_read = await _exec_target_ssh(
            engine,
            player,
            helper_path,
            "cat /app/app.py >/dev/null && echo file-readable",
        )
        assert file_read.strip() == "file-readable"

        restart_output = await _exec_target_ssh(engine, player, helper_path, "supervisorctl restart web")
        assert "web: started" in restart_output

        await _wait_for_health_200(engine, player, helper_path)


@pytest.mark.asyncio
async def test_phase8_live_probe_fails_with_wrong_private_key():
    async with _live_phase8_match("test_main_phase8_live_wrong_private_key") as (module, engine, match):
        player, ssh_key_material = _phase8_player(match)
        wrong_key_material = await engine._generate_player_ssh_keypair(match.match_id, 99)
        await _write_agent_private_key(engine, player, ssh_key_material, wrong_key_material.private_key)

        with pytest.raises(module.TargetSSHProbeError) as exc_info:
            await engine._verify_agent_target_ssh(
                player.player_id,
                player.container_name,
                ssh_key_material.helper_path or "/usr/local/bin/target-ssh",
                retries=1,
            )

        assert exc_info.value.reason == "TARGET_SSH_AUTHORIZED_KEYS_MISSING"
        assert "Permission denied" in exc_info.value.details


@pytest.mark.asyncio
async def test_phase8_live_probe_fails_when_authorized_keys_is_missing():
    async with _live_phase8_match("test_main_phase8_live_missing_authorized_keys") as (module, engine, match):
        player, ssh_key_material = _phase8_player(match)
        await engine._docker_exec(
            player.target_container,
            ["sh", "-lc", _maintenance_authorized_keys_command("rm -f")],
            timeout=10,
        )

        with pytest.raises(module.TargetSSHProbeError) as exc_info:
            await engine._verify_agent_target_ssh(
                player.player_id,
                player.container_name,
                ssh_key_material.helper_path or "/usr/local/bin/target-ssh",
                retries=1,
            )

        assert exc_info.value.reason == "TARGET_SSH_AUTHORIZED_KEYS_MISSING"
        assert "Permission denied" in exc_info.value.details


@pytest.mark.asyncio
async def test_phase8_live_probe_fails_when_sshd_is_stopped():
    async with _live_phase8_match("test_main_phase8_live_sshd_stopped") as (module, engine, match):
        player, ssh_key_material = _phase8_player(match)
        stop_output = await engine._docker_exec(
            player.target_container,
            ["sh", "-lc", "supervisorctl stop sshd"],
            timeout=15,
        )
        assert "sshd: stopped" in stop_output

        with pytest.raises(module.TargetSSHProbeError) as exc_info:
            await engine._verify_agent_target_ssh(
                player.player_id,
                player.container_name,
                ssh_key_material.helper_path or "/usr/local/bin/target-ssh",
                retries=1,
            )

        assert exc_info.value.reason == "TARGET_SSHD_NOT_READY"
        assert "Connection refused" in exc_info.value.details or "Connection reset by peer" in exc_info.value.details
