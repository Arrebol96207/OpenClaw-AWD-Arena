"""Pure helpers for target-ssh maintenance access."""

import asyncio
import re
import shlex
from typing import Protocol


CONTAINER_ABSOLUTE_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9._/@+-]{1,255}$")
CONTAINER_ACCOUNT_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,31}$")


class SSHKeyMaterialLike(Protocol):
    private_key_path: str


def validate_container_absolute_path(value: str, *, label: str) -> None:
    if not CONTAINER_ABSOLUTE_PATH_PATTERN.match(value):
        raise ValueError(f"invalid target SSH {label}")


def validate_container_account(value: str, *, label: str) -> None:
    if not CONTAINER_ACCOUNT_PATTERN.match(value):
        raise ValueError(f"invalid target SSH {label}")


def build_target_ssh_helper(
    target_ip: str,
    ssh_key_material: SSHKeyMaterialLike,
    maintenance_username: str,
    *,
    connect_timeout: int,
) -> str:
    private_key_path = shlex.quote(ssh_key_material.private_key_path)
    remote = shlex.quote(f"{maintenance_username}@{target_ip}")
    return "\n".join([
        "#!/bin/sh",
        "set -eu",
        'if [ "$#" -eq 0 ]; then',
        '  printf "Usage: target-ssh \'<remote command>\'\\n" >&2',
        "  exit 64",
        "fi",
        (
            f"exec ssh -i {private_key_path} "
            "-o BatchMode=yes "
            "-o StrictHostKeyChecking=no "
            "-o UserKnownHostsFile=/dev/null "
            f"-o ConnectTimeout={connect_timeout} "
            f"{remote} \"$@\""
        ),
        "",
    ])


def classify_target_ssh_probe_failure(error: BaseException) -> tuple[str, str]:
    if isinstance(error, asyncio.TimeoutError):
        return (
            "TARGET_SSH_NETWORK_UNREACHABLE",
            "target-ssh probe timed out while waiting for SSH connectivity",
        )

    details = str(error).strip() or type(error).__name__
    normalized = details.lower()

    if "target-ssh" in normalized and "no such file or directory" in normalized:
        return ("TARGET_SSH_HELPER_MISSING", details)
    if "awd_target_key" in normalized and "no such file or directory" in normalized:
        return ("TARGET_SSH_KEY_MISSING", details)
    if "ssh: not found" in normalized or "exec: ssh" in normalized:
        return ("TARGET_SSH_CLIENT_MISSING", details)
    if "permission denied (publickey" in normalized or "permission denied" in normalized and "publickey" in normalized:
        return ("TARGET_SSH_AUTHORIZED_KEYS_MISSING", details)
    if "connection refused" in normalized or "kex_exchange_identification" in normalized or "connection reset by peer" in normalized:
        return ("TARGET_SSHD_NOT_READY", details)
    if (
        "connection timed out" in normalized
        or "operation timed out" in normalized
        or "no route to host" in normalized
        or "network is unreachable" in normalized
    ):
        return ("TARGET_SSH_NETWORK_UNREACHABLE", details)

    return ("TARGET_SSH_PROBE_FAILED", details)
