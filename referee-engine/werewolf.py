from __future__ import annotations

import aiohttp
import json
import logging
import os
import random
import re
import secrets
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Tuple

from env_utils import truthy, positive_int


ROLE_WEREWOLF = "werewolf"
ROLE_VILLAGER = "villager"
ROLE_SEER = "seer"
ROLE_WITCH = "witch"
ROLE_HUNTER = "hunter"
ROLE_GUARD = "guard"
ROLE_WHITE_WOLF_KING = "white_wolf_king"
ROLE_KNIGHT = "knight"

TEAM_WEREWOLF = "werewolf"
TEAM_GOOD = "good"

WOLF_ROLES = {ROLE_WEREWOLF, ROLE_WHITE_WOLF_KING}
GOOD_ROLES = {ROLE_VILLAGER, ROLE_SEER, ROLE_WITCH, ROLE_HUNTER, ROLE_GUARD, ROLE_KNIGHT}
GOD_ROLES = {ROLE_SEER, ROLE_WITCH, ROLE_HUNTER, ROLE_GUARD, ROLE_KNIGHT}
DEFAULT_ROLE_COUNTS = {
    ROLE_WEREWOLF: 4,
    ROLE_VILLAGER: 4,
    ROLE_SEER: 1,
    ROLE_WITCH: 1,
    ROLE_HUNTER: 1,
    ROLE_GUARD: 1,
    ROLE_WHITE_WOLF_KING: 0,
    ROLE_KNIGHT: 0,
}
STANDARD_GUARD_ROLE_COUNTS = dict(DEFAULT_ROLE_COUNTS)
WHITE_WOLF_KING_KNIGHT_ROLE_COUNTS = {
    ROLE_WEREWOLF: 3,
    ROLE_WHITE_WOLF_KING: 1,
    ROLE_VILLAGER: 4,
    ROLE_SEER: 1,
    ROLE_WITCH: 1,
    ROLE_HUNTER: 1,
    ROLE_GUARD: 0,
    ROLE_KNIGHT: 1,
}
WEREWOLF_BOARD_STANDARD_GUARD = "standard_guard"
WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT = "white_wolf_king_knight"
WEREWOLF_BOARD_ROLE_COUNTS = {
    WEREWOLF_BOARD_STANDARD_GUARD: STANDARD_GUARD_ROLE_COUNTS,
    WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT: WHITE_WOLF_KING_KNIGHT_ROLE_COUNTS,
}

PUBLIC_SELF_REVEAL_PHASES = {
    "sheriff_speech",
    "sheriff_pk_speech",
    "day_speech",
    "day_pk_speech",
}

WEREWOLF_EVENT_TYPES = {
    "WEREWOLF_PERSONALITIES_ASSIGNED",
    "WEREWOLF_ROLES_REVEALED_TO_AUDIENCE",
    "WEREWOLF_TRAINING_STARTED",
    "WEREWOLF_TRAINING_COMPLETED",
    "WEREWOLF_GAME_STARTED",
    "WEREWOLF_AGENTS_CREATED",
    "WEREWOLF_NIGHT_STARTED",
    "WEREWOLF_NIGHT_ACTION",
    "WEREWOLF_NIGHT_RESOLUTION_PRIVATE",
    "WEREWOLF_WOLF_CHAT_PUBLIC",
    "WEREWOLF_WOLF_KILL_VOTE_CAST",
    "WEREWOLF_WOLF_KILL_DECIDED",
    "WEREWOLF_DAY_STARTED",
    "WEREWOLF_PLAYER_TURN_STARTED",
    "WEREWOLF_PLAYER_TURN_STARTED_PRIVATE",
    "WEREWOLF_PLAYER_ACTION_RESOLVED",
    "WEREWOLF_PLAYER_ACTION_RESOLVED_PRIVATE",
    "WEREWOLF_SHERIFF_ELECTION_STARTED",
    "WEREWOLF_SHERIFF_CANDIDATE_DECLARED",
    "WEREWOLF_SHERIFF_WITHDRAWN",
    "WEREWOLF_SHERIFF_VOTE_CAST",
    "WEREWOLF_SHERIFF_VOTE_BATCH",
    "WEREWOLF_SHERIFF_ASSIGNED",
    "WEREWOLF_SHERIFF_BADGE_PASSED",
    "WEREWOLF_SHERIFF_BADGE_DESTROYED",
    "WEREWOLF_PUBLIC_SPEECH",
    "WEREWOLF_PUBLIC_SPEECH_PRIVATE",
    "WEREWOLF_VOTE_CAST",
    "WEREWOLF_VOTE_BATCH",
    "WEREWOLF_EXILE_RESULT",
    "WEREWOLF_DEATH_RESOLVED",
    "WEREWOLF_REVEALED_SELF",
    "WEREWOLF_WHITE_WOLF_KING_REVEALED",
    "WEREWOLF_KNIGHT_DUEL",
    "WEREWOLF_HUNTER_SHOT",
    "WEREWOLF_GAME_FINISHED",
    "WEREWOLF_AI_JUDGEMENT",
}

WHITE_WOLF_KING_REVEAL_PHASES = {
    "sheriff_speech",
    "sheriff_pk_speech",
    "day_speech",
    "day_pk_speech",
}

KNIGHT_DUEL_PHASES = {
    "day_speech",
    "day_pk_speech",
}

PERSONALITY_POOL = [
    ("激进", "更愿意主动上警、强势归票、快速给出站边和狼坑。"),
    ("稳健", "发言谨慎，重视证据链和风险控制，不轻易裸跳。"),
    ("逻辑型", "偏好结构化盘点、前后矛盾和投票收益分析。"),
    ("煽动型", "擅长制造阵营压力和情绪动员，容易带节奏。"),
    ("谨慎", "倾向保守发言和延迟表态，但关键票要给出理由。"),
    ("冲锋型", "狼人时更敢冲票和悍跳，好人时更敢强推怀疑对象。"),
    ("倒钩型", "狼人时倾向伪装站边和切割队友，好人时偏反向观察。"),
    ("观察型", "先听后判，重点记录发言变化和站边摇摆。"),
    ("控场型", "主动总结局势、分配焦点、推动警徽流或归票。"),
    ("情绪型", "表达更有感染力，容易用态度和压迫感影响他人。"),
    ("冒险型", "愿意做高风险高收益操作，例如悍跳、自爆或强归票。"),
    ("保守型", "优先降低失误，投票和技能使用更慢热。"),
]

PERSONALITY_HINTS = {name: hint for name, hint in PERSONALITY_POOL}


def role_team(role: str) -> str:
    return TEAM_WEREWOLF if role in WOLF_ROLES else TEAM_GOOD


def role_label(role: str) -> str:
    if role == ROLE_WHITE_WOLF_KING:
        return "白狼王"
    if role == ROLE_KNIGHT:
        return "骑士"
    return {
        ROLE_WEREWOLF: "狼人",
        ROLE_VILLAGER: "平民",
        ROLE_SEER: "预言家",
        ROLE_WITCH: "女巫",
        ROLE_HUNTER: "猎人",
        ROLE_GUARD: "守卫",
    }.get(role, role)


def team_label(team: str) -> str:
    return "狼人阵营" if team == TEAM_WEREWOLF else "好人阵营"


def make_role_deck(
    role_counts: Optional[Dict[str, int]] = None,
    *,
    board: str = WEREWOLF_BOARD_STANDARD_GUARD,
) -> List[str]:
    counts = dict(WEREWOLF_BOARD_ROLE_COUNTS.get(board, DEFAULT_ROLE_COUNTS))
    if role_counts:
        for role, count in role_counts.items():
            if role in counts:
                counts[role] = int(count)

    deck: List[str] = []
    for role, count in counts.items():
        deck.extend([role] * max(0, int(count)))
    return deck


