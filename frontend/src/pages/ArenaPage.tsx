import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Brain, CheckCircle2, Clock, Crown, ListChecks, Network, Radio, ShieldAlert, ShieldCheck, Square, Trophy, XCircle } from 'lucide-react'
import { API_BASE, buildWebSocketUrl, createWebSocketTicket, fetchApi, fetchMatchEvents, readApiError } from '../api'
import AgentStreamView, { buildAgentBubbles, StreamViewMode } from '../components/AgentStreamView'
import TopologyMap from '../components/TopologyMap'
import { Button, ErrorBanner, Panel, StatusBadge, cx, tableClassName } from '../components/ui'
import { submissionReasonLabel } from '../lib/submissionReason'


// ============ Werewolf types ============
type WerewolfPlayerView = {
  player_id: number
  name?: string
  personality?: string
  role?: string
  role_label?: string
  team?: string
  alive: boolean
  is_sheriff?: boolean
  sheriff_candidate?: boolean
}

type WerewolfState = {
  day: number
  phase: string
  sheriff_id: number | null
  badge_destroyed: boolean
  winner: string | null
  players: WerewolfPlayerView[]
  board_label?: string | null
}

type WerewolfSpeechItem = {
  timestamp: string
  day?: number
  stage?: string
  player_id: number
  personality?: string
  text: string
  claim_role?: string
  suspects?: number[]
  vote_intent?: number
}

type WerewolfTurnItem = {
  timestamp: string
  day?: number
  phase?: string
  player_id: number
  personality?: string
  request?: string
  allowed_actions?: string[]
  action?: string
  target_player_id?: number
  text?: string
  reason?: string
  valid?: boolean
  error?: string
  kind?: string
  isPrivateNight?: boolean
}

type WerewolfNightResolutionItem = {
  timestamp: string
  day?: number
  death_count?: number
  dead_players: number[]
}

type WerewolfVoteRow = {
  voter_id: number
  target_player_id: number | null
  weight: number
}

type WerewolfVoteBatch = {
  timestamp: string
  day?: number
  stage: string
  scope: 'sheriff' | 'exile'
  votes: WerewolfVoteRow[]
  fallback?: boolean
}

type WerewolfDayEvent =
  | { kind: 'night_resolution'; day: number; timestamp: string; dead_players: number[] }
  | { kind: 'sheriff_assigned'; day: number; timestamp: string; player_id: number; reason: string }
  | { kind: 'sheriff_badge_destroyed'; day: number; timestamp: string; reason: string }
  | { kind: 'sheriff_badge_passed'; day: number; timestamp: string; from_player_id: number; to_player_id: number }
  | { kind: 'exile'; day: number; timestamp: string; exiled_player_id: number | null }
  | { kind: 'reveal'; day: number; timestamp: string; player_id: number }
  | { kind: 'white_wolf_king_reveal'; day: number; timestamp: string; player_id: number; target_player_id: number | null }
  | { kind: 'knight_duel'; day: number; timestamp: string; knight_id: number; target_player_id: number | null; hit_wolf: boolean; dead_player_id: number | null }
  | { kind: 'hunter_shot'; day: number; timestamp: string; hunter_id: number; target_player_id: number }

type WerewolfDayBucket = {
  day: number
  deaths_at_night: number[]
  events: WerewolfDayEvent[]
}

type LeaderboardEntry = {
  player_id: number
  name?: string
  display_name?: string
  score: number
  flags_captured: number
  flags_lost: number
  sla_ok: boolean
}

type RawLeaderboardEntry = LeaderboardEntry & {
  total_score?: number
  sla_up?: boolean
  model?: string
  display_name?: string
}

type MatchStatus = {
  match_id: string
  status: string
  elapsed_seconds: number
  remaining_seconds: number
  player_count: number
  leaderboard: Record<string, RawLeaderboardEntry> | RawLeaderboardEntry[]
  recent_events?: Array<{ type: string; data: Record<string, unknown>; timestamp: string }>
  agent_logs?: Record<string, unknown>
  submissions?: SubmissionItem[]
  mode?: string
  werewolf_board?: string | null
  werewolf_board_label?: string | null
  werewolf?: Partial<WerewolfState> | null
}

type MatchEvent = {
  type: string
  data?: Record<string, unknown>
  timestamp: string
}

type SubmissionItem = {
  attacker_id?: unknown
  victim_id?: unknown
  declared_target_player_id?: unknown
  flag?: unknown
  flag_slot?: unknown
  flag_index?: unknown
  success?: unknown
  reason?: unknown
  timestamp?: unknown
}

type WsMessage = {
  type?: string
  match_id?: string
  [key: string]: unknown
}

const toNumber = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

const splitAgentLog = (value: string): string[] => value.split(/\r?\n/)

const normalizePersistedAgentLogs = (raw: unknown): Record<number, string[]> => {
  if (!raw || typeof raw !== 'object') return {}

  const normalized: Record<number, string[]> = {}
  for (const [pidRaw, content] of Object.entries(raw)) {
    const pid = toNumber(pidRaw)
    if (pid == null || typeof content !== 'string') continue
    normalized[pid] = splitAgentLog(content)
  }
  return normalized
}

const eventTime = (timestamp: string): number => {
  const t = new Date(timestamp).getTime()
  return Number.isFinite(t) ? t : 0
}

const formatVictimLabel = (value: unknown): string => {
  const victimId = toNumber(value)
  return victimId == null ? '无' : `P${String(victimId)}`
}

const formatFlagIndexLabel = (value: unknown): string => {
  const flagIndex = toNumber(value)
  return flagIndex == null ? '-' : `#${String(flagIndex)}`
}

const formatFlagEventSuffix = (data: Record<string, unknown>): string => {
  const flagIndex = toNumber(data.flag_index)
  return flagIndex == null ? '' : ` 的 #${String(flagIndex)} Flag`
}

const formatArenaEvent = (event: MatchEvent): string | null => {
  const data = event.data ?? {}
  switch (event.type) {
    case 'STATUS':
      return `状态切换: ${String(data.status ?? 'unknown')}`
    case 'MATCH_STARTED':
      return `比赛开始: ${String(data.status ?? 'defense')}`
    case 'PHASE_CHANGE':
      return `阶段切换: ${String(data.phase ?? 'unknown')}`
    case 'AGENT_READY':
      return `Agent 就绪: P${String(data.player_id ?? '?')}`
    case 'FLAG_CAPTURED':
      return `夺旗: P${String(data.attacker_id ?? data.player_id ?? '?')} -> ${formatVictimLabel(data.victim_id)}${formatFlagEventSuffix(data)}`
    case 'FLAG_SUBMISSION':
      return `提交: P${String(data.attacker_id ?? '?')} -> ${formatVictimLabel(data.victim_id)}${formatFlagEventSuffix(data)} (${String(data.success ? '成功' : '失败')})`
    case 'FLAG_SUBMISSION_REJECTED':
      return `提交被拒: P${String(data.attacker_id ?? '?')} (${submissionReasonLabel(data.reason)})`
    case 'HEARTBEAT':
      return `心跳: 剩余 ${String(data.remaining_seconds ?? '?')} 秒`
    case 'NETWORK_OPENED':
      return `网络打通: ${String(data.arena_network ?? '')}`
    case 'FLAGS_REFRESHED':
      return `Flag 已刷新: ${String(data.player_count ?? '?')} 个靶机`
    case 'MATCH_FINISHED':
      return '比赛结束'
    case 'AGENT_STREAM':
    case 'AGENT_LOGS_COLLECTED':
      return null
    default:
      return `${event.type}: ${JSON.stringify(data)}`
  }
}


// ============ Werewolf helpers ============
const STAGE_LABELS: Record<string, string> = {
  sheriff_speech: '警上发言',
  sheriff_pk_speech: '警上 PK 发言',
  sheriff_vote: '警上投票',
  sheriff_pk_vote: '警上 PK 投票',
  day_speech: '白天发言',
  day_pk_speech: '白天 PK 发言',
  day_vote: '白天放逐投票',
  day_vote_pk: '白天 PK 投票',
  exile_pk_speech: '放逐 PK 发言',
}

const PRIVATE_NIGHT_KINDS = new Set([
  'werewolf_wolf_chat',
  'werewolf_night_kill',
  'werewolf_witch',
  'werewolf_seer',
  'werewolf_guard',
])

const stageLabel = (stage: string | undefined): string => (stage ? STAGE_LABELS[stage] ?? stage : '')

const isWerewolfEvent = (type: string): boolean => type.startsWith('WEREWOLF_')

const phaseLabel = (phase: string): string => {
  if (phase === 'defense') return '防御阶段'
  if (phase === 'attack') return '攻击阶段'
  if (phase === 'finished') return '已结束'
  if (phase === 'creating_containers') return '创建容器'
  if (phase === 'creating_werewolf_agents') return '创建狼人杀 Agent'
  if (phase === 'initializing_agents') return '初始化 Agent'
  if (phase === 'werewolf_training') return '赛前训练'
  if (phase === 'sheriff_election') return '警长竞选'
  if (phase === 'sheriff_speech') return '警上发言'
  if (phase === 'sheriff_pk_speech') return '警上 PK 发言'
  if (phase === 'sheriff_vote') return '警上投票'
  if (phase === 'sheriff_pk_vote') return '警上 PK 投票'
  if (phase === 'night') return '夜晚'
  if (phase === 'day') return '白天'
  if (phase === 'day_speech') return '白天发言'
  if (phase === 'day_pk_speech') return '白天 PK 发言'
  if (phase === 'day_vote') return '放逐投票'
  if (phase === 'day_vote_pk') return '放逐 PK 投票'
  if (phase === 'werewolf_sheriff') return '警长竞选'
  if (phase === 'werewolf_night') return '狼人杀夜晚'
  if (phase === 'werewolf_day') return '狼人杀白天'
  if (phase === 'error') return '异常'
  return phase || '初始化'
}

