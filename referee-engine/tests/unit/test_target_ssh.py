import asyncio
from types import SimpleNamespace

import pytest

from target_ssh import (
    build_target_ssh_helper,
    classify_target_ssh_probe_failure,
    validate_container_absolute_path,
    validate_container_account,
)


def test_build_target_ssh_helper_quotes_dynamic_values():
    material = SimpleNamespace(private_key_path="/home/node/.ssh/awd target+key")

    helper = build_target_ssh_helper(
        "10.0.0.8",
        material,
        "defender user",
        connect_timeout=7,
    )

    assert helper.startswith("#!/bin/sh\nset -eu\n")
    assert "Usage: target-ssh" in helper
    assert "ssh -i '/home/node/.ssh/awd target+key'" in helper
    assert "-o ConnectTimeout=7" in helper
    assert "'defender user@10.0.0.8'" in helper
    assert helper.endswith('\n')


def test_container_path_and_account_validation_accept_safe_values():
    validate_container_absolute_path("/usr/local/bin/target-ssh", label="helper_path")
    validate_container_absolute_path("/home/node/.ssh/awd+target_key", label="private_key_path")
    validate_container_account("node", label="owner_user")
    validate_container_account("node-group_1", label="owner_group")


def test_container_path_and_account_validation_reject_unsafe_values():
    with pytest.raises(ValueError, match="invalid target SSH helper_path"):
        validate_container_absolute_path("/usr/local/bin/target-ssh; touch /tmp/pwned", label="helper_path")

    with pytest.raises(ValueError, match="invalid target SSH owner_user"):
        validate_container_account("node;root", label="owner_user")


def test_classify_target_ssh_probe_failure_maps_known_causes():
    assert classify_target_ssh_probe_failure(asyncio.TimeoutError()) == (
        "TARGET_SSH_NETWORK_UNREACHABLE",
        "target-ssh probe timed out while waiting for SSH connectivity",
    )
    assert classify_target_ssh_probe_failure(
        RuntimeError("docker exec failed for claw: sh: target-ssh: not found")
    ) == (
        "TARGET_SSH_CLIENT_MISSING",
        "docker exec failed for claw: sh: target-ssh: not found",
    )
    assert classify_target_ssh_probe_failure(
        RuntimeError("Warning: Identity file /home/node/.ssh/awd_target_key not accessible: No such file or directory")
    ) == (
        "TARGET_SSH_KEY_MISSING",
        "Warning: Identity file /home/node/.ssh/awd_target_key not accessible: No such file or directory",
    )
    assert classify_target_ssh_probe_failure(
        RuntimeError("defender@10.0.0.8: Permission denied (publickey,password).")
    ) == (
        "TARGET_SSH_AUTHORIZED_KEYS_MISSING",
        "defender@10.0.0.8: Permission denied (publickey,password).",
    )
    assert classify_target_ssh_probe_failure(
        RuntimeError("ssh: connect to host 10.0.0.8 port 22: Connection refused")
    ) == (
        "TARGET_SSHD_NOT_READY",
        "ssh: connect to host 10.0.0.8 port 22: Connection refused",
    )
    assert classify_target_ssh_probe_failure(
        RuntimeError("ssh: connect to host 10.0.0.8 port 22: Network is unreachable")
    ) == (
        "TARGET_SSH_NETWORK_UNREACHABLE",
        "ssh: connect to host 10.0.0.8 port 22: Network is unreachable",
    )


def test_classify_target_ssh_probe_failure_has_generic_fallback():
    assert classify_target_ssh_probe_failure(RuntimeError("surprising failure")) == (
        "TARGET_SSH_PROBE_FAILED",
        "surprising failure",
    )
