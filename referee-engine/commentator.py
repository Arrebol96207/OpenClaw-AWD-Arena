from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol

import aiohttp

from env_utils import truthy, positive_int
from redaction import is_sensitive_key, redact_text, redact_value

REDACTED = "[REDACTED]"
DEFAULT_INTERVAL_SECONDS = 20
DEFAULT_MAX_LOG_CHARS = 4000
MAX_PENDING_EVENTS = 100

OBSERVED_EVENT_TYPES = {
    "MATCH_STARTED",
    "STATUS",
    "PHASE_CHANGE",
    "HEARTBEAT",
    "SLA_UPDATE",
    "FLAG_SUBMISSION",
    "FLAG_CAPTURED",
    "FLAG_SUBMISSION_REJECTED",
    "MATCH_FINISHED",
    "AGENT_STREAM",
    "AGENT_LOGS_COLLECTED",
}
OBSERVED_EVENT_TYPES.update({
    "WEREWOLF_TRAINING_STARTED",
    "WEREWOLF_TRAINING_COMPLETED",
    "WEREWOLF_GAME_STARTED",
    "WEREWOLF_NIGHT_STARTED",
    "WEREWOLF_DAY_STARTED",
    "WEREWOLF_SHERIFF_ELECTION_STARTED",
    "WEREWOLF_SHERIFF_CANDIDATE_DECLARED",
    "WEREWOLF_SHERIFF_WITHDRAWN",
    "WEREWOLF_SHERIFF_VOTE_CAST",
    "WEREWOLF_SHERIFF_ASSIGNED",
    "WEREWOLF_SHERIFF_BADGE_PASSED",
    "WEREWOLF_SHERIFF_BADGE_DESTROYED",
    "WEREWOLF_WOLF_CHAT_PUBLIC",
    "WEREWOLF_WOLF_KILL_VOTE_CAST",
    "WEREWOLF_WOLF_KILL_DECIDED",
    "WEREWOLF_PUBLIC_SPEECH",
    "WEREWOLF_VOTE_CAST",
    "WEREWOLF_EXILE_RESULT",
    "WEREWOLF_DEATH_RESOLVED",
    "WEREWOLF_REVEALED_SELF",
    "WEREWOLF_WHITE_WOLF_KING_REVEALED",
    "WEREWOLF_KNIGHT_DUEL",
    "WEREWOLF_HUNTER_SHOT",
    "WEREWOLF_GAME_FINISHED",
    "WEREWOLF_AI_JUDGEMENT",
})
IMMEDIATE_TRIGGER_EVENTS = {
    "FLAG_CAPTURED",
    "FLAG_SUBMISSION_REJECTED",
    "PHASE_CHANGE",
    "MATCH_FINISHED",
}
IMMEDIATE_TRIGGER_EVENTS.update({
    "WEREWOLF_SHERIFF_ASSIGNED",
    "WEREWOLF_REVEALED_SELF",
    "WEREWOLF_WHITE_WOLF_KING_REVEALED",
    "WEREWOLF_KNIGHT_DUEL",
    "WEREWOLF_EXILE_RESULT",
    "WEREWOLF_DEATH_RESOLVED",
    "WEREWOLF_GAME_FINISHED",
    "WEREWOLF_AI_JUDGEMENT",
})
IGNORED_EVENT_TYPES = {
    "AI_COMMENTARY",
    "AI_COMMENTARY_STATUS",
}

TOPIC_KEYWORDS: List[tuple[str, List[str]]] = [
    ("鉴权", ["auth", "authorization", "api key", "api_key", "token", "cookie", "session", "login", "鉴权", "权限"]),
    ("路径", ["path", "file", "dir", "normalize", "backup", "static", "路径", "目录", "文件"]),
    ("SQL/数据库", ["sql", "sqlite", "database", "db", "query", "数据库", "数据表"]),
    ("SSRF/转发", ["ssrf", "redirect", "fetch", "proxy", "webhook", "forward", "转发", "回调", "内网"]),
    ("模板/渲染", ["template", "render", "include", "jinja", "模板", "渲染"]),
    ("队列/任务", ["queue", "job", "worker", "async", "cron", "任务", "队列", "异步"]),
    ("容器/执行", ["docker", "exec", "shell", "subprocess", "容器", "执行"]),
    ("报告/导出", ["report", "bundle", "export", "导出", "报告", "打包"]),
    ("审计/日志", ["audit", "logs", "log", "snapshot", "审计", "日志"]),
    ("网络/头部", ["host", "header", "ip", "network", "networking", "网络", "头部"]),
]


