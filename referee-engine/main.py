"""
OpenClaw AWD 裁判引擎 — 完整比赛生命周期管理

功能:
- 比赛创建/启动/结束
- 容器编排（创建/销毁选手+靶机容器）
- Agent 初始化（配置模型、注入提示词、等待READY）
- Flag 管理（定时生成/注入）
- SLA 检查（定时HTTP健康检查）
- 计分引擎（实时分数计算）
- Flag 提交 API
- WebSocket 实时事件广播
"""
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File, BackgroundTasks, Depends, Query, Request, Security, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from fastapi.encoders import jsonable_encoder
from typing import Annotated, Dict, List, Optional, Any, Tuple, cast
import asyncio
import copy
import json
import sys
import os
import logging
import secrets
import subprocess
import tempfile
import time
import uuid
import hashlib
import re
import shutil
import shlex
from dataclasses import dataclass
from datetime import datetime
from contextlib import asynccontextmanager, suppress
from urllib.parse import urlparse
import docker
from docker.errors import APIError
from docker.types import IPAMConfig, IPAMPool

# 本地模块
from docker_networking import (
    choose_available_subnet,
    iter_existing_docker_subnets,
    parse_api_version,
)
from docker_utils import docker_exec as _docker_exec_shared
from deployment_config import (
    DEFAULT_CORS_ORIGINS,
    cors_allow_credentials,
    frontend_dist_from_env,
    parse_cors_origins,
    should_serve_frontend_path,
)
from flag_manager import FlagManager, SLAChecker, ScoringEngine, PlayerState
from health import build_health_payload
from history_restore import (
    apply_leaderboard_snapshot_to_players,
    event_type,
    latest_leaderboard_event_data,
    latest_leaderboard_snapshot,
    restore_container_metadata_from_events,
)
from agent_client import (
    AgentClient,
    AgentSession,
    PromptRenderer,
    MESSAGE_MODE_NORMAL,
    MESSAGE_MODE_BUFFERED,
    MESSAGE_MODE_INTERRUPT,
)
from player_code_export import (
    build_failed_export_payload,
    export_match_player_code,
    get_exports_root,
    get_player_code_export_path,
    player_code_export_payload_is_partial,
    safe_player_code_export_dir,
)
from player_status import (
    PlayerNotInLeaderboardError,
    apply_leaderboard_snapshot,
    build_player_identity_fields,
    build_leaderboard_summary,
    build_score_changes_since_last_query,
    enrich_leaderboard,
    leaderboard_has_non_zero_scores,
    normalize_player_label_value,
    restore_scores_from_persisted_state,
    snapshot_player_scores,
)
from player_tokens import PlayerReadTokenStore
from submission_feedback import build_submission_feedback
from target_ssh import (
    CONTAINER_ABSOLUTE_PATH_PATTERN,
    CONTAINER_ACCOUNT_PATTERN,
    build_target_ssh_helper,
    classify_target_ssh_probe_failure,
    validate_container_absolute_path,
    validate_container_account,
)
from backends import AgentBackendAdapter, backend_registry
from api_auth import (
    api_key_header,
    api_key_is_valid,
    auth_mode_label,
    auth_status_payload,
    configured_api_key,
    insecure_no_auth_allowed,
    player_token_header,
    verify_api_key,
    ws_api_key_query_allowed,
)
from commentator import CommentatorService
from outbound_policy import (
    outbound_private_urls_allowed,
    validate_outbound_url,
)
from match_report import (
    build_match_report_markdown,
    format_report_time,
    leaderboard_rows_for_report,
    markdown_cell,
    submission_summary_for_report,
)
from match_summary import merge_match_summaries
from public_payload import (
    REDACTED_VALUE,
    is_sensitive_public_key as _is_sensitive_public_key,
    paginated_visible_match_events,
    sanitize_public_agent_logs,
    sanitize_public_event,
    sanitize_public_payload,
    sanitize_public_text,
    visible_match_events,
    visible_recent_match_events,
)
from match_models import (
    MAX_PLAYERS,
    AttackContext,
    AttackTargetEntry,
    FlagSubmission,
    LLMConfig,
    LLMTestRequest,
    LeaderboardSummary,
    LoopMatchConfig,
    MatchConfig,
    MatchDetails,
    MatchPhaseConfig,
    PlayerBackendConfig,
    PlayerConfig,
    PlayerScoreDeltaEntry,
    PlayerSelfStatus,
    PlayerStatusResponse,
    ScoreChangesSinceLastQuery,
    TopPlayerEntry,
    WerewolfConfig,
    WerewolfRoleConfig,
)
from template_store import ConfigTemplate, TemplateStore
from ws_ticket import (
    DEFAULT_WS_TICKET_RATE_LIMIT_MAX_REQUESTS,
    DEFAULT_WS_TICKET_RATE_LIMIT_WINDOW_SECONDS,
    DEFAULT_WS_TICKET_TTL_SECONDS,
    WebSocketTicketStore,
)
from ws_auth import websocket_auth_is_valid
from werewolf import (
    DEFAULT_ROLE_COUNTS,
    board_label,
    TEAM_GOOD,
    TEAM_WEREWOLF,
    WEREWOLF_EVENT_TYPES,
    WerewolfGameState,
    WerewolfJudgeConfig,
    WerewolfMatchRunner,
    apply_judgement_to_state,
    create_werewolf_state,
    judge_werewolf_match,
    render_werewolf_init_prompt,
)
import database

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger("referee")

CONTAINER_TIMEZONE = "Asia/Shanghai"

# 编排器（可选，如果独立进程则不需要）
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    from orchestrator.round_orchestrator import RoundOrchestrator  # noqa: F401
    HAS_ORCHESTRATOR = True
except ImportError:
    HAS_ORCHESTRATOR = False
    logger.info("RoundOrchestrator not available, using external container management")


# ==================== Constants ====================

DEFAULT_SCORING = {
    "attackSuccess": 100,
    "defenseFailure": -50,
    "slaViolation": -50,
}

INIT_CONTAINER_STABILIZATION_DELAY = 3
TARGET_SSH_INSTALL_TIMEOUT = 30
TARGET_SSH_CONNECT_TIMEOUT = 5
TARGET_SSH_PROBE_RETRIES = 10
TARGET_SSH_PROBE_RETRY_DELAY = 2
TARGET_SSH_PROBE_TIMEOUT = 15
CONTAINER_IP_RETRIES = 15
CONTAINER_IP_INSPECT_TIMEOUT = 10
CONTAINER_IP_RETRY_DELAY = 2
TARGET_HTTP_READY_RETRIES = 20
TARGET_HTTP_READY_TIMEOUT = 5
TARGET_HTTP_READY_RETRY_DELAY = 2
AGENT_READY_RETRY_DELAY = 2
AGENT_READY_MAX_WAIT = 600
AGENT_INIT_RETRY_MAX_WAIT = 120
_READINESS_PREVIOUS_UNSET = object()
MAX_TEMPLATE_IMPORT_BYTES = 1024 * 1024
DEFAULT_EVENTS_LIMIT = 200
MAX_EVENTS_LIMIT = 2000
WS_TICKET_TTL_SECONDS = DEFAULT_WS_TICKET_TTL_SECONDS
WS_TICKET_RATE_LIMIT_WINDOW_SECONDS = DEFAULT_WS_TICKET_RATE_LIMIT_WINDOW_SECONDS
WS_TICKET_RATE_LIMIT_MAX_REQUESTS = DEFAULT_WS_TICKET_RATE_LIMIT_MAX_REQUESTS
_outbound_private_urls_allowed = outbound_private_urls_allowed
_validate_outbound_url = validate_outbound_url
_markdown_cell = markdown_cell
_format_report_time = format_report_time
_leaderboard_rows_for_report = leaderboard_rows_for_report
_submission_summary_for_report = submission_summary_for_report
_event_type = event_type
_latest_leaderboard_snapshot = latest_leaderboard_snapshot
_apply_leaderboard_snapshot_to_players = apply_leaderboard_snapshot_to_players
_parse_api_version = parse_api_version
_iter_existing_docker_subnets = iter_existing_docker_subnets
_choose_available_subnet = choose_available_subnet
_normalize_player_label_value = normalize_player_label_value
_build_player_identity_fields = build_player_identity_fields
_enrich_leaderboard = enrich_leaderboard
_leaderboard_has_non_zero_scores = leaderboard_has_non_zero_scores
_apply_leaderboard_snapshot = apply_leaderboard_snapshot
_build_target_ssh_helper = build_target_ssh_helper
_classify_target_ssh_probe_failure = classify_target_ssh_probe_failure


def _hydrate_report_match_from_row(row: Dict[str, Any], submissions: List[Dict[str, Any]]) -> "MatchState":
    config = MatchConfig.model_validate(row["config"])
    match = MatchState(row["match_id"], config)
    match.status = row.get("status") or match.status
    try:
        match.created_at = datetime.fromisoformat(row["created_at"]) if row.get("created_at") else match.created_at
    except (TypeError, ValueError):
        pass
    try:
        match.finished_at = datetime.fromisoformat(row["finished_at"]) if row.get("finished_at") else None
    except (TypeError, ValueError):
        match.finished_at = None

    match.events = list(row.get("events") or [])
    match.persisted_submissions = list(submissions)
    match.persisted_leaderboard = _latest_leaderboard_snapshot(match.events)
    match.resources_destroyed = any(_event_type(event) == "MATCH_RESOURCES_DESTROYED" for event in match.events)
    for event in reversed(match.events):
        if _event_type(event) not in {"PLAYER_CODE_EXPORT_READY", "PLAYER_CODE_EXPORT_FAILED"}:
            continue
        data = event.get("data")
        if isinstance(data, dict):
            match.player_code_export = data
        break
    container_metadata_by_player = restore_container_metadata_from_events(match.events, match.match_id)
    for player in config.players:
        metadata = container_metadata_by_player.get(player.id, {})
        target_container = metadata.get("target_container") or f"target_{match.match_id}_{player.id}"
        match.players[player.id] = PlayerState(
            player_id=player.id,
            container_name=metadata.get("agent_container") or f"claw_{match.match_id}_{player.id}",
            target_container=target_container,
            target_ip=metadata.get("target_ip") or "",
            network_name=metadata.get("network") or f"awd_{match.match_id}_player_{player.id}",
        )

    if match.persisted_leaderboard:
        _apply_leaderboard_snapshot_to_players(match, match.persisted_leaderboard)
    else:
        match.scoring_engine.update_scores(match.players, match.persisted_submissions)
    return match


async def load_match_for_report(match_id: str) -> "MatchState":
    match = referee.matches.get(match_id)
    if match is not None:
        return match

    for row in await database.load_all_matches():
        if row["match_id"] != match_id:
            continue
        submissions = await database.load_submissions(match_id)
        return _hydrate_report_match_from_row(row, submissions)

    raise HTTPException(status_code=404, detail="Match not found")


def _player_code_export_bundle_exists(match_id: str) -> bool:
    try:
        return get_player_code_export_path(match_id).exists()
    except ValueError:
        return False


CONTAINER_RESTART_POLICY = cast(Any, {"Name": "always"})


# ==================== Match State ====================

class MatchState:
    """Complete state for one match."""

    def __init__(self, match_id: str, config: MatchConfig):
        self.match_id = match_id
        self.config = config
        self.status = "initializing"  # initializing -> defense -> attack -> finished
        self.created_at = datetime.now()
        self.started_at: Optional[datetime] = None
        self.finished_at: Optional[datetime] = None
        self.defense_started_at: Optional[datetime] = None
        self.attack_started_at: Optional[datetime] = None

        # 组件
        self.flag_manager = FlagManager(scoring_config=config.scoring.model_dump())
        self.sla_checker = SLAChecker(
            check_interval=60,
            penalty_per_minute=abs(config.scoring.slaViolation),
        )
        self.scoring_engine = ScoringEngine(config.scoring.model_dump())
        self.agent_client: Optional[AgentClient] = AgentClient(
            llm_api_key=config.llm.apiKey,
            llm_base_url=config.llm.baseUrl,
            llm_model=config.players[0].model or config.llm.model if config.players else config.llm.model,
            proxy_url=config.llm.proxy,
        )
        self.player_clients: Dict[int, Any] = {}
        self.player_backends: Dict[int, AgentBackendAdapter] = {}
        self._submission_lock: Optional[asyncio.Lock] = None

        # 选手状态
        self.players: Dict[int, PlayerState] = {}
        self.agent_sessions: Dict[int, AgentSession] = {}
        self.player_ssh_key_materials: Dict[int, PlayerSSHKeyMaterial] = {}

        # 后台任务
        self.flag_refresh_interval = config.flags.refreshInterval
        self._startup_task: Optional[asyncio.Task] = None
        self._flag_task: Optional[asyncio.Task] = None
        self._sla_task: Optional[asyncio.Task] = None
        self._match_timer_task: Optional[asyncio.Task] = None

        self.events: List[Dict] = []
        self.agent_logs: Dict[int, str] = {}
        self.player_read_tokens: Dict[int, str] = {}
        self.player_status_checkpoints: Dict[int, Dict[str, Any]] = {}
        self.player_status_checkpoint_locks: Dict[int, asyncio.Lock] = {}
        self.attack_targets_by_player: Dict[int, List[Dict[str, Any]]] = {}
        self.persisted_leaderboard: Dict[int, Dict] = {}
        self.persisted_submissions: List[Dict[str, Any]] = []
        self.player_code_export: Optional[Dict[str, Any]] = None
        self._player_code_export_lock: Optional[asyncio.Lock] = None
        self.resources_destroyed = False
        self._destroy_task: Optional[asyncio.Task] = None
        self.werewolf_state: Optional[WerewolfGameState] = None

    def submission_lock(self) -> asyncio.Lock:
        if self._submission_lock is None:
            self._submission_lock = asyncio.Lock()
        return self._submission_lock

    def player_code_export_lock(self) -> asyncio.Lock:
        if self._player_code_export_lock is None:
            self._player_code_export_lock = asyncio.Lock()
        return self._player_code_export_lock

    def add_event(self, event_type: str, data: dict):
        """记录比赛事件并异步持久化"""
        now = datetime.now()
        public_data = sanitize_public_payload(data)
        event = self._record_event(event_type, public_data, now)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._persist_event_background(event_type, public_data, now))
        except RuntimeError:
            pass

        return event

    async def _persist_event_background(self, event_type: str, data: dict, timestamp: datetime):
        try:
            await database.save_event(self.match_id, event_type, data, timestamp)
        except Exception as exc:
            logger.warning(
                f"[{self.match_id}] background event persistence failed for {event_type}: {exc}"
            )

    async def add_event_and_persist(self, event_type: str, data: dict):
        now = datetime.now()
        public_data = sanitize_public_payload(data)
        event = self._record_event(event_type, public_data, now)
        await database.save_event(self.match_id, event_type, public_data, now)
        return event

    def _record_event(self, event_type: str, data: dict, now: datetime):
        event = {
            "type": event_type,
            "data": data,
            "timestamp": now.isoformat(),
            "match_id": self.match_id,
        }
        self.events.append(event)
        # Cap in-memory events to prevent unbounded memory growth in long matches.
        # Events are already persisted to the database, so dropping old ones is safe.
        if len(self.events) > 5000:
            del self.events[:500]
        leaderboard = data.get("leaderboard") if isinstance(data, dict) else None
        if isinstance(leaderboard, dict) and leaderboard:
            existing_values = [entry for entry in self.persisted_leaderboard.values() if isinstance(entry, dict)]
            incoming_values = [entry for entry in leaderboard.values() if isinstance(entry, dict)]
            existing_has_non_zero = any((entry.get("total_score") or 0) != 0 for entry in existing_values)
            incoming_has_non_zero = any((entry.get("total_score") or 0) != 0 for entry in incoming_values)
            if incoming_has_non_zero or not existing_has_non_zero:
                self.persisted_leaderboard = leaderboard
        logger.info(f"[{self.match_id}] Event: {event_type} - {json.dumps(data, default=str)[:200]}")

        return event


@dataclass
class AgentInitializationResult:
    player_id: int
    success: bool
    reason: Optional[str] = None
    details: Optional[str] = None
    client: Optional[Any] = None


class TargetSSHProbeError(RuntimeError):
    def __init__(self, reason: str, details: str):
        super().__init__(f"{reason}: {details}")
        self.reason = reason
        self.details = details


@dataclass
class PlayerTokenContext:
    match_id: str
    player_id: int


@dataclass
class PlayerSSHKeyMaterial:
    player_id: int
    private_key: str
    public_key: str
    private_key_path: str = "/home/node/.ssh/awd_target_key"
    helper_path: Optional[str] = None
    key_type: str = "ed25519"
    owner_user: str = "node"
    owner_group: str = "node"


# ==================== Referee Engine ====================