const phaseTone = (phase: string): 'neutral' | 'info' | 'success' | 'warning' | 'danger' => {
  if (phase === 'defense' || phase === 'day' || phase === 'day_speech' || phase === 'werewolf_day') return 'success'
  if (phase === 'attack' || phase === 'night' || phase === 'werewolf_night') return 'danger'
  if (phase === 'finished') return 'neutral'
  if (phase === 'error') return 'danger'
  if (phase.includes('vote') || phase.includes('sheriff')) return 'warning'
  return 'info'
}

const buildWerewolfState = (events: MatchEvent[], fallback?: Partial<WerewolfState> | null): WerewolfState => {
  const state: WerewolfState = {
    day: fallback?.day ?? 0,
    phase: fallback?.phase ?? 'setup',
    sheriff_id: fallback?.sheriff_id ?? null,
    badge_destroyed: fallback?.badge_destroyed ?? false,
    winner: fallback?.winner ?? null,
    players: [],
  }
  const playersMap = new Map<number, WerewolfPlayerView>()
  if (fallback && Array.isArray(fallback.players)) {
    for (const p of fallback.players) {
      if (p && typeof p.player_id === 'number') playersMap.set(p.player_id, { ...p })
    }
  }
  const ensure = (pid: number): WerewolfPlayerView => {
    let p = playersMap.get(pid)
    if (!p) {
      p = { player_id: pid, alive: true }
      playersMap.set(pid, p)
    }
    return p
  }
  for (const event of [...events].sort((a, b) => eventTime(a.timestamp) - eventTime(b.timestamp))) {
    if (!isWerewolfEvent(event.type)) continue
    const data = event.data ?? {}
    const day = toNumber(data.day)
    if (day != null) state.day = day
    if (event.type === 'WEREWOLF_PERSONALITIES_ASSIGNED') {
      const map = data.players ?? data.personalities
      if (map && typeof map === 'object') {
        for (const [pidRaw, persona] of Object.entries(map as Record<string, unknown>)) {
          const pid = toNumber(pidRaw)
          if (pid == null) continue
          if (typeof persona === 'string') {
            ensure(pid).personality = persona
          } else if (persona && typeof persona === 'object') {
            const row = persona as Record<string, unknown>
            const p = ensure(pid)
            if (typeof row.personality === 'string') p.personality = row.personality
          }
        }
      }
    } else if (event.type === 'WEREWOLF_SHERIFF_CANDIDATE_DECLARED') {
      const pid = toNumber(data.player_id)
      if (pid != null) ensure(pid).sheriff_candidate = true
    } else if (event.type === 'WEREWOLF_SHERIFF_WITHDRAWN') {
      const pid = toNumber(data.player_id)
      if (pid != null) ensure(pid).sheriff_candidate = false
    } else if (event.type === 'WEREWOLF_SHERIFF_ASSIGNED') {
      const pid = toNumber(data.player_id)
      state.sheriff_id = pid ?? null
      for (const p of playersMap.values()) p.is_sheriff = p.player_id === pid
    } else if (event.type === 'WEREWOLF_SHERIFF_BADGE_DESTROYED') {
      state.sheriff_id = null
      state.badge_destroyed = true
      for (const p of playersMap.values()) p.is_sheriff = false
    } else if (event.type === 'WEREWOLF_SHERIFF_BADGE_PASSED') {
      const to = toNumber(data.to_player_id)
      state.sheriff_id = to ?? null
      for (const p of playersMap.values()) p.is_sheriff = p.player_id === to
    } else if (event.type === 'WEREWOLF_NIGHT_ACTION') {
      const dead = Array.isArray(data.dead_players) ? data.dead_players.map(toNumber).filter((x): x is number => x != null) : []
      for (const pid of dead) ensure(pid).alive = false
    } else if (event.type === 'WEREWOLF_EXILE_RESULT') {
      const exiled = toNumber(data.exiled_player_id)
      if (exiled != null) ensure(exiled).alive = false
    } else if (event.type === 'WEREWOLF_REVEALED_SELF' || event.type === 'WEREWOLF_HUNTER_SHOT') {
      const pid = toNumber(data.player_id ?? data.target_player_id)
      if (pid != null) ensure(pid).alive = false
    } else if (event.type === 'WEREWOLF_WHITE_WOLF_KING_REVEALED') {
      const playerId = toNumber(data.player_id)
      const targetId = toNumber(data.target_player_id)
      if (playerId != null) ensure(playerId).alive = false
      if (targetId != null) ensure(targetId).alive = false
    } else if (event.type === 'WEREWOLF_KNIGHT_DUEL') {
      const dead = toNumber(data.dead_player_id)
      if (dead != null) ensure(dead).alive = false
    } else if (event.type === 'WEREWOLF_GAME_FINISHED') {
      const w = typeof data.winner === 'string' ? data.winner : null
      state.winner = w
    } else if (event.type === 'WEREWOLF_ROLES_REVEALED_TO_AUDIENCE') {
      const reveal = data.players ?? data.role_reveal
      if (reveal && typeof reveal === 'object') {
        for (const [pidRaw, info] of Object.entries(reveal as Record<string, unknown>)) {
          const pid = toNumber(pidRaw)
          if (pid == null || !info || typeof info !== 'object') continue
          const row = info as Record<string, unknown>
          const p = ensure(pid)
          if (typeof row.role === 'string') p.role = row.role
          if (typeof row.role_label === 'string') p.role_label = row.role_label
          if (typeof row.team === 'string') p.team = row.team
        }
      }
    }
  }
  // Auto-populate 12 default players if none observed yet
  if (playersMap.size === 0) {
    for (let i = 1; i <= 12; i++) playersMap.set(i, { player_id: i, alive: true })
  }
  state.players = Array.from(playersMap.values()).sort((a, b) => a.player_id - b.player_id)
  return state
}

