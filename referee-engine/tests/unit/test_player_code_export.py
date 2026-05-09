import asyncio
import importlib.util
import io
import json
import sys
import tarfile
import types
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flag_manager import PlayerState  # noqa: E402
import player_code_export  # noqa: E402


def _load_main_module(module_name: str):
    main_path = ROOT / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, main_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _make_file_archive(path: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name=path.lstrip("/"))
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _make_named_file_archive(member_name: str, content: bytes) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(name=member_name)
        info.size = len(content)
        tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


def _make_directory_archive(path: str, files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        directory_info = tarfile.TarInfo(name=path.lstrip("/"))
        directory_info.type = tarfile.DIRTYPE
        directory_info.mode = 0o755
        tar.addfile(directory_info)
        for file_path, content in files.items():
            info = tarfile.TarInfo(name=file_path.lstrip("/"))
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class _FakeContainer:
    def __init__(self, diff_entries, files, archive_mode=0o100644, archives=None, archive_modes=None):
        self._diff_entries = diff_entries
        self._files = files
        self._archives = archives or {}
        self._archive_mode = archive_mode
        self._archive_modes = archive_modes or {}

    def diff(self):
        return list(self._diff_entries)

    def get_archive(self, path: str):
        if path in self._archives:
            mode = self._archive_modes.get(path, self._archive_mode)
            return [self._archives[path]], {"mode": mode}
        if path not in self._files:
            raise FileNotFoundError(path)
        content = self._files[path]
        mode = self._archive_modes.get(path, self._archive_mode)
        return [_make_file_archive(path, content)], {"mode": mode}


class _FakeContainers:
    def __init__(self, containers):
        self._containers = containers

    def get(self, name: str):
        return self._containers[name]


class _FakeDockerClient:
    def __init__(self, containers):
        self.containers = _FakeContainers(containers)


def test_export_match_player_code_builds_expected_zip(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "legacy")

    container = _FakeContainer(
        diff_entries=[
            {"Path": "/app/main.py", "Kind": 1},
            {"Path": "/app/settings.yaml", "Kind": 0},
            {"Path": "/app/old.sql", "Kind": 2},
            {"Path": "/tmp/runtime.log", "Kind": 1},
            {"Path": "/app/logo.png", "Kind": 1},
        ],
        files={
            "/app/main.py": b"print('hardened')\n",
            "/app/settings.yaml": b"debug: false\n",
        },
    )
    fake_docker = types.SimpleNamespace(from_env=lambda: _FakeDockerClient({"target_match_export_1": container}))
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    match = types.SimpleNamespace(
        match_id="match_export",
        config=types.SimpleNamespace(players=[types.SimpleNamespace(id=1, name="Alpha")]),
        players={
            1: PlayerState(
                player_id=1,
                container_name="agent_match_export_1",
                target_container="target_match_export_1",
                target_ip="10.0.0.1",
            )
        },
    )

    result = player_code_export.export_match_player_code(match)

    export_path = Path(result.bundle_path)
    assert export_path.exists()
    assert result.complete is True

    with zipfile.ZipFile(export_path) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "player_1/summary.json" in names
        assert "player_1/added/app/main.py" in names
        assert "player_1/changed/app/settings.yaml" in names
        assert "player_1/added/tmp/runtime.log" not in names

        manifest = archive.read("manifest.json").decode("utf-8")
        summary = archive.read("player_1/summary.json").decode("utf-8")
        assert '"complete": true' in manifest
        assert '"deleted": 1' in summary
        assert '"filtered": 2' in summary


def test_copy_file_from_container_accepts_permission_only_mode(tmp_path):
    container = _FakeContainer(
        diff_entries=[],
        files={"/app/app.py": b"print('patched')\n"},
        archive_mode=0o755,
    )

    output_path = tmp_path / "player_1" / "changed" / "app" / "app.py"
    written_bytes = player_code_export.copy_file_from_container(container, "/app/app.py", output_path)

    assert written_bytes == len(b"print('patched')\n")
    assert output_path.read_text(encoding="utf-8") == "print('patched')\n"


def test_report_json_paths_are_exportable_under_app_reports():
    assert player_code_export.is_exportable_code_file("/app/reports/security_review.json", profile="legacy") is True
    assert player_code_export.is_exportable_code_file("/app/reports/remediation_plan.json", profile="legacy") is True
    assert player_code_export.is_exportable_code_file("/app/reports/test_report.json", profile="legacy") is True

    assert player_code_export.is_exportable_code_file("/tmp/test_report.json", profile="legacy") is False
    assert player_code_export.is_exportable_code_file("/var/log/test_report.json", profile="legacy") is False
    assert player_code_export.is_exportable_code_file("/home/node/.openclaw/test_report.json", profile="legacy") is False
    assert player_code_export.is_exportable_code_file("/app/reports/test_report.md", profile="legacy") is False


def test_default_export_profile_is_replay(monkeypatch):
    monkeypatch.delenv("OPENCLAW_PLAYER_EXPORT_PROFILE", raising=False)

    assert player_code_export.get_player_code_export_profile() == "replay"
    assert player_code_export.is_exportable_code_file("/tmp/test_report.json") is True


def test_replay_classification_expands_export_scope(monkeypatch):
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "replay")

    assert player_code_export.is_exportable_code_file("/tmp/test_report.json") is True
    tmp_python = player_code_export.classify_export_artifact("/tmp/app_patched.py", "target")
    assert tmp_python.bucket == "core_code"
    assert tmp_python.requires_redaction is True

    tmp_head_python = player_code_export.classify_export_artifact("/tmp/head_app.py", "target")
    assert tmp_head_python.bucket == "core_code"

    env_file = player_code_export.classify_export_artifact("/workspace/.env", "agent")
    assert env_file.bucket == "core_code"
    assert env_file.requires_redaction is True

    backup_file = player_code_export.classify_export_artifact("/app/app.py.backup", "target")
    assert backup_file.bucket == "supporting_materials"

    admin_notes = player_code_export.classify_export_artifact(
        "/app/static/backup/admin_notes.txt",
        "target",
    )
    assert admin_notes.bucket == "supporting_materials"
    assert admin_notes.requires_redaction is True

    review_candidate = player_code_export.classify_export_artifact("/tmp/head_app", "target")
    assert review_candidate.should_export is True
    assert review_candidate.bucket == "review_candidates"
    assert review_candidate.requires_content_inspection is True

    run_log = player_code_export.classify_export_artifact("/run/sshd.pid", "target")
    assert run_log.should_export is False
    assert run_log.reason == "default_excluded_prefix"

    sensitive = player_code_export.classify_export_artifact("/home/node/.ssh/id_rsa", "agent")
    assert sensitive.should_export is False
    assert sensitive.reason == "sensitive_path"

    sensitive_directory = player_code_export.classify_export_artifact("/home/node/.ssh", "agent")
    assert sensitive_directory.should_export is False
    assert sensitive_directory.reason == player_code_export.FILTER_REASON_SENSITIVE_FILENAME

    sensitive_openclaw_root = player_code_export.classify_export_artifact("/home/node/.openclaw", "agent")
    assert sensitive_openclaw_root.should_export is False
    assert sensitive_openclaw_root.reason == player_code_export.FILTER_REASON_SENSITIVE_FILENAME

    session_dump = player_code_export.classify_export_artifact(
        "/home/node/.openclaw/agents/main/sessions/latest.jsonl",
        "agent",
    )
    assert session_dump.should_export is False
    assert session_dump.reason == "agent_session_dump"

    sensitive_suffix = player_code_export.classify_export_artifact("/workspace/deploy.pem", "agent")
    assert sensitive_suffix.should_export is False
    assert sensitive_suffix.reason == player_code_export.FILTER_REASON_SENSITIVE_SUFFIX

    agent_cache = player_code_export.classify_export_artifact("/home/node/.cache/model.db", "agent")
    assert agent_cache.should_export is False
    assert agent_cache.reason == player_code_export.FILTER_REASON_AGENT_CACHE

    runtime_tmp = player_code_export.classify_export_artifact("/tmp/jiti/tools-bash.cjs", "agent")
    assert runtime_tmp.should_export is False
    assert runtime_tmp.reason == player_code_export.FILTER_REASON_AGENT_RUNTIME_ARTIFACT

    runtime_lock = player_code_export.classify_export_artifact("/tmp/openclaw-1000/gateway.lock", "agent")
    assert runtime_lock.should_export is False
    assert runtime_lock.reason == player_code_export.FILTER_REASON_AGENT_RUNTIME_ARTIFACT

    workspace_template = player_code_export.classify_export_artifact(
        "/home/node/.openclaw/workspace/AGENTS.md",
        "agent",
    )
    assert workspace_template.should_export is False
    assert workspace_template.reason == player_code_export.FILTER_REASON_AGENT_RUNTIME_ARTIFACT

    workspace_custom = player_code_export.classify_export_artifact(
        "/home/node/.openclaw/workspace/custom_probe.py",
        "agent",
    )
    assert workspace_custom.should_export is True
    assert workspace_custom.bucket == "core_code"