class RefereeEngine:
    """裁判引擎主类"""

    def __init__(self):
        self.matches: Dict[str, MatchState] = {}
        self.player_match_index: Dict[int, str] = {}  # player_id -> match_id
        self.player_read_token_store = PlayerReadTokenStore()
        self.player_token_index = self.player_read_token_store.index
        self.loop_runtime_configs: Dict[str, Dict[str, Any]] = {}
        self.ws_connections: List[WebSocket] = []
        self.ws_subscriptions: Dict[WebSocket, str] = {}
        self.ws_ticket_store = WebSocketTicketStore()
        self.commentator = CommentatorService.from_env(logger=logger)
        self._ws_heartbeat_task: Optional[asyncio.Task] = None

    def check_ws_ticket_rate_limit(self, *, client_host: Optional[str] = None, now: Optional[float] = None) -> tuple[bool, int]:
        return self.ws_ticket_store.check_rate_limit(client_host=client_host, now=now)

    def issue_ws_ticket(self, *, client_host: Optional[str] = None, user_agent: Optional[str] = None) -> Dict[str, Any]:
        return self.ws_ticket_store.issue(client_host=client_host, user_agent=user_agent)

    def _prune_ws_tickets(self, now: Optional[float] = None) -> None:
        self.ws_ticket_store.prune(now)

    def consume_ws_ticket(
        self,
        ticket: Optional[str],
        *,
        client_host: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> bool:
        return self.ws_ticket_store.consume(ticket, client_host=client_host, user_agent=user_agent)

    @staticmethod
    def _build_readiness_details(player: PlayerState, session: Optional[AgentSession] = None) -> Dict[str, Any]:
        existing = dict(player.readiness_details or {})
        existing.setdefault("runtime_ready", False)
        existing.setdefault("session_ready", False)
        existing.setdefault("interactive_ready", False)
        existing.setdefault("init_ready", False)
        existing.setdefault("session_id", None)
        if session is None:
            return existing

        existing.update({
            "runtime_ready": bool(session.runtime_ready),
            "session_ready": bool(session.session_ready),
            "interactive_ready": bool(session.interactive_ready),
            "init_ready": bool(session.init_ready),
            "session_id": session.session_id,
        })
        return existing

    def _sync_player_readiness_details(self, match: MatchState, player_id: int) -> Dict[str, Any]:
        player = match.players.get(player_id)
        if player is None:
            return {}
        session = match.agent_sessions.get(player_id)
        player.readiness_details = self._build_readiness_details(player, session)
        return dict(player.readiness_details)

    async def _mark_player_readiness_layer(
        self,
        match: MatchState,
        player_id: int,
        *,
        phase: str,
        layer: str,
        enabled: bool,
        reason: str,
        details: Optional[str] = None,
        readiness_details: Optional[Dict[str, Any]] = None,
        previous_value: Any = _READINESS_PREVIOUS_UNSET,
        force_emit: bool = False,
    ) -> bool:
        player = match.players.get(player_id)
        if player is None:
            return False

        current_readiness_details = dict(readiness_details or player.readiness_details or self._build_readiness_details(player))
        prior_value = (
            current_readiness_details.get(layer)
            if previous_value is _READINESS_PREVIOUS_UNSET
            else previous_value
        )
        if prior_value is enabled and not force_emit:
            return False

        current_readiness_details[layer] = enabled
        player.readiness_details = current_readiness_details

        payload = {
            "player_id": player_id,
            "phase": phase,
            "layer": layer,
            "enabled": enabled,
            "reason": reason,
            "readiness_details": dict(current_readiness_details),
        }
        if prior_value is not None:
            payload["previous_value"] = prior_value
        if details:
            payload["details"] = details

        match.add_event("AGENT_READINESS_LAYER", payload)
        await self.broadcast({
            "type": "AGENT_READINESS_LAYER",
            "match_id": match.match_id,
            **payload,
        })
        logger.info(
            f"[Player {player_id}] readiness layer updated: {layer}={enabled} via {reason}"
            + (f": {details}" if details else "")
        )
        return True

    @staticmethod
    def _readiness_layer_metadata_changed(
        previous_details: Dict[str, Any],
        current_details: Dict[str, Any],
        layer: str,
    ) -> bool:
        if layer == "session_ready":
            return previous_details.get("session_id") != current_details.get("session_id")
        return False

    async def _sync_and_emit_readiness_layers(
        self,
        match: MatchState,
        player_id: int,
        *,
        phase: str,
        reason: str,
        details: Optional[str] = None,
    ) -> None:
        player = match.players.get(player_id)
        session = match.agent_sessions.get(player_id)
        if player is None or session is None:
            return

        previous_details = dict(player.readiness_details or {})
        current_details = self._sync_player_readiness_details(match, player_id)
        for layer in ("runtime_ready", "session_ready", "interactive_ready", "init_ready"):
            previous_value = bool(previous_details.get(layer))
            current_value = bool(current_details.get(layer))
            metadata_changed = current_value and self._readiness_layer_metadata_changed(
                previous_details,
                current_details,
                layer,
            )
            if (current_value and not previous_value) or metadata_changed:
                await self._mark_player_readiness_layer(
                    match,
                    player_id,
                    phase=phase,
                    layer=layer,
                    enabled=True,
                    reason=reason,
                    details=details,
                    readiness_details=current_details,
                    previous_value=previous_value,
                    force_emit=metadata_changed,
                )

    def _normalize_loop_config(self, config: MatchConfig) -> MatchConfig:
        loop_cfg = config.loop
        if config.mode == "werewolf" and loop_cfg.enabled:
            logger.warning("Loop mode is disabled for werewolf matches in the first implementation")
            config.loop = LoopMatchConfig(enabled=False, repeatCount=1, currentIteration=1)
            return config
        repeat_count = max(1, int(loop_cfg.repeatCount or 1))
        enabled = repeat_count > 1 or bool(loop_cfg.enabled)
        current_iteration = max(1, int(loop_cfg.currentIteration or 1))

        if not enabled:
            config.loop = LoopMatchConfig(enabled=False, repeatCount=1, currentIteration=1)
            return config

        if current_iteration > repeat_count:
            current_iteration = repeat_count

        config.loop = LoopMatchConfig(
            enabled=True,
            repeatCount=repeat_count,
            loopId=loop_cfg.loopId,
            currentIteration=current_iteration,
        )
        return config

    async def _ensure_loop_record(self, config: MatchConfig) -> Optional[Dict[str, Any]]:
        config = self._normalize_loop_config(config)
        if not config.loop.enabled:
            return None

        loop_cfg = config.loop
        loop_id = loop_cfg.loopId or f"loop_{uuid.uuid4().hex[:10]}"
        config.loop.loopId = loop_id
        existing = await database.get_loop(loop_id)
        if existing is not None:
            self.loop_runtime_configs.setdefault(loop_id, config.model_dump())
            return existing

        now = datetime.now()
        base_config = config.model_dump()
        base_config.setdefault("loop", {})
        base_config["loop"].update({
            "enabled": True,
            "repeatCount": loop_cfg.repeatCount,
            "loopId": loop_id,
            "currentIteration": 1,
        })
        await database.save_loop(
            loop_id=loop_id,
            status="running",
            repeat_count=loop_cfg.repeatCount,
            current_iteration=1,
            config_dict=base_config,
            created_at=now,
            updated_at=now,
        )
        self.loop_runtime_configs[loop_id] = base_config
        return await database.get_loop(loop_id)

    async def _build_next_loop_config(self, loop_state: Dict[str, Any], next_iteration: int) -> MatchConfig:
        runtime_payload = self.loop_runtime_configs.get(loop_state["loop_id"])
        next_payload = copy.deepcopy(runtime_payload if isinstance(runtime_payload, dict) else loop_state["config"])
        next_payload.setdefault("loop", {})
        next_payload["loop"].update({
            "enabled": True,
            "repeatCount": loop_state["repeat_count"],
            "loopId": loop_state["loop_id"],
            "currentIteration": next_iteration,
        })
        return self._normalize_loop_config(MatchConfig(**next_payload))

    async def _update_loop_after_match_cleanup(self, match: MatchState) -> None:
        loop_cfg = self._normalize_loop_config(match.config).loop
        if not loop_cfg.enabled or not loop_cfg.loopId:
            return

        loop_state = await database.get_loop(loop_cfg.loopId)
        if loop_state is None:
            return

        now = datetime.now()
        if loop_state["status"] == "stopped":
            await database.save_loop(
                loop_id=loop_state["loop_id"],
                status="stopped",
                repeat_count=loop_state["repeat_count"],
                current_iteration=max(loop_state["current_iteration"], loop_cfg.currentIteration),
                current_match_id=None,
                last_match_id=match.match_id,
                config_dict=self.loop_runtime_configs.get(loop_state["loop_id"], loop_state["config"]),
                created_at=datetime.fromisoformat(loop_state["created_at"]),
                updated_at=now,
                stopped_at=datetime.fromisoformat(loop_state["stopped_at"]) if loop_state.get("stopped_at") else now,
            )
            await self.broadcast({
                "type": "LOOP_MATCH_STOPPED",
                "loop_id": loop_state["loop_id"],
                "match_id": match.match_id,
                "current_iteration": max(loop_state["current_iteration"], loop_cfg.currentIteration),
                "repeat_count": loop_state["repeat_count"],
            })
            return

        if loop_cfg.currentIteration >= loop_cfg.repeatCount:
            await database.save_loop(
                loop_id=loop_state["loop_id"],
                status="completed",
                repeat_count=loop_state["repeat_count"],
                current_iteration=loop_cfg.currentIteration,
                current_match_id=None,
                last_match_id=match.match_id,
                config_dict=self.loop_runtime_configs.get(loop_state["loop_id"], loop_state["config"]),
                created_at=datetime.fromisoformat(loop_state["created_at"]),
                updated_at=now,
                stopped_at=None,
            )
            await match.add_event_and_persist("LOOP_MATCH_COMPLETED", {
                "loop_id": loop_state["loop_id"],
                "current_iteration": loop_cfg.currentIteration,
                "repeat_count": loop_cfg.repeatCount,
            })
            await self.broadcast({
                "type": "LOOP_MATCH_COMPLETED",
                "loop_id": loop_state["loop_id"],
                "match_id": match.match_id,
                "current_iteration": loop_cfg.currentIteration,
                "repeat_count": loop_cfg.repeatCount,
            })
            return

        next_iteration = loop_cfg.currentIteration + 1
        next_config = await self._build_next_loop_config(loop_state, next_iteration)
        next_result = await self.start_match(next_config)

        await database.save_loop(
            loop_id=loop_state["loop_id"],
            status="running",
            repeat_count=loop_state["repeat_count"],
            current_iteration=next_iteration,
            current_match_id=next_result["match_id"],
            last_match_id=match.match_id,
            config_dict=self.loop_runtime_configs.get(loop_state["loop_id"], loop_state["config"]),
            created_at=datetime.fromisoformat(loop_state["created_at"]),
            updated_at=now,
            stopped_at=None,
        )
        await match.add_event_and_persist("LOOP_MATCH_NEXT_STARTED", {
            "loop_id": loop_state["loop_id"],
            "next_match_id": next_result["match_id"],
            "current_iteration": next_iteration,
            "repeat_count": loop_state["repeat_count"],
        })
        await self.broadcast({
            "type": "LOOP_MATCH_NEXT_STARTED",
            "loop_id": loop_state["loop_id"],
            "previous_match_id": match.match_id,
            "match_id": next_result["match_id"],
            "current_iteration": next_iteration,
            "repeat_count": loop_state["repeat_count"],
        })

    async def stop_loop(self, loop_id: str) -> Dict[str, Any]:
        loop_state = await database.get_loop(loop_id)
        if loop_state is None:
            raise HTTPException(status_code=404, detail="Loop not found")

        if loop_state["status"] in {"completed", "stopped"}:
            return {
                "loop_id": loop_id,
                "status": loop_state["status"],
                "current_iteration": loop_state["current_iteration"],
                "repeat_count": loop_state["repeat_count"],
            }

        stopped_at = datetime.now()
        await database.save_loop(
            loop_id=loop_state["loop_id"],
            status="stopped",
            repeat_count=loop_state["repeat_count"],
            current_iteration=loop_state["current_iteration"],
            current_match_id=loop_state.get("current_match_id"),
            last_match_id=loop_state.get("last_match_id"),
            config_dict=self.loop_runtime_configs.get(loop_id, loop_state["config"]),
            created_at=datetime.fromisoformat(loop_state["created_at"]),
            updated_at=stopped_at,
            stopped_at=stopped_at,
        )
        await self.broadcast({
            "type": "LOOP_MATCH_STOP_REQUESTED",
            "loop_id": loop_id,
            "current_match_id": loop_state.get("current_match_id"),
            "current_iteration": loop_state["current_iteration"],
            "repeat_count": loop_state["repeat_count"],
        })
        return {
            "loop_id": loop_id,
            "status": "stopped",
            "current_iteration": loop_state["current_iteration"],
            "repeat_count": loop_state["repeat_count"],
        }

    async def list_loops(self) -> Dict[str, Any]:
        loops = await database.list_loops()
        db_match_rows = await database.list_matches_summary()
        db_match_map = {row["match_id"]: row for row in db_match_rows}
        items: List[Dict[str, Any]] = []

        for loop_state in loops:
            current_match_id = loop_state.get("current_match_id")
            last_match_id = loop_state.get("last_match_id")
            current_match = self.matches.get(current_match_id) if current_match_id else None
            current_match_row = db_match_map.get(current_match_id) if current_match_id else None
            last_match_row = db_match_map.get(last_match_id) if last_match_id else None
            config = loop_state.get("config") or {}
            match_cfg = config.get("match") or {}
            public_config = sanitize_public_payload(config)
            completed_runs = loop_state["current_iteration"]
            if loop_state["status"] == "running" and current_match_id:
                completed_runs = max(0, loop_state["current_iteration"] - 1)
            if loop_state["status"] == "stopped" and current_match_id:
                completed_runs = max(0, loop_state["current_iteration"] - 1)

            items.append({
                "loop_id": loop_state["loop_id"],
                "status": loop_state["status"],
                "name": match_cfg.get("name") or loop_state["loop_id"],
                "mode": config.get("mode") or "awd",
                "repeat_count": loop_state["repeat_count"],
                "current_iteration": loop_state["current_iteration"],
                "completed_runs": completed_runs,
                "current_match_id": current_match_id,
                "current_match_status": current_match.status if current_match else current_match_row.get("status") if current_match_row else None,
                "last_match_id": last_match_id,
                "last_match_status": last_match_row.get("status") if last_match_row else None,
                "created_at": loop_state["created_at"],
                "updated_at": loop_state["updated_at"],
                "stopped_at": loop_state.get("stopped_at"),
                "match": match_cfg,
                "config": public_config,
            })

        return {"loops": items}

    def _issue_player_read_token(self, match: MatchState, player_id: int) -> str:
        return self.player_read_token_store.issue(match, player_id)

    def _revoke_player_read_token(self, match: MatchState, player_id: int) -> None:
        self.player_read_token_store.revoke(match, player_id)

    async def _generate_player_ssh_keypair(self, match_id: str, player_id: int) -> PlayerSSHKeyMaterial:
        loop = asyncio.get_running_loop()

        def _generate() -> PlayerSSHKeyMaterial:
            comment = f"awd:{match_id}:{player_id}"
            with tempfile.TemporaryDirectory(prefix=f"awd_ssh_{match_id}_{player_id}_") as temp_dir:
                private_key_file = os.path.join(temp_dir, "awd_target_key")
                try:
                    subprocess.run(
                        [
                            "ssh-keygen",
                            "-q",
                            "-t",
                            "ed25519",
                            "-N",
                            "",
                            "-C",
                            comment,
                            "-f",
                            private_key_file,
                        ],
                        check=True,
                        capture_output=True,
                        text=True,
                    )
                except subprocess.CalledProcessError as exc:
                    stderr = (exc.stderr or exc.stdout or str(exc)).strip()
                    raise RuntimeError(
                        f"ssh-keygen failed for player {player_id}: {stderr or 'unknown error'}"
                    ) from exc
                except FileNotFoundError as exc:
                    raise RuntimeError("ssh-keygen is not available in referee runtime") from exc

                with open(private_key_file, "r", encoding="utf-8") as private_fp:
                    private_key = private_fp.read()
                with open(f"{private_key_file}.pub", "r", encoding="utf-8") as public_fp:
                    public_key = public_fp.read()

            return PlayerSSHKeyMaterial(
                player_id=player_id,
                private_key=private_key,
                public_key=public_key,
            )

        return await loop.run_in_executor(None, _generate)

    async def _docker_exec(
        self,
        container_name: str,
        command: List[str],
        *,
        timeout: int = 30,
        user: Optional[str] = None,
        stdin_text: Optional[str] = None,
    ) -> str:
        _, stdout_text, _ = await _docker_exec_shared(
            container_name,
            command,
            timeout=timeout,
            user=user,
            stdin_text=stdin_text,
        )
        return stdout_text

    @staticmethod
    def _build_target_ssh_helper(
        target_ip: str,
        ssh_key_material: PlayerSSHKeyMaterial,
        maintenance_username: str,
    ) -> str:
        return build_target_ssh_helper(
            target_ip,
            ssh_key_material,
            maintenance_username,
            connect_timeout=TARGET_SSH_CONNECT_TIMEOUT,
        )

    async def _install_agent_target_ssh(
        self,
        player_id: int,
        agent_container: str,
        target_ip: str,
        ssh_key_material: PlayerSSHKeyMaterial,
        *,
        maintenance_username: str = "defender",
    ) -> None:
        ssh_dir = os.path.dirname(ssh_key_material.private_key_path)
        helper_path = ssh_key_material.helper_path or "/usr/local/bin/target-ssh"
        helper_script = self._build_target_ssh_helper(target_ip, ssh_key_material, maintenance_username)
        owner_user = ssh_key_material.owner_user or "node"
        owner_group = ssh_key_material.owner_group or owner_user
        for label, value in {
            "private_key_path": ssh_key_material.private_key_path,
            "ssh_dir": ssh_dir,
            "helper_path": helper_path,
        }.items():
            validate_container_absolute_path(value, label=label)
        for label, value in {"owner_user": owner_user, "owner_group": owner_group}.items():
            validate_container_account(value, label=label)

        quoted_ssh_dir = shlex.quote(ssh_dir)
        quoted_private_key_path = shlex.quote(ssh_key_material.private_key_path)
        quoted_helper_path = shlex.quote(helper_path)

        await self._docker_exec(
            agent_container,
            [
                "sh",
                "-lc",
                (
                    f"mkdir -p {quoted_ssh_dir} && "
                    f"chmod 700 {quoted_ssh_dir} && "
                    f"cat > {quoted_private_key_path} && "
                    f"chmod 600 {quoted_private_key_path}"
                ),
            ],
            timeout=TARGET_SSH_INSTALL_TIMEOUT,
            user=owner_user,
            stdin_text=ssh_key_material.private_key,
        )

        await self._docker_exec(
            agent_container,
            [
                "sh",
                "-lc",
                f"cat > {quoted_helper_path} && chmod 755 {quoted_helper_path}",
            ],
            timeout=TARGET_SSH_INSTALL_TIMEOUT,
            user="root",
            stdin_text=helper_script,
        )

        ssh_key_material.helper_path = helper_path

    @staticmethod
    def _classify_target_ssh_probe_failure(error: BaseException) -> tuple[str, str]:
        return classify_target_ssh_probe_failure(error)

    async def _verify_agent_target_ssh(
        self,
        player_id: int,
        agent_container: str,
        helper_path: str,
        *,
        retries: int = TARGET_SSH_PROBE_RETRIES,
        delay_seconds: int = TARGET_SSH_PROBE_RETRY_DELAY,
    ) -> None:
        last_reason = "TARGET_SSH_PROBE_FAILED"
        last_details = "target-ssh probe did not run"

        for attempt in range(retries):
            try:
                result = await self._docker_exec(
                    agent_container,
                    ["sh", "-lc", f"{shlex.quote(helper_path)} 'echo ready'"],
                    timeout=TARGET_SSH_PROBE_TIMEOUT,
                )
                if result.strip() == "ready":
                    logger.info(f"[Player {player_id}] Agent target SSH ready")
                    return
                last_reason = "TARGET_SSH_UNEXPECTED_OUTPUT"
                last_details = (
                    "target-ssh probe returned unexpected output: "
                    f"{result.strip() or '<empty>'}"
                )
            except Exception as exc:
                last_reason, last_details = self._classify_target_ssh_probe_failure(exc)

            if attempt < retries - 1:
                await asyncio.sleep(delay_seconds)

        raise TargetSSHProbeError(last_reason, last_details)

    @staticmethod
    def _get_remaining_seconds(match: MatchState, now: datetime) -> int:
        elapsed = 0
        if match.started_at:
            elapsed = (now - match.started_at).total_seconds()

        if match.status == "defense" and match.defense_started_at:
            return int(max(
                0,
                match.config.match.phases.defense - (now - match.defense_started_at).total_seconds(),
            ))
        if match.status == "attack" and match.attack_started_at:
            return int(max(
                0,
                match.config.match.phases.attack - (now - match.attack_started_at).total_seconds(),
            ))
        if match.status == "finished":
            return 0
        return int(max(0, match.config.match.duration - elapsed))

    @staticmethod
    def _leaderboard_has_non_zero_scores(leaderboard: Dict[Any, Dict]) -> bool:
        return leaderboard_has_non_zero_scores(leaderboard)

    @staticmethod
    def _apply_leaderboard_snapshot(match: MatchState, leaderboard: Dict[Any, Dict]) -> None:
        apply_leaderboard_snapshot(match, leaderboard)

    @classmethod
    def _restore_scores_from_persisted_state(cls, match: MatchState) -> Dict[int, Dict]:
        return restore_scores_from_persisted_state(match)

    @staticmethod
    def _get_player_client(match: MatchState, player_id: int) -> Optional[Any]:
        return match.player_clients.get(player_id)

    @staticmethod
    def _get_player_backend(match: MatchState, player_id: int) -> Optional[AgentBackendAdapter]:
        backend = match.player_backends.get(player_id)
        if backend is not None:
            return backend

        player_cfg = next((cfg for cfg in match.config.players if cfg.id == player_id), None)
        if player_cfg is None:
            return None

        try:
            return backend_registry.get(player_cfg.backend_type)
        except Exception:
            return None

    async def _mark_player_ready(
        self,
        match: MatchState,
        player_id: int,
        *,
        phase: str,
        reason: str,
        details: Optional[str] = None,
    ) -> bool:
        player = match.players.get(player_id)
        if player is None:
            return False

        previous_ready_status = player.ready_status
        previous_ready_reason = player.ready_reason
        if previous_ready_status == "AGENT_READY":
            return False

        player.ready_status = "AGENT_READY"
        player.ready_reason = reason or "READY_UNKNOWN"

        session = match.agent_sessions.get(player_id)
        if session is not None:
            session.ready = True
            session.interactive_ready = True
            session.init_ready = session.init_ready or phase == "defense"
        readiness_details = self._sync_player_readiness_details(match, player_id)

        payload = {
            "player_id": player_id,
            "phase": phase,
            "ready_status": player.ready_status,
            "ready_reason": player.ready_reason,
            "readiness_details": readiness_details,
        }
        if previous_ready_status:
            payload["previous_ready_status"] = previous_ready_status
        if previous_ready_reason:
            payload["previous_ready_reason"] = previous_ready_reason
        if details:
            payload["details"] = details

        match.add_event("AGENT_READY", payload)
        await self.broadcast({
            "type": "AGENT_READY",
            "match_id": match.match_id,
            **payload,
        })
        logger.info(
            f"[Player {player_id}] AGENT_READY via {reason}"
            + (f": {details}" if details else "")
        )
        return True

    @staticmethod
    def _get_not_ready_player_ids(match: MatchState) -> List[int]:
        return [
            player_id
            for player_id, player in match.players.items()
            if player.ready_status != "AGENT_READY"
        ]

    @staticmethod
    def _count_ready_players(match: MatchState) -> int:
        return len(match.players) - len(RefereeEngine._get_not_ready_player_ids(match))

    async def _apply_agent_initialization_results(
        self,
        match: MatchState,
        results: List[Any],
    ) -> int:
        ready_count = 0
        for result in results:
            if isinstance(result, BaseException):
                logger.exception(f"[{match.match_id}] Unexpected agent initialization error", exc_info=result)
                continue

            pid = result.player_id
            if result.client is not None:
                match.player_clients[pid] = result.client
                session = match.agent_sessions.get(pid)
                if session is not None:
                    session.runtime_ready = True
                    await self._sync_and_emit_readiness_layers(
                        match,
                        pid,
                        phase="defense",
                        reason="RUNTIME_CLIENT_READY",
                        details="Backend client retained for this player runtime",
                    )

            if result.success and result.client is not None:
                backend = self._get_player_backend(match, pid)
                match.agent_sessions[pid].last_activity_at = asyncio.get_running_loop().time()
                if backend is not None:
                    await backend.observe_session_activity(result.client, match.agent_sessions[pid])
                    await backend.observe_code_activity(result.client, match.agent_sessions[pid])
                await self._sync_and_emit_readiness_layers(
                    match,
                    pid,
                    phase="defense",
                    reason=result.reason or "READY_UNKNOWN",
                    details=result.details,
                )
                await self._mark_player_ready(
                    match,
                    pid,
                    phase="defense",
                    reason=result.reason or "READY_UNKNOWN",
                )
                ready_count += 1
            else:
                match.players[pid].ready_status = "AGENT_NOT_READY"
                match.players[pid].ready_reason = result.reason or "UNKNOWN_INIT_FAILURE"
                readiness_details = self._sync_player_readiness_details(match, pid)
                error_payload = {
                    "player_id": pid,
                    "ready_status": match.players[pid].ready_status,
                    "ready_reason": match.players[pid].ready_reason,
                    "readiness_details": readiness_details,
                    "reason": match.players[pid].ready_reason,
                    "details": result.details or "No initialization details captured",
                }
                match.add_event("AGENT_NOT_READY", error_payload)
                logger.warning(
                    f"[Player {pid}] AGENT_NOT_READY: {error_payload['reason']} - {error_payload['details']}"
                )
                if result.client is not None:
                    logger.info(
                        f"[Player {pid}] Retaining player client for runtime READY re-evaluation"
                    )

        return ready_count

    async def _retry_not_ready_agents(self, match: MatchState, player_ids: List[int]) -> int:
        retry_tasks: List[asyncio.Task] = []
        for pid in player_ids:
            session = match.agent_sessions.get(pid)
            if session is None or session.ready or session.is_busy:
                continue

            session.init_error_reason = None
            session.init_error_details = None
            retry_tasks.append(asyncio.create_task(self._initialize_single_agent(match, pid, session)))

        if not retry_tasks:
            return 0

        results = await asyncio.gather(*retry_tasks, return_exceptions=True)
        return await self._apply_agent_initialization_results(match, results)

    async def _wait_for_all_players_ready(self, match: MatchState) -> None:
        retry_deadline = asyncio.get_running_loop().time() + min(
            AGENT_READY_MAX_WAIT,
            AGENT_INIT_RETRY_MAX_WAIT,
            max(30, match.config.match.phases.defense),
        )

        while True:
            pending_player_ids = self._get_not_ready_player_ids(match)
            if not pending_player_ids:
                return

            if asyncio.get_running_loop().time() >= retry_deadline:
                logger.warning(
                    f"[{match.match_id}] Continuing after bounded init retries; "
                    f"pending_players={pending_player_ids}"
                )
                return

            logger.warning(
                f"[{match.match_id}] Retrying agent initialization before defense starts; "
                f"pending_players={pending_player_ids}"
            )
            ready_count = await self._retry_not_ready_agents(match, pending_player_ids)
            if ready_count > 0:
                continue
            await asyncio.sleep(AGENT_READY_RETRY_DELAY)

    @staticmethod
    def _normalize_player_label_value(value: Optional[str]) -> Optional[str]:
        return normalize_player_label_value(value)

    @staticmethod
    def _build_player_identity_fields(match: MatchState, player_id: int) -> Dict[str, Optional[str]]:
        return build_player_identity_fields(match, player_id)

    @staticmethod
    def _enrich_leaderboard(match: MatchState, leaderboard: Dict[int, Dict]) -> Dict[int, Dict]:
        return enrich_leaderboard(match, leaderboard)

    @staticmethod
    def _get_match_leaderboard(match: MatchState) -> Dict[int, Dict]:
        if match.config.mode == "werewolf" and match.persisted_leaderboard:
            return RefereeEngine._enrich_leaderboard(match, match.persisted_leaderboard)
        leaderboard = match.scoring_engine.get_leaderboard(match.players)
        if match.status == "finished" and match.persisted_leaderboard:
            computed_has_non_zero = RefereeEngine._leaderboard_has_non_zero_scores(leaderboard)
            persisted_has_non_zero = RefereeEngine._leaderboard_has_non_zero_scores(match.persisted_leaderboard)
            if persisted_has_non_zero and not computed_has_non_zero:
                leaderboard = RefereeEngine._restore_scores_from_persisted_state(match)
                if RefereeEngine._leaderboard_has_non_zero_scores(leaderboard):
                    return RefereeEngine._enrich_leaderboard(match, leaderboard)
                return RefereeEngine._enrich_leaderboard(match, match.persisted_leaderboard)
        return RefereeEngine._enrich_leaderboard(match, leaderboard)

    @staticmethod
    def _build_leaderboard_summary(leaderboard: Dict[int, Dict], player_id: int) -> Dict[str, Any]:
        try:
            return build_leaderboard_summary(leaderboard, player_id)
        except PlayerNotInLeaderboardError:
            raise HTTPException(status_code=404, detail="Player not found in leaderboard") from None

    @staticmethod
    def _snapshot_player_scores(match: MatchState) -> Dict[int, Dict[str, int]]:
        return snapshot_player_scores(match)

    @staticmethod
    def _build_score_changes_since_last_query(
        match: MatchState,
        viewer_player_id: int,
        now: datetime,
        current_scores: Dict[int, Dict[str, int]],
    ) -> Dict[str, Any]:
        return build_score_changes_since_last_query(
            match.player_status_checkpoints.get(viewer_player_id),
            viewer_player_id,
            now,
            current_scores,
        )

    def _build_player_status_payload(
        self,
        match: MatchState,
        player_id: int,
        now: datetime,
        leaderboard: Dict[int, Dict],
        score_changes: Dict[str, Any],
    ) -> Dict[str, Any]:
        player = match.players[player_id]
        readiness_details = self._sync_player_readiness_details(match, player_id)
        attack_context = None
        if match.status == "attack":
            attack_context = {
                "enemy_targets": list(match.attack_targets_by_player.get(player_id, [])),
            }

        return {
            "schema_version": 2,
            "match_id": match.match_id,
            "phase": match.status,
            "server_time": now.isoformat(),
            "remaining_seconds": self._get_remaining_seconds(match, now),
            "poll_after_seconds": 30 if match.status == "attack" else 60,
            "can_submit_flags": match.status == "attack",
            "flag_refresh_interval": match.flag_refresh_interval,
            "self": {
                "player_id": player.player_id,
                **self._build_player_identity_fields(match, player.player_id),
                "ready_status": player.ready_status,
                "ready_reason": player.ready_reason,
                "readiness_details": readiness_details,
                "score": player.score,
                "attack_score": player.attack_score,
                "defense_score": player.defense_score,
                "sla_score": player.sla_score,
                "sla_up": player.sla_up,
                "sla_down_minutes": player.sla_down_minutes,
                "flags_captured": player.flags_captured,
                "flags_lost": player.flags_lost,
            },
            "leaderboard_summary": self._build_leaderboard_summary(leaderboard, player_id),
            "score_changes_since_last_query": score_changes,
            "attack_context": attack_context,
        }

    async def _build_player_status_view(
        self,
        match_id: str,
        player_id: int,
        *,
        update_checkpoint: bool,
    ) -> Dict[str, Any]:
        match = self.matches.get(match_id)
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")
        if player_id not in match.players:
            raise HTTPException(status_code=404, detail="Player not found")

        checkpoint_lock = match.player_status_checkpoint_locks.get(player_id)
        if checkpoint_lock is None:
            checkpoint_lock = asyncio.Lock()
            match.player_status_checkpoint_locks[player_id] = checkpoint_lock

        async with checkpoint_lock:
            now = datetime.now()
            leaderboard = self._get_match_leaderboard(match)
            current_scores = self._snapshot_player_scores(match)
            score_changes = self._build_score_changes_since_last_query(
                match,
                player_id,
                now,
                current_scores,
            )
            payload = self._build_player_status_payload(
                match,
                player_id,
                now,
                leaderboard,
                score_changes,
            )

            if update_checkpoint:
                match.player_status_checkpoints[player_id] = {
                    "queried_at": score_changes["current_query_at"],
                    "scores_by_player": current_scores,
                }

            return payload

    async def build_player_status(self, match_id: str, player_id: int) -> Dict[str, Any]:
        return await self._build_player_status_view(match_id, player_id, update_checkpoint=True)

    @staticmethod
    def _build_submission_feedback(result: Dict[str, Any]) -> Dict[str, Any]:
        return build_submission_feedback(result)

    async def validate_docker_api_compatibility(self) -> None:
        # Use the docker Python SDK (which talks to /var/run/docker.sock directly) instead of
        # shelling out to the docker CLI binary. This way we don't need the docker CLI
        # installed in the referee container.
        try:
            client = await asyncio.to_thread(docker.from_env)
            version_info = await asyncio.to_thread(client.version)
        except Exception as exc:
            raise RuntimeError(f"Docker API compatibility check failed: {exc}") from exc

        # Extract API versions. version_info is a dict from the daemon.
        server_api = str(version_info.get("ApiVersion", "")).strip()
        server_min_api = str(version_info.get("MinAPIVersion", "")).strip()
        # The SDK negotiates its own API version; if we can talk to the daemon, we are
        # compatible by definition. Log the values for visibility.
        try:
            client_api = client.api._version  # type: ignore[attr-defined]
        except Exception:
            client_api = "unknown"

        logger.info(
            "Docker API compatibility check passed: "
            f"client={client_api}, server_min={server_min_api}, server={server_api}"
        )

    async def create_match(self, config: MatchConfig) -> str:
        """Create a match without starting containers."""
        config = self._normalize_loop_config(config)
        match_id = f"match_{int(time.time())}_{secrets.token_hex(4)}"
        match = MatchState(match_id, config)
        self.matches[match_id] = match

        await database.save_match(
            match_id=match_id,
            status=match.status,
            config_dict=config.model_dump(),
            created_at=match.created_at
        )

        match.add_event("MATCH_CREATED", {
            "match_id": match_id,
            "mode": config.mode,
            "player_count": len(config.players),
            "duration": config.match.duration,
        })

        return match_id

    async def start_match(self, config: MatchConfig) -> Dict:
        """Create a match and start its lifecycle asynchronously."""
        config = self._normalize_loop_config(config)
        loop_state = await self._ensure_loop_record(config)
        match_id = await self.create_match(config)
        match = self.matches[match_id]
        if config.mode == "werewolf":
            match._startup_task = asyncio.create_task(self._run_werewolf_match_startup(match))
        else:
            match._startup_task = asyncio.create_task(self._run_match_startup(match))

        if loop_state is not None:
            await database.save_loop(
                loop_id=config.loop.loopId or loop_state["loop_id"],
                status="running",
                repeat_count=config.loop.repeatCount,
                current_iteration=config.loop.currentIteration,
                current_match_id=match_id,
                last_match_id=loop_state.get("last_match_id"),
                config_dict=self.loop_runtime_configs.get(loop_state["loop_id"], loop_state["config"]),
                created_at=datetime.fromisoformat(loop_state["created_at"]),
                updated_at=datetime.now(),
                stopped_at=None,
            )

        return {
            "match_id": match_id,
            "status": match.status,
            "loop_id": config.loop.loopId,
            "current_iteration": config.loop.currentIteration,
            "repeat_count": config.loop.repeatCount,
        }

    async def _run_match_startup(self, match: MatchState) -> None:
        """Run match startup in the background."""
        match_id = match.match_id

        try:
            await self.validate_docker_api_compatibility()

            # Step 1: 创建容器
            match.status = "creating_containers"
            await database.update_match_status(match_id, match.status)
            match.add_event("STATUS", {"status": "creating_containers"})
            await self.broadcast({"type": "STATUS", "match_id": match_id, "status": "creating_containers"})

            await self._setup_containers(match)

            for pid in match.players:
                self.player_match_index[pid] = match_id
                self._issue_player_read_token(match, pid)

            # Step 2: 配置 Agent + 发送提示词
            match.status = "initializing_agents"
            await database.update_match_status(match_id, match.status)
            match.add_event("STATUS", {"status": "initializing_agents"})
            await self.broadcast({"type": "STATUS", "match_id": match_id, "status": "initializing_agents"})

            ready_count = await self._initialize_agents(match)

            if ready_count < len(match.players):
                logger.warning(
                    f"[{match_id}] Only {ready_count}/{len(match.players)} agents ready"
                )
                await self._wait_for_all_players_ready(match)
                ready_count = self._count_ready_players(match)

            if ready_count == 0:
                error_msg = (
                    f"All {len(match.players)} agents failed to initialize. "
                    "Aborting match — a game with no functional agents is not playable."
                )
                logger.error(f"[{match_id}] {error_msg}")
                match.status = "error"
                match.player_ssh_key_materials = {}
                await database.update_match_status(match_id, match.status)
                await match.add_event_and_persist("MATCH_ERROR", {
                    "error": error_msg,
                    "ready_count": 0,
                    "total_players": len(match.players),
                })
                await self.broadcast({
                    "type": "MATCH_ERROR",
                    "match_id": match_id,
                    "error": error_msg,
                })
                if not match.resources_destroyed:
                    await self.destroy_match(match_id)
                return

            # Step 3: 首次 Flag 注入
            await match.flag_manager.generate_and_inject(match.players)
            match.add_event("FLAGS_INJECTED", {"round": 1})

            # Step 4: 启动比赛
            match.started_at = datetime.now()
            match.defense_started_at = match.started_at
            match.attack_started_at = None
            match.status = "defense"
            await database.update_match_status(match_id, match.status)
            match.add_event("MATCH_STARTED", {
                "status": "defense",
                "player_count": len(match.players),
                "defense_duration": match.config.match.phases.defense,
            })

            await self.broadcast({
                "type": "MATCH_STARTED",
                "match_id": match_id,
                "status": "defense",
                "player_count": len(match.players),
                "defense_duration": match.config.match.phases.defense,
            })

            # Step 5: 启动后台任务
            match._flag_task = asyncio.create_task(
                self._flag_refresh_loop(match)
            )
            match._sla_task = match.sla_checker.start(
                match.players,
                broadcast_callback=lambda message: self._broadcast_match_scoped_event(match, message),
            )
            match._match_timer_task = asyncio.create_task(
                self._match_timer(match)
            )

            logger.info(f"[{match_id}] Match started with {len(match.players)} players")

        except docker.errors.ImageNotFound as e:
            logger.error(f"[{match_id}] Docker image not found: {e}")
            match.status = "error"
            match.player_ssh_key_materials = {}
            await database.update_match_status(match_id, match.status)
            match.add_event("MATCH_ERROR", {"error": f"Docker image not found: {e}", "error_type": "image_not_found"})
        except docker.errors.APIError as e:
            logger.error(f"[{match_id}] Docker API error: {e}")
            match.status = "error"
            match.player_ssh_key_materials = {}
            await database.update_match_status(match_id, match.status)
            match.add_event("MATCH_ERROR", {"error": f"Docker API error: {e}", "error_type": "docker_api_error"})
        except Exception as e:
            logger.error(f"[{match_id}] Failed to start match: {e}")
            match.status = "error"
            match.player_ssh_key_materials = {}
            await database.update_match_status(match_id, match.status)
            match.add_event("MATCH_ERROR", {"error": str(e), "error_type": "unknown"})
            if not match.resources_destroyed:
                try:
                    await self.destroy_match(match_id)
                except Exception as cleanup_err:
                    logger.warning(f"[{match_id}] cleanup after error failed: {cleanup_err}")

    async def _broadcast_match_scoped_event(self, match: MatchState, message: Dict[str, Any]) -> None:
        scoped_message = dict(message)
        scoped_message.setdefault("match_id", match.match_id)
        await self.broadcast(scoped_message)

    @staticmethod
    async def _cancel_task(task: Optional[asyncio.Task], current_task: Optional[asyncio.Task] = None) -> None:
        if task is None or task.done() or task is current_task:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    @staticmethod
    def _attack_prompt_delivery_timeout(attack_duration: int, attack_prompt_timeout: int) -> int:
        return max(30, min(attack_duration, attack_prompt_timeout + 30))

    async def _stop_match_background_tasks(self, match: MatchState, current_task: Optional[asyncio.Task] = None) -> None:
        await self._cancel_task(match._flag_task, current_task)
        match.sla_checker.stop()
        await self._cancel_task(match._match_timer_task, current_task)

    async def _set_match_status(self, match: MatchState, status_value: str, data: Optional[Dict[str, Any]] = None) -> None:
        match.status = status_value
        await database.update_match_status(match.match_id, match.status)
        payload = {"status": status_value, **(data or {})}
        await match.add_event_and_persist("STATUS", payload)
        await self.broadcast({"type": "STATUS", "match_id": match.match_id, **payload})

    async def _setup_werewolf_agent_containers(self, match: MatchState) -> None:
        client = docker.from_env()
        loop = asyncio.get_running_loop()
        network_name = f"werewolf_{match.match_id}"

        try:
            await loop.run_in_executor(
                None,
                lambda: client.networks.create(network_name, driver="bridge", check_duplicate=True),
            )
        except APIError as exc:
            if "already exists" not in str(exc):
                raise

        async def _create_agent_container(player_cfg: PlayerConfig) -> None:
            pid = player_cfg.id
            try:
                player_backend = backend_registry.get(player_cfg.backend_type)
            except Exception as exc:
                raise RuntimeError(f"Player {pid} backend setup failed: {exc}") from exc
            match.player_backends[pid] = player_backend
            agent_spec = player_backend.build_agent_container_spec(match, player_cfg)
            container_name = f"werewolf_{match.match_id}_{pid}"
            await loop.run_in_executor(None, lambda: client.containers.run(
                agent_spec.image,
                name=container_name,
                hostname=f"werewolf_{pid}",
                network=network_name,
                environment=agent_spec.environment,
                detach=True,
                remove=False,
                mem_limit="2g",
                nano_cpus=2_000_000_000,
                pids_limit=512,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                restart_policy=CONTAINER_RESTART_POLICY,
                entrypoint=agent_spec.entrypoint,
                command=agent_spec.command,
                volumes=agent_spec.volumes or None,
                labels={
                    "awd.match_id": match.match_id,
                    "awd.player_id": str(pid),
                    "awd.role": "werewolf-agent",
                },
            ))

        await asyncio.gather(*[_create_agent_container(player_cfg) for player_cfg in match.config.players])
        await asyncio.sleep(INIT_CONTAINER_STABILIZATION_DELAY)

        for player_cfg in match.config.players:
            pid = player_cfg.id
            container_name = f"werewolf_{match.match_id}_{pid}"
            match.players[pid] = PlayerState(
                player_id=pid,
                container_name=container_name,
                target_container=container_name,
                target_ip="127.0.0.1",
                target_port=0,
                network_name=network_name,
                maintenance_auth_mode="none",
                maintenance_helper_command="",
            )
            match.agent_sessions[pid] = AgentSession(
                player_id=pid,
                container_name=container_name,
                target_container=container_name,
                target_ip="127.0.0.1",
            )

        await match.add_event_and_persist("WEREWOLF_AGENTS_CREATED", {
            "players": {
                pid: {
                    "agent_container": player.container_name,
                    "network": player.network_name,
                    "isolated": False,
                }
                for pid, player in match.players.items()
            }
        })

    async def _initialize_single_werewolf_agent(self, match: MatchState, player_id: int, session: AgentSession) -> AgentInitializationResult:
        player_cfg = next((p for p in match.config.players if p.id == player_id), None)
        if player_cfg is None:
            return AgentInitializationResult(
                player_id=player_id,
                success=False,
                reason="PLAYER_CONFIG_NOT_FOUND",
                details=f"No PlayerConfig found for player {player_id}",
            )

        backend = self._get_player_backend(match, player_id)
        if backend is None:
            return AgentInitializationResult(
                player_id=player_id,
                success=False,
                reason="BACKEND_NOT_CONFIGURED",
                details=f"No backend adapter available for player {player_id}",
            )

        try:
            player_client = backend.create_client(match.config, player_cfg)
        except Exception as exc:
            return AgentInitializationResult(
                player_id=player_id,
                success=False,
                reason="BACKEND_CLIENT_INIT_FAILED",
                details=str(exc) or "Failed to create backend client",
            )

        prompt = render_werewolf_init_prompt(player_id, player_cfg.name)
        try:
            init_result = await backend.initialize_agent(
                player_client,
                session,
                prompt,
                stream_callback=await self._make_werewolf_agent_stream_callback(match, player_id),
            )
        except Exception as exc:
            reason = session.init_error_reason or type(exc).__name__
            details = session.init_error_details or str(exc) or "No initialization details captured"
            return AgentInitializationResult(
                player_id=player_id,
                success=False,
                reason=reason,
                details=details,
                client=player_client,
            )

        return AgentInitializationResult(
            player_id=player_id,
            success=init_result.success,
            reason=init_result.reason or session.init_error_reason,
            details=init_result.details or session.init_error_details,
            client=player_client,
        )

    async def _initialize_werewolf_agents(self, match: MatchState) -> int:
        tasks = [
            asyncio.create_task(self._initialize_single_werewolf_agent(match, pid, session))
            for pid, session in match.agent_sessions.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return await self._apply_agent_initialization_results(match, results)

    async def _make_werewolf_agent_stream_callback(self, match: MatchState, player_id: int):
        async def cb(line: str):
            safe_line = sanitize_public_text(line)
            session = match.agent_sessions.get(player_id)
            if session is not None:
                activity_now = asyncio.get_running_loop().time()
                session.last_activity_at = activity_now
                session.last_stream_output_at = activity_now
                session.interactive_ready = True
                await self._sync_and_emit_readiness_layers(
                    match,
                    player_id,
                    phase=match.status,
                    reason="READY_STREAM_ACTIVITY",
                    details="Observed werewolf agent stream output",
                )
            # Werewolf agent token streams contain chain-of-thought with private role reasoning
            # (seer checks, wolf coordination, witch decisions). Never broadcast — and don't keep
            # them in werewolf_state.public_events either: that list is consumed by the AI judge
            # (which only reads WEREWOLF_* events) and would otherwise grow unbounded for long
            # matches. The agent's stdout is still captured by agent_client session logs if a
            # post-match audit is needed.
        return cb

    async def _send_werewolf_agent_request(
        self,
        match: MatchState,
        player_id: int,
        prompt: str,
        kind: str,
        timeout: int,
    ) -> Optional[str]:
        backend = self._get_player_backend(match, player_id)
        player_client = self._get_player_client(match, player_id)
        session = match.agent_sessions.get(player_id)
        if backend is None or player_client is None or session is None:
            return None
        return await backend.send_message(
            player_client,
            session,
            prompt,
            timeout=timeout,
            stream_callback=await self._make_werewolf_agent_stream_callback(match, player_id),
            message_kind=kind,
            message_mode=MESSAGE_MODE_NORMAL,
        )

    async def _run_werewolf_match_startup(self, match: MatchState) -> None:
        match_id = match.match_id
        try:
            await self.validate_docker_api_compatibility()
            await self._set_match_status(match, "creating_werewolf_agents", {"mode": "werewolf"})
            await self._setup_werewolf_agent_containers(match)

            for pid in match.players:
                self.player_match_index[pid] = match_id
                self._issue_player_read_token(match, pid)

            await self._set_match_status(match, "initializing_agents", {"mode": "werewolf"})
            ready_count = await self._initialize_werewolf_agents(match)
            if ready_count == 0:
                error_msg = (
                    f"All {len(match.players)} werewolf agents failed to initialize. "
                    "Aborting match — a game with no functional agents is not playable."
                )
                logger.error(f"[{match_id}] {error_msg}")
                match.status = "error"
                await database.update_match_status(match_id, match.status)
                await match.add_event_and_persist("MATCH_ERROR", {
                    "mode": "werewolf",
                    "error": error_msg,
                    "ready_count": 0,
                    "total_players": len(match.players),
                })
                await self.broadcast({
                    "type": "MATCH_ERROR",
                    "match_id": match_id,
                    "mode": "werewolf",
                    "error": error_msg,
                })
                if not match.resources_destroyed:
                    await self.destroy_match(match_id)
                return
            elif ready_count < len(match.players):
                logger.warning(f"[{match_id}] Only {ready_count}/{len(match.players)} werewolf agents ready — continuing with partial players")

            player_names = {
                player.id: player.name
                for player in match.config.players
            }
            role_counts = match.config.werewolf.roles.model_dump()
            match.werewolf_state = create_werewolf_state(
                [player.id for player in match.config.players],
                player_names=player_names,
                role_counts=role_counts,
                board=match.config.werewolf.board,
                sheriff_enabled=match.config.werewolf.sheriffEnabled,
                werewolf_reveal_enabled=match.config.werewolf.werewolfRevealEnabled,
                max_days=match.config.werewolf.maxDays,
            )

            match.started_at = datetime.now()
            await self._set_match_status(match, "werewolf_training" if match.config.werewolf.preMatchTraining else "werewolf_night", {
                "mode": "werewolf",
                "player_count": len(match.players),
            })

            async def emit_event(event_type: str, data: Dict[str, Any], *, audience: str = "public") -> None:
                payload = {"mode": "werewolf", **data}
                if audience != "public":
                    # Hidden audit-only event: kept in werewolf_state for AI judge / post-match audit.
                    # Never persisted into match.events (REST-exposed) and never broadcast to WS.
                    # The live commentator only sees spectator-visible events; do not put
                    # private roles, seer checks, witch decisions, or guard targets in an LLM
                    # prompt and rely on it to keep them hidden.
                    now = datetime.now()
                    event = {
                        "type": event_type,
                        "data": payload,
                        "timestamp": now.isoformat(),
                        "match_id": match.match_id,
                        "audience": "hidden",
                    }
                    if match.werewolf_state is not None:
                        match.werewolf_state.append_public_event(event)
                    return
                event = await match.add_event_and_persist(event_type, payload)
                if match.werewolf_state is not None:
                    match.werewolf_state.append_public_event(event)
                await self.broadcast({
                    "type": event_type,
                    "match_id": match.match_id,
                    **payload,
                    "timestamp": event["timestamp"],
                })

            async def set_status(status_value: str, data: Dict[str, Any]) -> None:
                await self._set_match_status(match, status_value, {"mode": "werewolf", **data})

            async def agent_request(player_id: int, prompt: str, kind: str, timeout: int) -> Optional[str]:
                return await self._send_werewolf_agent_request(match, player_id, prompt, kind, timeout)

            runner = WerewolfMatchRunner(
                match.werewolf_state,
                agent_request=agent_request,
                emit_event=emit_event,
                set_status=set_status,
                logger=logger,
            )
            if match.config.werewolf.preMatchTraining:
                training_ok = await runner.run_training()
                if not training_ok:
                    error_msg = (
                        "All werewolf agents failed training — no agent produced a valid JSON response. "
                        "Aborting game."
                    )
                    logger.error(f"[{match_id}] {error_msg}")
                    match.status = "error"
                    await database.update_match_status(match_id, match.status)
                    await match.add_event_and_persist("MATCH_ERROR", {
                        "mode": "werewolf",
                        "error": error_msg,
                    })
                    await self.broadcast({
                        "type": "MATCH_ERROR",
                        "match_id": match_id,
                        "mode": "werewolf",
                        "error": error_msg,
                    })
                    if not match.resources_destroyed:
                        await self.destroy_match(match_id)
                    return
            await runner.run_game()
            await self.end_match(match.match_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"[{match_id}] Failed to run werewolf match: {exc}")
            match.status = "error"
            await database.update_match_status(match_id, match.status)
            await match.add_event_and_persist("MATCH_ERROR", {"mode": "werewolf", "error": str(exc)})
            await self.broadcast({"type": "MATCH_ERROR", "match_id": match_id, "mode": "werewolf", "error": str(exc)})
            # Ensure agent containers are cleaned up on error — without this they accumulate
            # forever (88 orphan containers after a few crashed matches).
            if not match.resources_destroyed:
                try:
                    await self.destroy_match(match_id)
                except Exception as cleanup_err:
                    logger.warning(f"[{match_id}] cleanup after error failed: {cleanup_err}")


    async def _setup_containers(self, match: MatchState):
        """Create Docker containers for each isolated player network."""
        client = docker.from_env()
        loop = asyncio.get_running_loop()
        maintenance_passwords: Dict[int, str] = {}

        async def _create_player_containers(player_cfg: PlayerConfig):
            pid = player_cfg.id
            try:
                player_backend = backend_registry.get(player_cfg.backend_type)
            except Exception as exc:
                raise RuntimeError(f"Player {pid} backend setup failed: {exc}") from exc
            match.player_backends[pid] = player_backend

            player_network_name = f"awd_{match.match_id}_player_{pid}"
            try:
                match_hash = int(hashlib.md5(match.match_id.encode()).hexdigest()[:4], 16) % 124
                second_octets = list(range(100 + match_hash, 224)) + list(range(100, 100 + match_hash))
                candidate_subnets = [f"10.{second_octet}.{pid % 256}.0/24" for second_octet in second_octets]
                subnet, gateway = await loop.run_in_executor(
                    None,
                    lambda: _choose_available_subnet(client, candidate_subnets),
                )

                ipam_pool = IPAMPool(subnet=subnet, gateway=gateway)
                ipam_config = IPAMConfig(pool_configs=[ipam_pool])

                await loop.run_in_executor(
                    None, lambda: client.networks.create(
                        player_network_name,
                        driver="bridge",
                        check_duplicate=True,
                        ipam=ipam_config
                    )
                )
                logger.info(f"Created isolated network: {player_network_name} with subnet {subnet}")
            except APIError as e:
                if "already exists" in str(e):
                    await loop.run_in_executor(None, lambda: client.networks.get(player_network_name))
                else:
                    raise

            target_name = f"target_{match.match_id}_{pid}"
            claw_name = f"claw_{match.match_id}_{pid}"

            try:
                match.player_ssh_key_materials[pid] = await self._generate_player_ssh_keypair(match.match_id, pid)
            except Exception as exc:
                raise RuntimeError(f"Failed to generate SSH keypair for player {pid}: {exc}") from exc
            ssh_key_material = match.player_ssh_key_materials[pid]

            flags = {
                f"FLAG_{i}": f"FLAG{{{secrets.token_hex(16)}}}"
                for i in range(1, 7)
            }
            maintenance_password = secrets.token_urlsafe(12)
            maintenance_passwords[pid] = maintenance_password
            flags["TZ"] = CONTAINER_TIMEZONE
            flags["MAINTENANCE_USERNAME"] = "defender"
            flags["MAINTENANCE_PASSWORD"] = maintenance_password
            flags["MAINTENANCE_AUTHORIZED_KEY"] = ssh_key_material.public_key.rstrip("\n")

            target_image = match.config.target_image or "openclaw/ctf-target:v1"
            agent_spec = player_backend.build_agent_container_spec(match, player_cfg)

            await loop.run_in_executor(None, lambda: client.containers.run(
                target_image,
                name=target_name,
                hostname=f"target_{pid}",
                network=player_network_name,
                environment=flags,
                detach=True,
                remove=False,
                mem_limit="1g",
                nano_cpus=1_000_000_000,  # 1 CPU core
                pids_limit=512,
                restart_policy=CONTAINER_RESTART_POLICY,
                labels={
                    "awd.match_id": match.match_id,
                    "awd.player_id": str(pid),
                    "awd.role": "target",
                },
            ))

            await loop.run_in_executor(None, lambda: client.containers.run(
                agent_spec.image,
                name=claw_name,
                hostname=f"claw_{pid}",
                network=player_network_name,
                environment=agent_spec.environment,
                detach=True,
                remove=False,
                mem_limit="2g",
                nano_cpus=2_000_000_000,  # 2 CPU cores
                pids_limit=512,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                restart_policy=CONTAINER_RESTART_POLICY,
                entrypoint=agent_spec.entrypoint,
                command=agent_spec.command,
                volumes=agent_spec.volumes or None,
                labels={
                    "awd.match_id": match.match_id,
                    "awd.player_id": str(pid),
                    "awd.role": "agent",
                },
            ))

            logger.info(f"[Player {pid}] Containers launched: target={target_name}, agent={claw_name}")

        await asyncio.gather(
            *[_create_player_containers(player_cfg) for player_cfg in match.config.players]
        )

        await asyncio.sleep(INIT_CONTAINER_STABILIZATION_DELAY)

        async def _get_container_ip(container_name: str, network_name: str, retries: int = CONTAINER_IP_RETRIES) -> str:
            fmt = f"{{{{.NetworkSettings.Networks.{network_name}.IPAddress}}}}"
            for attempt in range(retries):
                proc = await asyncio.create_subprocess_exec(
                    "docker",
                    "inspect",
                    "--format",
                    fmt,
                    container_name,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=CONTAINER_IP_INSPECT_TIMEOUT)
                ip = stdout.decode().strip()
                if ip and ip != "<no value>":
                    return ip
                logger.debug(
                    f"[{container_name}] IP not ready yet (attempt {attempt + 1}/{retries}), retrying..."
                )
                await asyncio.sleep(CONTAINER_IP_RETRY_DELAY)
            raise RuntimeError(
                f"Failed to obtain IP for container {container_name} on network {network_name} "
                f"after {retries} attempts"
            )

        for player_cfg in match.config.players:
            pid = player_cfg.id
            player_network_name = f"awd_{match.match_id}_player_{pid}"
            target_name = f"target_{match.match_id}_{pid}"
            claw_name = f"claw_{match.match_id}_{pid}"
            player_backend = match.player_backends[pid]
            ssh_key_material = match.player_ssh_key_materials[pid]
            ssh_spec = player_backend.resolve_target_ssh_spec(match.config, player_cfg)
            ssh_key_material.private_key_path = ssh_spec.private_key_path
            ssh_key_material.helper_path = ssh_spec.helper_path
            ssh_key_material.owner_user = ssh_spec.owner_user
            ssh_key_material.owner_group = ssh_spec.owner_group

            target_ip = await _get_container_ip(target_name, player_network_name)

            match.players[pid] = PlayerState(
                player_id=pid,
                container_name=claw_name,
                target_container=target_name,
                target_ip=target_ip,
                target_port=3000,
                network_name=player_network_name,
                maintenance_username="defender",
                maintenance_auth_mode="ssh_key",
                maintenance_helper_command="target-ssh",
                maintenance_password=maintenance_passwords.get(pid),
            )

            match.agent_sessions[pid] = AgentSession(
                player_id=pid,
                container_name=claw_name,
                target_container=target_name,
                target_ip=target_ip,
            )

            logger.info(
                f"[Player {pid}] Containers created on isolated network {player_network_name}: "
                f"target={target_name} (IP={target_ip}), agent={claw_name}"
            )

        async def _wait_target_ready(pid: int, player: Any) -> None:
            for attempt in range(TARGET_HTTP_READY_RETRIES):
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "docker",
                        "exec",
                        player.target_container,
                        "curl",
                        "-sf",
                        "http://localhost:3000/health",
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=TARGET_HTTP_READY_TIMEOUT)
                    if proc.returncode == 0:
                        logger.info(f"[Player {pid}] Target HTTP ready")
                        return
                except Exception:
                    pass
                await asyncio.sleep(TARGET_HTTP_READY_RETRY_DELAY)
            logger.warning(
                f"[Player {pid}] Target HTTP not ready after "
                f"{TARGET_HTTP_READY_RETRIES * TARGET_HTTP_READY_RETRY_DELAY}s"
            )

        await asyncio.gather(
            *[_wait_target_ready(pid, player) for pid, player in match.players.items()]
        )

        async def _prepare_agent_target_ssh(pid: int, player: Any) -> None:
            ssh_key_material = match.player_ssh_key_materials.get(pid)
            if ssh_key_material is None:
                raise RuntimeError(f"Missing SSH key material for player {pid}")
            await self._install_agent_target_ssh(
                pid,
                player.container_name,
                player.target_ip,
                ssh_key_material,
                maintenance_username=player.maintenance_username,
            )
            player.maintenance_helper_command = os.path.basename(
                ssh_key_material.helper_path or "/usr/local/bin/target-ssh"
            )

        await asyncio.gather(
            *[_prepare_agent_target_ssh(pid, player) for pid, player in match.players.items()]
        )

        match.add_event("CONTAINERS_CREATED", {
            "players": {
                pid: {
                    "target_ip": p.target_ip,
                    "agent_container": p.container_name,
                    "target_container": p.target_container,
                    "network": p.network_name,
                    "isolated": True,
                }
                for pid, p in match.players.items()
            }
        })

    async def _make_agent_stream_callback(self, match: MatchState, player_id: int):
        async def cb(line: str):
            safe_line = sanitize_public_text(line)
            session = match.agent_sessions.get(player_id)
            if session is not None:
                activity_now = asyncio.get_running_loop().time()
                session.last_activity_at = activity_now
                session.last_stream_output_at = activity_now
                session.interactive_ready = True
                await self._sync_and_emit_readiness_layers(
                    match,
                    player_id,
                    phase="defense",
                    reason="READY_STREAM_ACTIVITY",
                    details="Observed defense-phase agent stream output",
                )
            match.add_event("AGENT_STREAM", {
                "player_id": player_id,
                "phase": "defense",
                "content": safe_line,
            })
            await self.broadcast({
                "type": "AGENT_STREAM",
                "match_id": match.match_id,
                "player_id": player_id,
                "content": safe_line
            })
        return cb

    async def _send_defense_keepalive(self, match: MatchState, player_id: int, session: AgentSession) -> None:
        backend = self._get_player_backend(match, player_id)
        agent_client = self._get_player_client(match, player_id)
        if backend is None or agent_client is None:
            return

        remaining_seconds = self._get_remaining_seconds(match, datetime.now())
        if remaining_seconds < 120:
            return

        reminder_prompt = (
            "[Defense keepalive reminder]\n"
            f"Defense phase is still running, remaining seconds: {remaining_seconds}.\n"
            "Keep checking target services, fixing exposed flag paths, and validating your defense changes.\n"
        )

        enqueue_state = await backend.enqueue_buffered_message(
            agent_client,
            session,
            reminder_prompt,
            timeout=60,
            message_kind="keepalive",
            dedupe_key="keepalive",
            merge_strategy="replace",
        )
        session.last_keepalive_sent_at = asyncio.get_running_loop().time()
        event_type = "DEFENSE_KEEPALIVE_SENT" if enqueue_state == "sent" else "DEFENSE_KEEPALIVE_BUFFERED"
        match.add_event(event_type, {
            "player_id": player_id,
            "remaining_seconds": remaining_seconds,
            "buffer_state": enqueue_state,
        })
        await self.broadcast({
            "type": event_type,
            "match_id": match.match_id,
            "player_id": player_id,
            "remaining_seconds": remaining_seconds,
            "buffer_state": enqueue_state,
            "timestamp": datetime.now().isoformat(),
        })
        if enqueue_state == "sent" and session.last_completed_message_kind == "keepalive" and session.last_response:
            session.interactive_ready = True
            await self._sync_and_emit_readiness_layers(
                match,
                player_id,
                phase="defense",
                reason="READY_DEFENSE_KEEPALIVE_RESPONSE",
                details="Agent returned a non-empty response to the defense keepalive",
            )
            await self._mark_player_ready(
                match,
                player_id,
                phase="defense",
                reason="READY_DEFENSE_KEEPALIVE_RESPONSE",
                details="Agent returned a non-empty response to the defense keepalive",
            )

    async def _defense_keepalive_loop(self, match: MatchState) -> None:
        check_interval = 5
        idle_threshold = 40
        session_probe_threshold = 15
        loop = asyncio.get_running_loop()

        while match.status == "defense":
            now = loop.time()
            remaining_seconds = self._get_remaining_seconds(match, datetime.now())
            if remaining_seconds < 120:
                await asyncio.sleep(check_interval)
                continue

            for player_id, session in match.agent_sessions.items():
                backend = self._get_player_backend(match, player_id)
                agent_client = self._get_player_client(match, player_id)
                if backend is None or agent_client is None:
                    continue

                last_activity = session.last_activity_at
                if last_activity is not None and now - last_activity >= session_probe_threshold:
                    try:
                        if await backend.observe_session_activity(agent_client, session):
                            await self._sync_and_emit_readiness_layers(
                                match,
                                player_id,
                                phase="defense",
                                reason="READY_SESSION_ACTIVITY",
                                details="Observed new session file activity during defense phase",
                            )
                            continue
                        if await backend.observe_code_activity(agent_client, session):
                            await self._sync_and_emit_readiness_layers(
                                match,
                                player_id,
                                phase="defense",
                                reason="READY_CODE_ACTIVITY",
                                details="Observed target code activity during defense phase",
                            )
                            continue
                    except Exception as exc:
                        logger.debug(f"[Player {player_id}] Session activity probe failed: {exc}")

                last_activity = session.last_activity_at
                if last_activity is not None and now - last_activity < idle_threshold:
                    continue

                last_keepalive = session.last_keepalive_sent_at
                if last_keepalive is not None and now - last_keepalive < idle_threshold:
                    continue

                if backend.is_session_busy(agent_client, session) and backend.has_buffered_message_kind(agent_client, session, "keepalive"):
                    continue

                await self._send_defense_keepalive(match, player_id, session)

            await asyncio.sleep(check_interval)

    async def _send_attack_keepalive(self, match: MatchState, player_id: int, session: AgentSession) -> None:
        backend = self._get_player_backend(match, player_id)
        agent_client = self._get_player_client(match, player_id)
        if backend is None or agent_client is None:
            return

        remaining_seconds = self._get_remaining_seconds(match, datetime.now())
        if remaining_seconds <= 0:
            return

        try:
            status_payload = await self._build_player_status_view(
                match.match_id,
                player_id,
                update_checkpoint=False,
            )
        except Exception as exc:
            logger.warning(f"[Player {player_id}] Failed to build attack keepalive status snapshot: {exc}")
            return

        status_snapshot = json.dumps(status_payload, ensure_ascii=False, indent=2, default=str)
        player = match.players.get(player_id)
        maintenance_helper = (
            player.maintenance_helper_command
            if player is not None and player.maintenance_helper_command
            else "target-ssh"
        )
        reminder_prompt = (
            "[Attack keepalive reminder]\n"
            f"Attack phase is still running, remaining seconds: {remaining_seconds}.\n"
            "Latest player status follows:\n"
            f"```json\n{status_snapshot}\n```\n"
            "Continue attacking opponents while keeping your own target patched. "
            f"Use `{maintenance_helper} '<remote command>'` for maintenance commands when needed.\n"
            f"Restart with `{maintenance_helper} 'supervisorctl restart web'` and validate with "
            f"`{maintenance_helper} 'curl -sf http://localhost:3000/health'`."
        )

        enqueue_state = await backend.enqueue_buffered_message(
            agent_client,
            session,
            reminder_prompt,
            timeout=60,
            message_kind="attack_keepalive",
            dedupe_key="attack_keepalive",
            merge_strategy="replace",
        )
        session.last_keepalive_sent_at = asyncio.get_running_loop().time()
        event_type = "ATTACK_KEEPALIVE_SENT" if enqueue_state == "sent" else "ATTACK_KEEPALIVE_BUFFERED"
        match.add_event(event_type, {
            "player_id": player_id,
            "remaining_seconds": remaining_seconds,
            "buffer_state": enqueue_state,
        })
        await self.broadcast({
            "type": event_type,
            "match_id": match.match_id,
            "player_id": player_id,
            "remaining_seconds": remaining_seconds,
            "buffer_state": enqueue_state,
            "timestamp": datetime.now().isoformat(),
        })
        if enqueue_state == "sent" and session.last_completed_message_kind == "attack_keepalive" and session.last_response:
            session.interactive_ready = True
            await self._sync_and_emit_readiness_layers(
                match,
                player_id,
                phase="attack",
                reason="READY_ATTACK_KEEPALIVE_RESPONSE",
                details="Agent returned a non-empty response to the attack keepalive",
            )
            await self._mark_player_ready(
                match,
                player_id,
                phase="attack",
                reason="READY_ATTACK_KEEPALIVE_RESPONSE",
                details="Agent returned a non-empty response to the attack keepalive",
            )

    async def _attack_keepalive_loop(self, match: MatchState) -> None:
        check_interval = 5
        idle_threshold = 300
        loop = asyncio.get_running_loop()

        while match.status == "attack":
            now = loop.time()
            remaining_seconds = self._get_remaining_seconds(match, datetime.now())
            if remaining_seconds <= 0:
                await asyncio.sleep(check_interval)
                continue

            for player_id, session in match.agent_sessions.items():
                backend = self._get_player_backend(match, player_id)
                agent_client = self._get_player_client(match, player_id)
                if backend is None or agent_client is None:
                    continue

                last_stream_output = session.last_stream_output_at
                if last_stream_output is None:
                    continue

                if now - last_stream_output < idle_threshold:
                    continue

                last_keepalive = session.last_keepalive_sent_at
                if last_keepalive is not None and now - last_keepalive < idle_threshold:
                    continue

                if backend.is_session_busy(agent_client, session) and backend.has_buffered_message_kind(agent_client, session, "attack_keepalive"):
                    continue

                await self._send_attack_keepalive(match, player_id, session)

            await asyncio.sleep(check_interval)

    async def _initialize_single_agent(self, match: MatchState, player_id: int, session: AgentSession) -> AgentInitializationResult:
        if session.init_error_reason:
            return AgentInitializationResult(
                player_id=player_id,
                success=False,
                reason=session.init_error_reason,
                details=session.init_error_details or "No initialization details captured",
            )

        referee_url = "http://host.docker.internal:8000"
        player_cfg = next((p for p in match.config.players if p.id == player_id), None)
        if player_cfg is None:
            return AgentInitializationResult(
                player_id=player_id,
                success=False,
                reason="PLAYER_CONFIG_NOT_FOUND",
                details=f"No PlayerConfig found for player {player_id}",
            )

        backend = self._get_player_backend(match, player_id)
        if backend is None:
            return AgentInitializationResult(
                player_id=player_id,
                success=False,
                reason="BACKEND_NOT_CONFIGURED",
                details=f"No backend adapter available for player {player_id}",
            )

        player = match.players[player_id]
        ssh_key_material = match.player_ssh_key_materials.get(player_id)
        helper_path = (ssh_key_material.helper_path if ssh_key_material is not None else None) or "/usr/local/bin/target-ssh"

        try:
            await self._verify_agent_target_ssh(
                player_id,
                session.container_name,
                helper_path,
            )
        except TargetSSHProbeError as exc:
            session.init_error_reason = exc.reason
            session.init_error_details = exc.details
            return AgentInitializationResult(
                player_id=player_id,
                success=False,
                reason=exc.reason,
                details=exc.details,
            )

        try:
            player_client = backend.create_client(match.config, player_cfg)
        except Exception as exc:
            return AgentInitializationResult(
                player_id=player_id,
                success=False,
                reason="BACKEND_CLIENT_INIT_FAILED",
                details=str(exc) or "Failed to create backend client",
            )

        scoring = match.config.scoring.model_dump()
        phases = match.config.match.phases
        prompt = PromptRenderer.render_defense_init(
            player_id=player_id,
            own_target_ip=player.target_ip,
            target_port=player.target_port,
            maintenance_auth_mode=player.maintenance_auth_mode,
            maintenance_helper_command=player.maintenance_helper_command,
            referee_api_url=referee_url,
            scoring=scoring,
            flag_refresh_interval=match.flag_refresh_interval,
            defense_duration=phases.defense,
            attack_duration=phases.attack,
        )

        try:
            init_result = await backend.initialize_agent(
                player_client,
                session,
                prompt,
                stream_callback=await self._make_agent_stream_callback(match, player_id),
            )
        except Exception as exc:
            reason = session.init_error_reason or type(exc).__name__
            details = session.init_error_details or str(exc) or "No initialization details captured"
            return AgentInitializationResult(
                player_id=player_id,
                success=False,
                reason=reason,
                details=details,
                client=player_client,
            )

        return AgentInitializationResult(
            player_id=player_id,
            success=init_result.success,
            reason=init_result.reason or session.init_error_reason,
            details=init_result.details or session.init_error_details,
            client=player_client,
        )

    async def _initialize_agents(self, match: MatchState) -> int:
        """Initialize all agents for the defense phase."""
        tasks = [
            asyncio.create_task(self._initialize_single_agent(match, pid, session))
            for pid, session in match.agent_sessions.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return await self._apply_agent_initialization_results(match, results)

    async def _flag_refresh_loop(self, match: MatchState):
        """定时 Flag 刷新"""
        while match.status in ("defense", "attack"):
            await asyncio.sleep(match.flag_refresh_interval)

            if match.status not in ("defense", "attack"):
                break

            new_flags = await match.flag_manager.generate_and_inject(match.players)

            # 更新分数
            match.scoring_engine.update_scores(match.players, match.persisted_submissions)

            match.add_event("FLAGS_REFRESHED", {
                "player_count": len(new_flags),
            })

            await self.broadcast({
                "type": "FLAGS_REFRESHED",
                "match_id": match.match_id,
                "timestamp": datetime.now().isoformat(),
            })

    async def _match_timer(self, match: MatchState):
        """Run the match timer across defense and attack phases."""
        phases = match.config.match.phases
        defense_duration = phases.defense
        attack_duration = phases.attack
        current_task = asyncio.current_task()
        heartbeat_task: Optional[asyncio.Task] = None
        defense_keepalive_task: Optional[asyncio.Task] = None
        attack_keepalive_task: Optional[asyncio.Task] = None
        attack_tasks: List[asyncio.Task] = []
        verification_tasks: List[asyncio.Task] = []
        drain_tasks: List[asyncio.Task] = []

        try:
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(match, defense_duration + attack_duration)
            )
            defense_keepalive_task = asyncio.create_task(self._defense_keepalive_loop(match))

            logger.info(f"[{match.match_id}] Defense phase: {defense_duration}s (networks isolated)")
            await asyncio.sleep(defense_duration)

            if match.status != "defense":
                return
            # 切换到攻击阶段
            match.status = "attack"
            match.attack_started_at = datetime.now()
            await self._cancel_task(defense_keepalive_task, current_task)
            defense_keepalive_task = None
            await database.update_match_status(match.match_id, match.status)
            match.add_event("PHASE_CHANGE", {"phase": "attack", "action": "opening_network"})

            await self._open_arena_network(match)

            client = docker.from_env()
            loop = asyncio.get_running_loop()
            arena_network_name = f"awd_{match.match_id}_arena"

            async def _get_arena_ip(pid: int, player) -> tuple[int, str]:
                def _fetch():
                    try:
                        target_c = client.containers.get(player.target_container)
                        target_c.reload()
                        return target_c.attrs["NetworkSettings"]["Networks"][arena_network_name]["IPAddress"]
                    except Exception as e:
                        logger.error(f"[Player {pid}] Failed to get arena IP: {e}")
                        return player.target_ip
                return pid, await loop.run_in_executor(None, _fetch)

            ip_results = await asyncio.gather(*[_get_arena_ip(pid, p) for pid, p in match.players.items()])
            arena_ips = dict(ip_results)
            for pid, ip in arena_ips.items():
                logger.info(f"[Player {pid}] Target arena IP: {ip}")

            await self.broadcast({
                "type": "PHASE_CHANGE",
                "match_id": match.match_id,
                "phase": "attack",
                "remaining_seconds": attack_duration,
                "arena_ips": arena_ips,
            })
            match.add_event("PHASE_CHANGE", {
                "phase": "attack",
                "remaining_seconds": attack_duration,
                "arena_ips": arena_ips,
            })

            referee_url = "http://host.docker.internal:8000"
            scoring = match.config.scoring.model_dump()

            for pid, session in match.agent_sessions.items():
                backend = self._get_player_backend(match, pid)
                agent_client = self._get_player_client(match, pid)
                if backend is not None and agent_client is not None:
                    backend.freeze_buffered_messages(agent_client, session)
                session.last_keepalive_sent_at = None

            attack_prompt_timeout = 300
            for pid, session in match.agent_sessions.items():
                session.last_stream_output_at = loop.time()
                enemy_targets = [
                    {
                        "player_id": other_pid,
                        "ip": arena_ips.get(other_pid, p.target_ip),
                        "port": p.target_port,
                    }
                    for other_pid, p in match.players.items()
                    if other_pid != pid
                ]
                match.attack_targets_by_player[pid] = list(enemy_targets)

                attack_prompt = PromptRenderer.render_attack_start(
                    player_id=pid,
                    enemy_targets=enemy_targets,
                    target_port=match.players[pid].target_port,
                    own_target_ip=match.players[pid].target_ip,
                    maintenance_auth_mode=match.players[pid].maintenance_auth_mode,
                    maintenance_helper_command=match.players[pid].maintenance_helper_command,
                    referee_api_url=referee_url,
                    scoring=scoring,
                    flag_refresh_interval=match.flag_refresh_interval,
                    attack_duration=attack_duration,
                    player_status_url=f"{referee_url}/api/player/status",
                    player_read_token=match.player_read_tokens[pid],
                )

                agent_client = self._get_player_client(match, pid)
                backend = self._get_player_backend(match, pid)
                if backend is None or agent_client is None:
                    logger.warning(f"[Player {pid}] Skipping attack prompt dispatch because no player client is available")
                    continue

                async def make_stream_cb(player_id: int):
                    async def cb(line: str):
                        safe_line = sanitize_public_text(line)
                        session = match.agent_sessions.get(player_id)
                        if session is not None:
                            activity_now = asyncio.get_running_loop().time()
                            session.last_activity_at = activity_now
                            session.last_stream_output_at = activity_now
                            session.interactive_ready = True
                            await self._sync_and_emit_readiness_layers(
                                match,
                                player_id,
                                phase="attack",
                                reason="READY_STREAM_ACTIVITY",
                                details="Observed attack-phase agent stream output",
                            )
                        match.add_event("AGENT_STREAM", {
                            "player_id": player_id,
                            "phase": "attack",
                            "content": safe_line,
                        })
                        await self.broadcast({
                            "type": "AGENT_STREAM",
                            "match_id": match.match_id,
                            "player_id": player_id,
                            "content": safe_line
                        })
                    return cb

                attack_stream_cb = await make_stream_cb(pid)
                setattr(attack_stream_cb, "_agent_session", session)
                logger.info(
                    f"[Player {pid}] Dispatching attack prompt: session_id={session.session_id or 'unknown'} "
                    f"prompt_chars={len(attack_prompt)} enemy_count={len(enemy_targets)}"
                )

                async def dispatch_attack_prompt(
                    player_id: int,
                    player_session: AgentSession,
                    player_backend: AgentBackendAdapter,
                    player_client: Any,
                    prompt_text: str,
                    stream_cb,
                ):
                    response = await player_backend.send_message(
                        player_client,
                        player_session,
                        prompt_text,
                        timeout=attack_prompt_timeout,
                        stream_callback=stream_cb,
                        message_kind="attack_prompt",
                        message_mode=MESSAGE_MODE_INTERRUPT,
                    )
                    if response is not None:
                        player_session.interactive_ready = True
                        await self._sync_and_emit_readiness_layers(
                            match,
                            player_id,
                            phase="attack",
                            reason="READY_ATTACK_PROMPT_RESPONSE",
                            details="Agent returned a non-empty response to the attack prompt",
                        )
                        await self._mark_player_ready(
                            match,
                            player_id,
                            phase="attack",
                            reason="READY_ATTACK_PROMPT_RESPONSE",
                            details="Agent returned a non-empty response to the attack prompt",
                        )
                    return response

                attack_tasks.append(
                    asyncio.create_task(
                        dispatch_attack_prompt(pid, session, backend, agent_client, attack_prompt, attack_stream_cb),
                        name=f"attack_prompt_player_{pid}"
                    )
                )

            prompt_delivery_timeout = self._attack_prompt_delivery_timeout(attack_duration, attack_prompt_timeout)
            delivered_players = set()

            async def check_prompt_delivered(pid: int, session: AgentSession, player_backend: AgentBackendAdapter, agent_client: Any, max_wait: int = 30):
                start = asyncio.get_running_loop().time()
                while asyncio.get_running_loop().time() - start < max_wait:
                    try:
                        contains = await player_backend.check_session_contains(
                            agent_client,
                            session,
                            "[Phase change] Attack phase",
                            tail_lines=10,
                        )
                        if contains:
                            logger.info(f"[Player {pid}] Attack prompt confirmed in session file")
                            match.add_event("ATTACK_PROMPT_DELIVERED", {"player_id": pid})
                            delivered_players.add(pid)
                            return True
                    except Exception as e:
                        logger.debug(f"[Player {pid}] Error checking session: {e}")
                    await asyncio.sleep(2)
                logger.warning(f"[Player {pid}] Attack prompt not found in session file after {max_wait}s")
                return False

            for pid, session in match.agent_sessions.items():
                backend = self._get_player_backend(match, pid)
                agent_client = self._get_player_client(match, pid)
                if backend is None or agent_client is None:
                    continue
                verification_tasks.append(
                    asyncio.create_task(
                        check_prompt_delivered(pid, session, backend, agent_client, max_wait=prompt_delivery_timeout),
                        name=f"verify_prompt_player_{pid}"
                    )
                )

            try:
                await asyncio.wait_for(
                    asyncio.gather(*verification_tasks, return_exceptions=True),
                    timeout=prompt_delivery_timeout,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    f"[{match.match_id}] Attack prompt verification timed out after {prompt_delivery_timeout}s; continuing"
                )
            finally:
                for task in verification_tasks:
                    if not task.done():
                        task.cancel()
                if verification_tasks:
                    await asyncio.gather(*verification_tasks, return_exceptions=True)

            undelivered = set(match.agent_sessions.keys()) - delivered_players
            for pid in undelivered:
                logger.error(f"[Player {pid}] Failed to deliver attack prompt: not confirmed in session file")

            for pid, session in match.agent_sessions.items():
                backend = self._get_player_backend(match, pid)
                agent_client = self._get_player_client(match, pid)
                if backend is None or agent_client is None:
                    continue
                backend.unfreeze_buffered_messages(agent_client, session)
                if session.has_buffered_messages:
                    drain_tasks.append(
                        asyncio.create_task(
                            backend.drain_buffered_messages(agent_client, session),
                            name=f"drain_buffered_player_{pid}",
                        )
                    )

            if attack_tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*attack_tasks, return_exceptions=True),
                        timeout=prompt_delivery_timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[{match.match_id}] Attack prompt dispatch timed out after {prompt_delivery_timeout}s; continuing"
                    )
                finally:
                    for task in attack_tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*attack_tasks, return_exceptions=True)
            if drain_tasks:
                drain_results = await asyncio.gather(*drain_tasks, return_exceptions=True)
                for result in drain_results:
                    if isinstance(result, BaseException):
                        logger.warning(f"[{match.match_id}] buffered message drain failed: {result}")

            attack_keepalive_task = asyncio.create_task(self._attack_keepalive_loop(match))

            logger.info(f"[{match.match_id}] Attack phase: {attack_duration}s (network open)")
            await asyncio.sleep(attack_duration)

            await self.end_match(match.match_id)
        finally:
            for task in [*attack_tasks, *verification_tasks, *drain_tasks]:
                await self._cancel_task(task, current_task)
            await self._cancel_task(attack_keepalive_task, current_task)
            await self._cancel_task(defense_keepalive_task, current_task)
            await self._cancel_task(heartbeat_task, current_task)

    async def _heartbeat_loop(self, match: MatchState, total_seconds: int):
        HEARTBEAT_INTERVAL = 30
        start = asyncio.get_running_loop().time()
        while match.status in ("defense", "attack"):
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            elapsed = asyncio.get_running_loop().time() - start
            remaining = max(0, total_seconds - elapsed)
            leaderboard = self._get_match_leaderboard(match)
            match.add_event("HEARTBEAT", {
                "phase": match.status,
                "remaining_seconds": int(remaining),
                "leaderboard": leaderboard,
            })
            await self.broadcast({
                "type": "HEARTBEAT",
                "match_id": match.match_id,
                "phase": match.status,
                "remaining_seconds": int(remaining),
                "leaderboard": leaderboard,
                "timestamp": datetime.now().isoformat(),
            })

    async def _open_arena_network(self, match: MatchState):
        """创建共享竞技场网络，将所有容器连接上去（全异步，不阻塞事件循环）"""
        client = docker.from_env()
        loop = asyncio.get_running_loop()

        arena_network_name = f"awd_{match.match_id}_arena"

        def _create_arena_network():
            try:
                # arena 网络分配单独的 /24 子网，避免耗尽 Docker 默认地址池
                match_hash = int(hashlib.md5(match.match_id.encode()).hexdigest()[:4], 16) % 256
                third_octets = list(range(match_hash, 256)) + list(range(0, match_hash))
                candidate_subnets = [f"10.200.{third_octet}.0/24" for third_octet in third_octets]
                subnet, gateway = _choose_available_subnet(client, candidate_subnets)

                ipam_pool = IPAMPool(subnet=subnet, gateway=gateway)
                ipam_config = IPAMConfig(pool_configs=[ipam_pool])

                net = client.networks.create(
                    arena_network_name,
                    driver="bridge",
                    check_duplicate=True,
                    ipam=ipam_config
                )
                logger.info(f"Created arena network: {arena_network_name} with subnet {subnet}")
                return net
            except APIError as e:
                if "already exists" in str(e):
                    return client.networks.get(arena_network_name)
                raise

        arena_network = await loop.run_in_executor(None, _create_arena_network)

        # 把所有容器（agent + target）并行连接 arena 网络
        async def _connect_container(container_name: str):
            def _do_connect():
                try:
                    container = client.containers.get(container_name)
                    arena_network.connect(container)
                    logger.info(f"Connected {container_name} to arena network")
                except APIError as e:
                    if "already exists" in str(e):
                        pass
                    else:
                        logger.error(f"Failed to connect {container_name} to arena: {e}")
            await loop.run_in_executor(None, _do_connect)

        connect_tasks = [
            _connect_container(cname)
            for player in match.players.values()
            for cname in [player.container_name, player.target_container]
        ]
        await asyncio.gather(*connect_tasks)

        await asyncio.sleep(2)

        match.add_event("NETWORK_OPENED", {
            "arena_network": arena_network_name,
            "containers_connected": len(match.players) * 2,
        })

    async def submit_flag(self, match_id: str, submission: FlagSubmission, player_id: Optional[int] = None) -> Dict:
        """处理 Flag 提交"""
        match = self.matches.get(match_id)
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")

        if match.status != "attack":
            raise HTTPException(status_code=400, detail="Flag submissions are only accepted during attack phase")

        attacker_id = player_id if player_id is not None else submission.player_id
        if attacker_id is None:
            raise HTTPException(status_code=400, detail="Missing player identity")
        if attacker_id not in match.players:
            raise HTTPException(status_code=404, detail="Player not found")

        async with match.submission_lock():
            result = await match.flag_manager.validate_submission(
                attacker_id,
                submission.flag,
                submission.target_player_id,
                player_count=len(match.players),
            )
            submission_record = dict(result["submission_record"])
            submission_record["points"] = result.get("points", 0)
            await database.save_submission(match_id, submission_record)
            match.persisted_submissions.append(dict(submission_record))
            public_submission_record = sanitize_public_payload(submission_record)
            await match.add_event_and_persist("FLAG_SUBMISSION", public_submission_record)
            await self.broadcast({
                "type": "FLAG_SUBMISSION",
                "match_id": match_id,
                **public_submission_record,
            })

            if result["success"]:
                match.scoring_engine.update_scores(
                    match.players, match.persisted_submissions
                )
                leaderboard = self._get_match_leaderboard(match)
                captured_event = {
                    "attacker_id": attacker_id,
                    "victim_id": result["victim_id"],
                    "points": result["points"],
                    "leaderboard": leaderboard,
                }
                if submission_record.get("flag_slot") is not None:
                    captured_event["flag_slot"] = submission_record["flag_slot"]
                if submission_record.get("flag_index") is not None:
                    captured_event["flag_index"] = submission_record["flag_index"]

                match.add_event("FLAG_CAPTURED", captured_event)

                await self.broadcast({
                    "type": "FLAG_CAPTURED",
                    "match_id": match_id,
                    **captured_event,
                })

                victim_session = match.agent_sessions.get(result["victim_id"])
                if victim_session:
                    victim_backend = self._get_player_backend(match, result["victim_id"])
                    victim_client = self._get_player_client(match, result["victim_id"])
                    if victim_backend is None or victim_client is None:
                        logger.warning(
                            f"[Player {result['victim_id']}] No agent client available for interruption delivery"
                        )
                    else:
                        flag_index = submission_record.get("flag_index")
                        flag_label = f" #{flag_index}" if isinstance(flag_index, int) else ""
                        alert_text = (
                            f"Your flag{flag_label} was captured by Player {attacker_id}! "
                            f"You lost {abs(match.scoring_engine.config.get('defenseFailure', -50))} points. "
                            f"Check your defenses!"
                        )
                        alert_state = await victim_backend.enqueue_buffered_message(
                            victim_client,
                            victim_session,
                            f"[ALERT] {alert_text}",
                            timeout=120,
                            message_kind="flag_alert",
                            dedupe_key="flag_alert",
                            merge_strategy="append",
                        )
                        match.add_event("FLAG_CAPTURED_ALERT", {
                            "player_id": result["victim_id"],
                            "attacker_id": attacker_id,
                            "buffer_state": alert_state,
                            "mode": MESSAGE_MODE_BUFFERED,
                        })
                        logger.info(
                            f"[Player {result['victim_id']}] flag alert enqueue result="
                            f"{alert_state} attacker={attacker_id}"
                        )
            else:
                public_submission_record = sanitize_public_payload(submission_record)
                await match.add_event_and_persist("FLAG_SUBMISSION_REJECTED", public_submission_record)
                await self.broadcast({
                    "type": "FLAG_SUBMISSION_REJECTED",
                    "match_id": match_id,
                    **public_submission_record,
                })

        result = dict(result)
        result["player_feedback"] = self._build_submission_feedback(result)
        return result

    async def end_match(self, match_id: str) -> Dict:
        match = self.matches.get(match_id)
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")

        if match.status == "finished":
            final_leaderboard = self._enrich_leaderboard(
                match,
                self._restore_scores_from_persisted_state(match),
            )
            return {
                "match_id": match_id,
                "status": "finished",
                "leaderboard": final_leaderboard,
                "agent_logs": sanitize_public_agent_logs(match.agent_logs),
                "player_code_export": match.player_code_export,
                "events": visible_match_events(match),
            }

        current_task = asyncio.current_task()
        await self._cancel_task(match._startup_task, current_task)

        match.status = "finished"
        match.finished_at = datetime.now()
        await database.update_match_status(match_id, match.status, match.finished_at)

        # 停止后台任务
        await self._stop_match_background_tasks(match, current_task)

        # 收集所有 Agent 的完整会话日志
        agent_logs = {}
        for pid, session in match.agent_sessions.items():
            try:
                player_backend = self._get_player_backend(match, pid)
                player_client = match.player_clients.get(pid) or match.agent_client
                if player_backend is None or player_client is None:
                    raise RuntimeError("No agent client available for session log collection")
                log_content = await player_backend.collect_session_log(player_client, session)
                if log_content:
                    agent_logs[pid] = log_content
                    logger.info(f"[Player {pid}] Session log collected ({len(log_content)} bytes)")
                else:
                    agent_logs[pid] = "(no session log found)"
            except Exception as e:
                agent_logs[pid] = f"(error collecting log: {e})"
                logger.error(f"[Player {pid}] Failed to collect session log: {e}")
        match.agent_logs = agent_logs
        public_agent_logs = sanitize_public_agent_logs(agent_logs)

        await match.add_event_and_persist("AGENT_LOGS_COLLECTED", {
            "players": {pid: len(log) for pid, log in agent_logs.items()},
            "logs": public_agent_logs,
        })

        if match.config.mode == "werewolf":
            if match.werewolf_state is not None:
                judge_config = WerewolfJudgeConfig.from_env()
                if not match.config.werewolf.aiJudgeEnabled:
                    judge_config = WerewolfJudgeConfig(enabled=False)
                judgement = await judge_werewolf_match(
                    match.werewolf_state,
                    # Must pass werewolf_state.public_events (which holds both public and hidden
                    # events) — match.events only contains public ones after the audience split.
                    list(match.werewolf_state.public_events),
                    config=judge_config,
                    logger=logger,
                )
                final_leaderboard = apply_judgement_to_state(match.werewolf_state, judgement)
            else:
                final_leaderboard = match.scoring_engine.get_leaderboard(match.players)
                judgement = {
                    "winning_team": None,
                    "losing_team": None,
                    "player_scores": [],
                    "match_summary": "比赛在狼人杀状态初始化前结束。",
                    "key_turning_points": [],
                    "judge_confidence": 0,
                    "judge_fallback": True,
                    "fallback_reason": "werewolf_state_not_initialized",
                }
            match.persisted_leaderboard = final_leaderboard
            judgement_payload = {
                "mode": "werewolf",
                "leaderboard": final_leaderboard,
                **judgement,
            }
            await match.add_event_and_persist("WEREWOLF_AI_JUDGEMENT", judgement_payload)
            await self.broadcast({
                "type": "WEREWOLF_AI_JUDGEMENT",
                "match_id": match_id,
                **judgement_payload,
            })
            match.player_code_export = None
        else:
            try:
                export_result = await asyncio.to_thread(export_match_player_code, match)
                match.player_code_export = export_result.to_event_payload()
                await match.add_event_and_persist("PLAYER_CODE_EXPORT_READY", match.player_code_export)
            except Exception as export_error:
                logger.exception(f"[{match_id}] Failed to export player code bundle: {export_error}")
                match.player_code_export = build_failed_export_payload(
                    match_id,
                    str(export_error),
                    generated_at=datetime.now().isoformat(),
                    failure_stage="export_generation",
                )
                await match.add_event_and_persist("PLAYER_CODE_EXPORT_FAILED", match.player_code_export)

            final_leaderboard = self._restore_scores_from_persisted_state(match)
            if match.persisted_leaderboard:
                recomputed_has_non_zero = self._leaderboard_has_non_zero_scores(final_leaderboard)
                persisted_has_non_zero = self._leaderboard_has_non_zero_scores(match.persisted_leaderboard)
                if persisted_has_non_zero and not recomputed_has_non_zero:
                    logger.warning(
                        f"[{match_id}] Recomputed final leaderboard was zeroed; using last persisted leaderboard snapshot"
                    )
                    final_leaderboard = match.persisted_leaderboard
            final_leaderboard = self._enrich_leaderboard(match, final_leaderboard)

        duration_seconds = (
            match.finished_at - match.started_at
        ).total_seconds() if match.started_at else 0

        await match.add_event_and_persist("MATCH_FINISHED", {
            "mode": match.config.mode,
            "leaderboard": final_leaderboard,
            "duration_seconds": duration_seconds,
        })

        await self.broadcast({
            "type": "MATCH_FINISHED",
            "match_id": match_id,
            "leaderboard": final_leaderboard,
        })

        logger.info(f"[{match_id}] Match finished. Final leaderboard: {json.dumps(final_leaderboard, default=str)}")

        if not match.resources_destroyed:
            if match._destroy_task is None or match._destroy_task.done():
                match._destroy_task = asyncio.create_task(self.destroy_match(match_id))
            await match._destroy_task

        return {
            "match_id": match_id,
            "status": "finished",
            "leaderboard": final_leaderboard,
            "agent_logs": public_agent_logs,
            "player_code_export": match.player_code_export,
            "events": visible_match_events(match),
        }

    async def destroy_match(self, match_id: str):
        match = self.matches.get(match_id)
        if not match:
            return

        if match.resources_destroyed:
            return

        current_task = asyncio.current_task()
        original_status = match.status
        if match._destroy_task and not match._destroy_task.done() and match._destroy_task is not current_task:
            await match._destroy_task
            return

        await self._cancel_task(match._startup_task, current_task)
        await self._stop_match_background_tasks(match, current_task)

        client = docker.from_env()
        loop = asyncio.get_running_loop()

        # 并行停止 + 删除所有容器
        async def _remove_container(container_name: str):
            if not container_name:
                return
            def _do():
                try:
                    c = client.containers.get(container_name)
                    c.stop(timeout=10)
                    c.remove()
                    logger.info(f"Removed container: {container_name}")
                except Exception as e:
                    logger.warning(f"Failed to remove {container_name}: {e}")
            await loop.run_in_executor(None, _do)

        container_names: set[str] = set()
        for player in match.players.values():
            if player.container_name:
                container_names.add(player.container_name)
            if match.config.mode != "werewolf" and player.target_container:
                container_names.add(player.target_container)
        container_tasks = [_remove_container(cname) for cname in container_names]
        await asyncio.gather(*container_tasks)

        # 清理所有网络：每个选手的隔离网络 + arena 共享网络
        network_names: set[str] = set()
        for player in match.players.values():
            if player.network_name:
                network_names.add(player.network_name)
        if match.config.mode != "werewolf":
            network_names.add(f"awd_{match_id}_arena")

        async def _remove_network(network_name: str):
            def _do():
                try:
                    net = client.networks.get(network_name)
                    net.remove()
                    logger.info(f"Removed network: {network_name}")
                except Exception:
                    pass
            await loop.run_in_executor(None, _do)

        await asyncio.gather(*[_remove_network(n) for n in network_names])

        cleanup_tasks = []
        for pid, session in match.agent_sessions.items():
            backend = self._get_player_backend(match, pid)
            if backend is None:
                continue
            cleanup_tasks.append(
                backend.cleanup(
                    match,
                    pid,
                    session,
                    match.player_clients.get(pid) or match.agent_client,
                )
            )
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)

        # 清理 player_id -> match_id 反向索引
        for pid in list(match.players.keys()):
            self.player_match_index.pop(pid, None)
            self._revoke_player_read_token(match, pid)

        match.player_ssh_key_materials = {}

        match.resources_destroyed = True

        if original_status in {"finished", "aborted", "error"}:
            await match.add_event_and_persist("MATCH_RESOURCES_DESTROYED", {
                "containers_removed": len(container_names),
                "networks_removed": len(network_names),
                "status": original_status,
            })

        if original_status == "finished":
            await self._update_loop_after_match_cleanup(match)

        match.agent_client = None
        match.player_clients = {}
        match.player_backends = {}
        match.agent_sessions = {}
        if match._startup_task is not current_task:
            match._startup_task = None
        match._flag_task = None
        match._sla_task = None
        match._match_timer_task = None
        if match._destroy_task is not current_task:
            match._destroy_task = None

    def get_match_status(self, match_id: str) -> Dict:
        """Get match status."""
        match = self.matches.get(match_id)
        if not match:
            raise HTTPException(status_code=404, detail="Match not found")

        leaderboard = self._get_match_leaderboard(match)

        now = datetime.now()
        elapsed = 0
        if match.started_at:
            elapsed = (now - match.started_at).total_seconds()

        remaining_seconds = self._get_remaining_seconds(match, now)
        players_payload = {
            str(pid): {
                "player_id": player.player_id,
                **self._build_player_identity_fields(match, player.player_id),
                "container_name": player.container_name,
                "target_container": player.target_container,
                "target_ip": player.target_ip,
                "target_port": player.target_port,
                "network_name": player.network_name,
                "ready_status": player.ready_status,
                "ready_reason": player.ready_reason,
                "readiness_details": self._sync_player_readiness_details(match, pid),
                "score": player.score,
                "attack_score": player.attack_score,
                "defense_score": player.defense_score,
                "sla_score": player.sla_score,
                "sla_up": player.sla_up,
                "sla_down_minutes": player.sla_down_minutes,
                "flags_captured": player.flags_captured,
                "flags_lost": player.flags_lost,
        }
        for pid, player in match.players.items()
        }
        visible_events = visible_match_events(match)

        return jsonable_encoder({
            "match_id": match_id,
            "mode": match.config.mode,
            "status": match.status,
            "elapsed_seconds": elapsed,
            "remaining_seconds": remaining_seconds,
            "player_count": len(match.players),
            "players": players_payload,
            "leaderboard": leaderboard,
            "events_count": len(visible_events),
            "recent_events": visible_events[-10:],
            "werewolf_board": match.config.werewolf.board if match.config.mode == "werewolf" else None,
            "werewolf_board_label": board_label(match.config.werewolf.board) if match.config.mode == "werewolf" else None,
            "werewolf": match.werewolf_state.public_summary(include_roles=match.status == "finished")
            if match.werewolf_state is not None else None,
        })

    async def broadcast(self, message: dict):
        safe_message = sanitize_public_payload(message)
        if not isinstance(safe_message, dict):
            safe_message = {}
        msg_match_id = safe_message.get("match_id")

        targets = [
            ws for ws in self.ws_connections
            if not (msg_match_id and self.ws_subscriptions.get(ws) and self.ws_subscriptions.get(ws) != msg_match_id)
        ]

        async def _send(ws):
            try:
                await ws.send_json(safe_message)
                return ws, True
            except Exception:
                return ws, False

        if targets:
            results = await asyncio.gather(*[_send(ws) for ws in targets], return_exceptions=True)
            for item in results:
                if isinstance(item, Exception):
                    continue
                ws, ok = item
                if not ok:
                    self.ws_connections.remove(ws)
                    self.ws_subscriptions.pop(ws, None)

        await self._observe_for_commentary(safe_message)

    async def _observe_for_commentary(self, message: dict):
        if not getattr(self.commentator, "available", False):
            return
        match_id = message.get("match_id")
        if not isinstance(match_id, str):
            return
        match = self.matches.get(match_id)
        if match is None:
            return
        await self.commentator.observe_event(match, message, self._emit_commentary)

    async def _emit_commentary(self, match: MatchState, payload: Dict[str, Any]) -> None:
        await match.add_event_and_persist("AI_COMMENTARY", payload)
        await self.broadcast({
            "type": "AI_COMMENTARY",
            "match_id": match.match_id,
            **payload,
        })

    async def start_ws_heartbeat(self):
        """启动 WebSocket 心跳任务，定期清理死连接"""
        self._ws_heartbeat_task = asyncio.create_task(self._ws_heartbeat_loop())

    async def _ws_heartbeat_loop(self):
        while True:
            await asyncio.sleep(30)
            dead = []
            for ws in list(self.ws_connections):
                try:
                    await ws.send_json({"type": "ping"})
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.ws_connections.remove(ws)
                self.ws_subscriptions.pop(ws, None)