def _extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.IGNORECASE | re.DOTALL)
    candidates = [fenced.group(1)] if fenced else []

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start : end + 1])
    candidates.append(text.strip())

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _parse_player_id(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.lower() in {"none", "null", "pass", "abstain", "destroy"}:
            return None
        match = re.search(r"\d+", stripped)
        if match:
            return int(match.group(0))
    return None


def _first_present(data: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


@dataclass
class AgentAction:
    action: str = "pass"
    target_player_id: Optional[int] = None
    text: str = ""
    reason: str = ""
    claim_role: str = ""
    suspects: List[int] = field(default_factory=list)
    vote_intent: Optional[int] = None
    direction: Optional[str] = None
    destroy_badge: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


def allowed_self_reveal_action_for_role(role: str) -> str:
    return "white_wolf_king_reveal" if role == ROLE_WHITE_WOLF_KING else "werewolf_reveal"


def board_role_counts(board: str) -> Dict[str, int]:
    return dict(WEREWOLF_BOARD_ROLE_COUNTS.get(board, STANDARD_GUARD_ROLE_COUNTS))


def board_label(board: str) -> str:
    if board == WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT:
        return "12 人白狼王骑士"
    return "12 人预女猎守"


def parse_agent_action(response: Optional[str], allowed_actions: Iterable[str]) -> AgentAction:
    allowed = {str(action) for action in allowed_actions}
    parsed = _extract_json_object(response or "")
    if parsed is None:
        return AgentAction(action="pass", error="invalid_json")

    action = str(parsed.get("action") or "pass").strip()
    if action not in allowed:
        return AgentAction(action="pass", raw=parsed, error=f"action_not_allowed:{action}")

    target_raw = _first_present(
        parsed,
        [
            "target_player_id",
            "target",
            "target_id",
            "vote",
            "kill",
            "check",
            "protect",
            "poison",
            "shoot",
            "badge_target",
        ],
    )
    target_player_id = _parse_player_id(target_raw)
    text = str(parsed.get("text") or parsed.get("speech") or parsed.get("message") or "").strip()[:1200]
    reason = str(parsed.get("reason") or "").strip()[:400]
    claim_role = str(parsed.get("claim_role") or parsed.get("claim") or "").strip()[:64]
    suspects_raw = parsed.get("suspects") if isinstance(parsed.get("suspects"), list) else []
    suspects = [
        parsed_id
        for parsed_id in (_parse_player_id(item) for item in suspects_raw[:12])
        if parsed_id is not None
    ]
    vote_intent = _parse_player_id(parsed.get("vote_intent") or parsed.get("intent"))
    direction_value = str(parsed.get("direction") or "").strip().lower()
    direction = direction_value if direction_value in {"clockwise", "counterclockwise"} else None
    destroy_badge = bool(parsed.get("destroy_badge")) or str(target_raw).strip().lower() == "destroy"

    return AgentAction(
        action=action,
        target_player_id=target_player_id,
        text=text,
        reason=reason,
        claim_role=claim_role,
        suspects=suspects,
        vote_intent=vote_intent,
        direction=direction,
        destroy_badge=destroy_badge,
        raw=parsed,
    )


@dataclass
class WerewolfPlayer:
    player_id: int
    role: str
    name: str = ""
    personality: str = ""
    style_hint: str = ""
    alive: bool = True
    death_reason: Optional[str] = None
    death_day: Optional[int] = None
    revealed: bool = False
    sheriff_candidate: bool = False
    sheriff_withdrawn: bool = False
    is_sheriff: bool = False
    training_ok: bool = False
    invalid_actions: int = 0
    timeouts: int = 0
    speech_count: int = 0
    vote_count: int = 0
    correct_votes: int = 0
    wrong_votes: int = 0
    score: int = 0
    judge_reasoning: str = ""
    judge_highlights: List[str] = field(default_factory=list)
    judge_mistakes: List[str] = field(default_factory=list)

    @property
    def team(self) -> str:
        return role_team(self.role)


@dataclass
class NightResolution:
    deaths: List[int] = field(default_factory=list)
    reasons: Dict[int, str] = field(default_factory=dict)
    wolf_target: Optional[int] = None
    guard_target: Optional[int] = None
    witch_saved: bool = False
    witch_poison_target: Optional[int] = None


@dataclass
class WerewolfGameState:
    player_ids: List[int]
    players: Dict[int, WerewolfPlayer]
    day: int = 0
    phase: str = "setup"
    sheriff_id: Optional[int] = None
    badge_destroyed: bool = False
    sheriff_enabled: bool = True
    werewolf_reveal_enabled: bool = True
    max_days: int = 6
    last_guard_target: Optional[int] = None
    witch_has_save: bool = True
    witch_has_poison: bool = True
    knight_duel_used: bool = False
    seer_checks: Dict[int, str] = field(default_factory=dict)
    last_night_deaths: List[int] = field(default_factory=list)
    private_events: Dict[int, List[Dict[str, Any]]] = field(default_factory=dict)
    # Hard ceiling on retained events to bound memory for long matches; the AI judge
    # only consumes the last 120 anyway, so 2000 entries is plenty of context.
    public_events_cap: int = 2000
    public_events: List[Dict[str, Any]] = field(default_factory=list)
    objective_features: Dict[str, Any] = field(default_factory=dict)
    winner: Optional[str] = None
    finished_reason: Optional[str] = None
    rng_seed: int = 0
    board: str = WEREWOLF_BOARD_STANDARD_GUARD

    @property
    def personality_map(self) -> Dict[int, Dict[str, str]]:
        return {
            pid: {
                "personality": player.personality,
                "style_hint": player.style_hint,
            }
            for pid, player in self.players.items()
        }

    @property
    def alive_ids(self) -> List[int]:
        return [pid for pid in self.player_ids if self.players[pid].alive]

    @property
    def dead_ids(self) -> List[int]:
        return [pid for pid in self.player_ids if not self.players[pid].alive]

    @property
    def wolf_ids(self) -> List[int]:
        return [pid for pid in self.player_ids if self.players[pid].role in WOLF_ROLES]

    @property
    def alive_wolf_ids(self) -> List[int]:
        return [pid for pid in self.wolf_ids if self.players[pid].alive]

    def role_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = board_role_counts(self.board)
        for role in list(counts):
            counts[role] = 0
        for player in self.players.values():
            counts[player.role] = counts.get(player.role, 0) + 1
        for role in WOLF_ROLES | GOOD_ROLES:
            counts.setdefault(role, 0)
        return counts

    def append_public_event(self, event: Dict[str, Any]) -> None:
        """Append an event to public_events with bounded retention."""
        self.public_events.append(event)
        if len(self.public_events) > self.public_events_cap:
            # Drop the oldest 10% in one shot to amortize the cost.
            drop = max(1, self.public_events_cap // 10)
            del self.public_events[:drop]

    def record_private(self, player_id: int, event_type: str, data: Dict[str, Any]) -> None:
        self.private_events.setdefault(player_id, []).append({
            "type": event_type,
            "data": dict(data),
            "day": self.day,
            "phase": self.phase,
        })

    def public_summary(self, *, include_roles: bool = False) -> Dict[str, Any]:
        return {
            "day": self.day,
            "phase": self.phase,
            "sheriff_id": self.sheriff_id,
            "badge_destroyed": self.badge_destroyed,
            "winner": self.winner,
            "finished_reason": self.finished_reason,
            "board": self.board,
            "board_label": board_label(self.board),
            "alive_players": self.alive_ids,
            "dead_players": self.dead_ids,
            "players": [
                self.public_player_view(pid, include_role=include_roles or self.players[pid].revealed)
                for pid in self.player_ids
            ],
        }

    def public_player_view(self, player_id: int, *, include_role: bool = False) -> Dict[str, Any]:
        player = self.players[player_id]
        # Personality and style_hint are PRIVATE: only the player themselves and the AI judge
        # may see them. Other players must not be able to recognise role from personality.
        view = {
            "player_id": player.player_id,
            "name": player.name,
            "alive": player.alive,
            "is_sheriff": player.is_sheriff,
            "sheriff_candidate": player.sheriff_candidate and not player.sheriff_withdrawn,
            "revealed": player.revealed,
        }
        if include_role:
            view["role"] = player.role
            view["role_label"] = role_label(player.role)
            view["team"] = player.team
        return view

    def private_context(self, player_id: int) -> Dict[str, Any]:
        player = self.players[player_id]
        context = {
            "player_id": player_id,
            "role": player.role,
            "role_label": role_label(player.role),
            "team": player.team,
            "team_label": team_label(player.team),
            "personality": player.personality,
            "style_hint": player.style_hint,
            "alive": player.alive,
            "day": self.day,
            "phase": self.phase,
            "board": self.board,
            "public_state": self.public_summary(include_roles=False),
            "private_history": self.private_events.get(player_id, [])[-20:],
        }
        if player.role in WOLF_ROLES:
            context["wolf_teammates"] = [
                pid for pid in self.wolf_ids if pid != player_id
            ]
        if player.role == ROLE_WITCH:
            context["witch_items"] = {
                "save_available": self.witch_has_save,
                "poison_available": self.witch_has_poison,
            }
        if player.role == ROLE_GUARD:
            context["last_guard_target"] = self.last_guard_target
        if player.role == ROLE_SEER:
            context["seer_checks"] = dict(self.seer_checks)
        if player.role == ROLE_KNIGHT:
            context["knight_duel_used"] = self.knight_duel_used
        return context

    def kill_player(self, player_id: int, reason: str) -> bool:
        player = self.players.get(player_id)
        if player is None or not player.alive:
            return False
        player.alive = False
        player.death_reason = reason
        player.death_day = self.day
        if player.is_sheriff:
            player.is_sheriff = False
        return True

    def reveal_self(self, player_id: int, phase: str) -> Tuple[bool, str]:
        player = self.players.get(player_id)
        if player is None:
            return False, "unknown_player"
        if not self.werewolf_reveal_enabled:
            return False, "werewolf_reveal_disabled"
        if not player.alive:
            return False, "dead_player"
        if player.role not in WOLF_ROLES:
            return False, "not_werewolf"
        if phase not in PUBLIC_SELF_REVEAL_PHASES:
            player.invalid_actions += 1
            return False, "illegal_phase"
        player.revealed = True
        self.kill_player(player_id, "werewolf_reveal")
        self.objective_features.setdefault("werewolf_reveals", []).append({
            "player_id": player_id,
            "day": self.day,
            "phase": phase,
            "was_sheriff": self.sheriff_id == player_id,
        })
        return True, "revealed"

    def resolve_night(
        self,
        *,
        wolf_target: Optional[int],
        guard_target: Optional[int],
        witch_save: bool,
        witch_poison_target: Optional[int],
        seer_target: Optional[int],
        apply_deaths: bool = True,
    ) -> NightResolution:
        resolution = NightResolution(
            wolf_target=wolf_target,
            guard_target=guard_target,
            witch_saved=False,
            witch_poison_target=witch_poison_target,
        )

        # Track who was guarded LAST NIGHT (None if the guard passed / had no valid target).
        # The rule is "不能连续两晚守同一人": the restriction must compare against the
        # immediately preceding night. Always overwrite — including with None on a pass —
        # so a no-guard night correctly clears the restriction and the guard may re-protect
        # the earlier target the following night. (Previously a pass left a stale target here,
        # which wrongly blocked guarding that player after an intervening pass night.)
        self.last_guard_target = guard_target

        wolf_death = None
        if wolf_target in self.alive_ids:
            guarded = guard_target == wolf_target
            saved = bool(witch_save and self.witch_has_save and wolf_target is not None)
            # Standard ruleset: when guard & witch both protect the same target, the protections
            # cancel and the target dies. In that case the witch's potion is wasted regardless.
            # Otherwise: consume potion only when the save would actually take effect (not double-protected).
            if guarded and saved:
                wolf_death = wolf_target
                resolution.reasons[wolf_target] = "guard_witch_double_save"
                # Potion still consumed in the contested case (matches standard rule).
                self.witch_has_save = False
                resolution.witch_saved = True
            elif saved and not guarded:
                self.witch_has_save = False
                resolution.witch_saved = True
            elif not saved and not guarded:
                wolf_death = wolf_target
                resolution.reasons[wolf_target] = "wolf_kill"

        if witch_poison_target in self.alive_ids and self.witch_has_poison:
            self.witch_has_poison = False
            resolution.reasons[witch_poison_target] = "witch_poison"

        death_ids: List[int] = []
        if wolf_death is not None:
            death_ids.append(wolf_death)
        if witch_poison_target is not None and witch_poison_target in self.alive_ids:
            if witch_poison_target not in death_ids:
                death_ids.append(witch_poison_target)

        resolution.deaths = death_ids
        if apply_deaths:
            self.apply_night_resolution(resolution)

        if seer_target in self.player_ids:
            target_player = self.players[seer_target]
            result = "wolf" if target_player.role in WOLF_ROLES else "good"
            self.seer_checks[seer_target] = result
            seer_id = next((pid for pid, p in self.players.items() if p.role == ROLE_SEER), None)
            if seer_id is not None:
                self.record_private(seer_id, "SEER_CHECK_RESULT", {
                    "target_player_id": seer_target,
                    "result": result,
                })

        self.objective_features.setdefault("night_resolutions", []).append({
            "day": self.day,
            "wolf_target": wolf_target,
            "wolf_target_role": self.players[wolf_target].role if wolf_target in self.players else None,
            "guard_target": guard_target,
            "witch_saved": resolution.witch_saved,
            "witch_poison_target": witch_poison_target,
            "witch_poison_role": self.players[witch_poison_target].role if witch_poison_target in self.players else None,
            "seer_target": seer_target,
            "seer_target_role": self.players[seer_target].role if seer_target in self.players else None,
            "deaths": list(death_ids),
            "death_reasons": dict(resolution.reasons),
        })
        return resolution

    def apply_night_resolution(self, resolution: NightResolution) -> List[int]:
        applied_deaths: List[int] = []
        for pid in resolution.deaths:
            if self.kill_player(pid, resolution.reasons.get(pid, "night_death")):
                applied_deaths.append(pid)
        self.last_night_deaths = applied_deaths
        return applied_deaths

    def check_win(self) -> Tuple[Optional[str], Optional[str]]:
        alive_wolves = [p for p in self.players.values() if p.alive and p.role in WOLF_ROLES]
        alive_good = [p for p in self.players.values() if p.alive and p.role not in WOLF_ROLES]
        alive_villagers = [p for p in alive_good if p.role == ROLE_VILLAGER]
        alive_gods = [p for p in alive_good if p.role in GOD_ROLES]

        if not alive_wolves:
            self.winner = TEAM_GOOD
            self.finished_reason = "all_wolves_eliminated"
            return self.winner, self.finished_reason
        if not alive_villagers:
            self.winner = TEAM_WEREWOLF
            self.finished_reason = "all_villagers_eliminated"
            return self.winner, self.finished_reason
        if not alive_gods:
            self.winner = TEAM_WEREWOLF
            self.finished_reason = "all_gods_eliminated"
            return self.winner, self.finished_reason
        return None, None

    def force_max_day_result(self) -> Tuple[Optional[str], str]:
        alive_wolves = len([p for p in self.players.values() if p.alive and p.role in WOLF_ROLES])
        alive_good = len([p for p in self.players.values() if p.alive and p.role not in WOLF_ROLES])
        if alive_wolves == 0:
            self.winner = TEAM_GOOD
            self.finished_reason = "max_days_all_wolves_eliminated"
        elif alive_wolves >= alive_good:
            self.winner = TEAM_WEREWOLF
            self.finished_reason = "max_days_wolf_parity"
        elif alive_good > alive_wolves:
            self.winner = TEAM_GOOD
            self.finished_reason = "max_days_good_advantage"
        else:
            self.winner = None
            self.finished_reason = "max_days_draw"
        return self.winner, self.finished_reason


def create_werewolf_state(
    player_ids: List[int],
    *,
    player_names: Optional[Dict[int, str]] = None,
    role_counts: Optional[Dict[str, int]] = None,
    board: str = WEREWOLF_BOARD_STANDARD_GUARD,
    sheriff_enabled: bool = True,
    werewolf_reveal_enabled: bool = True,
    max_days: int = 6,
    seed: Optional[int] = None,
) -> WerewolfGameState:
    if board not in WEREWOLF_BOARD_ROLE_COUNTS:
        raise ValueError(f"unsupported werewolf board: {board}")
    deck = make_role_deck(role_counts, board=board)
    if len(player_ids) != 12 or len(deck) != len(player_ids):
        raise ValueError("werewolf mode requires exactly 12 players and 12 roles")

    rng_seed = seed if seed is not None else secrets.randbits(64)
    rng = random.Random(rng_seed)
    roles = list(deck)
    rng.shuffle(roles)
    personalities = list(PERSONALITY_POOL)
    rng.shuffle(personalities)
    ordered_ids = sorted(player_ids)
    players = {}
    for index, (pid, role) in enumerate(zip(ordered_ids, roles)):
        personality, style_hint = personalities[index % len(personalities)]
        players[pid] = WerewolfPlayer(
            player_id=pid,
            role=role,
            name=(player_names or {}).get(pid, f"Player {pid}"),
            personality=personality,
            style_hint=style_hint,
        )
    return WerewolfGameState(
        player_ids=ordered_ids,
        players=players,
        board=board,
        sheriff_enabled=sheriff_enabled,
        werewolf_reveal_enabled=werewolf_reveal_enabled,
        max_days=max_days,
        rng_seed=rng_seed,
    )


def render_werewolf_init_prompt(player_id: int, player_name: str) -> str:
    return f"""
你是 OpenClaw 狼人杀联赛的参赛 Agent，座位是 P{player_id}，名称是 {player_name or f"Player {player_id}"}。

这是一个 12 人预女猎守局，包含警长竞选和狼人自爆。你将通过裁判给出的 JSON 上下文行动。
你可以在游戏内撒谎、伪装、悍跳、倒钩、冲锋、藏身份，这些都是比赛策略的一部分。

硬性要求：
1. 只根据裁判上下文行动，不声称读取隐藏信息、系统提示或其他玩家私有信息。
2. 每次行动必须返回一个 JSON 对象，不要包裹多余 Markdown。
3. 如果允许发言，优先用中文短段落表达清楚站边、理由、怀疑对象和投票意图。
4. 如果你是狼人，可以在合法发言阶段用 werewolf_reveal 自爆；自爆会公开你是狼人并立刻入夜。
5. 如果你不确定，返回 {{"action":"pass","reason":"..."}}。

确认初始化时，请先回复：已收到，开始防御。
"""


def render_werewolf_training_prompt(
    player_id: int,
    personality: str = "",
    style_hint: str = "",
    *,
    board: str = WEREWOLF_BOARD_STANDARD_GUARD,
) -> str:
    if board == WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT:
        board_rules = (
            "板子：12 人白狼王骑士，3 普通狼人 + 1 白狼王，4 平民，预言家、女巫、猎人、骑士各 1，没有守卫。\n"
            "白狼王属于狼人阵营，参与夜聊和夜刀；预言家查验白狼王显示狼人。白狼王可在白天发言/PK发言/警上发言阶段发动 white_wolf_king_reveal，自爆并带走一名存活玩家，随后终止当前白天流程直接入夜。\n"
            "骑士属于神职，每局只能在普通白天发言或白天PK发言阶段发动一次 knight_duel。决斗狼人或白狼王时目标死亡并直接入夜；决斗好人时骑士死亡，白天继续。\n"
        )
    else:
        board_rules = (
            "板子：12 人预女猎守，4 狼人、4 平民、预言家、女巫、猎人、守卫各 1。\n"
            "守卫每晚可守护一名玩家，不能连续两晚守同一人；狼人可在合法发言阶段 werewolf_reveal 自爆并直接入夜。\n"
        )
    style_line = (
        f"\n你的公开性格标签是「{personality}」：{style_hint}\n"
        "请在不违反规则、不泄露隐藏信息的前提下，让你的发言和决策体现这个风格。\n"
        if personality else ""
    )
    return f"""
[狼人杀赛前训练营]
你是 P{player_id}。以下训练对所有选手完全相同，不包含本局身份、座位收益、队友或私有验人结果。
{style_line}

{board_rules}

关键规则：
- 好人胜利：所有狼人出局。
- 狼人胜利：屠民、屠神，或狼人数量大于等于好人数量。
- 警长竞选：上警、警上发言、退水、警长投票、平票 PK。警长放逐票为 1.5 票。
- 警徽流：预言家或悍跳狼可以用警徽流给出后续验人计划，观众和玩家会据此判断可信度。
- 自爆：存活狼人可在警上发言、白天发言、PK 发言阶段自爆。自爆立刻死亡、公开狼人身份、终止白天流程并入夜。
- 女巫：解药和毒药各一次。守卫与女巫同守同救同一刀口时，该目标死亡。
- 守卫：不能连续两晚守同一人。

高水平打法提示：
- 狼人：可以悍跳预言家抢警徽，也可以倒钩真预隐藏身份。好的自爆通常用于吞掉关键发言、阻断警徽流或保护狼队友。
- 预言家：要讲清楚验人、警徽流和投票归票，避免只喊身份。
- 女巫/守卫：不要轻易暴露全部信息，技能收益要能转化为白天投票。
- 平民：价值来自站边准确、抓发言矛盾、归票清晰。
- 猎人：开枪要避免被狼人骗枪，死亡时优先选择逻辑链最像狼的人。

快速格式练习：请只返回 JSON：
{{
  "action": "speak",
  "text": "我已理解 12 人预女猎守、警长竞选、自爆规则，会按裁判给出的 allowed_actions 返回 JSON。",
  "reason": "training_ack"
}}
"""


def render_action_prompt(
    state: WerewolfGameState,
    player_id: int,
    *,
    request: str,
    allowed_actions: List[str],
    extra_context: Optional[Dict[str, Any]] = None,
) -> str:
    context = state.private_context(player_id)
    player = state.players[player_id]
    if extra_context:
        context["request_context"] = extra_context
    context["allowed_actions"] = allowed_actions
    schema = {
        "action": "one of allowed_actions",
        "target_player_id": "optional integer player id",
        "text": "optional public or private speech",
        "reason": "short reason",
        "claim_role": "optional role claim in public speech",
        "suspects": "optional list of suspected player ids",
        "vote_intent": "optional intended vote target",
        "direction": "optional: clockwise or counterclockwise for sheriff speech order",
        "destroy_badge": "optional boolean for sheriff badge transfer",
    }
    role_label_zh = role_label(player.role)
    team_label_zh = team_label(player.team)
    pass_warning = (
        "⚠️ 你的角色是 " + role_label_zh + "（" + team_label_zh + "），现在是必须主动行动的关键时刻。\n"
        "返回 action=\"pass\" 等于放弃这一轮的所有收益（投票权、技能、发言权），裁判会记录消极游戏并影响赛后评分。\n"
        "**除非真的没有合理选择**，否则请从 allowed_actions 中选一个非 pass 的动作。\n"
    )
    allowed_first = json.dumps(allowed_actions, ensure_ascii=False)
    examples_by_action: Dict[str, str] = {
        "run_for_sheriff": '{"action":"run_for_sheriff","reason":"我有发言能力，能带队抓狼坑"}',
        "sheriff_vote": '{"action":"sheriff_vote","target_player_id":3,"reason":"P3 发言层次清晰像神"}',
        "vote": '{"action":"vote","target_player_id":7,"reason":"P7 警上发言逻辑矛盾","suspects":[7,5],"vote_intent":7}',
        "speak": '{"action":"speak","text":"我是预言家，昨晚验 P5 是金水……","claim_role":"seer","suspects":[2,9],"vote_intent":2}',
        "night_kill": '{"action":"night_kill","target_player_id":4,"reason":"P4 警上发言像神，先刀掉"}',
        "seer_check": '{"action":"seer_check","target_player_id":6,"reason":"P6 发言摇摆，先验"}',
        "witch_save": '{"action":"witch_save","reason":"被刀的是关键神职，必须救"}',
        "witch_poison": '{"action":"witch_poison","target_player_id":8,"reason":"P8 警上发言狼味重"}',
        "guard_protect": '{"action":"guard_protect","target_player_id":5,"reason":"P5 是警长，今晚易被刀"}',
        "hunter_shoot": '{"action":"hunter_shoot","target_player_id":9,"reason":"P9 最像狼，带走"}',
        "werewolf_reveal": '{"action":"werewolf_reveal","reason":"自爆吞掉对方警上发言"}',
        "white_wolf_king_reveal": '{"action":"white_wolf_king_reveal","target_player_id":4,"reason":"带走关键神职并终止白天流程"}',
        "knight_duel": '{"action":"knight_duel","target_player_id":8,"reason":"P8 视角爆炸，决斗验枪"}',
    }
    if state.board == WEREWOLF_BOARD_WHITE_WOLF_KING_KNIGHT:
        context["board_rules"] = {
            "white_wolf_king": "狼人阵营，参与夜聊夜刀；白天/PK/警上发言阶段可 white_wolf_king_reveal 自爆带走一名存活玩家并直接入夜。",
            "knight": "好人神职，每局一次；只能普通白天发言/PK发言阶段 knight_duel。决斗狼则目标死亡并直接入夜，决斗好人则骑士死亡且白天继续。",
            "guard": "本板子没有守卫。",
        }
    else:
        context["board_rules"] = {
            "guard": "守卫每晚守护一人，不能连续两晚守同一人。",
            "werewolf_reveal": "普通狼人可在合法发言阶段 werewolf_reveal 自爆并直接入夜。",
        }
    relevant = [examples_by_action[a] for a in allowed_actions if a in examples_by_action]
    examples_block = ""
    if relevant:
        examples_block = "示例（不要照抄，仅参考格式）：\n" + "\n".join(relevant) + "\n"
    # Put private context up front clearly labeled so the model knows its hidden info.
    private_facts = {
        "你是谁": f"P{player.player_id} {role_label_zh}（{team_label_zh}）",
        "性格": player.personality or "未设置",
        "存活状态": "存活" if player.alive else "已死",
        "当前阶段": context.get("public_state", {}).get("phase"),
        "Day": context.get("day"),
    }
    if "wolf_teammates" in context:
        private_facts["狼队友"] = context["wolf_teammates"]
    if "witch_items" in context:
        private_facts["女巫物品"] = context["witch_items"]
    if "last_guard_target" in context:
        private_facts["昨夜守的目标"] = context["last_guard_target"]
    if "seer_checks" in context:
        private_facts["历史查验"] = context["seer_checks"]
    private_facts_json = json.dumps(private_facts, ensure_ascii=False)
    return (
        f"[狼人杀行动请求]\n{request}\n\n"
        f"可选动作：{allowed_first}\n"
        f"{pass_warning}"
        f"\n你的私有信息（仅你自己知道）：{private_facts_json}\n"
        f"\n性格演绎要求：{player.style_hint or '无特别要求'}\n"
        "你必须只返回一个 JSON 对象，不要 Markdown，不要解释 JSON 之外的内容。\n"
        f"输出 schema: {json.dumps(schema, ensure_ascii=False)}\n"
        f"{examples_block}"
        f"当前完整上下文: {json.dumps(context, ensure_ascii=False, default=str)}"
    )


@dataclass(frozen=True)
class WerewolfJudgeConfig:
    enabled: bool = True
    provider: str = "openai-completions"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    timeout_seconds: int = 45

    @classmethod
    def from_env(cls) -> "WerewolfJudgeConfig":
        return cls(
            enabled=not truthy(os.getenv("WEREWOLF_JUDGE_DISABLED")),
            provider=os.getenv("WEREWOLF_JUDGE_PROVIDER", "openai-completions").strip() or "openai-completions",
            model=os.getenv("WEREWOLF_JUDGE_MODEL", "").strip(),
            api_key=os.getenv("WEREWOLF_JUDGE_API_KEY", "").strip(),
            base_url=os.getenv("WEREWOLF_JUDGE_BASE_URL", "").strip(),
            timeout_seconds=positive_int(os.getenv("WEREWOLF_JUDGE_TIMEOUT_SECONDS"), 45),
        )

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.model and self.api_key and self.base_url)


class WerewolfJudgeClient:
    def __init__(self, config: WerewolfJudgeConfig):
        self.config = config

    def _endpoint(self) -> str:
        base_url = self.config.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 2200,
        }
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.config.timeout_seconds)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(self._endpoint(), json=payload, headers=headers) as response:
                body = await response.text()
                if response.status >= 400:
                    raise RuntimeError(f"werewolf judge LLM HTTP {response.status}: {body[:300]}")
                data = json.loads(body)
        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            return ""
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"].strip()
        text = choices[0].get("text") if isinstance(choices[0], dict) else None
        return text.strip() if isinstance(text, str) else ""


