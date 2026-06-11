from types import SimpleNamespace

from player_tokens import PlayerReadTokenStore


def build_match():
    return SimpleNamespace(
        match_id="match_tokens",
        player_read_tokens={},
        player_status_checkpoints={1: {"scores_by_player": {}}},
        player_status_checkpoint_locks={1: object()},
    )


def test_issue_creates_token_and_reverse_index():
    store = PlayerReadTokenStore()
    match = build_match()

    token = store.issue(match, 1)

    assert match.player_read_tokens[1] == token
    assert store.resolve(token) == ("match_tokens", 1)


def test_issue_reuses_existing_match_token_and_repairs_index():
    store = PlayerReadTokenStore()
    match = build_match()
    match.player_read_tokens[1] = "existing-token"

    assert store.issue(match, 1) == "existing-token"
    assert store.index["existing-token"] == ("match_tokens", 1)


def test_revoke_removes_token_and_status_checkpoints():
    store = PlayerReadTokenStore()
    match = build_match()
    token = store.issue(match, 1)

    assert store.revoke(match, 1) == token
    assert 1 not in match.player_read_tokens
    assert token not in store.index
    assert 1 not in match.player_status_checkpoints
    assert 1 not in match.player_status_checkpoint_locks


def test_resolve_rejects_missing_tokens():
    store = PlayerReadTokenStore()

    assert store.resolve(None) is None
    assert store.resolve("") is None
    assert store.resolve("unknown") is None