template_store = TemplateStore()


# ==================== Lifespan & DB Recovery ====================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await referee.validate_docker_api_compatibility()

    # 启动时初始化数据库并加载数据
    await database.init_db()
    matches_data = await database.load_all_matches()

    for m_data in matches_data:
        try:
            config = MatchConfig(**m_data["config"])
            match = MatchState(m_data["match_id"], config)
            if config.mode == "werewolf":
                role_reveal: Dict[str, Any] = {}
                public_state: Optional[Dict[str, Any]] = None
                for event in reversed(m_data["events"]):
                    if event.get("type") != "WEREWOLF_GAME_FINISHED":
                        continue
                    data = event.get("data")
                    if isinstance(data, dict):
                        raw_reveal = data.get("role_reveal")
                        if isinstance(raw_reveal, dict):
                            role_reveal = raw_reveal
                        raw_public_state = data.get("public_state")
                        if isinstance(raw_public_state, dict):
                            public_state = raw_public_state
                    break
                if role_reveal:
                    player_names = {player.id: player.name for player in config.players}
                    restored_players: Dict[int, Any] = {}
                    for raw_pid, item in role_reveal.items():
                        if not str(raw_pid).isdigit() or not isinstance(item, dict):
                            continue
                        restored_players[int(raw_pid)] = item.get("role")
                    if len(restored_players) == len(config.players):
                        counts = {role: list(restored_players.values()).count(role) for role in DEFAULT_ROLE_COUNTS}
                        match.werewolf_state = create_werewolf_state(
                            [player.id for player in config.players],
                            player_names=player_names,
                            role_counts=counts,
                            board=config.werewolf.board,
                            sheriff_enabled=config.werewolf.sheriffEnabled,
                            werewolf_reveal_enabled=config.werewolf.werewolfRevealEnabled,
                            max_days=config.werewolf.maxDays,
                            seed=0,
                        )
                        for pid, role in restored_players.items():
                            if pid in match.werewolf_state.players:
                                ww_player = match.werewolf_state.players[pid]
                                ww_player.role = role
                                if isinstance(role_reveal.get(str(pid)), dict):
                                    ww_player.alive = bool(role_reveal[str(pid)].get("alive", True))
                        if public_state:
                            match.werewolf_state.day = int(public_state.get("day") or 0)
                            match.werewolf_state.phase = str(public_state.get("phase") or "finished")
                            sheriff_id = public_state.get("sheriff_id")
                            match.werewolf_state.sheriff_id = sheriff_id if isinstance(sheriff_id, int) else None
                            match.werewolf_state.badge_destroyed = bool(public_state.get("badge_destroyed", False))

            # 恢复状态，如果是进行中，标记为 aborted
            status = m_data["status"]
            if status in ["initializing", "defense", "attack"]:
                status = "aborted"
                await database.update_match_status(m_data["match_id"], status, datetime.now())
            if status.startswith("werewolf_") or status in {"creating_werewolf_agents"}:
                status = "aborted"
                await database.update_match_status(m_data["match_id"], status, datetime.now())

            match.status = status
            match.created_at = datetime.fromisoformat(m_data["created_at"])
            if m_data.get("finished_at"):
                match.finished_at = datetime.fromisoformat(m_data["finished_at"])
            has_destroyed_event = any(_event_type(event) == "MATCH_RESOURCES_DESTROYED" for event in m_data["events"])
            if status in {"finished", "aborted", "error"}:
                # Resource cleanup is a separate fact from match terminal state. Trust the
                # persisted cleanup event; otherwise schedule best-effort orphan cleanup.
                match.resources_destroyed = has_destroyed_event
            if status in {"finished", "aborted", "error"} and not match.resources_destroyed:
                async def _cleanup_orphan(mid=m_data["match_id"]):
                    try:
                        # Wait until referee.matches is populated by lifespan
                        await asyncio.sleep(2)
                        await referee.destroy_match(mid)
                    except Exception as e:
                        logger.warning(f"[{mid}] orphan cleanup failed: {e}")
                asyncio.create_task(_cleanup_orphan())

            match.events = m_data["events"]
            match.persisted_submissions = await database.load_submissions(match.match_id)
            container_metadata_by_player = restore_container_metadata_from_events(match.events, match.match_id)
            for event in reversed(match.events):
                data = event.get("data")
                if not isinstance(data, dict):
                    continue
                leaderboard = data.get("leaderboard")
                if isinstance(leaderboard, dict) and leaderboard:
                    if config.mode == "werewolf":
                        match.persisted_leaderboard = leaderboard
                        break
                    existing_values = [entry for entry in match.persisted_leaderboard.values() if isinstance(entry, dict)]
                    incoming_values = [entry for entry in leaderboard.values() if isinstance(entry, dict)]
                    existing_has_non_zero = any((entry.get("total_score") or 0) != 0 for entry in existing_values)
                    incoming_has_non_zero = any((entry.get("total_score") or 0) != 0 for entry in incoming_values)
                    if incoming_has_non_zero or not existing_has_non_zero:
                        match.persisted_leaderboard = leaderboard
                    if incoming_has_non_zero:
                        break

            if status == "finished" and not any(_event_type(event) == "MATCH_FINISHED" for event in match.events):
                source_data = latest_leaderboard_event_data(match.events)
                if source_data is not None:
                    source_payload = copy.deepcopy(source_data)
                    backfill_payload = {
                        "leaderboard": source_payload.get("leaderboard"),
                        "duration_seconds": source_payload.get("duration_seconds", 0),
                        "backfilled": True,
                        "source": "latest_leaderboard_event",
                    }
                    if "remaining_seconds" in source_payload:
                        backfill_payload["source_remaining_seconds"] = source_payload["remaining_seconds"]
                    await match.add_event_and_persist("MATCH_FINISHED", backfill_payload)

            for event in reversed(match.events):
                if event.get("type") != "AGENT_LOGS_COLLECTED":
                    continue
                data = event.get("data")
                if isinstance(data, dict):
                    logs = data.get("logs")
                    if isinstance(logs, dict):
                        match.agent_logs = {
                            int(pid): str(content)
                            for pid, content in logs.items()
                            if str(pid).isdigit() and isinstance(content, str)
                        }
                break

            for event in reversed(match.events):
                if event.get("type") not in {"PLAYER_CODE_EXPORT_READY", "PLAYER_CODE_EXPORT_FAILED"}:
                    continue
                data = event.get("data")
                if isinstance(data, dict):
                    match.player_code_export = data
                break

            latest_ready_by_player: Dict[int, Dict[str, Any]] = {}
            for event in match.events:
                data = event.get("data")
                if not isinstance(data, dict):
                    continue
                if event.get("type") not in {"AGENT_READY", "AGENT_NOT_READY", "AGENT_READINESS_LAYER"}:
                    continue
                player_id = data.get("player_id")
                if not isinstance(player_id, int):
                    continue
                ready_snapshot = latest_ready_by_player.get(player_id, {})
                ready_status = ready_snapshot.get("ready_status")
                ready_reason = ready_snapshot.get("ready_reason")
                if event.get("type") in {"AGENT_READY", "AGENT_NOT_READY"}:
                    ready_status = data.get("ready_status")
                    if not isinstance(ready_status, str):
                        ready_status = event.get("type")
                    ready_reason = data.get("ready_reason")
                    if not isinstance(ready_reason, str):
                        fallback_reason = data.get("reason")
                        ready_reason = fallback_reason if isinstance(fallback_reason, str) else None
                readiness_details_value = data.get("readiness_details")
                readiness_details: Dict[str, Any] = {}
                if isinstance(readiness_details_value, dict):
                    readiness_details = readiness_details_value
                else:
                    fallback_readiness_details = ready_snapshot.get("readiness_details")
                    if isinstance(fallback_readiness_details, dict):
                        readiness_details = fallback_readiness_details
                latest_ready_by_player[player_id] = {
                    "ready_status": ready_status,
                    "ready_reason": ready_reason,
                    "readiness_details": readiness_details,
                }

            # 恢复基本玩家信息以便展示
            for player in config.players:
                referee.player_match_index[player.id] = match.match_id
                ready_snapshot = latest_ready_by_player.get(player.id, {})
                readiness_details: Dict[str, Any] = {}
                readiness_details_value = ready_snapshot.get("readiness_details")
                if isinstance(readiness_details_value, dict):
                    readiness_details = readiness_details_value
                container_metadata = container_metadata_by_player.get(player.id, {})
                match.players[player.id] = PlayerState(
                    player_id=player.id,
                    container_name=str(container_metadata.get("agent_container") or f"claw_{match.match_id}_{player.id}"),
                    target_container=str(container_metadata.get("target_container") or f"target_{match.match_id}_{player.id}"),
                    network_name=str(container_metadata.get("network") or f"awd_{match.match_id}_player_{player.id}"),
                    target_ip=str(container_metadata.get("target_ip") or f"10.1.{player.id}.100"),
                    maintenance_auth_mode="ssh_key",
                    maintenance_helper_command="target-ssh",
                    ready_status=str(ready_snapshot.get("ready_status") or "PENDING"),
                    ready_reason=ready_snapshot.get("ready_reason") if isinstance(ready_snapshot.get("ready_reason"), str) else None,
                    readiness_details=readiness_details,
                )

            if config.mode == "werewolf" and match.persisted_leaderboard:
                RefereeEngine._apply_leaderboard_snapshot(match, match.persisted_leaderboard)
            else:
                RefereeEngine._restore_scores_from_persisted_state(match)

            referee.matches[match.match_id] = match

            if status == "aborted":
                logger.info(f"Cleaning up resources for aborted match {match.match_id}...")
                await referee.destroy_match(match.match_id)

        except Exception as e:
            logger.error(f"Failed to load match {m_data.get('match_id')}: {e}")

    logger.info(f"Loaded {len(referee.matches)} historical matches from database.")

    # Match retention is opt-in. Silent default pruning can delete replay/history data
    # after a calendar rollover, so only prune when the operator explicitly configures it.
    retention_raw = (
        os.environ.get("OPENCLAW_MATCH_RETENTION_DAYS")
        or os.environ.get("WEREWOLF_MATCH_RETENTION_DAYS")
        or "0"
    )
    try:
        retention_days = int(retention_raw)
    except ValueError:
        retention_days = 0
    if retention_days > 0:
        try:
            pruned = await database.prune_old_matches(retention_days)
            if pruned > 0:
                logger.info(f"Match retention: pruned {pruned} matches older than {retention_days} days.")
        except Exception as exc:
            logger.warning(f"Match retention failed: {exc}")

    # 启动 WebSocket 心跳
    await referee.start_ws_heartbeat()

    yield