def _is_sensitive_key(key: Any) -> bool:
    return is_sensitive_key(key)


def sanitize_commentary_text(value: str) -> str:
    return redact_text(value, redacted=REDACTED)


def sanitize_commentary_value(value: Any) -> Any:
    return redact_value(value, redacted=REDACTED)


def _normalize_text_sources(values: List[str]) -> str:
    pieces = [sanitize_commentary_text(value) for value in values if value and value.strip()]
    return "\n".join(pieces)


def _extract_topic_labels(text: str, *, limit: int = 3) -> List[str]:
    lowered = text.lower()
    labels: List[str] = []
    for label, keywords in TOPIC_KEYWORDS:
        if any(keyword.lower() in lowered for keyword in keywords):
            labels.append(label)
    return labels[:limit]


def _summarize_event_for_prompt(event: Dict[str, Any]) -> str:
    event_type = str(event.get("type") or "UNKNOWN")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    if event_type == "FLAG_CAPTURED":
        attacker_id = data.get("attacker_id", data.get("player_id", "?"))
        victim_id = data.get("victim_id", "?")
        flag_index = data.get("flag_index")
        points = data.get("points")
        suffix = f" #{flag_index}" if flag_index is not None else ""
        gain = f" +{points}" if isinstance(points, int) else ""
        return f"FLAG_CAPTURED P{attacker_id} -> P{victim_id}{suffix}{gain}"
    if event_type == "FLAG_SUBMISSION_REJECTED":
        attacker_id = data.get("attacker_id", "?")
        reason = data.get("reason", "unknown")
        return f"FLAG_SUBMISSION_REJECTED P{attacker_id} ({reason})"
    if event_type == "PHASE_CHANGE":
        phase = data.get("phase", "unknown")
        return f"PHASE_CHANGE {phase}"
    if event_type == "MATCH_STARTED":
        status = data.get("status", "defense")
        return f"MATCH_STARTED {status}"
    if event_type == "SLA_UPDATE":
        down = data.get("results", {})
        if isinstance(down, dict):
            down_count = sum(1 for item in down.values() if isinstance(item, dict) and item.get("up") is False)
            return f"SLA_UPDATE down={down_count}"
    if event_type == "AGENT_LOGS_COLLECTED":
        players = data.get("players", {})
        if isinstance(players, dict):
            return f"AGENT_LOGS_COLLECTED players={len(players)}"
    if event_type == "AGENT_STREAM":
        player_id = data.get("player_id", "?")
        content = data.get("content")
        size = len(content) if isinstance(content, str) else 0
        return f"AGENT_STREAM P{player_id} chars={size}"
    if event_type.startswith("WEREWOLF_"):
        if event_type == "WEREWOLF_PUBLIC_SPEECH":
            return f"狼人杀发言 P{data.get('player_id', '?')} {data.get('stage', '')}"
        if event_type == "WEREWOLF_VOTE_CAST":
            return f"狼人杀投票 P{data.get('voter_id', '?')} -> P{data.get('target_player_id', '?')}"
        if event_type == "WEREWOLF_SHERIFF_ASSIGNED":
            return f"警长当选 P{data.get('player_id', '?')}"
        if event_type == "WEREWOLF_REVEALED_SELF":
            return f"狼人自爆 P{data.get('player_id', '?')}"
        if event_type == "WEREWOLF_WHITE_WOLF_KING_REVEALED":
            return f"白狼王自爆 P{data.get('player_id', '?')} 带走 P{data.get('target_player_id', '?')}"
        if event_type == "WEREWOLF_KNIGHT_DUEL":
            outcome = "命中狼人" if data.get("hit_wolf") else "撞到好人"
            return f"骑士决斗 P{data.get('knight_id', '?')} -> P{data.get('target_player_id', '?')} {outcome}"
        if event_type == "WEREWOLF_WOLF_KILL_DECIDED":
            return f"狼队刀口 P{data.get('target_player_id', 'none')}"
        if event_type == "WEREWOLF_EXILE_RESULT":
            return f"放逐结果 P{data.get('exiled_player_id', 'none')}"
        if event_type == "WEREWOLF_DEATH_RESOLVED":
            return f"死亡结算 count={data.get('death_count', '?')}"
        if event_type == "WEREWOLF_AI_JUDGEMENT":
            return "AI裁判评分完成"
        return event_type
    return event_type


