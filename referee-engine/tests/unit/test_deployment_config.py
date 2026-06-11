from deployment_config import (
    DEFAULT_CORS_ORIGINS,
    bind_host_is_local,
    binds_are_local,
    cors_allow_credentials,
    parse_cors_origins,
    resolve_frontend_dist_paths,
    should_serve_frontend_path,
)


def test_parse_cors_origins_uses_defaults_for_missing_or_blank_values():
    assert parse_cors_origins(None) == list(DEFAULT_CORS_ORIGINS)
    assert parse_cors_origins("   ") == list(DEFAULT_CORS_ORIGINS)
    assert parse_cors_origins(",,") == list(DEFAULT_CORS_ORIGINS)


def test_parse_cors_origins_trims_and_drops_empty_entries():
    assert parse_cors_origins(" https://arena.test , http://localhost:8080 ,, ") == [
        "https://arena.test",
        "http://localhost:8080",
    ]


def test_cors_allow_credentials_is_disabled_for_wildcard_origin():
    assert cors_allow_credentials(["https://arena.test"]) is True
    assert cors_allow_credentials(["*", "https://arena.test"]) is False


def test_bind_host_is_local_handles_loopback_hosts_and_blank_policy():
    assert bind_host_is_local("127.0.0.1") is True
    assert bind_host_is_local(" localhost ") is True
    assert bind_host_is_local("::1") is True
    assert bind_host_is_local("0.0.0.0") is False
    assert bind_host_is_local("") is False
    assert bind_host_is_local(None) is False
    assert bind_host_is_local("", blank_is_local=True) is True
    assert bind_host_is_local(None, blank_is_local=True) is True


def test_binds_are_local_uses_same_loopback_policy_for_all_hosts():
    assert binds_are_local("127.0.0.1", "localhost") is True
    assert binds_are_local("127.0.0.1", "0.0.0.0") is False
    assert binds_are_local(None, "127.0.0.1") is False
    assert binds_are_local(None, "127.0.0.1", blank_is_local=True) is True


def test_frontend_dist_paths_require_index_and_assets_directory(tmp_path):
    dist = tmp_path / "dist"
    paths = resolve_frontend_dist_paths(env_value=str(dist), default_dist="unused")

    assert paths.dist == dist
    assert paths.complete is False

    dist.mkdir()
    paths.index.write_text("<!doctype html>", encoding="utf-8")
    assert paths.complete is False

    paths.assets.mkdir()
    assert paths.complete is True


def test_should_serve_frontend_path_rejects_backend_entrypoints():
    assert should_serve_frontend_path("") is True
    assert should_serve_frontend_path("history") is True
    assert should_serve_frontend_path("/replay/match_1") is True
    assert should_serve_frontend_path("assets/index.js") is True

    assert should_serve_frontend_path("api/matches") is False
    assert should_serve_frontend_path("/api/matches") is False
    assert should_serve_frontend_path("ws") is False
    assert should_serve_frontend_path("/ws") is False
    assert should_serve_frontend_path("ws/ticket") is False
