import importlib.util
import sys
from pathlib import Path

import pytest


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
    assert config.players[0].baseUrl is None
    assert config.players[0].provider is None
    assert config.players[0].api is None
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
                baseUrl="https://player-api.test/v1",
                provider="anthropic",
                api="anthropic",
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
    assert dumped["players"][0]["baseUrl"] == "https://player-api.test/v1"
    assert dumped["players"][0]["provider"] == "anthropic"
    assert dumped["players"][0]["api"] == "anthropic"
    assert dumped["players"][0]["backend_config"] == {
        "image": "custom/openclaw:phase0",
        "profile_name": "default",
        "extra_env": {"A": "1"},
    }


def test_match_config_rejects_duplicate_player_ids(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_match_duplicate_players")

    with pytest.raises(ValueError, match="player ids must be unique"):
        module.MatchConfig(
            players=[
                module.PlayerConfig(id=1, name="P1"),
                module.PlayerConfig(id=1, name="P1 duplicate"),
            ]
        )


def test_match_config_rejects_too_many_players(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_match_too_many_players")

    with pytest.raises(ValueError, match="players must contain at most"):
        module.MatchConfig(
            players=[
                module.PlayerConfig(id=i, name=f"P{i}")
                for i in range(1, module.MAX_PLAYERS + 2)
            ]
        )


def test_match_config_rejects_invalid_backend_and_image(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_match_invalid_backend_image")

    with pytest.raises(ValueError, match="unsupported backend_type"):
        module.PlayerConfig(id=1, name="P1", backend_type="unknown")

    with pytest.raises(ValueError, match="invalid Docker image reference"):
        module.MatchConfig(players=[module.PlayerConfig(id=1, name="P1")], target_image="bad image;rm")

    with pytest.raises(ValueError, match="invalid Docker image reference"):
        module.PlayerBackendConfig(image="bad image;rm")


def test_werewolf_config_accepts_both_supported_boards(monkeypatch):
    monkeypatch.setattr("asyncio.create_subprocess_shell", lambda *args, **kwargs: None)
    module = _load_main_module("test_main_werewolf_boards")
    players = [module.PlayerConfig(id=i, name=f"P{i}") for i in range(1, 13)]

    standard = module.MatchConfig(mode="werewolf", players=players)
    assert standard.werewolf.board == "standard_guard"
    assert standard.werewolf.roles.model_dump() == {
        "werewolf": 4,
        "white_wolf_king": 0,
        "villager": 4,
        "seer": 1,
        "witch": 1,
        "hunter": 1,
        "guard": 1,
        "knight": 0,
    }

    white_wolf = module.MatchConfig(
        mode="werewolf",
        players=players,
        werewolf=module.WerewolfConfig(
            board="white_wolf_king_knight",
            roles=module.WerewolfRoleConfig(
                werewolf=3,
                white_wolf_king=1,
                villager=4,
                seer=1,
                witch=1,
                hunter=1,
                guard=0,
                knight=1,
            ),
        ),
    )
    assert white_wolf.werewolf.board == "white_wolf_king_knight"

    with pytest.raises(ValueError, match="werewolf roles must match board"):
        module.WerewolfConfig(
            board="white_wolf_king_knight",
            roles=module.WerewolfRoleConfig(),
        )