def _build_phase_focus(phase: str, topic_labels: List[str], recent_event_summaries: List[str]) -> str:
    if phase.startswith("werewolf"):
        summary = "；".join(recent_event_summaries[-4:]) if recent_event_summaries else "当前轮次信息"
        if "sheriff" in phase:
            return f"狼人杀警上阶段要讲清楚谁在争警徽、谁可能悍跳、警徽流是否可信。最近事件：{summary}。"
        if "night" in phase:
            return f"狼人杀夜晚阶段只能解说公开态势，不要泄露隐藏身份或夜间结果。最近事件：{summary}。"
        if "day" in phase:
            return f"狼人杀白天阶段要分析发言矛盾、站边变化、归票压力和自爆收益。最近事件：{summary}。"
        if "finished" in phase:
            return "狼人杀收官阶段要复盘身份、关键投票、自爆/警徽/技能收益和个人评分。"
        return f"狼人杀赛况解说要聚焦社交推理、发言可信度和局势转折。最近事件：{summary}。"
    labels = "、".join(topic_labels[:3]) if topic_labels else "边界校验、输入处理和权限收紧"
    if phase == "defense":
        return f"防御阶段要讲清楚他们在修补什么。优先说 {labels}，不要说“没有积分变化”。"
    if phase == "attack":
        summary = "；".join(recent_event_summaries[-2:]) if recent_event_summaries else "最近的攻势和破口"
        return f"攻击阶段要讲清楚攻击了什么、为什么能打进去或被挡住。参考 {labels}，结合最近事件：{summary}。"
    if phase == "finished":
        return "收官阶段要总结决定胜负的关键破口、关键防守和比分拐点。"
    return "保持简洁，优先讲当前最值得观众听懂的战术变化。"


@dataclass(frozen=True)
class CommentaryConfig:
    enabled: bool = False
    provider: str = "openai-completions"
    model: str = ""
    api_key: str = ""
    base_url: str = ""
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    max_log_chars: int = DEFAULT_MAX_LOG_CHARS
    timeout_seconds: int = 20

    @classmethod
    def from_env(cls) -> "CommentaryConfig":
        return cls(
            enabled=truthy(os.getenv("COMMENTATOR_ENABLED")),
            provider=os.getenv("COMMENTATOR_PROVIDER", "openai-completions").strip() or "openai-completions",
            model=os.getenv("COMMENTATOR_MODEL", "").strip(),
            api_key=os.getenv("COMMENTATOR_API_KEY", "").strip(),
            base_url=os.getenv("COMMENTATOR_BASE_URL", "").strip(),
            interval_seconds=positive_int(os.getenv("COMMENTATOR_INTERVAL_SECONDS"), DEFAULT_INTERVAL_SECONDS),
            max_log_chars=positive_int(os.getenv("COMMENTATOR_MAX_LOG_CHARS"), DEFAULT_MAX_LOG_CHARS),
        )

    @property
    def available(self) -> bool:
        return bool(self.enabled and self.model and self.api_key and self.base_url)


class CommentaryClient(Protocol):
    async def generate_commentary(self, system_prompt: str, user_prompt: str) -> str:
        ...


class OpenAICompatibleCommentaryClient:
    def __init__(self, config: CommentaryConfig):
        self.config = config

    def _endpoint(self) -> str:
        base_url = self.config.base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        return f"{base_url}/chat/completions"

    async def generate_commentary(self, system_prompt: str, user_prompt: str) -> str:
        payload = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.35,
            "max_tokens": 260,
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
                    raise RuntimeError(f"commentary LLM HTTP {response.status}: {body[:300]}")
                try:
                    data = json.loads(body)
                except json.JSONDecodeError as exc:
                    raise RuntimeError("commentary LLM returned invalid JSON") from exc

        choices = data.get("choices") if isinstance(data, dict) else None
        if not isinstance(choices, list) or not choices:
            return ""
        first = choices[0]
        if not isinstance(first, dict):
            return ""
        message = first.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"].strip()
        text = first.get("text")
        return text.strip() if isinstance(text, str) else ""


