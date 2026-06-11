import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from match_models import MatchConfig  # noqa: E402


def test_full_awd_match_config_accepts_current_image_defaults():
    config = MatchConfig(
        **{
            "match": {
                "name": "Test Match",
                "duration": 1800,
                "phases": {"defense": 600, "attack": 1200},
            },
            "llm": {
                "provider": "openai",
                "baseUrl": "http://test",
                "apiKey": "test",
                "model": "gpt-4",
                "proxy": "http://host.docker.internal:7897",
            },
            "players": [
                {"id": 1, "name": "P1", "model": None, "apiKey": None, "gatewayPort": None},
                {"id": 2, "name": "P2", "model": None, "apiKey": None, "gatewayPort": None},
            ],
            "scoring": {"attackSuccess": 100, "defenseFailure": -50, "slaViolation": -10},
            "flags": {"refreshInterval": 300, "format": "flag{{{hash}}}"},
            "network": {"arenaSubnet": "172.20.0.0/16", "mgmtSubnetPrefix": "172.21"},
            "target_image": "openclaw/ctf-target:v1",
            "agent_image": "openclaw/local-agent:ssh",
        }
    )

    assert config.target_image == "openclaw/ctf-target:v1"
    assert config.agent_image == "openclaw/local-agent:ssh"