# ==================== FastAPI App ====================

referee = RefereeEngine()

app = FastAPI(title="OpenClaw AWD Referee Engine", version="2.0.0", lifespan=lifespan)

def _parse_cors_origins() -> List[str]:
    return parse_cors_origins(os.environ.get("CORS_ORIGINS"), DEFAULT_CORS_ORIGINS)


_cors_origins = _parse_cors_origins()
_cors_allow_credentials = cors_allow_credentials(_cors_origins)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-API-Key"],
)


# ==================== API Auth ====================

_insecure_no_auth_allowed = insecure_no_auth_allowed
_ws_api_key_query_allowed = ws_api_key_query_allowed
_configured_api_key = configured_api_key
_api_key_is_valid = api_key_is_valid


@app.get("/api/auth/status")
async def auth_status(api_key: Optional[str] = Security(api_key_header)):
    return auth_status_payload(api_key)


def verify_player_token(token: str = Security(player_token_header)) -> PlayerTokenContext:
    resolved = referee.player_read_token_store.resolve(token)
    if not resolved:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid player token",
        )

    match_id, player_id = resolved
    match = referee.matches.get(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    if player_id not in match.players:
        raise HTTPException(status_code=404, detail="Player not found")

    return PlayerTokenContext(match_id=match_id, player_id=player_id)


def _websocket_auth_is_valid(websocket: WebSocket) -> tuple[bool, int, str]:
    websocket_client = getattr(websocket, "client", None)
    return websocket_auth_is_valid(
        ticket=websocket.query_params.get("ticket"),
        header_key=websocket.headers.get("x-api-key"),
        query_key=websocket.query_params.get("api_key"),
        client_host=websocket_client.host if websocket_client else None,
        user_agent=websocket.headers.get("user-agent"),
        consume_ticket=lambda ticket, client_host, user_agent: referee.consume_ws_ticket(
            ticket,
            client_host=client_host,
            user_agent=user_agent,
        ),
        api_key_is_valid=_api_key_is_valid,
        ws_api_key_query_allowed=_ws_api_key_query_allowed,
    )

# --- 比赛管理 ---

@app.post("/api/matches/start", dependencies=[Depends(verify_api_key)])
async def start_match(config: MatchConfig):
    """启动完整比赛"""
    result = await referee.start_match(config)
    return result

@app.post("/api/matches/{match_id}/end", dependencies=[Depends(verify_api_key)])
async def end_match(match_id: str):
    """结束比赛"""
    result = await referee.end_match(match_id)
    return result

async def ensure_player_code_export_bundle(match: MatchState):
    export_path = get_player_code_export_path(match.match_id)
    export_payload = match.player_code_export if isinstance(match.player_code_export, dict) else {}
    export_is_partial = player_code_export_payload_is_partial(export_payload)
    if export_path.exists() and not export_is_partial:
        return export_path

    async with match.player_code_export_lock():
        export_payload = match.player_code_export if isinstance(match.player_code_export, dict) else {}
        export_is_partial = player_code_export_payload_is_partial(export_payload)
        if export_path.exists() and not export_is_partial:
            return export_path

        if match.status != "finished":
            raise HTTPException(status_code=409, detail="Match has not finished yet")

        if isinstance(match.player_code_export, dict) and match.player_code_export.get("status") == "failed":
            raise HTTPException(
                status_code=404,
                detail=str(match.player_code_export.get("error") or "Player code export bundle is not available"),
            )

        try:
            export_result = await asyncio.to_thread(export_match_player_code, match)
            match.player_code_export = export_result.to_event_payload()
            await match.add_event_and_persist("PLAYER_CODE_EXPORT_READY", match.player_code_export)
        except Exception as export_error:
            logger.exception(f"[{match.match_id}] Failed to generate player code export on demand: {export_error}")
            match.player_code_export = build_failed_export_payload(
                match.match_id,
                str(export_error),
                generated_at=datetime.now().isoformat(),
                failure_stage="on_demand_export_generation",
            )
            await match.add_event_and_persist("PLAYER_CODE_EXPORT_FAILED", match.player_code_export)
            raise HTTPException(status_code=404, detail=str(export_error))

    if export_path.exists():
        return export_path

    raise HTTPException(status_code=404, detail="Player code export bundle is not available")

@app.get("/api/matches/{match_id}/player-code-export", dependencies=[Depends(verify_api_key)])
async def get_player_code_export(match_id: str):
    try:
        export_path = get_player_code_export_path(match_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid match_id") from None

    match = referee.matches.get(match_id)
    if match is not None:
        export_path = await ensure_player_code_export_bundle(match)
        return FileResponse(
            export_path,
            media_type="application/zip",
            filename=export_path.name,
        )

    for row in await database.list_matches_summary():
        if row["match_id"] != match_id:
            continue
        if row["status"] != "finished":
            raise HTTPException(status_code=409, detail="Match has not finished yet")
        if export_path.exists():
            return FileResponse(
                export_path,
                media_type="application/zip",
                filename=export_path.name,
            )
        historical_match = await load_match_for_report(match_id)
        export_path = await ensure_player_code_export_bundle(historical_match)
        return FileResponse(
            export_path,
            media_type="application/zip",
            filename=export_path.name,
        )

    raise HTTPException(status_code=404, detail="Match not found")

@app.post("/api/matches/{match_id}/destroy", dependencies=[Depends(verify_api_key)])
async def destroy_match(match_id: str):
    """Destroy match resources."""
    await referee.destroy_match(match_id)
    return {"match_id": match_id, "status": "destroyed"}

@app.delete("/api/matches/{match_id}", dependencies=[Depends(verify_api_key)])
async def delete_match_record(match_id: str):
    """Permanently delete a match's DB rows. If match is still active, destroy its containers first."""
    match = referee.matches.get(match_id)
    if match is not None and not match.resources_destroyed:
        try:
            await referee.destroy_match(match_id)
        except Exception as exc:
            logger.warning(f"[{match_id}] destroy_match before delete failed: {exc}")
    # Drop from in-memory
    referee.matches.pop(match_id, None)
    rows = await database.delete_match(match_id)
    if rows == 0:
        raise HTTPException(status_code=404, detail="Match not found in database")
    try:
        export_dir = safe_player_code_export_dir(match_id, exports_root=get_exports_root())
        shutil.rmtree(export_dir, ignore_errors=True)
    except Exception as exc:
        logger.warning(f"[{match_id}] failed to remove player code export directory after delete: {exc}")
    logger.info(f"[{match_id}] match deleted by API")
    return {"match_id": match_id, "deleted": True}


@app.get("/api/matches/{match_id}", dependencies=[Depends(verify_api_key)])
async def get_match(match_id: str):
    """Get match status."""
    return referee.get_match_status(match_id)


@app.get("/api/player/status", response_model=PlayerStatusResponse)
async def get_player_status(ctx: PlayerTokenContext = Depends(verify_player_token)):
    return await referee.build_player_status(ctx.match_id, ctx.player_id)

@app.get("/api/matches", dependencies=[Depends(verify_api_key)])
async def list_matches():
    db_rows = await database.list_matches_summary()
    return {
        "matches": merge_match_summaries(
            db_rows,
            referee.matches,
            board_label=board_label,
            player_code_export_exists=_player_code_export_bundle_exists,
        )
    }


@app.get("/api/loops", dependencies=[Depends(verify_api_key)])
async def list_loops():
    return await referee.list_loops()


@app.post("/api/loops/{loop_id}/stop", dependencies=[Depends(verify_api_key)])
async def stop_loop(loop_id: str):
    return await referee.stop_loop(loop_id)


# --- Flag 提交 ---

@app.post("/api/submit")
async def submit_flag_global(submission: FlagSubmission, ctx: PlayerTokenContext = Depends(verify_player_token)):
    """Submit a flag through the global player lookup endpoint."""
    if submission.player_id is not None and submission.player_id != ctx.player_id:
        raise HTTPException(status_code=403, detail="Submission player_id does not match player token")
    match_id = ctx.match_id
    if not match_id or match_id not in referee.matches:
        raise HTTPException(status_code=404, detail="Player not found in any active match")
    return await referee.submit_flag(match_id, submission, player_id=ctx.player_id)

@app.post("/api/matches/{match_id}/submit")
async def submit_flag(match_id: str, submission: FlagSubmission, ctx: PlayerTokenContext = Depends(verify_player_token)):
    """Submit a flag for a specific match."""
    if ctx.match_id != match_id:
        raise HTTPException(status_code=403, detail="Player token does not belong to this match")
    if submission.player_id is not None and submission.player_id != ctx.player_id:
        raise HTTPException(status_code=403, detail="Submission player_id does not match player token")
    return await referee.submit_flag(match_id, submission, player_id=ctx.player_id)


# --- 排行榜 ---

@app.get("/api/matches/{match_id}/leaderboard", dependencies=[Depends(verify_api_key)])
async def get_leaderboard(match_id: str):
    """Get match leaderboard."""
    match = referee.matches.get(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    return {
        "match_id": match_id,
        "leaderboard": referee._get_match_leaderboard(match),
    }


@app.get("/api/matches/{match_id}/report.md", dependencies=[Depends(verify_api_key)])
async def get_match_report_markdown(match_id: str):
    match = await load_match_for_report(match_id)
    report = build_match_report_markdown(match, referee._get_match_leaderboard(match))
    safe_filename = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"match_{match_id}_report.md")
    return Response(
        content=report,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe_filename}"'},
    )


@app.get("/api/matches/{match_id}/submissions", dependencies=[Depends(verify_api_key)])
async def get_submissions(match_id: str):
    match = referee.matches.get(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    return {
        "match_id": match_id,
        "submissions": sanitize_public_payload(list(match.persisted_submissions)),
    }

@app.get("/api/leaderboard", dependencies=[Depends(verify_api_key)])
async def get_global_leaderboard():
    """Get the active match leaderboard."""
    for match_id, match in referee.matches.items():
        if match.status in ("defense", "attack"):
            return {
                "match_id": match_id,
                "leaderboard": referee._get_match_leaderboard(match),
            }

    return {"match_id": None, "leaderboard": {}}


@app.get("/api/matches/{match_id}/events", dependencies=[Depends(verify_api_key)])
async def get_events(
    match_id: str,
    limit: Annotated[int, Query(ge=1, le=MAX_EVENTS_LIMIT)] = DEFAULT_EVENTS_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    """获取比赛事件"""
    match = referee.matches.get(match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    return paginated_visible_match_events(match, limit=limit, offset=offset)


# --- WebSocket ---

@app.post("/api/ws-ticket", dependencies=[Depends(verify_api_key)])
async def issue_ws_ticket(request: Request):
    client_host = request.client.host if request.client else None
    allowed, retry_after = referee.check_ws_ticket_rate_limit(client_host=client_host)
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many WebSocket ticket requests",
            headers={"Retry-After": str(retry_after)},
        )
    return referee.issue_ws_ticket(
        client_host=client_host,
        user_agent=request.headers.get("user-agent"),
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    valid, _status_code, detail = _websocket_auth_is_valid(websocket)
    if not valid:
        await websocket.close(code=1008, reason=detail[:120])
        return

    await websocket.accept()
    referee.ws_connections.append(websocket)
    logger.info(f"WebSocket client connected (total: {len(referee.ws_connections)})")

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "subscribe":
                    subscribed_match_id = msg.get("match_id")
                    if subscribed_match_id:
                        referee.ws_subscriptions[websocket] = subscribed_match_id
                    await websocket.send_json({
                        "type": "subscribed",
                        "match_id": subscribed_match_id,
                    })
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        if websocket in referee.ws_connections:
            referee.ws_connections.remove(websocket)
        referee.ws_subscriptions.pop(websocket, None)
        logger.info(f"WebSocket client disconnected (total: {len(referee.ws_connections)})")


# --- 模板管理 ---

@app.get("/api/templates", dependencies=[Depends(verify_api_key)])
async def list_templates(tags: Optional[str] = None):
    """List templates, optionally filtered by tags."""
    templates = template_store.list()
    if tags:
        tag_list = [t.strip() for t in tags.split(",")]
        templates = [t for t in templates if any(tag in t.get("tags", []) for tag in tag_list)]
    return {"templates": templates}

@app.post("/api/templates", dependencies=[Depends(verify_api_key)])
async def create_template(data: ConfigTemplate):
    """Save a config as a template."""
    tpl = template_store.create(data)
    return {"success": True, "templateId": tpl["id"], "template": tpl}

@app.get("/api/templates/{template_id}", dependencies=[Depends(verify_api_key)])
async def get_template(template_id: str):
    """获取单个模板"""
    tpl = template_store.get(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template": tpl}

@app.put("/api/templates/{template_id}", dependencies=[Depends(verify_api_key)])
async def update_template(template_id: str, data: ConfigTemplate):
    """更新模板"""
    tpl = template_store.update(template_id, data)
    return {"success": True, "template": tpl}

@app.delete("/api/templates/{template_id}", dependencies=[Depends(verify_api_key)])
async def delete_template(template_id: str):
    """Delete a user template."""
    template_store.delete(template_id)
    return {"success": True}

@app.post("/api/templates/{template_id}/use", dependencies=[Depends(verify_api_key)])
async def use_template(template_id: str):
    """记录模板使用次数"""
    template_store.increment_usage(template_id)
    return {"success": True}

@app.get("/api/templates/{template_id}/export", dependencies=[Depends(verify_api_key)])
async def export_template(template_id: str, background_tasks: BackgroundTasks):
    """Export a template as JSON."""
    tpl = template_store.get(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(tpl, f, ensure_ascii=False, indent=2)
        tmp_path = f.name
    safe_name = tpl["name"].replace("/", "-").replace(" ", "_")
    background_tasks.add_task(os.unlink, tmp_path)
    return FileResponse(
        tmp_path,
        media_type="application/json",
        filename=f"{safe_name}.json",
    )

@app.post("/api/templates/import", dependencies=[Depends(verify_api_key)])
async def import_template(file: UploadFile = File(...)):
    """Import a template from JSON."""
    content = await file.read(MAX_TEMPLATE_IMPORT_BYTES + 1)
    if len(content) > MAX_TEMPLATE_IMPORT_BYTES:
        raise HTTPException(status_code=413, detail="Template file is too large")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Template JSON must be an object")

    tpl_data = ConfigTemplate(
        name=data.get("name", "Imported Template"),
        description=data.get("description", ""),
        tags=data.get("tags", []),
        config=data.get("config", {}),
        saveOptions=data.get("saveOptions") if isinstance(data.get("saveOptions"), dict) else None,
    )
    tpl = template_store.create(tpl_data)
    return {"success": True, "templateId": tpl["id"], "template": tpl}


# --- LLM 调试 ---

@app.post("/api/test-llm", dependencies=[Depends(verify_api_key)])
async def test_llm_connection(req: LLMTestRequest):
    """Test direct LLM connectivity."""
    import aiohttp
    import time

    base_url = _validate_outbound_url(req.baseUrl, field_name="baseUrl")
    proxy = _validate_outbound_url(req.proxy, field_name="proxy") if req.proxy else None

    payload = {
        "model": req.model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 16
    }

    headers = {
        "Authorization": f"Bearer {req.apiKey}",
        "Content-Type": "application/json"
    }

    start_time = time.time()
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{base_url}/chat/completions",
                json=payload,
                headers=headers,
                proxy=proxy,
                timeout=15
            ) as response:
                resp_text = await response.text()
                latency = time.time() - start_time

                if response.status == 200:
                    return {
                        "success": True,
                        "latency": latency,
                        "response": resp_text
                    }
                else:
                    return {
                        "success": False,
                        "error": f"HTTP {response.status}: {resp_text}"
                    }
    except Exception as e:
        return {"success": False, "error": str(e)}


# --- 健康检查 ---

@app.get("/health")
async def health_check():
    return build_health_payload(
        referee.matches,
        ws_connections=len(referee.ws_connections),
        orchestrator_available=HAS_ORCHESTRATOR,
        auth_mode=auth_mode_label(),
    )


_frontend_paths = frontend_dist_from_env(
    os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"),
)
FRONTEND_DIST = str(_frontend_paths.dist)
FRONTEND_INDEX = str(_frontend_paths.index)
FRONTEND_ASSETS = str(_frontend_paths.assets)

if _frontend_paths.complete:
    app.mount("/assets", StaticFiles(directory=FRONTEND_ASSETS), name="assets")

    @app.get("/", include_in_schema=False)
    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str = ""):
        if not should_serve_frontend_path(full_path):
            raise HTTPException(status_code=404)
        return FileResponse(FRONTEND_INDEX)
else:
    logger.info(f"Frontend dist incomplete at {FRONTEND_DIST}, skipping static file serving")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