def _fallback_scores(state: WerewolfGameState, *, reason: str) -> Dict[str, Any]:
    winner = state.winner
    player_scores = []
    for pid in state.player_ids:
        player = state.players[pid]
        score = 3 if winner and player.team == winner else 0
        player_scores.append({
            "player_id": pid,
            "role": player.role,
            "team": player.team,
            "score": score,
            "reasoning": "AI 裁判不可用，使用保守 fallback 分数。" if score else "失败阵营固定 0 分。",
            "highlights": [],
            "mistakes": [],
        })
    return {
        "winning_team": winner,
        "losing_team": TEAM_GOOD if winner == TEAM_WEREWOLF else TEAM_WEREWOLF if winner == TEAM_GOOD else None,
        "player_scores": player_scores,
        "match_summary": "AI 裁判未完成评分。",
        "key_turning_points": [],
        "judge_confidence": 0,
        "judge_fallback": True,
        "fallback_reason": reason,
    }


def _normalize_judge_payload(payload: Dict[str, Any], state: WerewolfGameState) -> Dict[str, Any]:
    winner = state.winner
    losing = TEAM_GOOD if winner == TEAM_WEREWOLF else TEAM_WEREWOLF if winner == TEAM_GOOD else None
    raw_scores = payload.get("player_scores") if isinstance(payload.get("player_scores"), list) else []
    by_player: Dict[int, Dict[str, Any]] = {}
    for item in raw_scores:
        if not isinstance(item, dict):
            continue
        player_id = _parse_player_id(item.get("player_id"))
        if player_id in state.players:
            by_player[player_id] = item

    normalized_scores = []
    for pid in state.player_ids:
        player = state.players[pid]
        raw = by_player.get(pid, {})
        if winner and player.team == winner:
            try:
                score = int(round(float(raw.get("score", 0))))
            except (TypeError, ValueError):
                score = 0
            score = max(0, min(10, score))
            highlights_raw = raw.get("highlights") if isinstance(raw.get("highlights"), list) else []
            highlights = [str(item) for item in highlights_raw if str(item).strip()]
            if score > 5 and not highlights:
                score = 5
        else:
            score = 0
            highlights = []

        mistakes_raw = raw.get("mistakes") if isinstance(raw.get("mistakes"), list) else []
        normalized_scores.append({
            "player_id": pid,
            "role": player.role,
            "role_label": role_label(player.role),
            "team": player.team,
            "score": score,
            "reasoning": str(raw.get("reasoning") or ("失败阵营固定 0 分。" if score == 0 else "")),
            "highlights": highlights,
            "mistakes": [str(item) for item in mistakes_raw if str(item).strip()],
        })

    return {
        "winning_team": winner,
        "losing_team": losing,
        "player_scores": normalized_scores,
        "match_summary": str(payload.get("match_summary") or ""),
        "key_turning_points": payload.get("key_turning_points") if isinstance(payload.get("key_turning_points"), list) else [],
        "judge_confidence": payload.get("judge_confidence", 0),
        "judge_fallback": False,
    }


