"""Player read-token bookkeeping."""

import secrets
from typing import Dict, Optional, Tuple


class PlayerReadTokenStore:
    def __init__(self, token_bytes: int = 24):
        self.token_bytes = token_bytes
        self.index: Dict[str, Tuple[str, int]] = {}

    def issue(self, match: object, player_id: int) -> str:
        tokens = getattr(match, "player_read_tokens")
        match_id = getattr(match, "match_id")

        existing = tokens.get(player_id)
        if existing:
            self.index[existing] = (match_id, player_id)
            return existing

        token = secrets.token_urlsafe(self.token_bytes)
        tokens[player_id] = token
        self.index[token] = (match_id, player_id)
        return token

    def revoke(self, match: object, player_id: int) -> Optional[str]:
        tokens = getattr(match, "player_read_tokens")
        token = tokens.pop(player_id, None)
        if token:
            self.index.pop(token, None)

        checkpoints = getattr(match, "player_status_checkpoints", None)
        if isinstance(checkpoints, dict):
            checkpoints.pop(player_id, None)

        checkpoint_locks = getattr(match, "player_status_checkpoint_locks", None)
        if isinstance(checkpoint_locks, dict):
            checkpoint_locks.pop(player_id, None)

        return token

    def resolve(self, token: Optional[str]) -> Optional[Tuple[str, int]]:
        if not token:
            return None
        return self.index.get(token)
