"""Pydantic models for match configuration and public API payloads."""

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from werewolf import (
    WEREWOLF_BOARD_ROLE_COUNTS,
    WEREWOLF_BOARD_STANDARD_GUARD,
    board_role_counts,
)


MAX_PLAYERS = 16
SUPPORTED_BACKEND_TYPES = {"openclaw", "hermes"}
IMAGE_REF_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}$")


class MatchPhaseConfig(BaseModel):
    defense: int = Field(default=600, ge=0, le=86400)
    attack: int = Field(default=6600, ge=0, le=86400)


class MatchDetails(BaseModel):
    name: str = "AWD Match"
    duration: int = Field(default=7200, ge=0, le=86400)
    phases: MatchPhaseConfig = Field(default_factory=MatchPhaseConfig)


class LLMConfig(BaseModel):
    provider: str = "openai-completions"
    baseUrl: str = "https://api.findmini.top/gpt"
    apiKey: str = ""
    model: str = "gpt-5.5"
    proxy: str = ""


class PlayerBackendConfig(BaseModel):
    image: Optional[str] = None
    profile_name: Optional[str] = None
    extra_env: Dict[str, str] = Field(default_factory=dict)

    @field_validator("image")
    @classmethod
    def validate_image_reference(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        image = str(value).strip()
        if not image:
            return None
        if not IMAGE_REF_PATTERN.match(image):
            raise ValueError("invalid Docker image reference")
        return image


class PlayerConfig(BaseModel):
    id: int = Field(gt=0, le=255)
    name: str
    model: Optional[str] = None
    apiKey: Optional[str] = None
    baseUrl: Optional[str] = None
    provider: Optional[str] = None
    api: Optional[str] = None
    gatewayPort: Optional[int] = None
    backend_type: str = "openclaw"
    backend_config: PlayerBackendConfig = Field(default_factory=PlayerBackendConfig)

    @field_validator("backend_type")
    @classmethod
    def validate_backend_type(cls, value: str) -> str:
        normalized = str(value or "openclaw").strip().lower()
        if normalized not in SUPPORTED_BACKEND_TYPES:
            raise ValueError(f"unsupported backend_type: {normalized}")
        return normalized


class ScoringConfig(BaseModel):
    attackSuccess: int = 100
    defenseFailure: int = -50
    slaViolation: int = -50


class FlagConfig(BaseModel):
    refreshInterval: int = Field(default=300, ge=30, le=86400)
    format: str = "flag{{{hash}}}"


class NetworkConfig(BaseModel):
    arenaSubnet: str = "172.20.0.0/16"
    mgmtSubnetPrefix: str = "172.21"


class LoopMatchConfig(BaseModel):
    enabled: bool = False
    repeatCount: int = Field(default=1, ge=1)
    loopId: Optional[str] = None
    currentIteration: int = Field(default=1, ge=1)


class WerewolfRoleConfig(BaseModel):
    werewolf: int = 4
    white_wolf_king: int = 0
    villager: int = 4
    seer: int = 1
    witch: int = 1
    hunter: int = 1
    guard: int = 1
    knight: int = 0


class WerewolfConfig(BaseModel):
    playerCount: int = 12
    board: str = WEREWOLF_BOARD_STANDARD_GUARD
    roles: WerewolfRoleConfig = Field(default_factory=WerewolfRoleConfig)
    sheriffEnabled: bool = True
    werewolfRevealEnabled: bool = True
    maxDays: int = Field(default=6, ge=1, le=20)
    speechSecondsPerPlayer: int = Field(default=45, ge=5, le=600)
    voteSeconds: int = Field(default=60, ge=5, le=600)
    nightActionSeconds: int = Field(default=45, ge=5, le=600)
    preMatchTraining: bool = True
    aiJudgeEnabled: bool = True

    @model_validator(mode="after")
    def validate_werewolf_config(self):
        role_counts = self.roles.model_dump()
        if self.playerCount != 12:
            raise ValueError("werewolf mode requires exactly 12 players")
        if self.board not in WEREWOLF_BOARD_ROLE_COUNTS:
            raise ValueError("unsupported werewolf board")
        if sum(int(value) for value in role_counts.values()) != self.playerCount:
            raise ValueError("werewolf roles must add up to playerCount")
        expected_roles = board_role_counts(self.board)
        if role_counts != expected_roles:
            raise ValueError(f"werewolf roles must match board {self.board}")
        return self


class MatchConfig(BaseModel):
    """Match configuration."""

    mode: str = "awd"
    match: MatchDetails = Field(default_factory=MatchDetails)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    players: List[PlayerConfig]
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    flags: FlagConfig = Field(default_factory=FlagConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    target_image: str = "openclaw/ctf-target:v1"
    agent_image: str = "openclaw/local-agent:ssh"
    loop: LoopMatchConfig = Field(default_factory=LoopMatchConfig)
    werewolf: WerewolfConfig = Field(default_factory=WerewolfConfig)

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, value: str) -> str:
        normalized = str(value or "awd").strip().lower()
        if normalized not in {"awd", "werewolf"}:
            raise ValueError("mode must be 'awd' or 'werewolf'")
        return normalized

    @field_validator("target_image", "agent_image")
    @classmethod
    def validate_image_reference(cls, value: str) -> str:
        image = str(value or "").strip()
        if not image or not IMAGE_REF_PATTERN.match(image):
            raise ValueError("invalid Docker image reference")
        return image

    @model_validator(mode="after")
    def validate_match_config(self):
        if not self.players:
            raise ValueError("players must contain at least one player")
        if len(self.players) > MAX_PLAYERS:
            raise ValueError(f"players must contain at most {MAX_PLAYERS} players")
        if self.mode == "werewolf" and len(self.players) != self.werewolf.playerCount:
            raise ValueError("werewolf mode requires exactly 12 players")

        player_ids = [player.id for player in self.players]
        if len(player_ids) != len(set(player_ids)):
            raise ValueError("player ids must be unique")

        total_phase_seconds = self.match.phases.defense + self.match.phases.attack
        if self.match.duration and total_phase_seconds and total_phase_seconds > self.match.duration:
            raise ValueError("defense + attack phases cannot exceed match duration")
        return self


class FlagSubmission(BaseModel):
    """Flag submission payload."""

    player_id: Optional[int] = None
    flag: str
    target_player_id: Optional[int] = None


class LLMTestRequest(BaseModel):
    """LLM connectivity test request."""

    baseUrl: str
    apiKey: str
    model: str
    proxy: Optional[str] = None


class TopPlayerEntry(BaseModel):
    player_id: int
    total_score: int


class LeaderboardSummary(BaseModel):
    rank: int
    total_players: int
    my_score: int
    leader_score: int
    score_gap_to_leader: int
    score_gap_to_next_above: Optional[int] = None
    score_gap_to_next_below: Optional[int] = None
    top_players: List[TopPlayerEntry] = Field(default_factory=list)


class PlayerSelfStatus(BaseModel):
    player_id: int
    ready_status: Optional[str] = None
    ready_reason: Optional[str] = None
    readiness_details: Dict[str, Any] = Field(default_factory=dict)
    score: int
    attack_score: int
    defense_score: int
    sla_score: int
    sla_up: bool
    sla_down_minutes: int
    flags_captured: int
    flags_lost: int


class AttackTargetEntry(BaseModel):
    player_id: int
    ip: str
    port: int


class AttackContext(BaseModel):
    enemy_targets: List[AttackTargetEntry] = Field(default_factory=list)


class PlayerScoreDeltaEntry(BaseModel):
    player_id: int
    is_self: bool = False
    total_delta: int
    attack_delta: int
    defense_delta: int
    sla_delta: int


class ScoreChangesSinceLastQuery(BaseModel):
    has_previous_query: bool
    previous_query_at: Optional[str] = None
    current_query_at: str
    players: List[PlayerScoreDeltaEntry] = Field(default_factory=list)


class PlayerStatusResponse(BaseModel):
    schema_version: int = 2
    match_id: str
    phase: str
    server_time: str
    remaining_seconds: int
    poll_after_seconds: int
    can_submit_flags: bool
    flag_refresh_interval: int
    self: PlayerSelfStatus
    leaderboard_summary: LeaderboardSummary
    score_changes_since_last_query: ScoreChangesSinceLastQuery
    attack_context: Optional[AttackContext] = None