const buildWerewolfObservations = (events: MatchEvent[]) => {
  const speeches: WerewolfSpeechItem[] = []
  const nightResolutions: WerewolfNightResolutionItem[] = []
  const voteBatches: WerewolfVoteBatch[] = []
  const dayBucketsMap = new Map<number, WerewolfDayBucket>()
  const turnsByPlayer = new Map<number, WerewolfTurnItem>()
  let currentTurn: WerewolfTurnItem | null = null
  let lastNightDeaths: number[] = []
  let lastNightDay: number | undefined

  const getBucket = (day: number): WerewolfDayBucket => {
    let bucket = dayBucketsMap.get(day)
    if (!bucket) {
      bucket = { day, deaths_at_night: [], events: [] }
      dayBucketsMap.set(day, bucket)
    }
    return bucket
  }

  for (const event of [...events].sort((a, b) => eventTime(a.timestamp) - eventTime(b.timestamp))) {
    const data = event.data ?? {}
    const day = toNumber(data.day) ?? 0
    if (event.type === 'WEREWOLF_PUBLIC_SPEECH') {
      const pid = toNumber(data.player_id ?? data.speaker_id)
      if (pid != null) {
        speeches.push({
          timestamp: event.timestamp,
          day: toNumber(data.day),
          stage: typeof data.stage === 'string' ? data.stage : undefined,
          player_id: pid,
          personality: typeof data.personality === 'string' ? data.personality : undefined,
          text: typeof data.text === 'string' ? data.text : '',
          claim_role: typeof data.claim_role === 'string' ? data.claim_role : undefined,
          suspects: Array.isArray(data.suspects) ? data.suspects.map(toNumber).filter((v): v is number => v != null) : undefined,
          vote_intent: toNumber(data.vote_intent),
        })
      }
    } else if (event.type === 'WEREWOLF_NIGHT_ACTION') {
      const dead = Array.isArray(data.dead_players) ? data.dead_players.map(toNumber).filter((v): v is number => v != null) : []
      nightResolutions.push({
        timestamp: event.timestamp,
        day: toNumber(data.day),
        death_count: toNumber(data.death_count) ?? dead.length,
        dead_players: dead,
      })
      lastNightDeaths = dead
      lastNightDay = toNumber(data.day)
      const bucket = getBucket(day)
      bucket.deaths_at_night = dead
      bucket.events.push({ kind: 'night_resolution', day, timestamp: event.timestamp, dead_players: dead })
    } else if (event.type === 'WEREWOLF_VOTE_BATCH' || event.type === 'WEREWOLF_SHERIFF_VOTE_BATCH') {
      const votes: WerewolfVoteRow[] = []
      if (Array.isArray(data.votes)) {
        for (const row of data.votes) {
          if (!row || typeof row !== 'object') continue
          const r = row as Record<string, unknown>
          const voter = toNumber(r.voter_id)
          if (voter == null) continue
          votes.push({
            voter_id: voter,
            target_player_id: toNumber(r.target_player_id) ?? null,
            weight: typeof r.weight === 'number' ? r.weight : 1.0,
          })
        }
      }
      voteBatches.push({
        timestamp: event.timestamp,
        day: toNumber(data.day),
        stage: typeof data.stage === 'string' ? data.stage : '',
        scope: event.type === 'WEREWOLF_SHERIFF_VOTE_BATCH' ? 'sheriff' : 'exile',
        votes,
        fallback: Boolean(data.fallback),
      })
    } else if (event.type === 'WEREWOLF_SHERIFF_ASSIGNED') {
      const pid = toNumber(data.player_id)
      if (pid != null) {
        getBucket(day).events.push({ kind: 'sheriff_assigned', day, timestamp: event.timestamp, player_id: pid, reason: typeof data.reason === 'string' ? data.reason : '' })
      }
    } else if (event.type === 'WEREWOLF_SHERIFF_BADGE_DESTROYED') {
      getBucket(day).events.push({ kind: 'sheriff_badge_destroyed', day, timestamp: event.timestamp, reason: typeof data.reason === 'string' ? data.reason : '' })
    } else if (event.type === 'WEREWOLF_SHERIFF_BADGE_PASSED') {
      const from = toNumber(data.from_player_id)
      const to = toNumber(data.to_player_id)
      if (from != null && to != null) {
        getBucket(day).events.push({ kind: 'sheriff_badge_passed', day, timestamp: event.timestamp, from_player_id: from, to_player_id: to })
      }
    } else if (event.type === 'WEREWOLF_EXILE_RESULT') {
      getBucket(day).events.push({ kind: 'exile', day, timestamp: event.timestamp, exiled_player_id: toNumber(data.exiled_player_id) ?? null })
    } else if (event.type === 'WEREWOLF_REVEALED_SELF') {
      const pid = toNumber(data.player_id)
      if (pid != null) getBucket(day).events.push({ kind: 'reveal', day, timestamp: event.timestamp, player_id: pid })
    } else if (event.type === 'WEREWOLF_WHITE_WOLF_KING_REVEALED') {
      const pid = toNumber(data.player_id)
      if (pid != null) {
        getBucket(day).events.push({
          kind: 'white_wolf_king_reveal',
          day,
          timestamp: event.timestamp,
          player_id: pid,
          target_player_id: toNumber(data.target_player_id) ?? null,
        })
      }
    } else if (event.type === 'WEREWOLF_KNIGHT_DUEL') {
      const knight = toNumber(data.knight_id)
      if (knight != null) {
        getBucket(day).events.push({
          kind: 'knight_duel',
          day,
          timestamp: event.timestamp,
          knight_id: knight,
          target_player_id: toNumber(data.target_player_id) ?? null,
          hit_wolf: Boolean(data.hit_wolf),
          dead_player_id: toNumber(data.dead_player_id) ?? null,
        })
      }
    } else if (event.type === 'WEREWOLF_HUNTER_SHOT') {
      const hunter = toNumber(data.hunter_id)
      const target = toNumber(data.target_player_id)
      if (hunter != null && target != null) {
        getBucket(day).events.push({ kind: 'hunter_shot', day, timestamp: event.timestamp, hunter_id: hunter, target_player_id: target })
      }
    } else if (event.type === 'WEREWOLF_PLAYER_TURN_STARTED') {
      const pid = toNumber(data.player_id)
      if (pid != null) {
        const kind = typeof data.kind === 'string' ? data.kind : undefined
        const isPrivateNight = PRIVATE_NIGHT_KINDS.has(kind ?? '')
        currentTurn = {
          timestamp: event.timestamp,
          day: toNumber(data.day),
          phase: typeof data.phase === 'string' ? data.phase : undefined,
          player_id: pid,
          personality: typeof data.personality === 'string' ? data.personality : undefined,
          request: isPrivateNight ? undefined : (typeof data.request === 'string' ? data.request : undefined),
          allowed_actions: isPrivateNight ? undefined : (Array.isArray(data.allowed_actions) ? data.allowed_actions.map(String) : undefined),
          kind,
          isPrivateNight,
        }
        turnsByPlayer.set(pid, currentTurn)
      }
    } else if (event.type === 'WEREWOLF_PLAYER_ACTION_RESOLVED') {
      const pid = toNumber(data.player_id)
      if (pid != null) {
        const kind = typeof data.kind === 'string' ? data.kind : turnsByPlayer.get(pid)?.kind
        const isPrivateNight = PRIVATE_NIGHT_KINDS.has(kind ?? '') || data.action === 'private_night_action'
        const merged: WerewolfTurnItem = {
          ...(turnsByPlayer.get(pid) ?? { timestamp: event.timestamp, player_id: pid }),
          timestamp: event.timestamp,
          day: toNumber(data.day),
          phase: typeof data.phase === 'string' ? data.phase : turnsByPlayer.get(pid)?.phase,
          personality: typeof data.personality === 'string' ? data.personality : turnsByPlayer.get(pid)?.personality,
          action: isPrivateNight ? '(夜间私有动作)' : (typeof data.action === 'string' ? data.action : undefined),
          target_player_id: isPrivateNight ? undefined : toNumber(data.target_player_id),
          text: isPrivateNight ? undefined : (typeof data.text === 'string' ? data.text : undefined),
          reason: isPrivateNight ? undefined : (typeof data.reason === 'string' ? data.reason : undefined),
          valid: typeof data.valid === 'boolean' ? data.valid : undefined,
          error: typeof data.error === 'string' ? data.error : undefined,
          kind,
          isPrivateNight,
        }
        turnsByPlayer.set(pid, merged)
        currentTurn = merged
      }
    }
  }
  const dayBuckets = Array.from(dayBucketsMap.values()).sort((a, b) => b.day - a.day)
  // Recent turns history (most recent first), excluding private night actions noise.
  const recentTurns = Array.from(turnsByPlayer.values())
    .filter((t) => t.action !== undefined)
    .sort((a, b) => eventTime(b.timestamp) - eventTime(a.timestamp))
    .slice(0, 12)
  return {
    speeches: speeches.reverse(),
    nightResolutions: nightResolutions.reverse(),
    voteBatches: voteBatches.reverse(),
    dayBuckets,
    currentTurn,
    turnsByPlayer,
    recentTurns,
    lastNightDeaths,
    lastNightDay,
  }
}

type WerewolfLeaderboardEntry = LeaderboardEntry & {
  werewolf_role_label?: string
  werewolf_team?: string
  judge_reasoning?: string
}

const JudgeReasoning: React.FC<{ text: string }> = ({ text }) => {
  const [open, setOpen] = useState(false)
  if (!text) return <span className="text-slate-500">-</span>
  if (text.length <= 80) return <span>{text}</span>
  return (
    <span>
      {open ? text : text.slice(0, 80) + '…'}
      <button
        type="button"
        className="ml-2 cursor-pointer rounded-sm text-xs text-cyan-300 underline-offset-2 transition duration-200 hover:text-cyan-100 hover:underline focus:outline-none focus:ring-2 focus:ring-cyan-400/30"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        {open ? '收起' : '展开'}
      </button>
    </span>
  )
}

const deriveArenaFeed = (events: MatchEvent[], persistedLogs?: unknown) => {
  const readyPlayers = new Set<number>()
  const agentLogs: Record<number, string[]> = {}
  const timeline: string[] = []

  for (const event of [...events].sort((a, b) => eventTime(a.timestamp) - eventTime(b.timestamp))) {
    const data = event.data ?? {}
    const formatted = formatArenaEvent(event)
    if (formatted) {
      timeline.push(`[${new Date(event.timestamp).toLocaleTimeString()}] ${formatted}`)
    }

    if (event.type === 'AGENT_READY') {
      const pid = toNumber(data.player_id)
      if (pid != null) readyPlayers.add(pid)
      continue
    }

    if (event.type === 'AGENT_STREAM') {
      const pid = toNumber(data.player_id)
      const content = data.content
      if (pid != null && typeof content === 'string') {
        const lines = agentLogs[pid] ?? []
        agentLogs[pid] = [...lines, content]
      }
      continue
    }

    if (event.type === 'AGENT_LOGS_COLLECTED') {
      const normalized = normalizePersistedAgentLogs(data.logs)
      for (const [pidRaw, lines] of Object.entries(normalized)) {
        agentLogs[Number(pidRaw)] = lines
      }
    }
  }

  if (Object.keys(agentLogs).length === 0) {
    const normalized = normalizePersistedAgentLogs(persistedLogs)
    for (const [pidRaw, lines] of Object.entries(normalized)) {
      agentLogs[Number(pidRaw)] = lines
    }
  }

  return {
    timeline: timeline.reverse(),
    readyPlayers,
    agentLogs,
  }
}

const normalizeLeaderboardRow = (row: RawLeaderboardEntry): LeaderboardEntry => ({
  player_id: row.player_id,
  name: row.name,
  display_name: row.display_name,
  score: row.score ?? row.total_score ?? 0,
  flags_captured: row.flags_captured ?? 0,
  flags_lost: row.flags_lost ?? 0,
  sla_ok: row.sla_ok ?? row.sla_up ?? false,
})

const computePlayerLabel = (row: LeaderboardEntry): string => {
  if (row.display_name) return row.display_name
  if (row.name) return row.name
  return `Player ${row.player_id}`
}

const toLeaderboardArray = (raw: Record<string, RawLeaderboardEntry> | RawLeaderboardEntry[]): LeaderboardEntry[] => {
  const values = Array.isArray(raw) ? raw : Object.values(raw)
  return values.map(normalizeLeaderboardRow).sort((a, b) => b.score - a.score)
}