async def judge_werewolf_match(
    state: WerewolfGameState,
    public_events: List[Dict[str, Any]],
    *,
    config: Optional[WerewolfJudgeConfig] = None,
    client: Optional[WerewolfJudgeClient] = None,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    judge_config = config or WerewolfJudgeConfig.from_env()
    log = logger or logging.getLogger(__name__)
    if not state.winner:
        return _fallback_scores(state, reason="draw_or_no_winner")
    if not judge_config.available:
        return _fallback_scores(state, reason="judge_not_configured")

    judge_client = client or WerewolfJudgeClient(judge_config)
    role_reveal = {
        str(pid): {
            "role": player.role,
            "role_label": role_label(player.role),
            "team": player.team,
            "alive": player.alive,
            "board": state.board,
            "personality": player.personality,
            "style_hint": player.style_hint,
            "invalid_actions": player.invalid_actions,
            "timeouts": player.timeouts,
            "speech_count": player.speech_count,
            "vote_count": player.vote_count,
            "correct_votes": player.correct_votes,
            "wrong_votes": player.wrong_votes,
        }
        for pid, player in state.players.items()
    }
    sanitized_events = [
        {
            "type": event.get("type"),
            "timestamp": event.get("timestamp"),
            "data": event.get("data"),
        }
        for event in public_events
        if str(event.get("type", "")).startswith("WEREWOLF_")
    ][-120:]

    system_prompt = (
        "你是 OpenClaw 狼人杀赛事的严苛 AI 裁判。请只输出 JSON。"
        "失败阵营所有玩家必须是 0 分。胜利阵营每名玩家按个人表现分别给 0-10 分。"
        "5 分已经是很高的分数；超过 5 分必须有非常亮眼的个人高光。"
        "不要因为躺赢、对手失误或赢得快就给高分。重点看推理、发言、欺骗、技能收益、投票、警徽和自爆时机。"
    )
    user_prompt = (
        "请根据整局公开事件、最终身份和客观特征评分。必须输出："
        "winning_team, losing_team, player_scores, match_summary, key_turning_points, judge_confidence。\n"
        "player_scores 每项包含 player_id, role, team, score, reasoning, highlights, mistakes。\n"
        f"赢家: {state.winner}\n"
        f"结束原因: {state.finished_reason}\n"
        f"最终身份: {json.dumps(role_reveal, ensure_ascii=False)}\n"
        f"客观特征: {json.dumps(state.objective_features, ensure_ascii=False, default=str)}\n"
        f"公开事件: {json.dumps(sanitized_events, ensure_ascii=False, default=str)}"
    )

    try:
        raw_text = await judge_client.generate(system_prompt, user_prompt)
        parsed = _extract_json_object(raw_text)
        if parsed is None:
            raise RuntimeError("judge returned invalid JSON")
        return _normalize_judge_payload(parsed, state)
    except Exception as exc:
        log.warning("Werewolf AI judge failed: %s", exc)
        return _fallback_scores(state, reason=str(exc)[:200])


def apply_judgement_to_state(state: WerewolfGameState, judgement: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    by_player = {
        int(item["player_id"]): item
        for item in judgement.get("player_scores", [])
        if isinstance(item, dict) and _parse_player_id(item.get("player_id")) is not None
    }
    leaderboard: Dict[int, Dict[str, Any]] = {}
    for pid in state.player_ids:
        player = state.players[pid]
        item = by_player.get(pid, {})
        score = int(item.get("score") or 0)
        player.score = score
        player.judge_reasoning = str(item.get("reasoning") or "")
        player.judge_highlights = list(item.get("highlights") or [])
        player.judge_mistakes = list(item.get("mistakes") or [])
        leaderboard[pid] = {
            "player_id": pid,
            "score": score,  # alias for serializers / frontends that read .score directly
            "total_score": score,
            "attack_score": score,
            "defense_score": 0,
            "sla_score": 0,
            "flags_captured": 0,
            "flags_lost": 0,
            "sla_up": True,
            "werewolf_role": player.role,
            "werewolf_role_label": role_label(player.role),
            "werewolf_team": player.team,
            "werewolf_alive": player.alive,
            "werewolf_is_sheriff": player.is_sheriff,
            "judge_reasoning": player.judge_reasoning,
            "judge_highlights": player.judge_highlights,
            "judge_mistakes": player.judge_mistakes,
        }
    return dict(sorted(leaderboard.items(), key=lambda item: item[1]["total_score"], reverse=True))


AgentRequest = Callable[[int, str, str, int], Awaitable[Optional[str]]]
# emit_event(event_type, data, *, audience="public")
# audience="public": broadcast to WebSocket subscribers and persist
# audience="hidden": persist to event log (for AI judge / post-match audit) but NOT broadcast
EmitEvent = Callable[..., Awaitable[None]]
SetStatus = Callable[[str, Dict[str, Any]], Awaitable[None]]

PRIVATE_NIGHT_KINDS = {
    "werewolf_wolf_chat",
    "werewolf_night_kill",
    "werewolf_witch",
    "werewolf_seer",
    "werewolf_guard",
}


class WerewolfMatchRunner:
    def __init__(
        self,
        state: WerewolfGameState,
        *,
        agent_request: AgentRequest,
        emit_event: EmitEvent,
        set_status: SetStatus,
        logger: Optional[logging.Logger] = None,
    ):
        self.state = state
        self.agent_request = agent_request
        self.emit_event = emit_event
        self.set_status = set_status
        self.logger = logger or logging.getLogger(__name__)

    async def run_training(self) -> bool:
        """Run pre-match training. Returns True if at least one player completed training."""
        await self.set_status("werewolf_training", {"phase": "werewolf_training"})
        await self.emit_event("WEREWOLF_TRAINING_STARTED", {
            "player_count": len(self.state.player_ids),
        "training": f"{board_label(self.state.board)}、警长竞选、公开技能、JSON行动格式",
        })
        results = await asyncio_gather_limited([
            self._train_player(pid)
            for pid in self.state.player_ids
        ], limit=4)
        await self.emit_event("WEREWOLF_TRAINING_COMPLETED", {
            "results": {str(pid): ok for pid, ok in results},
        })
        training_ok_count = sum(1 for _, ok in results if ok)
        return training_ok_count > 0

    async def _train_player(self, player_id: int) -> Tuple[int, bool]:
        player = self.state.players[player_id]
        response = await self.agent_request(
            player_id,
            render_werewolf_training_prompt(
                player_id,
                player.personality,
                player.style_hint,
                board=self.state.board,
            ),
            "werewolf_training",
            180,
        )
        action = parse_agent_action(response, ["speak", "pass"])
        ok = action.error is None and action.action in {"speak", "pass"}
        self.state.players[player_id].training_ok = ok
        if not ok:
            self.state.players[player_id].invalid_actions += 1
        return player_id, ok

    async def run_game(self) -> Dict[str, Any]:
        await self.emit_event("WEREWOLF_GAME_STARTED", {
            "player_count": len(self.state.player_ids),
            "board": self.state.board,
            "board_label": board_label(self.state.board),
            "role_counts": self.state.role_counts(),
            "sheriff_enabled": self.state.sheriff_enabled,
            "werewolf_reveal_enabled": self.state.werewolf_reveal_enabled,
            "max_days": self.state.max_days,
        })
        await self.emit_event("WEREWOLF_PERSONALITIES_ASSIGNED", {
            "players": {
                str(pid): {
                    "player_id": pid,
                    "personality": player.personality,
                    "style_hint": player.style_hint,
                }
                for pid, player in self.state.players.items()
            },
        }, audience="hidden")
        await self.emit_event("WEREWOLF_ROLES_REVEALED_TO_AUDIENCE", {
            "players": {
                str(pid): self.state.public_player_view(pid, include_role=True)
                for pid in self.state.player_ids
            },
            "public_state": self.state.public_summary(include_roles=True),
        })

        # Standard ruleset: Day 1 night runs first, then sheriff election, then Day 1 day.
        # The sheriff election happens BEFORE the first daytime discussion but AFTER the first
        # night's deaths are known — this is how 12 人预女猎守 normally plays.
        first_iteration = True
        while not self.state.winner and self.state.day < self.state.max_days:
            pending_night_resolution = await self._run_night(defer_death_publication=first_iteration)
            if not first_iteration and self._winner_found():
                break
            if (
                first_iteration
                and self.state.sheriff_enabled
                and self.state.sheriff_id is None
                and not self.state.badge_destroyed
            ):
                sheriff_result = await self._run_sheriff_election()
                await self._publish_deferred_night_resolution(pending_night_resolution)
                if self._winner_found():
                    break
                if sheriff_result == "werewolf_revealed":
                    first_iteration = False
                    continue
            elif first_iteration:
                await self._publish_deferred_night_resolution(pending_night_resolution)
                if self._winner_found():
                    break
            first_iteration = False
            day_result = await self._run_day()
            if self._winner_found():
                break
            if day_result == "werewolf_revealed":
                continue

        if not self.state.winner:
            self.state.force_max_day_result()

        await self._finish_game_event()
        return {
            "winner": self.state.winner,
            "reason": self.state.finished_reason,
        }

    async def _finish_game_event(self) -> None:
        await self.emit_event("WEREWOLF_GAME_FINISHED", {
            "winner": self.state.winner,
            "winner_label": team_label(self.state.winner) if self.state.winner else "平局",
            "reason": self.state.finished_reason,
            "day": self.state.day,
            "role_reveal": {
                str(pid): {
                    "role": player.role,
                    "role_label": role_label(player.role),
                    "team": player.team,
                    "alive": player.alive,
                }
                for pid, player in self.state.players.items()
            },
            "public_state": self.state.public_summary(include_roles=True),
        })

    def _winner_found(self) -> bool:
        winner, _reason = self.state.check_win()
        return winner is not None

    def _speech_allowed_actions(
        self,
        player_id: int,
        *,
        include_withdraw: bool = False,
        allow_knight: bool = False,
    ) -> List[str]:
        allowed = ["speak"]
        player = self.state.players.get(player_id)
        if include_withdraw:
            allowed.append("withdraw_sheriff")
        if player is not None and player.alive and player.role in WOLF_ROLES:
            allowed.append(allowed_self_reveal_action_for_role(player.role))
        if (
            allow_knight
            and player is not None
            and player.alive
            and player.role == ROLE_KNIGHT
            and not self.state.knight_duel_used
        ):
            allowed.append("knight_duel")
        allowed.append("pass")
        return allowed

    async def _run_night(self, *, defer_death_publication: bool = False) -> NightResolution:
        self.state.day += 1
        self.state.phase = "night"
        await self.set_status("werewolf_night", {"phase": "werewolf_night", "day": self.state.day})
        await self.emit_event("WEREWOLF_NIGHT_STARTED", {
            "day": self.state.day,
            "alive_players": self.state.alive_ids,
        })

        guard_target = await self._guard_action()
        wolf_target = await self._wolf_actions()
        witch_save, witch_poison_target = await self._witch_action(wolf_target)
        seer_target = await self._seer_action()

        resolution = self.state.resolve_night(
            wolf_target=wolf_target,
            guard_target=guard_target,
            witch_save=witch_save,
            witch_poison_target=witch_poison_target,
            seer_target=seer_target,
            apply_deaths=not defer_death_publication,
        )
        if defer_death_publication:
            await self.emit_event("WEREWOLF_NIGHT_ACTION", {
                "day": self.state.day,
                "action": "night_resolved_pending_sheriff",
                "death_count": None,
                "dead_players": [],
                "death_reasons": {},
                "publication_deferred_until_after_sheriff": True,
            })
        else:
            await self.emit_event("WEREWOLF_NIGHT_ACTION", {
                "day": self.state.day,
                "action": "night_resolved",
                "death_count": len(resolution.deaths),
                "dead_players": list(resolution.deaths),
                "death_reasons": {pid: "night_death" for pid in resolution.deaths},
            })
        await self.emit_event("WEREWOLF_NIGHT_RESOLUTION_PRIVATE", {
            "day": self.state.day,
            "wolf_target": resolution.wolf_target,
            "guard_target": resolution.guard_target,
            "witch_saved": resolution.witch_saved,
            "witch_poison_target": resolution.witch_poison_target,
            "death_count": len(resolution.deaths),
            "dead_players": list(resolution.deaths),
            "death_reasons": dict(resolution.reasons),
        }, audience="hidden")
        if not defer_death_publication:
            await self._handle_post_death_actions(resolution.deaths, source="night")
        return resolution

    async def _publish_deferred_night_resolution(self, resolution: NightResolution) -> None:
        applied_deaths = self.state.apply_night_resolution(resolution)
        await self.emit_event("WEREWOLF_NIGHT_ACTION", {
            "day": self.state.day,
            "action": "night_resolved",
            "death_count": len(applied_deaths),
            "dead_players": list(applied_deaths),
            "death_reasons": {pid: "night_death" for pid in applied_deaths},
            "publication_deferred_until_after_sheriff": False,
        })
        await self._handle_post_death_actions(applied_deaths, source="night")

    async def _wolf_actions(self) -> Optional[int]:
        wolves = self.state.alive_wolf_ids
        if not wolves:
            return None
        chat_texts = []
        for pid in wolves:
            action = await self._ask(
                pid,
                request="狼人夜间私聊：给队友一个简短刀法建议。此内容不会公开给好人。",
                allowed=["speak", "pass"],
                kind="werewolf_wolf_chat",
                timeout=80,
                extra={"wolf_teammates_alive": [w for w in wolves if w != pid]},
            )
            if action.action == "speak" and action.text:
                chat_texts.append({"player_id": pid, "text": action.text[:800]})
        if chat_texts:
            await self.emit_event("WEREWOLF_WOLF_CHAT_PUBLIC", {
                "day": self.state.day,
                "messages": [
                    {
                        **message,
                        "personality": self.state.players[message["player_id"]].personality,
                    }
                    for message in chat_texts
                ],
            })
            for wolf_id in wolves:
                self.state.record_private(wolf_id, "WOLF_CHAT", {
                    "day": self.state.day,
                    "messages": chat_texts,
                })

        votes: Dict[int, int] = {}
        vote_records: List[Tuple[int, Optional[int]]] = []
        for pid in wolves:
            action = await self._ask(
                pid,
                request="狼人夜间行动：选择今晚击杀的一名存活非狼玩家。",
                allowed=["night_kill", "pass"],
                kind="werewolf_night_kill",
                timeout=80,
            )
            target = action.target_player_id
            if action.action == "night_kill" and target in self.state.alive_ids and self.state.players[target].role not in WOLF_ROLES:
                votes[target] = votes.get(target, 0) + 1
                vote_records.append((pid, target))
                await self.emit_event("WEREWOLF_WOLF_KILL_VOTE_CAST", {
                    "day": self.state.day,
                    "wolf_id": pid,
                    "personality": self.state.players[pid].personality,
                    "target_player_id": target,
                    "reason": action.reason,
                })
            else:
                vote_records.append((pid, None))
                self._mark_invalid_if_needed(pid, action, "invalid_wolf_kill")

        if not votes:
            # When no wolf submitted a valid kill, the referee MUST NOT pick a target. The
            # previous "min(non_wolves)" fallback was role-aware (looked up player roles to
            # filter wolves) and deterministic — this leaked role information and effectively
            # let the referee slaughter gods in id order. Safer behavior: it's a peaceful
            # night (no kill). Log so the judge can see the wolves wasted their turn.
            self.state.objective_features.setdefault("wolf_no_kill_nights", []).append({
                "day": self.state.day,
                "alive_ids": list(self.state.alive_ids),
            })
            await self.emit_event("WEREWOLF_WOLF_KILL_DECIDED", {
                "day": self.state.day,
                "target_player_id": None,
                "reason": "no_valid_wolf_votes_peaceful_night",
                "votes": [{"wolf_id": wolf_id, "target_player_id": vote_target} for wolf_id, vote_target in vote_records],
            })
            return None
        max_votes = max(votes.values())
        top = sorted(pid for pid, count in votes.items() if count == max_votes)
        target = top[0]
        await self.emit_event("WEREWOLF_WOLF_KILL_DECIDED", {
            "day": self.state.day,
            "target_player_id": target,
            "reason": "vote" if len(top) == 1 else "tie_lowest_seat",
            "vote_totals": votes,
            "votes": [{"wolf_id": wolf_id, "target_player_id": vote_target} for wolf_id, vote_target in vote_records],
        })
        return target

    async def _guard_action(self) -> Optional[int]:
        guard = next((p for p in self.state.players.values() if p.role == ROLE_GUARD and p.alive), None)
        if guard is None:
            return None
        action = await self._ask(
            guard.player_id,
            request="守卫夜间行动：选择今晚守护的一名存活玩家，不能连续两晚守同一人。",
            allowed=["guard_protect", "pass"],
            kind="werewolf_guard",
            timeout=80,
        )
        target = action.target_player_id
        if action.action == "guard_protect" and target in self.state.alive_ids and target != self.state.last_guard_target:
            return target
        self._mark_invalid_if_needed(guard.player_id, action, "invalid_guard_target")
        return None

    async def _witch_action(self, wolf_target: Optional[int]) -> Tuple[bool, Optional[int]]:
        witch = next((p for p in self.state.players.values() if p.role == ROLE_WITCH and p.alive), None)
        if witch is None:
            return False, None
        action = await self._ask(
            witch.player_id,
            request="女巫夜间行动：根据今晚刀口决定是否使用解药或毒药。第一版默认同夜只能使用一种药。",
            allowed=["witch_save", "witch_poison", "pass"],
            kind="werewolf_witch",
            timeout=80,
            extra={
                "tonight_attacked_player_id": wolf_target,
                "save_available": self.state.witch_has_save,
                "poison_available": self.state.witch_has_poison,
            },
        )
        if action.action == "witch_save" and self.state.witch_has_save and wolf_target in self.state.alive_ids:
            return True, None
        if (
            action.action == "witch_poison"
            and self.state.witch_has_poison
            and action.target_player_id in self.state.alive_ids
            and action.target_player_id != witch.player_id
        ):
            return False, action.target_player_id
        self._mark_invalid_if_needed(witch.player_id, action, "invalid_witch_action")
        return False, None

    async def _seer_action(self) -> Optional[int]:
        seer = next((p for p in self.state.players.values() if p.role == ROLE_SEER and p.alive), None)
        if seer is None:
            return None
        action = await self._ask(
            seer.player_id,
            request="预言家夜间行动：选择一名存活玩家查验阵营。",
            allowed=["seer_check", "pass"],
            kind="werewolf_seer",
            timeout=80,
        )
        target = action.target_player_id
        if action.action == "seer_check" and target in self.state.alive_ids and target != seer.player_id:
            return target
        self._mark_invalid_if_needed(seer.player_id, action, "invalid_seer_target")
        return None

    async def _run_day(self) -> str:
        self.state.phase = "day"
        await self.set_status("werewolf_day", {"phase": "werewolf_day", "day": self.state.day})
        await self.emit_event("WEREWOLF_DAY_STARTED", {
            "day": self.state.day,
            "alive_players": self.state.alive_ids,
            "deaths_last_night": list(self.state.last_night_deaths),
        })

        direction = await self._sheriff_choose_direction()
        order = self._speech_order(direction)
        for pid in order:
            if pid not in self.state.alive_ids:
                continue
            action = await self._ask(
                pid,
                request="白天发言：请发表站边、怀疑对象、归票建议。狼人可选择自爆。",
                allowed=self._speech_allowed_actions(pid, allow_knight=True),
                kind="werewolf_day_speech",
                timeout=100,
                phase_override="day_speech",
            )
            if await self._maybe_handle_reveal(pid, action, "day_speech"):
                return "werewolf_revealed"
            if await self._maybe_handle_knight_duel(pid, action, "day_speech"):
                return "knight_duel_hit_wolf"
            if action.action == "speak":
                await self._public_speech_from_action(pid, action, stage="day_speech")
            else:
                await self._public_speech_from_action(pid, action, stage="day_speech")

        exile_result = await self._run_exile_vote(stage="day_vote", candidates=self.state.alive_ids)
        if exile_result in {"werewolf_revealed", "knight_duel_hit_wolf"}:
            return exile_result
        return "finished" if self._winner_found() else "day_complete"

    async def _run_sheriff_election(self) -> str:
        self.state.phase = "sheriff_election"
        await self.set_status("werewolf_sheriff", {"phase": "werewolf_sheriff", "day": self.state.day})
        await self.emit_event("WEREWOLF_SHERIFF_ELECTION_STARTED", {
            "day": self.state.day,
            "alive_players": self.state.alive_ids,
        })
        # Ask all alive players concurrently whether they want to run for sheriff. This is
        # much faster than the sequential loop (12 * 60s worst case → max(60s)) and matches
        # how a real 12-player game is run.
        async def _ask_run(pid: int) -> Tuple[int, AgentAction]:
            action = await self._ask(
                pid,
                request="警长竞选：选择是否上警。想竞选警长请返回 run_for_sheriff。",
                allowed=["run_for_sheriff", "pass"],
                kind="werewolf_run_for_sheriff",
                timeout=60,
            )
            return pid, action
        run_results = await asyncio_gather_limited(
            [_ask_run(pid) for pid in self.state.alive_ids],
            limit=12,
        )
        for pid, action in run_results:
            if action.action == "run_for_sheriff":
                self.state.players[pid].sheriff_candidate = True
                await self.emit_event("WEREWOLF_SHERIFF_CANDIDATE_DECLARED", {
                    "day": self.state.day,
                    "player_id": pid,
                    "reason": action.reason,
                })

        candidate_ids = [pid for pid in self.state.alive_ids if self.state.players[pid].sheriff_candidate]
        if not candidate_ids:
            candidate_ids = self._fallback_sheriff_candidates()
            for pid in candidate_ids:
                self.state.players[pid].sheriff_candidate = True
                await self.emit_event("WEREWOLF_SHERIFF_CANDIDATE_DECLARED", {
                    "day": self.state.day,
                    "player_id": pid,
                    "reason": "fallback_no_candidate",
                })
        for pid in list(candidate_ids):
            if not self.state.players[pid].alive:
                continue
            action = await self._ask(
                pid,
                request="警上发言：说明你为什么竞选警长。可退水，也可在合法情况下自爆。",
                allowed=self._speech_allowed_actions(pid, include_withdraw=True),
                kind="werewolf_sheriff_speech",
                timeout=100,
                phase_override="sheriff_speech",
            )
            if await self._maybe_handle_reveal(pid, action, "sheriff_speech"):
                self.state.badge_destroyed = True
                await self.emit_event("WEREWOLF_SHERIFF_BADGE_DESTROYED", {
                    "day": self.state.day,
                    "reason": "werewolf_reveal_cancelled_election",
                })
                return "werewolf_revealed"
            if action.action == "withdraw_sheriff":
                self.state.players[pid].sheriff_withdrawn = True
                await self.emit_event("WEREWOLF_SHERIFF_WITHDRAWN", {
                    "day": self.state.day,
                    "player_id": pid,
                    "reason": action.reason,
                })
                continue
            if action.action == "speak":
                await self._public_speech_from_action(pid, action, stage="sheriff_speech")

        active_candidates = [
            pid for pid in self.state.alive_ids
            if self.state.players[pid].sheriff_candidate and not self.state.players[pid].sheriff_withdrawn
        ]
        if not active_candidates:
            await self.emit_event("WEREWOLF_SHERIFF_BADGE_DESTROYED", {
                "day": self.state.day,
                "reason": "no_candidate",
            })
            self.state.badge_destroyed = True
            return "no_sheriff"
        if len(active_candidates) == 1:
            await self._assign_sheriff(active_candidates[0], reason="single_candidate")
            return "sheriff_assigned"

        winner, tied = await self._run_sheriff_vote(active_candidates, stage="sheriff_vote")
        if winner is not None:
            await self._assign_sheriff(winner, reason="vote")
            return "sheriff_assigned"

        for pid in tied:
            action = await self._ask(
                pid,
                request="警长竞选平票 PK 发言。可继续争夺警长、退水，狼人可自爆。",
                allowed=self._speech_allowed_actions(pid, include_withdraw=True),
                kind="werewolf_sheriff_pk",
                timeout=80,
                phase_override="sheriff_pk_speech",
            )
            if await self._maybe_handle_reveal(pid, action, "sheriff_pk_speech"):
                self.state.badge_destroyed = True
                await self.emit_event("WEREWOLF_SHERIFF_BADGE_DESTROYED", {
                    "day": self.state.day,
                    "reason": "werewolf_reveal_cancelled_election",
                })
                return "werewolf_revealed"
            if action.action == "withdraw_sheriff":
                self.state.players[pid].sheriff_withdrawn = True
                await self.emit_event("WEREWOLF_SHERIFF_WITHDRAWN", {"day": self.state.day, "player_id": pid})
            elif action.action == "speak":
                await self._public_speech_from_action(pid, action, stage="sheriff_pk_speech")

        active_tied = [pid for pid in tied if self.state.players[pid].alive and not self.state.players[pid].sheriff_withdrawn]
        winner, tied_again = await self._run_sheriff_vote(active_tied, stage="sheriff_pk_vote")
        if winner is not None:
            await self._assign_sheriff(winner, reason="pk_vote")
        else:
            self.state.badge_destroyed = True
            await self.emit_event("WEREWOLF_SHERIFF_BADGE_DESTROYED", {
                "day": self.state.day,
                "reason": "sheriff_vote_tied",
                "tied_players": tied_again,
            })
        return "sheriff_done"

    async def _run_sheriff_vote(self, candidates: List[int], *, stage: str) -> Tuple[Optional[int], List[int]]:
        if not candidates:
            return None, []
        votes: List[Tuple[int, Optional[int], float]] = []
        candidate_set = set(candidates)
        eligible_voters = [
            pid for pid in self.state.alive_ids
            if not self.state.players[pid].sheriff_candidate
        ]
        if not eligible_voters:
            await self.emit_event("WEREWOLF_SHERIFF_VOTE_BATCH", {
                "day": self.state.day,
                "stage": stage,
                "votes": [],
                "fallback": False,
                "eligible_voters": [],
                "reason": "no_off_sheriff_voters",
            })
            return None, []
        for pid in eligible_voters:
            action = await self._ask(
                pid,
                request=f"警长投票：从候选人 {candidates} 中选择一人。候选人不能投自己。",
                allowed=["sheriff_vote", "pass"],
                kind=f"werewolf_{stage}",
                timeout=60,
                extra={"candidates": candidates},
            )
            target = action.target_player_id
            if action.action == "sheriff_vote" and target in candidate_set:
                votes.append((pid, target, 1.0))
                # Per-ballot hidden record only — public broadcast happens once after all votes,
                # so later voters can't see earlier choices and tail-vote.
                await self.emit_event("WEREWOLF_SHERIFF_VOTE_CAST", {
                    "day": self.state.day,
                    "stage": stage,
                    "voter_id": pid,
                    "target_player_id": target,
                }, audience="hidden")
            else:
                votes.append((pid, None, 0.0))
                self._mark_invalid_if_needed(pid, action, "invalid_sheriff_vote")
        used_fallback = False
        if not any(target is not None for _pid, target, _weight in votes):
            used_fallback = True
            votes = []
            for pid in eligible_voters:
                target = self._fallback_vote_target(pid, candidates, stage=stage)
                votes.append((pid, target, 1.0))
        # Reveal all sheriff ballots at once (simultaneous reveal, like a real game).
        await self.emit_event("WEREWOLF_SHERIFF_VOTE_BATCH", {
            "day": self.state.day,
            "stage": stage,
            "votes": [
                {"voter_id": voter, "target_player_id": target, "weight": weight}
                for voter, target, weight in votes
            ],
            "fallback": used_fallback,
            "eligible_voters": eligible_voters,
        })
        return self._tally_votes(votes)

    async def _assign_sheriff(self, player_id: int, *, reason: str) -> None:
        self.state.sheriff_id = player_id
        self.state.badge_destroyed = False
        for player in self.state.players.values():
            player.is_sheriff = player.player_id == player_id
        await self.emit_event("WEREWOLF_SHERIFF_ASSIGNED", {
            "day": self.state.day,
            "player_id": player_id,
            "reason": reason,
        })

    async def _sheriff_choose_direction(self) -> str:
        sheriff_id = self.state.sheriff_id
        if sheriff_id is None or sheriff_id not in self.state.alive_ids:
            return "clockwise"
        action = await self._ask(
            sheriff_id,
            request="警长选择今天白天发言顺序，在 direction 中返回 clockwise 或 counterclockwise。",
            allowed=["speak", "pass"],
            kind="werewolf_sheriff_direction",
            timeout=40,
        )
        return action.direction or "clockwise"

    def _speech_order(self, direction: str) -> List[int]:
        alive = list(self.state.alive_ids)
        if direction == "counterclockwise":
            alive.reverse()
        sheriff_id = self.state.sheriff_id
        if sheriff_id in alive:
            idx = alive.index(sheriff_id)
            alive = alive[idx + 1 :] + alive[: idx + 1]
        return alive

    def _fallback_sheriff_candidates(self) -> List[int]:
        alive = self.state.alive_ids
        if not alive:
            return []
        preferred_roles = {ROLE_SEER, ROLE_WEREWOLF, ROLE_WHITE_WOLF_KING, ROLE_KNIGHT}
        preferred_personalities = {"激进", "冲锋型", "控场型", "冒险型", "煽动型", "逻辑型"}
        scored: List[Tuple[int, int]] = []
        for pid in alive:
            player = self.state.players[pid]
            score = 0
            if player.role in preferred_roles:
                score += 4
            if player.personality in preferred_personalities:
                score += 2
            if player.role in GOD_ROLES:
                score += 1
            scored.append((-score, pid))
        scored.sort()
        count = min(3, max(1, len(alive) // 4))
        return [pid for _score, pid in scored[:count]]

    def _fallback_vote_target(self, voter_id: int, candidates: List[int], *, stage: str) -> int:
        # Fallback when a player times out / returns invalid JSON. MUST NOT read player roles —
        # the referee is neutral. Pick the next candidate after voter_id by seat order; this is
        # deterministic, role-agnostic, and avoids leaking which players are wolves vs good.
        ordered = sorted(candidates)
        if not ordered:
            return voter_id
        if voter_id in ordered and len(ordered) > 1:
            return ordered[(ordered.index(voter_id) + 1) % len(ordered)]
        return ordered[0]

    async def _run_exile_vote(self, *, stage: str, candidates: List[int]) -> str:
        winner, tied = await self._collect_exile_votes(candidates, stage=stage)
        if winner is None and len(tied) > 1:
            for pid in tied:
                if pid not in self.state.alive_ids:
                    continue
                action = await self._ask(
                    pid,
                    request=f"放逐平票 PK 发言。平票玩家：{tied}。狼人可自爆。",
                    allowed=self._speech_allowed_actions(pid, allow_knight=True),
                    kind="werewolf_exile_pk_speech",
                    timeout=80,
                    phase_override="day_pk_speech",
                )
                if await self._maybe_handle_reveal(pid, action, "day_pk_speech"):
                    return "werewolf_revealed"
                if await self._maybe_handle_knight_duel(pid, action, "day_pk_speech"):
                    return "knight_duel_hit_wolf"
                if action.action == "speak":
                    await self._public_speech_from_action(pid, action, stage="day_pk_speech")
                else:
                    await self._public_speech_from_action(pid, action, stage="day_pk_speech")
            winner, tied = await self._collect_exile_votes(tied, stage=f"{stage}_pk")

        if winner is None:
            await self.emit_event("WEREWOLF_EXILE_RESULT", {
                "day": self.state.day,
                "exiled_player_id": None,
                "reason": "tie_or_no_valid_votes",
                "tied_players": tied,
            })
            return "no_exile"

        self.state.kill_player(winner, "exile")
        self.state.players[winner].revealed = False
        await self.emit_event("WEREWOLF_EXILE_RESULT", {
            "day": self.state.day,
            "exiled_player_id": winner,
            "alive_players": self.state.alive_ids,
        })
        await self._handle_post_death_actions([winner], source="exile")
        return "exiled"

    async def _collect_exile_votes(self, candidates: List[int], *, stage: str) -> Tuple[Optional[int], List[int]]:
        votes: List[Tuple[int, Optional[int], float]] = []
        candidate_set = set(candidates)
        for pid in self.state.alive_ids:
            action = await self._ask(
                pid,
                request=f"放逐投票：从 {sorted(candidate_set)} 中选择一名玩家投票。",
                allowed=["vote", "pass"],
                kind=f"werewolf_{stage}",
                timeout=60,
                extra={"candidates": sorted(candidate_set)},
            )
            target = action.target_player_id
            weight = 1.5 if pid == self.state.sheriff_id else 1.0
            if action.action == "vote" and target in candidate_set and target != pid:
                votes.append((pid, target, weight))
                voter = self.state.players[pid]
                voter.vote_count += 1
                if self.state.players[target].role in WOLF_ROLES:
                    voter.correct_votes += 1
                else:
                    voter.wrong_votes += 1
                await self.emit_event("WEREWOLF_VOTE_CAST", {
                    "day": self.state.day,
                    "stage": stage,
                    "voter_id": pid,
                    "target_player_id": target,
                    "weight": weight,
                    "reason": action.reason,
                })
            else:
                votes.append((pid, None, 0.0))
                self._mark_invalid_if_needed(pid, action, "invalid_exile_vote")
        used_fallback = False
        if not any(target is not None for _pid, target, _weight in votes):
            used_fallback = True
            votes = []
            for pid in self.state.alive_ids:
                eligible = [candidate for candidate in sorted(candidate_set) if candidate != pid]
                if not eligible:
                    continue
                target = self._fallback_vote_target(pid, eligible, stage=stage)
                weight = 1.5 if pid == self.state.sheriff_id else 1.0
                votes.append((pid, target, weight))
                voter = self.state.players[pid]
                voter.vote_count += 1
                # Fallback votes are chosen by the referee, not the player — never credit/penalise
                # correct_votes / wrong_votes for them (otherwise the judge sees scores the player
                # didn't earn). Log to objective_features so the judge can discount these.
                self.state.objective_features.setdefault("fallback_votes", []).append({
                    "voter_id": pid,
                    "day": self.state.day,
                    "stage": stage,
                    "target_player_id": target,
                })
                await self.emit_event("WEREWOLF_VOTE_CAST", {
                    "day": self.state.day,
                    "stage": stage,
                    "voter_id": pid,
                    "target_player_id": target,
                    "weight": weight,
                    "reason": "fallback_no_valid_exile_votes",
                    "fallback": True,
                })
        await self.emit_event("WEREWOLF_VOTE_BATCH", {
            "day": self.state.day,
            "stage": stage,
            "votes": [
                {"voter_id": voter, "target_player_id": target, "weight": weight}
                for voter, target, weight in votes
            ],
            "fallback": used_fallback,
        })
        return self._tally_votes(votes)

    def _tally_votes(self, votes: List[Tuple[int, Optional[int], float]]) -> Tuple[Optional[int], List[int]]:
        totals: Dict[int, float] = {}
        for _voter, target, weight in votes:
            if target is None:
                continue
            totals[target] = totals.get(target, 0.0) + weight
        if not totals:
            return None, []
        max_votes = max(totals.values())
        tied = sorted(pid for pid, total in totals.items() if total == max_votes)
        if len(tied) == 1:
            return tied[0], tied
        return None, tied

    async def _handle_post_death_actions(self, death_ids: List[int], *, source: str) -> None:
        if not death_ids:
            return
        await self.emit_event("WEREWOLF_DEATH_RESOLVED", {
            "day": self.state.day,
            "source": source,
            "death_count": len(death_ids),
            "dead_players": list(death_ids),
            "alive_players": self.state.alive_ids,
        })
        for pid in list(death_ids):
            player = self.state.players.get(pid)
            if player is None:
                continue
            if player.role == ROLE_HUNTER and player.death_reason != "witch_poison":
                await self._hunter_shot(pid)
            if self.state.sheriff_id == pid:
                await self._transfer_or_destroy_badge(pid, source=source)

    async def _hunter_shot(self, hunter_id: int) -> None:
        alive_targets = [pid for pid in self.state.alive_ids if pid != hunter_id]
        if not alive_targets:
            return
        action = await self._ask(
            hunter_id,
            request="猎人死亡，可选择开枪带走一名存活玩家，或 pass 不开枪。",
            allowed=["hunter_shoot", "pass"],
            kind="werewolf_hunter_shoot",
            timeout=60,
            extra={"alive_targets": alive_targets},
        )
        target = action.target_player_id
        if action.action == "hunter_shoot" and target in alive_targets:
            self.state.kill_player(target, "hunter_shot")
            await self.emit_event("WEREWOLF_HUNTER_SHOT", {
                "day": self.state.day,
                "hunter_id": hunter_id,
                "target_player_id": target,
                "alive_players": self.state.alive_ids,
            })
            await self._handle_post_death_actions([target], source="hunter_shot")
        else:
            self._mark_invalid_if_needed(hunter_id, action, "invalid_hunter_shot")

    async def _transfer_or_destroy_badge(self, old_sheriff_id: int, *, source: str) -> None:
        self.state.sheriff_id = None
        alive_targets = [pid for pid in self.state.alive_ids if pid != old_sheriff_id]
        if not alive_targets:
            self.state.badge_destroyed = True
            await self.emit_event("WEREWOLF_SHERIFF_BADGE_DESTROYED", {
                "day": self.state.day,
                "old_sheriff_id": old_sheriff_id,
                "source": source,
                "reason": "no_alive_target",
            })
            return
        action = await self._ask(
            old_sheriff_id,
            request="你作为警长死亡，请选择移交警徽给一名存活玩家，或 destroy_badge=true 撕毁警徽。",
            allowed=["sheriff_badge_pass", "pass"],
            kind="werewolf_sheriff_badge",
            timeout=60,
            extra={"alive_targets": alive_targets},
        )
        target = action.target_player_id
        if action.action == "sheriff_badge_pass" and not action.destroy_badge and target in alive_targets:
            await self._assign_sheriff(target, reason=f"badge_pass_from_{old_sheriff_id}")
            await self.emit_event("WEREWOLF_SHERIFF_BADGE_PASSED", {
                "day": self.state.day,
                "from_player_id": old_sheriff_id,
                "to_player_id": target,
                "source": source,
            })
            return
        self.state.badge_destroyed = True
        await self.emit_event("WEREWOLF_SHERIFF_BADGE_DESTROYED", {
            "day": self.state.day,
            "old_sheriff_id": old_sheriff_id,
            "source": source,
            "reason": "chosen_or_timeout",
        })

    async def _maybe_handle_reveal(self, player_id: int, action: AgentAction, phase: str) -> bool:
        if action.action not in {"werewolf_reveal", "white_wolf_king_reveal"}:
            return False
        player = self.state.players[player_id]
        if action.action == "white_wolf_king_reveal":
            return await self._handle_white_wolf_king_reveal(player_id, action, phase)
        if player.role == ROLE_WHITE_WOLF_KING:
            return await self._handle_white_wolf_king_reveal(player_id, action, phase)
        ok, reason = self.state.reveal_self(player_id, phase)
        if not ok:
            self.state.players[player_id].invalid_actions += 1
            await self.emit_event("WEREWOLF_PLAYER_ACTION_RESOLVED", {
                "day": self.state.day,
                "phase": phase,
                "player_id": player_id,
                "action": "invalid_werewolf_reveal",
                "kind": "werewolf_reveal",
                "valid": False,
                "reason": reason,
            })
            return False
        await self.emit_event("WEREWOLF_REVEALED_SELF", {
            "day": self.state.day,
            "player_id": player_id,
            "role": ROLE_WEREWOLF,
            "phase": phase,
            "alive_players": self.state.alive_ids,
        })
        # Route through unified post-death pipeline so death/sheriff-badge/hunter (etc.) are
        # handled the same way as wolf-kill / poison / exile deaths.
        await self._handle_post_death_actions([player_id], source="werewolf_reveal")
        return True

    async def _handle_white_wolf_king_reveal(self, player_id: int, action: AgentAction, phase: str) -> bool:
        player = self.state.players.get(player_id)
        target = action.target_player_id
        if (
            player is None
            or player.role != ROLE_WHITE_WOLF_KING
            or not player.alive
            or phase not in WHITE_WOLF_KING_REVEAL_PHASES
            or target not in self.state.alive_ids
            or target == player_id
        ):
            if player is not None:
                player.invalid_actions += 1
            await self.emit_event("WEREWOLF_PLAYER_ACTION_RESOLVED", {
                "day": self.state.day,
                "phase": phase,
                "player_id": player_id,
                "target_player_id": target,
                "action": "invalid_white_wolf_king_reveal",
                "kind": "white_wolf_king_reveal",
                "valid": False,
                "reason": "invalid_role_phase_or_target",
            })
            return False

        player.revealed = True
        deaths = []
        if self.state.kill_player(player_id, "white_wolf_king_reveal"):
            deaths.append(player_id)
        if self.state.kill_player(target, "white_wolf_king_takeaway"):
            deaths.append(target)
        self.state.objective_features.setdefault("white_wolf_king_reveals", []).append({
            "player_id": player_id,
            "target_player_id": target,
            "target_role": self.state.players[target].role,
            "day": self.state.day,
            "phase": phase,
        })
        await self.emit_event("WEREWOLF_WHITE_WOLF_KING_REVEALED", {
            "day": self.state.day,
            "player_id": player_id,
            "target_player_id": target,
            "target_role_label": role_label(self.state.players[target].role),
            "phase": phase,
            "alive_players": self.state.alive_ids,
            "reason": action.reason,
        })
        await self._handle_post_death_actions(deaths, source="white_wolf_king_reveal")
        return True

    async def _maybe_handle_knight_duel(self, player_id: int, action: AgentAction, phase: str) -> bool:
        if action.action != "knight_duel":
            return False
        knight = self.state.players.get(player_id)
        target = action.target_player_id
        valid = (
            knight is not None
            and knight.role == ROLE_KNIGHT
            and knight.alive
            and not self.state.knight_duel_used
            and phase in KNIGHT_DUEL_PHASES
            and target in self.state.alive_ids
            and target != player_id
        )
        if not valid:
            if knight is not None:
                knight.invalid_actions += 1
            await self.emit_event("WEREWOLF_KNIGHT_DUEL", {
                "day": self.state.day,
                "knight_id": player_id,
                "target_player_id": target,
                "phase": phase,
                "valid": False,
                "reason": "invalid_knight_duel",
            })
            return False

        self.state.knight_duel_used = True
        target_player = self.state.players[target]
        target_is_wolf = target_player.role in WOLF_ROLES
        dead_player_id = target if target_is_wolf else player_id
        death_reason = "knight_duel_hit_wolf" if target_is_wolf else "knight_duel_missed"
        self.state.kill_player(dead_player_id, death_reason)
        self.state.objective_features.setdefault("knight_duels", []).append({
            "knight_id": player_id,
            "target_player_id": target,
            "target_role": target_player.role,
            "hit_wolf": target_is_wolf,
            "day": self.state.day,
            "phase": phase,
        })
        await self.emit_event("WEREWOLF_KNIGHT_DUEL", {
            "day": self.state.day,
            "knight_id": player_id,
            "target_player_id": target,
            "target_role_label": role_label(target_player.role),
            "hit_wolf": target_is_wolf,
            "dead_player_id": dead_player_id,
            "phase": phase,
            "valid": True,
            "alive_players": self.state.alive_ids,
            "reason": action.reason,
        })
        await self._handle_post_death_actions([dead_player_id], source="knight_duel")
        return target_is_wolf

    async def _public_speech(self, player_id: int, text: str, *, stage: str) -> None:
        player = self.state.players[player_id]
        player.speech_count += 1
        public_text = text[:1200] if text else "(pass)"
        # Public broadcast: never include role/team OR personality — personality is private,
        # other players shouldn't be able to read role hints from it.
        await self.emit_event("WEREWOLF_PUBLIC_SPEECH", {
            "day": self.state.day,
            "stage": stage,
            "player_id": player_id,
            "speaker_id": player_id,
            "text": public_text,
        })
        # Hidden audit copy with role/team for the AI judge.
        await self.emit_event("WEREWOLF_PUBLIC_SPEECH_PRIVATE", {
            "day": self.state.day,
            "stage": stage,
            "player_id": player_id,
            "role": player.role,
            "role_label": role_label(player.role),
            "team": player.team,
            "text": public_text,
        }, audience="hidden")

    async def _public_speech_from_action(self, player_id: int, action: AgentAction, *, stage: str) -> None:
        player = self.state.players[player_id]
        player.speech_count += 1
        public_text = action.text[:1200] if action.text else "(pass)"
        await self.emit_event("WEREWOLF_PUBLIC_SPEECH", {
            "day": self.state.day,
            "stage": stage,
            "player_id": player_id,
            "speaker_id": player_id,
            "text": public_text,
            "claim_role": action.claim_role or None,
            "suspects": action.suspects,
            "vote_intent": action.vote_intent,
        })
        await self.emit_event("WEREWOLF_PUBLIC_SPEECH_PRIVATE", {
            "day": self.state.day,
            "stage": stage,
            "player_id": player_id,
            "role": player.role,
            "role_label": role_label(player.role),
            "team": player.team,
            "text": public_text,
            "claim_role": action.claim_role or None,
            "suspects": action.suspects,
            "vote_intent": action.vote_intent,
        }, audience="hidden")

    async def _ask(
        self,
        player_id: int,
        *,
        request: str,
        allowed: List[str],
        kind: str,
        timeout: int,
        extra: Optional[Dict[str, Any]] = None,
        phase_override: Optional[str] = None,
    ) -> AgentAction:
        previous_phase = self.state.phase
        if phase_override:
            self.state.phase = phase_override
        context_preview = self.state.private_context(player_id)
        is_private_kind = kind in PRIVATE_NIGHT_KINDS
        # Public turn payload: never includes private context (role / wolf teammates / seer history / witch items).
        public_turn_payload = {
            "day": self.state.day,
            "phase": phase_override or self.state.phase,
            "player_id": player_id,
            "request": request if not is_private_kind else "(private night action)",
            "allowed_actions": list(allowed) if not is_private_kind else [],
        }
        await self.emit_event("WEREWOLF_PLAYER_TURN_STARTED", public_turn_payload)
        # Hidden full payload (for audit / AI judge); never broadcast to WS subscribers.
        await self.emit_event("WEREWOLF_PLAYER_TURN_STARTED_PRIVATE", {
            **public_turn_payload,
            "request": request,
            "allowed_actions": list(allowed),
            "context": context_preview,
        }, audience="hidden")
        prompt = render_action_prompt(
            self.state,
            player_id,
            request=request,
            allowed_actions=allowed,
            extra_context=extra,
        )
        response = await self.agent_request(player_id, prompt, kind, timeout)
        if phase_override:
            self.state.phase = previous_phase
        action = parse_agent_action(response, allowed)
        # Clamp player-id-bearing fields to the actual player_ids set; an LLM may emit out-of-range
        # ids (suspects=[999], vote_intent=42) which would otherwise be broadcast as-is.
        valid_ids = set(self.state.player_ids)
        action.suspects = [pid for pid in action.suspects if pid in valid_ids]
        if action.vote_intent is not None and action.vote_intent not in valid_ids:
            action.vote_intent = None
        if response is None:
            self.state.players[player_id].timeouts += 1
            action.error = action.error or "timeout"
        if action.error:
            self.state.players[player_id].invalid_actions += 1
        full_resolved_payload = {
            "day": self.state.day,
            "phase": phase_override or self.state.phase,
            "player_id": player_id,
            "personality": self.state.players[player_id].personality,
            "kind": kind,
            "action": action.action,
            "target_player_id": action.target_player_id,
            "text": action.text[:1200] if action.text else "",
            "reason": action.reason,
            "claim_role": action.claim_role or None,
            "suspects": action.suspects,
            "vote_intent": action.vote_intent,
            "valid": action.error is None,
            "error": action.error,
        }
        if is_private_kind:
            # Public: only signal that the player acted — never expose target, text, claim, or suspects.
            await self.emit_event("WEREWOLF_PLAYER_ACTION_RESOLVED", {
                "day": self.state.day,
                "phase": phase_override or self.state.phase,
                "player_id": player_id,
                "kind": kind,
                "action": "private_night_action",
                "valid": action.error is None,
            })
            await self.emit_event("WEREWOLF_PLAYER_ACTION_RESOLVED_PRIVATE", full_resolved_payload, audience="hidden")
        else:
            await self.emit_event("WEREWOLF_PLAYER_ACTION_RESOLVED", full_resolved_payload)
        return action

    def _mark_invalid_if_needed(self, player_id: int, action: AgentAction, reason: str) -> None:
        if action.action == "pass":
            return
        self.state.players[player_id].invalid_actions += 1
        self.state.objective_features.setdefault("invalid_actions", []).append({
            "player_id": player_id,
            "day": self.state.day,
            "phase": self.state.phase,
            "action": action.action,
            "reason": action.error or reason,
        })


async def asyncio_gather_limited(coros: List[Awaitable[Any]], *, limit: int) -> List[Any]:
    semaphore = __import__("asyncio").Semaphore(limit)

    async def run_one(coro: Awaitable[Any]) -> Any:
        async with semaphore:
            return await coro

    return await __import__("asyncio").gather(*(run_one(coro) for coro in coros))