def test_export_match_player_code_replay_includes_target_agent_and_logs(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "replay")

    target_container = _FakeContainer(
        diff_entries=[
            {"Path": "/tmp/app_patched.py", "Kind": 1},
            {"Path": "/app/app.py.backup", "Kind": 1},
            {"Path": "/app/static/backup/admin_notes.txt", "Kind": 1},
            {"Path": "/etc/flag3.txt", "Kind": 1},
            {"Path": "/opt/.credentials/flag4.txt", "Kind": 1},
            {"Path": "/tmp/head_app", "Kind": 1},
            {"Path": "/app/Makefile", "Kind": 0},
            {"Path": "/proc/self/stat", "Kind": 1},
        ],
        files={
            "/tmp/app_patched.py": b"print('patched')\n",
            "/app/app.py.backup": b"print('backup')\n",
            "/app/static/backup/admin_notes.txt": b"Admin reminder: the system flag is FLAG{28bc79d648ac15e2c913aa722cb38076}\n",
            "/etc/flag3.txt": b"FLAG{4c699ac68e93d18bc8703ee4275498c9}\n",
            "/opt/.credentials/flag4.txt": b"FLAG{28bc79d648ac15e2c913aa722cb38076}\n",
            "/tmp/head_app": b"#!/bin/sh\necho hardened\n",
            "/app/Makefile": b"build:\n\tpython app.py\n",
        },
    )
    agent_container = _FakeContainer(
        diff_entries=[
            {"Path": "/workspace/attack.py", "Kind": 1},
            {"Path": "/workspace/ssh_run.py", "Kind": 1},
            {"Path": "/workspace/.env", "Kind": 1},
            {"Path": "/workspace/deploy.pem", "Kind": 1},
            {"Path": "/home/node/.openclaw/scan_notes.md", "Kind": 0},
            {"Path": "/home/node/.openclaw/agents/main/sessions/latest.jsonl", "Kind": 1},
            {"Path": "/home/node/.ssh/id_rsa", "Kind": 1},
        ],
        files={
            "/workspace/attack.py": b"print('exploit')\n",
            "/workspace/ssh_run.py": b'PASS = "ctf_target_2026"\n',
            "/workspace/.env": b"OPENAI_API_KEY=sk-secret\npassword=hunter2\n",
            "/workspace/deploy.pem": b"-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n",
            "/home/node/.openclaw/scan_notes.md": b"target list\n",
            "/home/node/.openclaw/agents/main/sessions/latest.jsonl": b'{"event":"secret"}\n',
        },
    )
    fake_docker = types.SimpleNamespace(
        from_env=lambda: _FakeDockerClient(
            {
                "target_match_export_1": target_container,
                "agent_match_export_1": agent_container,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    match = types.SimpleNamespace(
        match_id="match_export",
        config=types.SimpleNamespace(players=[types.SimpleNamespace(id=1, name="Alpha")]),
        players={
            1: PlayerState(
                player_id=1,
                container_name="agent_match_export_1",
                target_container="target_match_export_1",
                target_ip="10.0.0.1",
            )
        },
        agent_logs={
            1: (
                "Authorization: Bearer abc123\n"
                "sk-ant-api0123456789\n"
                "sshpass -p 'ctf_target_2026' ssh root@10.0.0.1\n"
                "Password for root@10.0.0.1: ctf_target_2026\n"
                "FLAG{94758b9800379a3babd50cb0d8dcf4b8}\n"
                "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----\n"
            )
        },
    )

    result = player_code_export.export_match_player_code(match)

    export_path = Path(result.bundle_path)
    assert export_path.exists()
    assert result.complete is True
    assert result.export_profile == "replay"
    assert result.schema_version == 2

    with zipfile.ZipFile(export_path) as archive:
        names = set(archive.namelist())
        assert "manifest.json" in names
        assert "player_1/player_summary.json" in names
        assert "player_1/target/summary.json" in names
        assert "player_1/agent/summary.json" in names
        assert "player_1/logs/agent_session.log" in names
        assert "player_1/target/core_code/added/tmp/app_patched.py" in names
        assert "player_1/target/supporting_materials/added/app/app.py.backup" in names
        assert "player_1/target/supporting_materials/added/app/static/backup/admin_notes.txt" in names
        assert "player_1/target/supporting_materials/added/etc/flag3.txt" in names
        assert "player_1/target/supporting_materials/added/opt/.credentials/flag4.txt" in names
        assert "player_1/target/review_candidates/added/tmp/head_app" in names
        assert "player_1/target/core_code/changed/app/Makefile" in names
        assert "player_1/agent/core_code/added/workspace/attack.py" in names
        assert "player_1/agent/core_code/added/workspace/ssh_run.py" in names
        assert "player_1/agent/core_code/added/workspace/.env" in names
        assert "player_1/agent/supporting_materials/changed/home/node/.openclaw/scan_notes.md" in names
        assert "player_1/agent/core_code/added/home/node/.ssh/id_rsa" not in names
        assert "player_1/agent/core_code/added/workspace/deploy.pem" not in names
        assert "player_1/agent/review_candidates/added/home/node/.openclaw/agents/main/sessions/latest.jsonl" not in names

        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        player_summary = json.loads(archive.read("player_1/player_summary.json").decode("utf-8"))
        target_summary = json.loads(archive.read("player_1/target/summary.json").decode("utf-8"))
        agent_summary = json.loads(archive.read("player_1/agent/summary.json").decode("utf-8"))
        logs_summary = player_summary["logs"]
        target_admin_notes_content = archive.read(
            "player_1/target/supporting_materials/added/app/static/backup/admin_notes.txt"
        ).decode("utf-8")
        target_flag3_content = archive.read("player_1/target/supporting_materials/added/etc/flag3.txt").decode("utf-8")
        target_flag4_content = archive.read(
            "player_1/target/supporting_materials/added/opt/.credentials/flag4.txt"
        ).decode("utf-8")
        env_content = archive.read("player_1/agent/core_code/added/workspace/.env").decode("utf-8")
        ssh_run_content = archive.read("player_1/agent/core_code/added/workspace/ssh_run.py").decode("utf-8")
        log_content = archive.read("player_1/logs/agent_session.log").decode("utf-8")

        assert manifest["schema_version"] == 2
        assert manifest["export_profile"] == "replay"
        assert manifest["filters"]["reason_enums"]["filtered"] == list(player_code_export.REPLAY_FILTER_REASONS)
        assert manifest["filters"]["reason_enums"]["classification"] == list(player_code_export.REPLAY_CLASSIFICATION_REASONS)
        assert target_summary["reason_enums"]["skipped"] == list(player_code_export.REPLAY_SKIP_REASONS)
        assert logs_summary["reason_enums"]["missing"] == list(player_code_export.LOG_MISSING_REASONS)
        assert player_summary["logs"]["available"] is True
        assert target_summary["counts"]["filtered"] == 1
        assert target_summary["counts"]["redacted"] == 3
        assert agent_summary["counts"]["filtered"] == 1
        assert agent_summary["counts"]["skipped_sensitive"] == 2
        assert agent_summary["counts"]["redacted"] == 2
        assert "FLAG{28bc79d648ac15e2c913aa722cb38076}" not in target_admin_notes_content
        assert "FLAG{4c699ac68e93d18bc8703ee4275498c9}" not in target_flag3_content
        assert "FLAG{28bc79d648ac15e2c913aa722cb38076}" not in target_flag4_content
        assert "FLAG{[REDACTED]}" in target_admin_notes_content
        assert "FLAG{[REDACTED]}" in target_flag3_content
        assert "FLAG{[REDACTED]}" in target_flag4_content
        assert "sk-secret" not in env_content
        assert "hunter2" not in env_content
        assert "[REDACTED]" in env_content
        assert "ctf_target_2026" not in ssh_run_content
        assert "[REDACTED]" in ssh_run_content
        assert "abc123" not in log_content
        assert "sk-ant-api0123456789" not in log_content
        assert "ctf_target_2026" not in log_content
        assert "FLAG{94758b9800379a3babd50cb0d8dcf4b8}" not in log_content
        assert "FLAG{[REDACTED]}" in log_content
        assert "BEGIN PRIVATE KEY" not in log_content
        assert "[REDACTED]" in log_content


def test_replay_filters_directory_entries_without_failed_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "replay")

    runner_path = "/tmp/tools/runner.sh"
    runner_bytes = b"#!/bin/sh\necho run\n"
    target_container = _FakeContainer(
        diff_entries=[
            {"Path": "/tmp/tools", "Kind": 1},
            {"Path": runner_path, "Kind": 1},
        ],
        files={runner_path: runner_bytes},
        archives={
            "/tmp/tools": _make_directory_archive(
                "/tmp/tools",
                {runner_path: runner_bytes},
            )
        },
        archive_modes={"/tmp/tools": 0o040755},
    )
    agent_container = _FakeContainer(diff_entries=[], files={})
    fake_docker = types.SimpleNamespace(
        from_env=lambda: _FakeDockerClient(
            {
                "target_match_export_directory": target_container,
                "agent_match_export_directory": agent_container,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    match = types.SimpleNamespace(
        match_id="match_export_directory",
        config=types.SimpleNamespace(players=[types.SimpleNamespace(id=8, name="Player 8")]),
        players={
            8: PlayerState(
                player_id=8,
                container_name="agent_match_export_directory",
                target_container="target_match_export_directory",
                target_ip="10.0.0.8",
            )
        },
        agent_logs={8: "(no session log found)\n"},
    )

    result = player_code_export.export_match_player_code(match)

    with zipfile.ZipFile(Path(result.bundle_path)) as archive:
        names = set(archive.namelist())
        target_summary = json.loads(archive.read("player_8/target/summary.json").decode("utf-8"))

        assert result.complete is True
        assert "player_8/target/core_code/added/tmp/tools/runner.sh" in names
        assert "player_8/target/review_candidates/added/tmp/tools" not in names
        assert target_summary["counts"]["core_code"]["added"] == 1
        assert target_summary["counts"]["filtered"] == 1
        assert target_summary["counts"]["failed"] == 0
        assert target_summary["filtered_paths"] == [
            {"path": "/tmp/tools", "reason": player_code_export.FILTER_REASON_DIRECTORY_PATH}
        ]


def test_replay_filters_sensitive_openclaw_root_file(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "replay")

    target_container = _FakeContainer(diff_entries=[], files={})
    agent_container = _FakeContainer(
        diff_entries=[
            {"Path": "/home/node/.openclaw", "Kind": 1},
            {"Path": "/workspace/attack.py", "Kind": 1},
        ],
        files={
            "/home/node/.openclaw": b'{"apiKey": "sk-secret"}\n',
            "/workspace/attack.py": b"print('attack')\n",
        },
    )
    fake_docker = types.SimpleNamespace(
        from_env=lambda: _FakeDockerClient(
            {
                "target_match_export_sensitive_root": target_container,
                "agent_match_export_sensitive_root": agent_container,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    match = types.SimpleNamespace(
        match_id="match_export_sensitive_root",
        config=types.SimpleNamespace(players=[types.SimpleNamespace(id=9, name="Player 9")]),
        players={
            9: PlayerState(
                player_id=9,
                container_name="agent_match_export_sensitive_root",
                target_container="target_match_export_sensitive_root",
                target_ip="10.0.0.9",
            )
        },
        agent_logs={9: "(no session log found)\n"},
    )

    result = player_code_export.export_match_player_code(match)

    with zipfile.ZipFile(Path(result.bundle_path)) as archive:
        names = set(archive.namelist())
        agent_summary = json.loads(archive.read("player_9/agent/summary.json").decode("utf-8"))

        assert result.complete is True
        assert "player_9/agent/core_code/added/workspace/attack.py" in names
        assert "player_9/agent/review_candidates/added/home/node/.openclaw" not in names
        assert agent_summary["counts"]["filtered"] == 0
        assert agent_summary["counts"]["skipped_sensitive"] == 1
        assert agent_summary["skipped_sensitive_files"] == [
            {
                "path": "/home/node/.openclaw",
                "reason": player_code_export.FILTER_REASON_SENSITIVE_FILENAME,
            }
        ]


def test_replay_filters_agent_runtime_artifacts_but_keeps_custom_workspace_files(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "replay")

    target_container = _FakeContainer(diff_entries=[], files={})
    agent_container = _FakeContainer(
        diff_entries=[
            {"Path": "/workspace/attack.py", "Kind": 1},
            {"Path": "/tmp/jiti/tools-bash.cjs", "Kind": 1},
            {"Path": "/tmp/openclaw/openclaw-2026-04-02.log", "Kind": 1},
            {"Path": "/tmp/openclaw-1000/gateway.lock", "Kind": 1},
            {"Path": "/home/node/.openclaw/workspace/AGENTS.md", "Kind": 1},
            {"Path": "/home/node/.openclaw/workspace/.git/config", "Kind": 1},
            {"Path": "/home/node/.openclaw/workspace/custom_probe.py", "Kind": 1},
            {"Path": "/home/node/.openclaw/openclaw.json", "Kind": 1},
        ],
        files={
            "/workspace/attack.py": b"print('attack')\n",
            "/tmp/jiti/tools-bash.cjs": b"module.exports = {}\n",
            "/tmp/openclaw/openclaw-2026-04-02.log": b"runtime log\n",
            "/tmp/openclaw-1000/gateway.lock": b"lock\n",
            "/home/node/.openclaw/workspace/AGENTS.md": b"system template\n",
            "/home/node/.openclaw/workspace/.git/config": b"[core]\nrepositoryformatversion = 0\n",
            "/home/node/.openclaw/workspace/custom_probe.py": b"print('probe')\n",
            "/home/node/.openclaw/openclaw.json": b'{"apiKey":"sk-secret"}\n',
        },
    )
    fake_docker = types.SimpleNamespace(
        from_env=lambda: _FakeDockerClient(
            {
                "target_match_export_runtime": target_container,
                "agent_match_export_runtime": agent_container,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    match = types.SimpleNamespace(
        match_id="match_export_runtime",
        config=types.SimpleNamespace(players=[types.SimpleNamespace(id=10, name="Player 10")]),
        players={
            10: PlayerState(
                player_id=10,
                container_name="agent_match_export_runtime",
                target_container="target_match_export_runtime",
                target_ip="10.0.0.10",
            )
        },
        agent_logs={10: "(no session log found)\n"},
    )

    result = player_code_export.export_match_player_code(match)

    with zipfile.ZipFile(Path(result.bundle_path)) as archive:
        names = set(archive.namelist())
        agent_summary = json.loads(archive.read("player_10/agent/summary.json").decode("utf-8"))

        assert result.complete is True
        assert "player_10/agent/core_code/added/workspace/attack.py" in names
        assert "player_10/agent/core_code/added/home/node/.openclaw/workspace/custom_probe.py" in names
        assert "player_10/agent/review_candidates/added/tmp/jiti/tools-bash.cjs" not in names
        assert "player_10/agent/review_candidates/added/tmp/openclaw/openclaw-2026-04-02.log" not in names
        assert "player_10/agent/review_candidates/added/tmp/openclaw-1000/gateway.lock" not in names
        assert "player_10/agent/supporting_materials/added/home/node/.openclaw/workspace/AGENTS.md" not in names
        assert "player_10/agent/supporting_materials/added/home/node/.openclaw/workspace/.git/config" not in names
        assert "player_10/agent/core_code/added/home/node/.openclaw/openclaw.json" not in names
        assert agent_summary["counts"]["core_code"]["added"] == 2
        assert agent_summary["counts"]["filtered"] == 6
        assert agent_summary["counts"]["skipped_sensitive"] == 0
        assert agent_summary["counts"]["failed"] == 0
        assert all(
            entry["reason"] == player_code_export.FILTER_REASON_AGENT_RUNTIME_ARTIFACT
            for entry in agent_summary["filtered_paths"]
        )


def test_replay_exports_root_ash_history_from_dotfile_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "replay")

    history_path = "/root/.ash_history"
    history_bytes = (
        b"ssh root@10.0.0.2\n"
        b"curl -X POST http://host.docker.internal:8000/api/submit -d 'flag=FLAG{4c699ac68e93d18bc8703ee4275498c9}'\n"
    )
    target_container = _FakeContainer(
        diff_entries=[{"Path": history_path, "Kind": 1}],
        files={history_path: history_bytes},
        archives={history_path: _make_named_file_archive(".ash_history", history_bytes)},
    )
    agent_container = _FakeContainer(diff_entries=[], files={})
    fake_docker = types.SimpleNamespace(
        from_env=lambda: _FakeDockerClient(
            {
                "target_match_export_ash_history": target_container,
                "agent_match_export_ash_history": agent_container,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    match = types.SimpleNamespace(
        match_id="match_export_ash_history",
        config=types.SimpleNamespace(players=[types.SimpleNamespace(id=11, name="Player 11")]),
        players={
            11: PlayerState(
                player_id=11,
                container_name="agent_match_export_ash_history",
                target_container="target_match_export_ash_history",
                target_ip="10.0.0.11",
            )
        },
        agent_logs={11: "(no session log found)\n"},
    )

    result = player_code_export.export_match_player_code(match)

    with zipfile.ZipFile(Path(result.bundle_path)) as archive:
        names = set(archive.namelist())
        target_summary = json.loads(archive.read("player_11/target/summary.json").decode("utf-8"))
        history_content = archive.read("player_11/target/review_candidates/added/root/.ash_history").decode("utf-8")

        assert result.complete is True
        assert "player_11/target/review_candidates/added/root/.ash_history" in names
        assert target_summary["counts"]["review_candidates"]["added"] == 1
        assert target_summary["counts"]["redacted"] == 1
        assert target_summary["counts"]["failed"] == 0
        assert target_summary["failed_files"] == []
        assert "FLAG{4c699ac68e93d18bc8703ee4275498c9}" not in history_content
        assert "FLAG{[REDACTED]}" in history_content


def test_replay_regression_covers_real_sample_omissions(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "replay")

    target_container = _FakeContainer(
        diff_entries=[
            {"Path": "/tmp/app_patched.py", "Kind": 1},
            {"Path": "/tmp/head_app.py", "Kind": 1},
            {"Path": "/app/app.py.backup", "Kind": 1},
            {"Path": "/app/static/backup/admin_notes.txt", "Kind": 1},
            {"Path": "/etc/flag3.txt", "Kind": 1},
            {"Path": "/opt/.credentials/flag4.txt", "Kind": 1},
            {"Path": "/run/sshd.pid", "Kind": 1},
        ],
        files={
            "/tmp/app_patched.py": b"print('patched from tmp')\n",
            "/tmp/head_app.py": b"print('head patch')\n",
            "/app/app.py.backup": b"print('backup copy')\n",
            "/app/static/backup/admin_notes.txt": b"Admin reminder: the system flag is FLAG{751ba8e128feed02bcd56c6ebd5059b9}\n",
            "/etc/flag3.txt": b"FLAG{3379a8261ee3eda92b27da80dd2d085b}\n",
            "/opt/.credentials/flag4.txt": b"FLAG{2d78c54f924501d2309bdbc40d8922f3}\n",
        },
    )
    agent_container = _FakeContainer(diff_entries=[], files={})
    fake_docker = types.SimpleNamespace(
        from_env=lambda: _FakeDockerClient(
            {
                "target_match_export_3": target_container,
                "agent_match_export_3": agent_container,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    match = types.SimpleNamespace(
        match_id="match_export_real_case",
        config=types.SimpleNamespace(players=[types.SimpleNamespace(id=3, name="Player 3")]),
        players={
            3: PlayerState(
                player_id=3,
                container_name="agent_match_export_3",
                target_container="target_match_export_3",
                target_ip="10.0.0.3",
            )
        },
        agent_logs={3: "(no session log found)\n"},
    )

    result = player_code_export.export_match_player_code(match)

    with zipfile.ZipFile(Path(result.bundle_path)) as archive:
        names = set(archive.namelist())
        target_summary = json.loads(archive.read("player_3/target/summary.json").decode("utf-8"))
        admin_notes_content = archive.read(
            "player_3/target/supporting_materials/added/app/static/backup/admin_notes.txt"
        ).decode("utf-8")
        flag3_content = archive.read("player_3/target/supporting_materials/added/etc/flag3.txt").decode("utf-8")
        flag4_content = archive.read(
            "player_3/target/supporting_materials/added/opt/.credentials/flag4.txt"
        ).decode("utf-8")

        assert "player_3/target/core_code/added/tmp/app_patched.py" in names
        assert "player_3/target/core_code/added/tmp/head_app.py" in names
        assert "player_3/target/supporting_materials/added/app/app.py.backup" in names
        assert "player_3/target/supporting_materials/added/app/static/backup/admin_notes.txt" in names
        assert "player_3/target/supporting_materials/added/etc/flag3.txt" in names
        assert "player_3/target/supporting_materials/added/opt/.credentials/flag4.txt" in names
        assert "player_3/target/core_code/added/run/sshd.pid" not in names

        assert target_summary["counts"]["core_code"]["added"] == 2
        assert target_summary["counts"]["supporting_materials"]["added"] == 4
        assert target_summary["counts"]["filtered"] == 1
        assert target_summary["counts"]["redacted"] == 3
        assert target_summary["filtered_paths"] == [
            {"path": "/run/sshd.pid", "reason": "default_excluded_prefix"}
        ]
        assert "FLAG{751ba8e128feed02bcd56c6ebd5059b9}" not in admin_notes_content
        assert "FLAG{3379a8261ee3eda92b27da80dd2d085b}" not in flag3_content
        assert "FLAG{2d78c54f924501d2309bdbc40d8922f3}" not in flag4_content
        assert "FLAG{[REDACTED]}" in admin_notes_content
        assert "FLAG{[REDACTED]}" in flag3_content
        assert "FLAG{[REDACTED]}" in flag4_content


def test_replay_exports_extensionless_dockerfile_and_makefile(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "replay")

    target_container = _FakeContainer(
        diff_entries=[
            {"Path": "/app/Dockerfile", "Kind": 1},
            {"Path": "/app/Makefile", "Kind": 0},
        ],
        files={
            "/app/Dockerfile": b"FROM python:3.11-slim\nCOPY . /app\n",
            "/app/Makefile": b"build:\n\tpython app.py\n",
        },
    )
    agent_container = _FakeContainer(diff_entries=[], files={})
    fake_docker = types.SimpleNamespace(
        from_env=lambda: _FakeDockerClient(
            {
                "target_match_export_4": target_container,
                "agent_match_export_4": agent_container,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    match = types.SimpleNamespace(
        match_id="match_export_extensionless",
        config=types.SimpleNamespace(players=[types.SimpleNamespace(id=4, name="Player 4")]),
        players={
            4: PlayerState(
                player_id=4,
                container_name="agent_match_export_4",
                target_container="target_match_export_4",
                target_ip="10.0.0.4",
            )
        },
        agent_logs={4: "(no session log found)\n"},
    )

    result = player_code_export.export_match_player_code(match)

    with zipfile.ZipFile(Path(result.bundle_path)) as archive:
        names = set(archive.namelist())
        target_summary = json.loads(archive.read("player_4/target/summary.json").decode("utf-8"))

        assert "player_4/target/core_code/added/app/Dockerfile" in names
        assert "player_4/target/core_code/changed/app/Makefile" in names
        assert target_summary["counts"]["core_code"]["added"] == 1
        assert target_summary["counts"]["core_code"]["changed"] == 1


def test_replay_ready_payload_marks_partial_when_player_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "replay")

    target_container = _FakeContainer(
        diff_entries=[{"Path": "/tmp/missing_patch.py", "Kind": 1}],
        files={},
    )
    agent_container = _FakeContainer(diff_entries=[], files={})
    fake_docker = types.SimpleNamespace(
        from_env=lambda: _FakeDockerClient(
            {
                "target_match_export_partial": target_container,
                "agent_match_export_partial": agent_container,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    match = types.SimpleNamespace(
        match_id="match_export_partial",
        config=types.SimpleNamespace(players=[types.SimpleNamespace(id=7, name="Player 7")]),
        players={
            7: PlayerState(
                player_id=7,
                container_name="agent_match_export_partial",
                target_container="target_match_export_partial",
                target_ip="10.0.0.7",
            )
        },
        agent_logs={7: "(no session log found)\n"},
    )

    result = player_code_export.export_match_player_code(match)
    payload = result.to_event_payload()

    assert result.complete is False
    assert payload["status"] == "ready"
    assert payload["result_status"] == "partial"
    assert payload["bundle_available"] is True
    assert payload["partial"] is True
    assert payload["incomplete_player_count"] == 1
    assert payload["incomplete_players"] == [
        {
            "player_id": 7,
            "player_name": "Player 7",
            "incomplete_sections": ["target"],
        }
    ]
    assert payload["players"][0]["result_status"] == "partial"
    assert payload["players"][0]["incomplete_sections"] == ["target"]


def test_replay_skips_large_file_and_records_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "replay")

    huge_path = "/tmp/huge_payload"
    small_path = "/tmp/small_payload"
    huge_content = b"#!/bin/sh\n" + (b"a" * (player_code_export.MAX_BYTES_BY_BUCKET["review_candidates"] + 1))
    small_content = b"#!/bin/sh\necho ok\n"
    target_container = _FakeContainer(
        diff_entries=[
            {"Path": huge_path, "Kind": 1},
            {"Path": small_path, "Kind": 1},
        ],
        files={
            huge_path: huge_content,
            small_path: small_content,
        },
    )
    agent_container = _FakeContainer(diff_entries=[], files={})
    fake_docker = types.SimpleNamespace(
        from_env=lambda: _FakeDockerClient(
            {
                "target_match_export_5": target_container,
                "agent_match_export_5": agent_container,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    match = types.SimpleNamespace(
        match_id="match_export_large_file",
        config=types.SimpleNamespace(players=[types.SimpleNamespace(id=5, name="Player 5")]),
        players={
            5: PlayerState(
                player_id=5,
                container_name="agent_match_export_5",
                target_container="target_match_export_5",
                target_ip="10.0.0.5",
            )
        },
        agent_logs={5: "(no session log found)\n"},
    )

    result = player_code_export.export_match_player_code(match)

    with zipfile.ZipFile(Path(result.bundle_path)) as archive:
        names = set(archive.namelist())
        target_summary = json.loads(archive.read("player_5/target/summary.json").decode("utf-8"))

        assert "player_5/target/review_candidates/added/tmp/small_payload" in names
        assert "player_5/target/review_candidates/added/tmp/huge_payload" not in names
        assert target_summary["counts"]["review_candidates"]["added"] == 1
        assert target_summary["counts"]["skipped_large"] == 1
        assert target_summary["skipped_large_files"] == [
            {
                "path": huge_path,
                "relative_path": "tmp/huge_payload",
                "bucket": "review_candidates",
                "size_bytes": len(huge_content),
                "size_limit_bytes": player_code_export.MAX_BYTES_BY_BUCKET["review_candidates"],
                "reason": "skipped_large_file",
            }
        ]


def test_replay_truncates_review_candidates_after_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCLAW_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "replay")

    limit = player_code_export.MAX_REVIEW_CANDIDATES_PER_CONTAINER
    diff_entries = []
    files = {}
    for index in range(limit + 1):
        path = f"/tmp/review_candidate_{index:02d}"
        diff_entries.append({"Path": path, "Kind": 1})
        files[path] = f"#!/bin/sh\necho candidate-{index}\n".encode("utf-8")

    target_container = _FakeContainer(diff_entries=diff_entries, files=files)
    agent_container = _FakeContainer(diff_entries=[], files={})
    fake_docker = types.SimpleNamespace(
        from_env=lambda: _FakeDockerClient(
            {
                "target_match_export_6": target_container,
                "agent_match_export_6": agent_container,
            }
        )
    )
    monkeypatch.setitem(sys.modules, "docker", fake_docker)

    match = types.SimpleNamespace(
        match_id="match_export_review_limit",
        config=types.SimpleNamespace(players=[types.SimpleNamespace(id=6, name="Player 6")]),
        players={
            6: PlayerState(
                player_id=6,
                container_name="agent_match_export_6",
                target_container="target_match_export_6",
                target_ip="10.0.0.6",
            )
        },
        agent_logs={6: "(no session log found)\n"},
    )

    result = player_code_export.export_match_player_code(match)

    skipped_path = f"/tmp/review_candidate_{limit:02d}"
    with zipfile.ZipFile(Path(result.bundle_path)) as archive:
        names = set(archive.namelist())
        target_summary = json.loads(archive.read("player_6/target/summary.json").decode("utf-8"))

        assert f"player_6/target/review_candidates/added/tmp/review_candidate_00" in names
        assert f"player_6/target/review_candidates/added/tmp/review_candidate_{limit:02d}" not in names
        assert target_summary["counts"]["review_candidates"]["added"] == limit
        assert target_summary["counts"]["skipped_limit"] == 1
        assert target_summary["skipped_limit_files"] == [
            {
                "path": skipped_path,
                "relative_path": f"tmp/review_candidate_{limit:02d}",
                "bucket": "review_candidates",
                "limit": limit,
                "reason": "review_candidate_limit",
            }
        ]


def test_build_failed_export_payload_marks_bundle_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENCLAW_EXPORTS_PATH", str(tmp_path / "exports"))
    monkeypatch.setenv("OPENCLAW_PLAYER_EXPORT_PROFILE", "replay")

    payload = player_code_export.build_failed_export_payload(
        "match_failed",
        "zip creation exploded",
        generated_at="2026-04-02T12:00:00",
        failure_stage="bundle_generation",
    )

    assert payload == {
        "status": "failed",
        "result_status": "failed",
        "bundle_available": False,
        "partial": False,
        "match_id": "match_failed",
        "bundle_path": str(tmp_path / "exports" / "match_failed" / "match_match_failed_player_code_export.zip"),
        "bundle_filename": "match_match_failed_player_code_export.zip",
        "generated_at": "2026-04-02T12:00:00",
        "complete": False,
        "schema_version": 2,
        "export_profile": "replay",
        "failure_stage": "bundle_generation",
        "error": "zip creation exploded",
        "players": [],
        "incomplete_player_count": 0,
        "incomplete_players": [],
    }


@pytest.mark.asyncio
async def test_player_code_export_endpoint_returns_zip_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_player_code_export_module")

    export_path = tmp_path / "match_demo_player_code_export.zip"
    export_path.write_bytes(b"zip-bytes")
    monkeypatch.setattr(module, "get_player_code_export_path", lambda _match_id: export_path)

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_demo", config)
    match.status = "finished"
    module.referee.matches[match.match_id] = match

    response = await module.get_player_code_export(match.match_id)

    assert Path(response.path) == export_path
    assert response.media_type == "application/zip"


@pytest.mark.asyncio
async def test_player_code_export_endpoint_surfaces_failed_export_error(tmp_path, monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_player_code_export_failed_module")

    missing_path = tmp_path / "missing_failed.zip"
    monkeypatch.setattr(module, "get_player_code_export_path", lambda _match_id: missing_path)

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_failed_export", config)
    match.status = "finished"
    match.player_code_export = {
        "status": "failed",
        "result_status": "failed",
        "bundle_available": False,
        "error": "agent export crashed",
    }
    module.referee.matches[match.match_id] = match

    with pytest.raises(HTTPException) as exc_info:
        await module.get_player_code_export(match.match_id)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "agent export crashed"


@pytest.mark.asyncio
async def test_player_code_export_endpoint_rejects_unfinished_match(tmp_path, monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_player_code_export_pending_module")

    missing_path = tmp_path / "missing.zip"
    monkeypatch.setattr(module, "get_player_code_export_path", lambda _match_id: missing_path)

    config = module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")])
    match = module.MatchState("match_pending", config)
    match.status = "attack"
    module.referee.matches[match.match_id] = match

    with pytest.raises(HTTPException) as exc_info:
        await module.get_player_code_export(match.match_id)

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "Match has not finished yet"
