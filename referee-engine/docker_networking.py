"""Docker networking helpers used by match container orchestration."""

import ipaddress
import logging
from typing import Any, List


logger = logging.getLogger("referee.docker_networking")


def parse_api_version(version: str) -> tuple[int, ...]:
    parts = version.strip().split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError(f"Invalid Docker API version: {version}")
    return tuple(int(part) for part in parts)


def iter_existing_docker_subnets(client) -> List[Any]:
    networks: List[Any] = []
    for network in client.networks.list():
        ipam = network.attrs.get("IPAM", {})
        for config in ipam.get("Config") or []:
            subnet = config.get("Subnet")
            if not subnet:
                continue
            try:
                networks.append(ipaddress.ip_network(subnet, strict=False))
            except ValueError:
                logger.warning("Skipping invalid Docker subnet on network %s: %s", network.name, subnet)
    return networks


def choose_available_subnet(client, candidate_subnets: List[str]) -> tuple[str, str]:
    existing_subnets = iter_existing_docker_subnets(client)
    for subnet in candidate_subnets:
        network = ipaddress.ip_network(subnet, strict=False)
        if any(network.overlaps(existing) for existing in existing_subnets):
            continue
        gateway = str(next(network.hosts()))
        return str(network), gateway
    raise RuntimeError("No available Docker subnet found for requested network pool")
