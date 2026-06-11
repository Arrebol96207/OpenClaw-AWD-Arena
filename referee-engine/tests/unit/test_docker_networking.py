from types import SimpleNamespace

import pytest

from docker_networking import (
    choose_available_subnet,
    iter_existing_docker_subnets,
    parse_api_version,
)


class _FakeNetworks:
    def __init__(self, networks):
        self._networks = networks

    def list(self):
        return self._networks


def _client_with_subnets(*subnets):
    networks = [
        SimpleNamespace(
            name=f"net-{index}",
            attrs={"IPAM": {"Config": [{"Subnet": subnet}] if subnet is not None else [{}]}},
        )
        for index, subnet in enumerate(subnets)
    ]
    return SimpleNamespace(networks=_FakeNetworks(networks))


def test_parse_api_version_accepts_numeric_version_parts():
    assert parse_api_version("1.43") == (1, 43)
    assert parse_api_version(" 24.0.7 ") == (24, 0, 7)


@pytest.mark.parametrize("version", ["", "abc", "1.x", "1.2-beta"])
def test_parse_api_version_rejects_invalid_versions(version):
    with pytest.raises(ValueError, match="Invalid Docker API version"):
        parse_api_version(version)


def test_iter_existing_docker_subnets_skips_missing_and_invalid_entries(caplog):
    client = _client_with_subnets("10.10.0.0/24", None, "not-a-subnet")

    subnets = iter_existing_docker_subnets(client)

    assert [str(subnet) for subnet in subnets] == ["10.10.0.0/24"]
    assert "Skipping invalid Docker subnet" in caplog.text


def test_choose_available_subnet_skips_overlapping_networks_and_returns_gateway():
    client = _client_with_subnets("10.20.0.0/16", "172.18.0.0/16")

    subnet, gateway = choose_available_subnet(
        client,
        ["10.20.1.0/24", "172.18.3.0/24", "10.21.0.0/24"],
    )

    assert subnet == "10.21.0.0/24"
    assert gateway == "10.21.0.1"


def test_choose_available_subnet_fails_when_all_candidates_overlap():
    client = _client_with_subnets("10.30.0.0/16")

    with pytest.raises(RuntimeError, match="No available Docker subnet"):
        choose_available_subnet(client, ["10.30.1.0/24", "10.30.2.0/24"])
