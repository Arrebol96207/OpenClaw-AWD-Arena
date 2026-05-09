import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_main_module(module_name: str):
    main_path = ROOT / "main.py"
    spec = importlib.util.spec_from_file_location(module_name, main_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_match_config_old_payload_defaults_player_backend_fields(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_match_backend_defaults")

    config = module.MatchConfig(
        players=[
            module.PlayerConfig(id=1, name="P1"),
            module.PlayerConfig(id=2, name="P2", model="model-2"),
        ]
    )

    assert [player.backend_type for player in config.players] == ["openclaw", "openclaw"]
    assert config.players[0].backend_config.image is None
    assert config.players[0].backend_config.profile_name is None
    assert config.players[0].backend_config.extra_env == {}


def test_match_config_preserves_explicit_backend_config(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_match_backend_explicit")

    config = module.MatchConfig(
        players=[
            module.PlayerConfig(
                id=1,
                name="P1",
                backend_type="openclaw",
                backend_config=module.PlayerBackendConfig(
                    image="custom/openclaw:phase0",
                    profile_name="default",
                    extra_env={"A": "1"},
                ),
            )
        ]
    )

    dumped = config.model_dump()

    assert dumped["players"][0]["backend_type"] == "openclaw"
    assert dumped["players"][0]["backend_config"] == {
        "image": "custom/openclaw:phase0",
        "profile_name": "default",
        "extra_env": {"A": "1"},
    }