def normalize_observed_event(raw_event: Dict[str, Any]) -> Dict[str, Any]:
    event_type = str(raw_event.get("type") or "UNKNOWN")
    timestamp = raw_event.get("timestamp")
    data = raw_event.get("data")
    if not isinstance(data, dict):
        data = {
            key: value
            for key, value in raw_event.items()
            if key not in {"type", "match_id", "timestamp"}
        }
    return {
        "type": event_type,
        "timestamp": timestamp if isinstance(timestamp, str) else datetime.now().isoformat(),
        "data": data,
    }


def _summarize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    event_type = str(event.get("type") or "UNKNOWN")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}

    if event_type == "AGENT_STREAM":
        content = data.get("content")
        safe_data = {
            "player_id": data.get("player_id"),
            "content_chars": len(content) if isinstance(content, str) else 0,
        }
    elif event_type == "AGENT_LOGS_COLLECTED":
        safe_data = {
            "players": data.get("players", {}),
            "logs_collected": True,
        }
    else:
        safe_data = dict(data)

    return {
        "type": event_type,
        "timestamp": event.get("timestamp"),
        "data": sanitize_commentary_value(safe_data),
    }


def _latest_remaining_seconds(match: Any, events: List[Dict[str, Any]]) -> Optional[int]:
    for event in reversed(events):
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        value = data.get("remaining_seconds")
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _leaderboard_snapshot(match: Any) -> List[Dict[str, Any]]:
    players = getattr(match, "players", {}) or {}
    rows: List[Dict[str, Any]] = []
    for player_id, player in players.items():
        rows.append(
            {
                "player_id": int(player_id),
                "score": getattr(player, "score", 0),
                "attack_score": getattr(player, "attack_score", 0),
                "defense_score": getattr(player, "defense_score", 0),
                "sla_score": getattr(player, "sla_score", 0),
                "flags_captured": getattr(player, "flags_captured", 0),
                "flags_lost": getattr(player, "flags_lost", 0),
                "sla_up": getattr(player, "sla_up", True),
                "sla_down_minutes": getattr(player, "sla_down_minutes", 0),
                "ready_status": getattr(player, "ready_status", None),
            }
        )

    rows.sort(key=lambda row: int(row.get("score") or 0), reverse=True)
    return rows


def _collect_agent_log_summary(match: Any, events: List[Dict[str, Any]], max_chars: int) -> Dict[str, str]:
    by_player: Dict[str, List[str]] = {}
    persisted_logs = getattr(match, "agent_logs", {}) or {}
    if isinstance(persisted_logs, dict) and persisted_logs:
        for player_id, content in persisted_logs.items():
            if isinstance(content, str):
                by_player.setdefault(str(player_id), []).append(content)
    else:
        for event in events:
            if event.get("type") != "AGENT_STREAM":
                continue
            data = event.get("data")
            if not isinstance(data, dict):
                continue
            player_id = data.get("player_id")
            content = data.get("content")
            if player_id is not None and isinstance(content, str):
                by_player.setdefault(str(player_id), []).append(content)

    remaining = max_chars
    summary: Dict[str, str] = {}
    for player_id in sorted(by_player, key=lambda raw: int(raw) if raw.isdigit() else raw):
        if remaining <= 0:
            break
        text = sanitize_commentary_text("\n".join(by_player[player_id]))
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        excerpt = "\n".join(lines[-12:])
        if len(excerpt) > remaining:
            excerpt = excerpt[-remaining:]
        summary[player_id] = excerpt
        remaining -= len(excerpt)
    return summary