const ArenaPage: React.FC = () => {
  const { matchId } = useParams<{ matchId: string }>()
  const navigate = useNavigate()
  const [matchInfo, setMatchInfo] = useState<MatchStatus | null>(null)
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([])
  const [events, setEvents] = useState<string[]>([])
  const [agentLogs, setAgentLogs] = useState<Record<number, string[]>>({})
  const [submissions, setSubmissions] = useState<SubmissionItem[]>([])
  const [selectedPlayerLog, setSelectedPlayerLog] = useState<number | null>(null)
  const [streamViewMode, setStreamViewMode] = useState<StreamViewMode>('cleaned')
  const [matchEnded, setMatchEnded] = useState(false)
  const [readyPlayers, setReadyPlayers] = useState<Set<number>>(new Set())
  const [wsConnected, setWsConnected] = useState(false)
  const [arenaError, setArenaError] = useState<string | null>(null)
  const [endingMatch, setEndingMatch] = useState(false)
  const socketRef = useRef<WebSocket | null>(null)
  const reconnectTimerRef = useRef<number | null>(null)
  const matchEndedRef = useRef(false)
  const matchNotFoundRef = useRef(false)
  const streamDebounceRef = useRef<number | null>(null)

  useEffect(() => {
    matchEndedRef.current = matchEnded || matchInfo?.status === 'finished'
  }, [matchEnded, matchInfo?.status])

  const fetchStatus = useCallback(async () => {
    if (!matchId || matchNotFoundRef.current) return
    try {
      const statusResponse = await fetchApi(`${API_BASE}/api/matches/${matchId}`)
      if (statusResponse.status === 404) {
        matchNotFoundRef.current = true
        throw new Error('比赛不存在或已被删除')
      }
      if (!statusResponse.ok) throw new Error(await readApiError(statusResponse))
      const statusData = await statusResponse.json() as MatchStatus

      setMatchInfo(statusData)
      if (statusData.leaderboard) setLeaderboard(toLeaderboardArray(statusData.leaderboard))
      if (statusData.status === 'finished') setMatchEnded(true)

      const submissionsResponse = await fetchApi(`${API_BASE}/api/matches/${matchId}/submissions`)
      if (submissionsResponse.status === 404) {
        matchNotFoundRef.current = true
        throw new Error('比赛提交记录不存在或比赛已被删除')
      }
      if (!submissionsResponse.ok) throw new Error(await readApiError(submissionsResponse))
      const submissionsData = await submissionsResponse.json() as { submissions?: SubmissionItem[] }

      setSubmissions(Array.isArray(submissionsData.submissions) ? submissionsData.submissions : [])
      setArenaError(null)
    } catch (error) {
      setArenaError(error instanceof Error ? error.message : '加载比赛状态失败')
    }
  }, [matchId])

  const fetchFeed = useCallback(async () => {
    if (!matchId || matchNotFoundRef.current) return
    try {
      const raw = await fetchMatchEvents<MatchEvent>(matchId)
      const { timeline, readyPlayers: nextReadyPlayers, agentLogs: nextAgentLogs } = deriveArenaFeed(raw, matchInfo?.agent_logs)
      setEvents(timeline)
      setReadyPlayers(nextReadyPlayers)
      setAgentLogs(nextAgentLogs)
      setSelectedPlayerLog((prevSelected) => {
        if (prevSelected != null && nextAgentLogs[prevSelected]) return prevSelected
        const firstPlayer = Object.keys(nextAgentLogs)
          .map((pid) => Number(pid))
          .filter((pid) => Number.isFinite(pid))
          .sort((a, b) => a - b)[0]
        return firstPlayer ?? null
      })
      setArenaError(null)
    } catch (error) {
      setArenaError(error instanceof Error ? error.message : '加载事件流失败')
    }
  }, [matchId, matchInfo?.agent_logs])

  const fetchFeedDebounced = useCallback(() => {
    if (streamDebounceRef.current != null) {
      window.clearTimeout(streamDebounceRef.current)
    }
    streamDebounceRef.current = window.setTimeout(() => {
      streamDebounceRef.current = null
      fetchFeed()
    }, 300)
  }, [fetchFeed])

  useEffect(() => {
    fetchStatus()
    fetchFeed()
    const interval = setInterval(() => {
      if (matchNotFoundRef.current) return
      fetchStatus()
      fetchFeed()
    }, 5000)
    return () => clearInterval(interval)
  }, [fetchFeed, fetchStatus])

  useEffect(() => {
    let cancelled = false
    let reconnectAttempt = 0

    const clearReconnectTimer = () => {
      if (reconnectTimerRef.current != null) {
        window.clearTimeout(reconnectTimerRef.current)
        reconnectTimerRef.current = null
      }
    }

    const scheduleReconnect = () => {
      if (cancelled || matchEndedRef.current || matchNotFoundRef.current) return
      clearReconnectTimer()
      const delay = Math.min(5000, 1000 * 2 ** reconnectAttempt)
      reconnectAttempt += 1
      reconnectTimerRef.current = window.setTimeout(() => {
        reconnectTimerRef.current = null
        connect()
      }, delay)
    }

    const connect = async () => {
      if (cancelled) return

      let ticket = ''
      try {
        ticket = await createWebSocketTicket()
      } catch (error) {
        setArenaError(error instanceof Error ? error.message : '获取 WebSocket 票据失败')
        scheduleReconnect()
        return
      }
      if (cancelled) return

      const socket = new WebSocket(buildWebSocketUrl('/ws/', { ticket }))
      socketRef.current = socket

      socket.onopen = () => {
        if (cancelled) return
        reconnectAttempt = 0
        setWsConnected(true)
        setArenaError(null)
        clearReconnectTimer()
        if (matchId) {
          socket.send(JSON.stringify({ type: 'subscribe', match_id: matchId }))
        }
        fetchStatus()
        fetchFeed()
      }

      socket.onmessage = (ev: MessageEvent) => {
        try {
          const data = JSON.parse(ev.data as string) as WsMessage
          const eventType = data?.type
          if (!eventType) return
          if (typeof data.match_id === 'string' && matchId && data.match_id !== matchId) return
          switch (eventType) {
            case 'subscribed': {
              setWsConnected(true)
              break
            }
            case 'STATUS': {
              fetchStatus()
              fetchFeed()
              break
            }
            case 'AGENT_READY': {
              fetchFeed()
              break
            }
            case 'FLAG_CAPTURED': {
              fetchStatus()
              fetchFeed()
              break
            }
            case 'FLAG_SUBMISSION': {
              fetchStatus()
              fetchFeed()
              break
            }
            case 'FLAG_SUBMISSION_REJECTED': {
              fetchStatus()
              fetchFeed()
              break
            }
            case 'PHASE_CHANGE': {
              fetchStatus()
              fetchFeed()
              break
            }
            case 'AGENT_STREAM': {
              fetchFeedDebounced()
              break
            }
            case 'AGENT_LOGS_COLLECTED': {
              fetchFeedDebounced()
              break
            }
            case 'MATCH_FINISHED': {
              setMatchEnded(true)
              matchEndedRef.current = true
              setWsConnected(true)
              clearReconnectTimer()
              setMatchInfo((prev) => {
                if (!prev) return prev
                return {
                  ...prev,
                  status: 'finished',
                  remaining_seconds: 0,
                }
              })
              fetchStatus()
              fetchFeed()
              try {
                socket.close()
              } catch (_closeError) {
                void _closeError
              }
              break
            }
          }
        } catch (_parseError) {
          void _parseError
        }
      }

      socket.onerror = () => {
        setWsConnected(false)
        setArenaError('WebSocket 连接异常，正在尝试重连')
      }

      socket.onclose = () => {
        const shouldReconnect = !cancelled && !matchEndedRef.current
        setWsConnected(!shouldReconnect)
        if (socketRef.current === socket) {
          socketRef.current = null
        }
        if (shouldReconnect) scheduleReconnect()
      }
    }

    connect()

    return () => {
      cancelled = true
      setWsConnected(false)
      clearReconnectTimer()
      const socket = socketRef.current
      socketRef.current = null
      if (socket) {
        try { socket.close() } catch (_e) { void _e }
      }
    }
  }, [fetchFeed, fetchStatus, matchId])

  const endMatch = async () => {
    if (!matchId) return
    setEndingMatch(true)
    setArenaError(null)
    try {
      const response = await fetchApi(`${API_BASE}/api/matches/${matchId}/end`, { method: 'POST' })
      if (!response.ok) throw new Error(await readApiError(response))
      await fetchStatus()
      await fetchFeed()
    } catch (error) {
      setArenaError(error instanceof Error ? error.message : '结束比赛失败')
    } finally {
      setEndingMatch(false)
    }
  }

  const closeMatchEndedModal = () => {
    if (!matchId) return
    navigate(`/history?matchId=${encodeURIComponent(matchId)}`)
  }

  const phase = matchInfo?.status ?? 'initializing'
  const remaining = matchInfo?.remaining_seconds ?? 0
  const mins = Math.floor(remaining / 60).toString().padStart(2, '0')
  const secs = Math.floor(remaining % 60).toString().padStart(2, '0')
  const totalPlayers = matchInfo?.player_count ?? 0
  const readyCount = readyPlayers.size
  const isInitPhase = phase === 'creating_containers' || phase === 'initializing_agents'
  const isWerewolf = matchInfo?.mode === 'werewolf'
  const werewolfState = buildWerewolfState(matchInfo?.recent_events as MatchEvent[] | undefined ?? [], matchInfo?.werewolf)
  const werewolfBoardLabel = matchInfo?.werewolf?.board_label ?? matchInfo?.werewolf_board_label ?? '12 人预女猎守'
  const werewolfObs = buildWerewolfObservations(matchInfo?.recent_events as MatchEvent[] | undefined ?? [])
  // Map: player_id → most recent speech text (for round-table hover tooltips)
  const latestSpeechByPlayer: Record<number, WerewolfSpeechItem> = {}
  for (const s of werewolfObs.speeches) {
    // speeches is already reverse-chronological (newest first), so keep the first hit per player
    if (!(s.player_id in latestSpeechByPlayer)) latestSpeechByPlayer[s.player_id] = s
  }
  const currentTurn = werewolfObs.currentTurn
  const wwLeaderboard = leaderboard as WerewolfLeaderboardEntry[]
  const playerLabelById = new Map(leaderboard.map((row) => [row.player_id, computePlayerLabel(row)]))
  const recentSubmissions = submissions
    .slice()
    .sort((a, b) => {
      const aTime = typeof a.timestamp === 'string' ? eventTime(a.timestamp) : 0
      const bTime = typeof b.timestamp === 'string' ? eventTime(b.timestamp) : 0
      return bTime - aTime
    })
  const recentSubmissionsViewportClass = 'h-[15rem] overflow-x-auto overflow-y-auto overscroll-contain'
  const selectedPlayerBubbles = selectedPlayerLog !== null && agentLogs[selectedPlayerLog]
    ? buildAgentBubbles(agentLogs[selectedPlayerLog], streamViewMode)
    : []
  const onlinePlayers = leaderboard.filter((row) => row.sla_ok).length

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 p-4">
      <div className="max-w-7xl mx-auto space-y-6">
        <header className="flex flex-col justify-between gap-3 rounded-lg border border-slate-700/70 bg-slate-950/40 p-4 lg:flex-row lg:items-center">
          <div className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-wider text-cyan-300">Live Arena</div>
            <h1 className="mt-1 truncate font-mono text-xl font-semibold text-slate-100 sm:text-2xl">{matchId}</h1>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge tone={wsConnected ? 'success' : 'warning'}>
              <Radio className="h-3.5 w-3.5" />
              WS {wsConnected ? '已连接' : '重连中'}
            </StatusBadge>
            <StatusBadge tone={phaseTone(phase)}>{phaseLabel(phase)}</StatusBadge>
            <StatusBadge tone="neutral">{onlinePlayers} / {totalPlayers || '?'} SLA 在线</StatusBadge>
            <Button variant="danger" icon={<Square className="h-4 w-4" />} disabled={endingMatch || matchEnded} onClick={endMatch}>
              {endingMatch ? '结束中' : '结束比赛'}
            </Button>
          </div>
        </header>

        <ErrorBanner message={arenaError} />

        {isInitPhase && (
          <div className="bg-slate-800 border border-slate-700 rounded-md p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-semibold text-slate-300">
                {phase === 'creating_containers' ? '正在创建容器…' : '正在初始化 Agent…'}
              </span>
              <span className="text-sm font-mono text-slate-400">
                {readyCount} / {totalPlayers || '?'} 就绪
              </span>
            </div>
            <div className="w-full bg-slate-700 rounded-full h-2 overflow-hidden">
              <div
                className="bg-cyan-500 h-2 rounded-full transition-all duration-500"
                style={{ width: totalPlayers > 0 ? `${(readyCount / totalPlayers) * 100}%` : '0%' }}
              />
            </div>
            {readyPlayers.size > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {[...readyPlayers].map((pid) => (
                  <span key={pid} className="px-2 py-0.5 rounded-full bg-emerald-800 text-emerald-300 text-xs font-mono">
                    P{pid} ✓
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {isWerewolf && (
          <div className="space-y-4">
            <div className="bg-slate-800 border border-slate-700 rounded-md p-4 flex flex-wrap items-center gap-3">
              <span className="px-3 py-1 rounded-md bg-amber-700/40 text-amber-200 text-sm">{phaseLabel(phase)}</span>
              <span className="px-3 py-1 rounded-md bg-slate-700/60 text-slate-200 text-sm">Day {werewolfState.day}</span>
              <span className="px-3 py-1 rounded-md bg-slate-700/60 text-slate-200 text-sm">
                警长 {werewolfState.sheriff_id ? `P${werewolfState.sheriff_id}` : werewolfState.badge_destroyed ? '已撕毁' : '未产生'}
              </span>
              {werewolfState.winner && (
                <span className={`px-3 py-1 rounded-md text-sm ${werewolfState.winner === 'werewolf' ? 'bg-rose-700/60 text-rose-100' : 'bg-emerald-700/60 text-emerald-100'}`}>
                  {werewolfState.winner === 'werewolf' ? '狼人胜利' : '好人胜利'}
                </span>
              )}
              {werewolfObs.lastNightDeaths.length > 0 && (
                <span className="px-3 py-1 rounded-md bg-rose-900/40 text-rose-200 text-sm">
                  昨夜出局 {werewolfObs.lastNightDeaths.map((p) => `P${p}`).join('、')}
                </span>
              )}
              <span className="ml-auto font-mono text-xs text-slate-400">{mins}:{secs}</span>
              <Button size="sm" variant="danger" icon={<Square className="h-3.5 w-3.5" />} disabled={endingMatch || matchEnded} onClick={endMatch}>{endingMatch ? '结束中' : '结束'}</Button>
            </div>

            <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(360px,1fr)] gap-4 items-start">
              <div className="space-y-4">
                <div className="bg-slate-800 border border-slate-700 rounded-md p-4">
                  <h3 className="text-sm font-semibold text-slate-400 mb-3">狼人杀桌面</h3>
                  <div className="hidden md:block">
                    <div className="relative mx-auto aspect-[5/4] w-full max-w-[640px]">
                      <div className="pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 rounded-full border border-cyan-500/30 bg-slate-950/70 px-4 py-3 text-center text-xs text-slate-300">
                        <div className="font-semibold text-cyan-200">{werewolfBoardLabel}</div>
                        <div className="mt-1 text-slate-500">座位号 = 玩家 ID</div>
                      </div>
                      {werewolfState.players.map((player, idx) => {
                        const row = wwLeaderboard.find((r) => r.player_id === player.player_id)
                        const alive = player.alive !== false
                        const n = werewolfState.players.length || 12
                        const angle = (idx / n) * Math.PI * 2 - Math.PI / 2
                        const cx = 50 + 42 * Math.cos(angle)
                        const cy = 50 + 42 * Math.sin(angle)
                        const justKilled = !alive && werewolfObs.lastNightDay === werewolfState.day && werewolfObs.lastNightDeaths.includes(player.player_id)
                        const isCurrentTurn = alive && currentTurn != null && currentTurn.player_id === player.player_id
                        const classes = [
                          'absolute w-[120px] -translate-x-1/2 -translate-y-1/2 rounded-md border p-2 text-xs transition-all',
                          alive ? 'border-slate-700 bg-slate-950/80' : 'border-rose-900/60 bg-rose-950/30 opacity-70',
                          player.is_sheriff ? 'border-amber-400/80 shadow-[0_0_16px_rgba(245,158,11,0.45)]' : '',
                          justKilled ? 'animate-pulse border-rose-400 shadow-[0_0_16px_rgba(244,63,94,0.6)]' : '',
                          // Current speaker / current actor: cyan breathing glow on top of any other state.
                          isCurrentTurn ? 'border-cyan-300 shadow-[0_0_24px_rgba(34,211,238,0.7)] animate-[pulse_2s_ease-in-out_infinite] z-10 scale-110' : '',
                        ].filter(Boolean).join(' ')
                        return (
                          <div
                            key={player.player_id}
                            className={classes + ' group'}
                            style={{ left: `${cx}%`, top: `${cy}%` }}
                          >
                            {/* Hover tooltip showing latest speech */}
                            {latestSpeechByPlayer[player.player_id] && (
                              <div className="pointer-events-none invisible absolute left-1/2 top-full z-30 mt-2 w-64 -translate-x-1/2 rounded-md border border-cyan-500/40 bg-slate-950/95 p-2 text-[11px] leading-snug text-slate-200 shadow-lg group-hover:visible">
                                <div className="mb-1 flex flex-wrap items-center gap-1 text-[10px] text-cyan-200">
                                  <span>Day {latestSpeechByPlayer[player.player_id].day ?? '?'}</span>
                                  {latestSpeechByPlayer[player.player_id].stage && (
                                    <span className="text-amber-200">{stageLabel(latestSpeechByPlayer[player.player_id].stage!) || latestSpeechByPlayer[player.player_id].stage}</span>
                                  )}
                                </div>
                                <div className="whitespace-pre-wrap text-slate-100">
                                  {latestSpeechByPlayer[player.player_id].text || '(pass)'}
                                </div>
                                {latestSpeechByPlayer[player.player_id].claim_role && (
                                  <div className="mt-1 text-slate-400">起跳：{latestSpeechByPlayer[player.player_id].claim_role}</div>
                                )}
                              </div>
                            )}
                            <div className="flex items-center justify-between gap-1">
                              <div className="flex items-center gap-1 font-semibold text-slate-100">
                                <span>P{player.player_id}</span>
                                {player.is_sheriff && <Crown className="h-3.5 w-3.5 text-amber-300" aria-label="警长" />}
                              </div>
                              {!alive && <span className="text-[10px] text-rose-300">出局</span>}
                            </div>
                            <div className="mt-1 truncate text-[11px] text-slate-400">{player.name || row?.display_name || row?.name || `Player ${player.player_id}`}</div>
                            <div className="mt-1 flex flex-wrap gap-1">
                              {player.sheriff_candidate ? <span className="rounded-full bg-cyan-700/40 text-cyan-100 px-1.5 text-[10px]">上警</span> : null}
                              {justKilled ? <span className="rounded-full bg-rose-700/40 text-rose-100 px-1.5 text-[10px]">昨夜出局</span> : null}
                              {matchEnded && (player.role_label || row?.werewolf_role_label) ? (
                                <span className={`rounded-full px-1.5 text-[10px] ${(player.team || row?.werewolf_team) === 'werewolf' ? 'bg-rose-700/40 text-rose-100' : 'bg-emerald-700/40 text-emerald-100'}`}>
                                  {player.role_label || row?.werewolf_role_label}
                                </span>
                              ) : null}
                              {matchEnded && row?.score != null ? <span className="rounded-full bg-slate-700/60 text-slate-200 px-1.5 text-[10px]">{row.score}/10</span> : null}
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  </div>
                  <div className="grid grid-cols-2 gap-3 md:hidden">
                    {werewolfState.players.map((player) => {
                      const row = wwLeaderboard.find((r) => r.player_id === player.player_id)
                      const alive = player.alive !== false
                      const justKilled = !alive && werewolfObs.lastNightDay === werewolfState.day && werewolfObs.lastNightDeaths.includes(player.player_id)
                      const isCurrentTurn = alive && currentTurn != null && currentTurn.player_id === player.player_id
                      const classes = [
                        'rounded-md border p-3 transition-all',
                        alive ? 'border-slate-700 bg-slate-950/60' : 'border-rose-900/60 bg-rose-950/20',
                        player.is_sheriff ? 'border-amber-400/80' : '',
                        justKilled ? 'animate-pulse border-rose-400' : '',
                        isCurrentTurn ? 'border-cyan-300 shadow-[0_0_18px_rgba(34,211,238,0.6)] ring-2 ring-cyan-400/40 animate-pulse' : '',
                      ].filter(Boolean).join(' ')
                      const narrowSpeech = latestSpeechByPlayer[player.player_id]
                      return (
                        <div key={player.player_id} className={classes + ' group relative'}>
                          {narrowSpeech && (
                            <div className="pointer-events-none invisible absolute left-1/2 top-full z-30 mt-2 w-60 -translate-x-1/2 rounded-md border border-cyan-500/40 bg-slate-950/95 p-2 text-[11px] leading-snug text-slate-200 shadow-lg group-hover:visible">
                              <div className="mb-1 flex flex-wrap items-center gap-1 text-[10px] text-cyan-200">
                                <span>Day {narrowSpeech.day ?? '?'}</span>
                                {narrowSpeech.stage && <span className="text-amber-200">{stageLabel(narrowSpeech.stage) || narrowSpeech.stage}</span>}
                              </div>
                              <div className="whitespace-pre-wrap text-slate-100">{narrowSpeech.text || '(pass)'}</div>
                            </div>
                          )}
                          <div className="flex items-center justify-between gap-2">
                            <div className="flex items-center gap-1.5 font-semibold text-slate-100">
                              <span>P{player.player_id}</span>
                              {player.is_sheriff && <Crown className="h-3.5 w-3.5 text-amber-300" aria-label="警长" />}
                            </div>
                            <span className={`text-xs ${alive ? 'text-emerald-300' : 'text-rose-300'}`}>{alive ? '存活' : '出局'}</span>
                          </div>
                          <div className="mt-2 truncate text-sm text-slate-400">{player.name || row?.display_name || row?.name || `Player ${player.player_id}`}</div>
                          <div className="mt-2 flex flex-wrap gap-1">
                            {player.sheriff_candidate ? <span className="rounded-full bg-cyan-700/40 text-cyan-100 px-1.5 text-[10px]">上警</span> : null}
                            {justKilled ? <span className="rounded-full bg-rose-700/40 text-rose-100 px-1.5 text-[10px]">昨夜出局</span> : null}
                            {matchEnded && (player.role_label || row?.werewolf_role_label) ? (
                              <span className={`rounded-full px-1.5 text-[10px] ${(player.team || row?.werewolf_team) === 'werewolf' ? 'bg-rose-700/40 text-rose-100' : 'bg-emerald-700/40 text-emerald-100'}`}>
                                {player.role_label || row?.werewolf_role_label}
                              </span>
                            ) : null}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>

                <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                  <div className="bg-slate-800 border border-slate-700 rounded-md p-4">
                    <h3 className="text-sm font-semibold text-slate-400 mb-3">公开发言（含警上发言）</h3>
                    <div className="max-h-[360px] overflow-auto rounded-md border border-slate-800 bg-slate-950/70 p-3">
                      {werewolfObs.speeches.length === 0 ? <div className="text-sm text-slate-500">等待白天发言...</div> : null}
                      {werewolfObs.speeches.map((s, i) => (
                        <div key={i} className="border-b border-slate-800/80 py-3 first:pt-0 last:border-0 last:pb-0">
                          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                            <span className="rounded-full bg-cyan-700/40 text-cyan-100 px-2 py-0.5">Day {s.day ?? '?'}</span>
                            <span className="rounded-full bg-slate-700/60 text-slate-200 px-2 py-0.5">P{s.player_id}</span>
                            {s.personality && <span className="rounded-full bg-slate-700/60 text-slate-200 px-2 py-0.5">{s.personality}</span>}
                            {s.stage && <span className="rounded-full bg-amber-700/40 text-amber-100 px-2 py-0.5">{stageLabel(s.stage)}</span>}
                          </div>
                          <p className="text-sm leading-6 text-slate-200">{s.text || '(pass)'}</p>
                          {(s.claim_role || (s.suspects && s.suspects.length) || s.vote_intent != null) ? (
                            <div className="mt-2 grid grid-cols-1 sm:grid-cols-3 gap-1 text-xs text-slate-400">
                              <div>{s.claim_role ? <><span className="text-slate-500">起跳：</span>{s.claim_role}</> : null}</div>
                              <div>{s.suspects && s.suspects.length ? <><span className="text-slate-500">怀疑：</span>{s.suspects.map((p) => `P${p}`).join(', ')}</> : null}</div>
                              <div>{s.vote_intent != null ? <><span className="text-slate-500">票意：</span>P{s.vote_intent}</> : null}</div>
                            </div>
                          ) : null}
                        </div>
                      ))}
                    </div>
                  </div>
                  <div className="bg-slate-800 border border-slate-700 rounded-md p-4">
                    <h3 className="text-sm font-semibold text-slate-400 mb-3">夜间结算</h3>
                    <div className="max-h-[360px] overflow-auto rounded-md border border-slate-800 bg-slate-950/70 p-3">
                      {werewolfObs.nightResolutions.length === 0 ? <div className="text-sm text-slate-500">等待夜间结算...</div> : null}
                      {werewolfObs.nightResolutions.map((n, i) => (
                        <div key={i} className="border-b border-slate-800/80 py-3 first:pt-0 last:border-0 last:pb-0">
                          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                            <span className="rounded-full bg-rose-700/40 text-rose-100 px-2 py-0.5">Night {n.day ?? '?'}</span>
                            <span className="rounded-full bg-slate-700/60 text-slate-200 px-2 py-0.5">{n.death_count ?? n.dead_players.length} 人出局</span>
                          </div>
                          <div className="text-sm text-slate-300">
                            {n.dead_players.length > 0
                              ? <span>出局：{n.dead_players.map((p) => `P${p}`).join('、')}</span>
                              : <span className="text-slate-500">平安夜</span>}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="bg-slate-800 border border-slate-700 rounded-md p-4">
                  <h3 className="text-sm font-semibold text-slate-400 mb-3">投票池</h3>
                  <div className="max-h-[420px] space-y-3 overflow-auto rounded-md border border-slate-800 bg-slate-950/70 p-3">
                    {werewolfObs.voteBatches.length === 0 ? <div className="text-sm text-slate-500">等待投票...</div> : null}
                    {werewolfObs.voteBatches.map((b, i) => {
                      const tally = new Map<number | 'pass', number>()
                      for (const v of b.votes) {
                        const k: number | 'pass' = v.target_player_id ?? 'pass'
                        tally.set(k, (tally.get(k) ?? 0) + v.weight)
                      }
                      const sorted = Array.from(tally.entries()).sort((x, y) => y[1] - x[1])
                      return (
                        <div key={i} className="rounded-md border border-slate-800 bg-slate-900/70 p-3">
                          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
                            <span className="rounded-full bg-cyan-700/40 text-cyan-100 px-2 py-0.5">Day {b.day ?? '?'}</span>
                            <span className={`rounded-full px-2 py-0.5 ${b.scope === 'sheriff' ? 'bg-amber-700/40 text-amber-100' : 'bg-rose-700/40 text-rose-100'}`}>
                              {b.scope === 'sheriff' ? '警长投票' : '放逐投票'}
                            </span>
                            {b.stage ? <span className="rounded-full bg-slate-700/60 text-slate-200 px-2 py-0.5">{stageLabel(b.stage) || b.stage}</span> : null}
                            {b.fallback ? <span className="rounded-full bg-amber-700/40 text-amber-100 px-2 py-0.5">裁判 fallback</span> : null}
                          </div>
                          <div className="mb-3 flex flex-wrap gap-2 text-xs text-slate-300">
                            {sorted.map(([k, c]) => (
                              <span key={String(k)} className={`rounded-full border px-2 py-0.5 ${k === 'pass' ? 'border-slate-700 text-slate-400' : 'border-cyan-500/40 bg-cyan-950/40 text-cyan-100'}`}>
                                {k === 'pass' ? '弃票' : `P${k}`} · {c.toFixed(1)}
                              </span>
                            ))}
                          </div>
                          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1 text-xs text-slate-300">
                            {b.votes.map((v, j) => (
                              <div key={j} className="flex items-center gap-2">
                                <span className="font-mono">P{v.voter_id}</span>
                                <span className="text-slate-500">→</span>
                                <span className="font-mono">{v.target_player_id == null ? '弃票' : `P${v.target_player_id}`}</span>
                                {v.weight !== 1 ? <span className="text-amber-300">×{v.weight}</span> : null}
                              </div>
                            ))}
                          </div>
                        </div>
                      )
                    })}
                  </div>
                </div>

                <div className="bg-slate-800 border border-slate-700 rounded-md p-4">
                  <h3 className="text-sm font-semibold text-slate-400 mb-3">时间轴</h3>
                  <div className="max-h-[480px] space-y-3 overflow-auto rounded-md border border-slate-800 bg-slate-950/70 p-3">
                    {werewolfObs.dayBuckets.length === 0 ? <div className="text-sm text-slate-500">等待比赛进程...</div> : null}
                    {werewolfObs.dayBuckets.map((bucket) => (
                      <details key={bucket.day} open className="rounded-md border border-slate-800 bg-slate-900/60 p-3">
                        <summary className="flex cursor-pointer flex-wrap items-center gap-2">
                          <span className="rounded-full bg-amber-700/40 text-amber-100 px-2 py-0.5 text-xs">Day {bucket.day}</span>
                          {bucket.deaths_at_night.length > 0
                            ? <span className="text-sm text-rose-300">夜间出局 {bucket.deaths_at_night.map((p) => `P${p}`).join('、')}</span>
                            : <span className="text-sm text-slate-500">平安夜</span>}
                          <span className="ml-auto text-xs text-slate-500">{bucket.events.length} 个事件</span>
                        </summary>
                        <ol className="mt-3 space-y-1.5 border-l border-slate-700 pl-4 text-sm text-slate-300">
                          {bucket.events.map((ev, idx) => {
                            let label: React.ReactNode = null
                            if (ev.kind === 'night_resolution') {
                              label = ev.dead_players.length === 0
                                ? <span className="text-slate-400">夜间结算：平安夜</span>
                                : <span>夜间结算：{ev.dead_players.map((p) => `P${p}`).join('、')} 出局</span>
                            } else if (ev.kind === 'sheriff_assigned') {
                              label = <span className="inline-flex items-center gap-1"><Crown className="h-3.5 w-3.5 text-amber-300" /> P{ev.player_id} 当选警长（{ev.reason || 'vote'}）</span>
                            } else if (ev.kind === 'sheriff_badge_destroyed') {
                              label = <span className="text-slate-400">警徽撕毁：{ev.reason}</span>
                            } else if (ev.kind === 'sheriff_badge_passed') {
                              label = <span>警徽移交：P{ev.from_player_id} → P{ev.to_player_id}</span>
                            } else if (ev.kind === 'exile') {
                              label = ev.exiled_player_id == null
                                ? <span className="text-slate-400">放逐：无人出局</span>
                                : <span>放逐：P{ev.exiled_player_id} 出局</span>
                            } else if (ev.kind === 'reveal') {
                              label = <span className="text-rose-300">P{ev.player_id} 狼人自爆</span>
                            } else if (ev.kind === 'white_wolf_king_reveal') {
                              label = <span className="text-rose-300">白狼王 P{ev.player_id} 自爆带走 {ev.target_player_id == null ? '未知目标' : `P${ev.target_player_id}`}</span>
                            } else if (ev.kind === 'knight_duel') {
                              label = <span className={ev.hit_wolf ? 'text-emerald-300' : 'text-amber-300'}>骑士 P{ev.knight_id} 决斗 {ev.target_player_id == null ? '未知目标' : `P${ev.target_player_id}`}，{ev.hit_wolf ? '命中狼人' : '撞到好人'}{ev.dead_player_id == null ? '' : `，P${ev.dead_player_id} 出局`}</span>
                            } else if (ev.kind === 'hunter_shot') {
                              label = <span>猎人 P{ev.hunter_id} 开枪 → P{ev.target_player_id}</span>
                            }
                            return <li key={idx} className="-ml-[7px] before:mr-2 before:inline-block before:h-1.5 before:w-1.5 before:rounded-full before:bg-cyan-400">{label}</li>
                          })}
                          {bucket.events.length === 0 ? <li className="text-slate-500 list-none -ml-4">尚无事件</li> : null}
                        </ol>
                      </details>
                    ))}
                  </div>
                </div>

                <div className="bg-slate-800 border border-slate-700 rounded-md p-4">
                  <h3 className="text-sm font-semibold text-slate-400 mb-3">{matchEnded ? '赛后 AI 评分' : 'AI 评分'}</h3>
                  {!matchEnded ? (
                    <div className="rounded-md border border-slate-700/60 bg-slate-950/50 p-6 text-center text-sm text-slate-400">
                      等待比赛结束后由 AI 裁判结算 · 失败阵营 0 分 · 胜方按个人表现 0-10 分
                    </div>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full text-sm min-w-[720px]">
                        <thead>
                          <tr className="border-b border-slate-700 text-left text-xs uppercase tracking-wider text-slate-500">
                            <th className="px-3 py-2">#</th>
                            <th className="px-3 py-2">选手</th>
                            <th className="px-3 py-2">身份</th>
                            <th className="px-3 py-2">阵营</th>
                            <th className="px-3 py-2">得分</th>
                            <th className="px-3 py-2">裁判点评</th>
                          </tr>
                        </thead>
                        <tbody>
                          {wwLeaderboard.map((row, idx) => (
                            <tr key={row.player_id} className="border-b border-slate-800 align-top">
                              <td className="px-3 py-2 text-slate-500">{idx + 1}</td>
                              <td className="px-3 py-2 text-slate-100">{computePlayerLabel(row)}</td>
                              <td className="px-3 py-2">{row.werewolf_role_label ?? '-'}</td>
                              <td className="px-3 py-2">{row.werewolf_team === 'werewolf' ? '狼人' : row.werewolf_team === 'good' ? '好人' : '-'}</td>
                              <td className="px-3 py-2 font-mono text-cyan-300">{row.score}</td>
                              <td className="max-w-[360px] px-3 py-2 text-sm text-slate-300"><JudgeReasoning text={row.judge_reasoning ?? ''} /></td>
                            </tr>
                          ))}
                          {wwLeaderboard.length === 0 ? <tr><td colSpan={6} className="py-6 text-center text-slate-500">等待 AI 裁判评分</td></tr> : null}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              </div>

              <div className="space-y-4 xl:sticky xl:top-4">
                <div className="bg-slate-800 border border-slate-700 rounded-md p-4">
                  <h3 className="text-sm font-semibold text-slate-400 mb-3">当前视角</h3>
                  <div className="rounded-md border border-slate-800 bg-slate-950/70 p-3 text-sm text-slate-300 min-h-[6rem]">
                    {!currentTurn ? <div className="text-slate-500">等待玩家行动...</div> : null}
                    {currentTurn ? (
                      <div className="space-y-2">
                        <div className="flex flex-wrap gap-2 text-xs">
                          <span className="rounded-full bg-cyan-700/40 text-cyan-100 px-2 py-0.5">P{currentTurn.player_id}</span>
                          {currentTurn.personality ? <span className="rounded-full bg-slate-700/60 text-slate-200 px-2 py-0.5">{currentTurn.personality}</span> : null}
                          {currentTurn.phase ? <span className="rounded-full bg-amber-700/40 text-amber-100 px-2 py-0.5">{phaseLabel(currentTurn.phase)}</span> : null}
                          {typeof currentTurn.valid === 'boolean' ? (
                            <span className={`rounded-full px-2 py-0.5 ${currentTurn.valid ? 'bg-emerald-700/40 text-emerald-100' : 'bg-rose-700/40 text-rose-100'}`}>
                              {currentTurn.valid ? '有效' : '无效'}
                            </span>
                          ) : null}
                        </div>
                        {currentTurn.isPrivateNight ? (
                          <p className="text-sm text-slate-400">该玩家正在进行夜间私有动作（信息已隐藏）</p>
                        ) : (
                          <>
                            {currentTurn.request ? <p className="text-sm leading-6 text-slate-300">{currentTurn.request}</p> : null}
                            {currentTurn.action ? (
                              <div className="rounded-md border border-slate-800 bg-slate-900/70 p-2 text-sm text-slate-200">
                                <div>动作：{currentTurn.action}</div>
                                {currentTurn.target_player_id != null ? <div>目标：P{currentTurn.target_player_id}</div> : null}
                                {currentTurn.text ? <div className="mt-1 text-slate-300">「{currentTurn.text}」</div> : null}
                                {currentTurn.reason ? <div className="text-xs text-slate-500">{currentTurn.reason}</div> : null}
                              </div>
                            ) : null}
                          </>
                        )}
                      </div>
                    ) : null}
                  </div>
                </div>

                <div className="bg-slate-800 border border-slate-700 rounded-md p-4">
                  <h3 className="text-sm font-semibold text-slate-400 mb-3">最近行动</h3>
                  <div className="max-h-[300px] overflow-auto rounded-md border border-slate-800 bg-slate-950/70 p-3 space-y-2">
                    {werewolfObs.recentTurns.length === 0 ? <div className="text-sm text-slate-500">暂无</div> : null}
                    {werewolfObs.recentTurns.map((t, i) => (
                      <div key={`${t.timestamp}-${i}`} className="rounded border border-slate-800 bg-slate-900/60 p-2 text-xs">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="rounded-full bg-cyan-700/40 text-cyan-100 px-2 py-0.5">P{t.player_id}</span>
                          {t.phase ? <span className="rounded-full bg-amber-700/40 text-amber-100 px-2 py-0.5">{phaseLabel(t.phase)}</span> : null}
                          {t.action ? <span className="rounded-full bg-slate-700/60 text-slate-200 px-2 py-0.5">{t.action}</span> : null}
                          {typeof t.valid === 'boolean' && !t.valid ? <span className="rounded-full bg-rose-700/40 text-rose-100 px-2 py-0.5">无效</span> : null}
                        </div>
                        {!t.isPrivateNight && t.target_player_id != null ? <div className="mt-1 text-slate-300">目标 P{t.target_player_id}</div> : null}
                        {!t.isPrivateNight && t.text ? <div className="mt-1 text-slate-300">「{t.text}」</div> : null}
                      </div>
                    ))}
                  </div>
                </div>

                {/* AGENT_STREAM is treated as private audit log in werewolf mode (not
                    broadcast to spectators), so the live stream panel intentionally omitted. */}
              </div>
            </div>
          </div>
        )}

        {!isWerewolf && (
        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(360px,1fr)] gap-4 items-stretch">
          <div className="flex flex-col gap-4 xl:h-[calc(100vh-8rem)]">
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 flex-shrink-0">
              <Panel className="flex flex-col items-center justify-center bg-slate-950/50 p-6 text-center">
                <div className="mb-3 inline-flex items-center gap-2 rounded-full border border-cyan-500/30 bg-cyan-950/30 px-3 py-1 text-xs font-medium text-cyan-200">
                  <Clock className="h-3.5 w-3.5" />
                  剩余时间
                </div>
                <div className="font-mono text-6xl font-semibold text-cyan-300 sm:text-7xl">{mins}:{secs}</div>
                <div className="mt-4 flex flex-wrap justify-center gap-2">
                  <StatusBadge tone={phaseTone(phase)}>{phaseLabel(phase)}</StatusBadge>
                  <StatusBadge tone={totalPlayers > 0 && onlinePlayers === totalPlayers ? 'success' : 'warning'}>
                    {onlinePlayers} / {totalPlayers || '?'} SLA
                  </StatusBadge>
                </div>
              </Panel>

              <Panel className="lg:col-span-2">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <h3 className="inline-flex items-center gap-2 text-sm font-semibold text-slate-200">
                    <Trophy className="h-4 w-4 text-amber-300" />
                    排行榜
                  </h3>
                  <StatusBadge tone="info">{leaderboard.length} 名选手</StatusBadge>
                </div>
                <div className="overflow-x-auto">
                  <table className={cx(tableClassName, 'min-w-[680px]')}>
                    <thead>
                      <tr className="border-b border-slate-700 text-left text-xs uppercase tracking-wider text-slate-500">
                        <th className="px-3 py-3">#</th>
                        <th className="px-3 py-3">选手</th>
                        <th className="px-3 py-3 font-mono">得分</th>
                        <th className="px-3 py-3">夺旗</th>
                        <th className="px-3 py-3">失旗</th>
                        <th className="px-3 py-3">SLA</th>
                      </tr>
                    </thead>
                    <tbody>
                      {leaderboard.map((row, i) => (
                        <tr key={row.player_id} className="border-b border-slate-800 transition duration-200 hover:bg-slate-800/60">
                          <td className="px-3 py-3 text-slate-500">{i + 1}</td>
                          <td className="px-3 py-3 font-medium text-slate-100">{computePlayerLabel(row)}</td>
                          <td className="px-3 py-3 font-mono text-cyan-300">{row.score ?? 0}</td>
                          <td className="px-3 py-3 text-emerald-300">{row.flags_captured ?? 0}</td>
                          <td className="px-3 py-3 text-rose-300">{row.flags_lost ?? 0}</td>
                          <td className="px-3 py-3">
                            <StatusBadge tone={row.sla_ok ? 'success' : 'danger'}>
                              {row.sla_ok ? <ShieldCheck className="h-3.5 w-3.5" /> : <ShieldAlert className="h-3.5 w-3.5" />}
                              {row.sla_ok ? '在线' : '异常'}
                            </StatusBadge>
                          </td>
                        </tr>
                      ))}
                      {leaderboard.length === 0 && (
                        <tr>
                          <td colSpan={6} className="px-3 py-8 text-center text-slate-500">等待选手加入...</td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </div>

            <Panel className="flex-shrink-0">
              <div className="mb-3 flex items-center justify-between gap-4">
                <h3 className="inline-flex items-center gap-2 text-sm font-semibold text-slate-200">
                  <ListChecks className="h-4 w-4 text-cyan-300" />
                  最近提交
                </h3>
                <span className="text-xs text-slate-500">同一选手对同一 Flag 仅第一次成功计分</span>
              </div>
              <div className={recentSubmissionsViewportClass}>
                <table className={cx(tableClassName, 'min-w-[760px]')}>
                  <thead>
                    <tr className="border-b border-slate-700 text-left text-xs uppercase tracking-wider text-slate-500">
                      <th className="px-3 py-3">时间</th>
                      <th className="px-3 py-3">提交者</th>
                      <th className="px-3 py-3">目标</th>
                      <th className="px-3 py-3">Flag</th>
                      <th className="px-3 py-3">结果</th>
                      <th className="px-3 py-3">原因</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recentSubmissions.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="px-3 py-8 text-center text-slate-500">暂无提交记录</td>
                      </tr>
                    ) : (
                      recentSubmissions.map((submission, index) => {
                        const attackerId = toNumber(submission.attacker_id)
                        const success = Boolean(submission.success)
                        const flagIndexLabel = formatFlagIndexLabel(submission.flag_index)
                        const timestamp = typeof submission.timestamp === 'string'
                          ? new Date(submission.timestamp).toLocaleTimeString()
                          : '-'

                        return (
                          <tr key={`${String(submission.timestamp ?? index)}-${index}`} className="border-b border-slate-800 transition duration-200 hover:bg-slate-800/60">
                            <td className="px-3 py-3 text-slate-300">{timestamp}</td>
                            <td className="px-3 py-3">P{attackerId ?? '?'}</td>
                            <td className="px-3 py-3">{formatVictimLabel(submission.victim_id)}</td>
                            <td className="px-3 py-3">
                              <div className="font-mono text-cyan-300">{flagIndexLabel}</div>
                              {typeof submission.flag_slot === 'string' && submission.flag_slot !== '' && (
                                <div className="text-xs text-slate-500">{submission.flag_slot}</div>
                              )}
                            </td>
                            <td className="px-3 py-3">
                              <StatusBadge tone={success ? 'success' : 'danger'}>
                                {success ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
                                {success ? '成功' : '失败'}
                              </StatusBadge>
                            </td>
                            <td className="px-3 py-3 text-slate-300">{submissionReasonLabel(submission.reason)}</td>
                          </tr>
                        )
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </Panel>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 flex-1 min-h-0">
              <Panel className="flex h-full min-h-0 flex-col">
                <h3 className="mb-3 inline-flex flex-shrink-0 items-center gap-2 text-sm font-semibold text-slate-200">
                  <Network className="h-4 w-4 text-cyan-300" />
                  网络拓扑
                </h3>
                <div className="flex-grow relative min-h-0">
                  <TopologyMap playerCount={totalPlayers} phase={phase} />
                </div>
              </Panel>

              <Panel className="flex h-full min-h-0 flex-col">
                <h3 className="mb-3 inline-flex flex-shrink-0 items-center gap-2 text-sm font-semibold text-slate-200">
                  <ListChecks className="h-4 w-4 text-cyan-300" />
                  事件日志
                </h3>
                <div className="min-h-0 flex-grow space-y-1 overflow-auto rounded-md border border-slate-800 bg-slate-950/60 p-3 font-mono text-xs">
                  {events.length === 0 && <div className="text-slate-500">暂无事件</div>}
                  {events.map((e, idx) => (
                    <div key={idx} className="text-slate-300">{e}</div>
                  ))}
                </div>
              </Panel>
            </div>
          </div>

          <Panel className="flex h-full min-h-[520px] flex-col xl:sticky xl:top-4 xl:h-[calc(100vh-8rem)]">
            <div className="flex items-center justify-between mb-3 flex-shrink-0">
              <h3 className="inline-flex items-center gap-2 text-sm font-semibold text-slate-200">
                <Brain className="h-4 w-4 text-cyan-300" />
                Agent 实时思考流
              </h3>
              <div className="flex gap-2 flex-wrap items-center justify-end">
                {Object.keys(agentLogs).length === 0 && (
                  <span className="text-xs text-slate-500">暂无数据</span>
                )}
                {Object.keys(agentLogs).map((pidStr) => {
                  const pid = parseInt(pidStr, 10)
                  return (
                    <Button
                      key={pid}
                      size="sm"
                      variant={selectedPlayerLog === pid ? 'primary' : 'secondary'}
                      onClick={() => setSelectedPlayerLog(pid)}
                      className="font-mono"
                    >
                      {playerLabelById.get(pid) ?? `Player ${pid}`}
                    </Button>
                  )
                })}
              </div>
            </div>
            <AgentStreamView
              mode={streamViewMode}
              onModeChange={setStreamViewMode}
              bubbles={selectedPlayerBubbles}
              emptyText="等待 Agent 输出..."
            />
          </Panel>
        </div>

        )}

        {matchEnded && (
          <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
            <div
              className="w-full max-w-lg rounded-md border border-slate-600 bg-slate-900 p-8 text-center shadow-2xl shadow-black/40"
              role="dialog"
              aria-modal="true"
              aria-labelledby="match-ended-title"
            >
              <h2 id="match-ended-title" className="mb-4 text-2xl font-bold text-cyan-400">比赛结束</h2>
              <h3 className="text-lg mb-4">最终排行榜</h3>
              <table className="w-full text-sm mb-6">
                <thead>
                  <tr className="border-b border-slate-700 text-slate-400">
                    <th>#</th><th>选手</th><th>得分</th>
                  </tr>
                </thead>
                <tbody>
                  {leaderboard.map((row, i) => (
                    <tr key={row.player_id} className="border-b border-slate-700/50">
                      <td className="py-1">{i + 1}</td>
                      <td className="py-1">{computePlayerLabel(row)}</td>
                      <td className="py-1 font-mono text-cyan-400">{row.score}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <Button variant="primary" onClick={closeMatchEndedModal}>
                关闭
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default ArenaPage
