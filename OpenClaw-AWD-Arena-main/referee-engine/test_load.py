import json
from main import MatchConfig

data = json.loads("""{"match": {"name": "Test Match", "duration": 1800, "phases": {"defense": 600, "attack": 1200}}, "llm": {"provider": "openai", "baseUrl": "http://test", "apiKey": "test", "model": "gpt-4", "proxy": "http://host.docker.internal:7897"}, "players": [{"id": 1, "name": "P1", "model": null, "apiKey": null, "gatewayPort": null}, {"id": 2, "name": "P2", "model": null, "apiKey": null, "gatewayPort": null}], "scoring": {"attackSuccess": 100, "defenseFailure": -50, "slaViolation": -10}, "flags": {"refreshInterval": 300, "format": "flag{{{hash}}}"}, "network": {"arenaSubnet": "172.20.0.0/16", "mgmtSubnetPrefix": "172.21"}, "target_image": "openclaw/ctf-target:v1", "agent_image": "alpine/openclaw:latest"}""")
try:
    c = MatchConfig(**data)
    print("OK")
except Exception:
    import traceback
    traceback.print_exc()