def build_commentary_context(match: Any, events: List[Dict[str, Any]], max_log_chars: int) -> Dict[str, Any]:
    summarized_events = [_summarize_event(event) for event in events[-25:]]
    prompt_event_summaries = [_summarize_event_for_prompt(event) for event in events[-25:]]
    agent_log_summary = _collect_agent_log_summary(match, events, max_log_chars)
    recent_text_sources = list(prompt_event_summaries)
    recent_text_sources.extend(value for value in agent_log_summary.values())
    topic_labels = _extract_topic_labels(_normalize_text_sources(recent_text_sources), limit=5)
    return {
        "match_id": getattr(match, "match_id", ""),
        "phase": getattr(match, "status", "unknown"),
        "player_count": len(getattr(match, "players", {}) or {}),
        "remaining_seconds": _latest_remaining_seconds(match, events),
        "leaderboard": sanitize_commentary_value(_leaderboard_snapshot(match)),
        "recent_events": summarized_events,
        "agent_log_summary": agent_log_summary,
        "topic_labels": topic_labels,
        "phase_focus": _build_phase_focus(str(getattr(match, "status", "unknown")), topic_labels, prompt_event_summaries),
    }


def render_commentary_prompts(context: Dict[str, Any]) -> tuple[str, str]:
    phase = str(context.get("phase") or "unknown")
    phase_focus = str(context.get("phase_focus") or "")
    topic_labels = context.get("topic_labels") or []
    if isinstance(topic_labels, list):
        topic_text = "、".join(str(label) for label in topic_labels[:3] if str(label).strip())
    else:
        topic_text = ""
    if phase.startswith("werewolf"):
        system_prompt = (
            "You are the AI live commentator for a 12-player Werewolf social deduction match. "
            "Reply in Simplified Chinese. Keep the commentary to 2-4 short sentences. "
            "Be concrete, tactical, and viewer-friendly. Analyze sheriff election, badge flow, speeches, voting pressure, "
            "werewolf self-reveal timing, role claims, and social deduction. "
            "You may discuss structured spectator-visible wolf chat, wolf kill votes, final kill targets, white wolf king reveal, and knight duel events when they are present. "
            "Before final reveal, do not infer or leak hidden roles, seer results, witch choices, guard choices, or private data that is not explicitly in spectator-visible events. "
            "After final judgement, you may discuss revealed roles and score reasons."
        )
        user_prompt = (
            "Generate one live commentary update from this sanitized Werewolf match context:\n"
            f"Phase: {phase}\n"
            f"Focus: {phase_focus}\n"
            f"{json.dumps(context, ensure_ascii=False, default=str)}"
        )
        return system_prompt, user_prompt
    system_prompt = (
        "You are the AI live commentator for an AWD cybersecurity match. "
        "Reply in Simplified Chinese. Keep the commentary to 2-4 short sentences. "
        "Be concrete, tactical, and viewer-friendly. "
        "If the phase is defense, talk about what is being patched or hardened, not about 'no score change'. "
        "If the phase is attack, talk about what is being attacked and why it broke or held. "
        "If a capture or rejection just happened, comment immediately on the turning point. "
        "Do not reveal flags, tokens, API keys, hidden paths, raw exploit commands, or step-by-step exploit instructions. "
        "If sensitive material appears in the input, refer to it only as redacted evidence."
    )
    user_prompt = (
        "Generate one live commentary update from this sanitized match context:\n"
        f"Phase: {phase}\n"
        f"Focus: {phase_focus}\n"
        f"Topics: {topic_text}\n"
        f"{json.dumps(context, ensure_ascii=False, default=str)}"
    )
    return system_prompt, user_prompt


EmitCallback = Callable[[Any, Dict[str, Any]], Awaitable[None]]


class CommentatorService:
    def __init__(
        self,
        config: CommentaryConfig,
        *,
        client: Optional[CommentaryClient] = None,
        logger: Optional[logging.Logger] = None,
    ):
        self.config = config
        self.client = client or OpenAICompatibleCommentaryClient(config)
        self.logger = logger or logging.getLogger(__name__)
        self._pending_events: Dict[str, List[Dict[str, Any]]] = {}
        self._tasks: Dict[str, asyncio.Task] = {}
        self._active_generations: set[str] = set()
        self._last_emit_at: Dict[str, float] = {}
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @classmethod
    def from_env(cls, *, logger: Optional[logging.Logger] = None) -> "CommentatorService":
        return cls(CommentaryConfig.from_env(), logger=logger)

    @property
    def available(self) -> bool:
        return self.config.available

    async def observe_event(
        self,
        match: Any,
        raw_event: Dict[str, Any],
        emit_callback: EmitCallback,
    ) -> None:
        if not self.available:
            return
        if raw_event.get("audience") == "hidden" or raw_event.get("_audience") == "hidden":
            return

        event = normalize_observed_event(raw_event)
        event_type = event["type"]
        if event_type in IGNORED_EVENT_TYPES or event_type not in OBSERVED_EVENT_TYPES:
            return

        match_id = str(getattr(match, "match_id", "") or raw_event.get("match_id") or "")
        if not match_id:
            return

        immediate = event_type in IMMEDIATE_TRIGGER_EVENTS
        trigger = event_type.lower()
        async with self._get_lock():
            pending = self._pending_events.setdefault(match_id, [])
            pending.append(event)
            if len(pending) > MAX_PENDING_EVENTS:
                del pending[:-MAX_PENDING_EVENTS]

            existing = self._tasks.get(match_id)
            if existing and not existing.done():
                if immediate and match_id not in self._active_generations:
                    existing.cancel()
                else:
                    return

            delay = 0.0
            if not immediate:
                loop = asyncio.get_running_loop()
                last_emit_at = self._last_emit_at.get(match_id)
                delay = (
                    float(self.config.interval_seconds)
                    if last_emit_at is None
                    else max(0.0, float(self.config.interval_seconds) - (loop.time() - last_emit_at))
                )
                trigger = "batch"

            self._tasks[match_id] = asyncio.create_task(
                self._delayed_generate(match_id, match, emit_callback, trigger, delay)
            )

    async def _delayed_generate(
        self,
        match_id: str,
        match: Any,
        emit_callback: EmitCallback,
        trigger: str,
        delay: float,
    ) -> None:
        try:
            if delay > 0:
                await asyncio.sleep(delay)
            await self.generate_pending(match, emit_callback, trigger=trigger)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.warning("AI commentator generation failed for %s: %s", match_id, exc)
        finally:
            task = self._tasks.get(match_id)
            if task is asyncio.current_task():
                self._tasks.pop(match_id, None)

    async def generate_pending(
        self,
        match: Any,
        emit_callback: EmitCallback,
        *,
        trigger: str = "manual",
    ) -> bool:
        if not self.available:
            return False

        match_id = str(getattr(match, "match_id", ""))
        generation_task = asyncio.current_task()
        async with self._get_lock():
            events = self._pending_events.pop(match_id, [])
        if not events:
            return False

        self._active_generations.add(match_id)
        try:
            context = build_commentary_context(match, events, self.config.max_log_chars)
            system_prompt, user_prompt = render_commentary_prompts(context)
            text = await self.client.generate_commentary(system_prompt, user_prompt)
        except asyncio.CancelledError:
            async with self._get_lock():
                self._pending_events.setdefault(match_id, events[:0])[0:0] = events
            raise
        except Exception as exc:
            self.logger.warning("AI commentator request failed for %s: %s", match_id, exc)
            return False
        finally:
            self._active_generations.discard(match_id)

        text = sanitize_commentary_text(str(text or "").strip())
        if not text:
            return False

        payload = {
            "commentary_id": uuid.uuid4().hex,
            "timestamp": datetime.now().isoformat(),
            "trigger": trigger,
            "style": "live_tactical_zh",
            "text": text,
            "covered_events": [
                {
                    "type": event.get("type"),
                    "timestamp": event.get("timestamp"),
                }
                for event in events[-20:]
            ],
        }
        await emit_callback(match, payload)
        self._last_emit_at[match_id] = asyncio.get_running_loop().time()

        async with self._get_lock():
            if self._tasks.get(match_id) is generation_task:
                self._tasks.pop(match_id, None)
            pending_events = self._pending_events.get(match_id, [])
            if pending_events and match_id not in self._tasks:
                immediate_pending = any(event.get("type") in IMMEDIATE_TRIGGER_EVENTS for event in pending_events)
                next_delay = 0.0 if immediate_pending else float(self.config.interval_seconds)
                next_trigger = "event" if immediate_pending else "batch"
                self._tasks[match_id] = asyncio.create_task(
                    self._delayed_generate(match_id, match, emit_callback, next_trigger, next_delay)
                )
        return True

    async def drain(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def shutdown_match(self, match_id: str) -> None:
        task = self._tasks.pop(match_id, None)
        if task and not task.done():
            task.cancel()
        self._pending_events.pop(match_id, None)
        self._active_generations.discard(match_id)
        self._last_emit_at.pop(match_id, None)
