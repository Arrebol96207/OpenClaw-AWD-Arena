import React, { useEffect, useMemo, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Download, Pause, Play, RotateCcw, SkipBack } from 'lucide-react'
import { API_BASE, downloadBlob, fetchApi, fetchJson, fetchMatchEvents, readApiError } from '../api'
import AgentStreamView, { buildAgentBubbles, StreamViewMode } from '../components/AgentStreamView'
import TopologyMap from '../components/TopologyMap'
import { Button, ErrorBanner, Panel, StatusBadge, cx, inputClassName, tableClassName } from '../components/ui'
import { submissionReasonLabel } from '../lib/submissionReason'

type EventItem = {
  timestamp: string
  type: string
  data?: Record<string, unknown>
}

type CommentaryItem = {
  commentary_id?: string
  timestamp: string
  trigger?: string
  style?: string
  text: string
}

type LeaderboardEntry = {
  player_id: number
  name?: string
  display_name?: string
  score: number
  flags_captured: number
  flags_lost: number
  sla_ok?: boolean
  werewolf_role?: string
  werewolf_role_label?: string
  werewolf_team?: string
  werewolf_alive?: boolean
  werewolf_is_sheriff?: boolean
  personality?: string
  style_hint?: string
  judge_reasoning?: string
}

type MatchInfo = {
  match_id?: string
  mode?: string
  status?: string
  finished_at?: string
  remaining_seconds?: number
  player_count?: number
  werewolf_board?: string | null
  werewolf_board_label?: string | null
  leaderboard?: Record<string, LeaderboardEntryLike> | LeaderboardEntryLike[]
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

type LeaderboardEntryLike = {
  player_id?: unknown
  name?: unknown
  display_name?: unknown
  model?: unknown
  score?: unknown
  total_score?: unknown
  flags_captured?: unknown
  flags_lost?: unknown
  sla_ok?: unknown
  sla_up?: unknown
  werewolf_role?: unknown
  werewolf_role_label?: unknown
  werewolf_team?: unknown
  werewolf_alive?: unknown
  werewolf_is_sheriff?: unknown
  personality?: unknown
  style_hint?: unknown
  judge_reasoning?: unknown
}

type WerewolfSpeechItem = {
  timestamp: string
  day?: number
  stage?: string
  player_id: number
  personality?: string
  role_label?: string
  team?: string
  text: string
  claim_role?: string
  suspects?: number[]
  vote_intent?: number
}

type WerewolfWolfNightItem = {
  timestamp: string
  type: 'chat' | 'vote' | 'decision' | 'resolution'
  day?: number
  player_id?: number
  personality?: string
  target_player_id?: number
  text?: string
  reason?: string
  dead_players?: number[]
}

type ReplaySnapshot = {
  phase: string
  remainingSeconds: number
  playerCount: number
  readyPlayers: Set<number>
  leaderboard: LeaderboardEntry[]
  agentLogs: Record<number, string[]>
  commentary: CommentaryItem[]
}

const toNumber = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

const eventTime = (timestamp: string): number => {
  const t = new Date(timestamp).getTime()
  return Number.isFinite(t) ? t : 0
}

const normalizeLeaderboard = (raw: MatchInfo['leaderboard']): LeaderboardEntry[] => {
  if (!raw) return []
  const values: LeaderboardEntryLike[] = Array.isArray(raw) ? raw : Object.values(raw)
  const normalized: LeaderboardEntry[] = []

  for (const row of values) {
    const playerId = toNumber(row.player_id)
    if (playerId == null) continue
    const slaOkRaw = row.sla_ok ?? row.sla_up
    normalized.push({
      player_id: playerId,
      name: typeof row.name === 'string' ? row.name : undefined,
      display_name: typeof row.display_name === 'string' ? row.display_name : undefined,
      score: toNumber(row.score) ?? toNumber(row.total_score) ?? 0,
      flags_captured: toNumber(row.flags_captured) ?? 0,
      flags_lost: toNumber(row.flags_lost) ?? 0,
      sla_ok: typeof slaOkRaw === 'boolean' ? slaOkRaw : undefined,
      werewolf_role: typeof row.werewolf_role === 'string' ? row.werewolf_role : undefined,
      werewolf_role_label: typeof row.werewolf_role_label === 'string' ? row.werewolf_role_label : undefined,
      werewolf_team: typeof row.werewolf_team === 'string' ? row.werewolf_team : undefined,
      werewolf_alive: typeof row.werewolf_alive === 'boolean' ? row.werewolf_alive : undefined,
      werewolf_is_sheriff: typeof row.werewolf_is_sheriff === 'boolean' ? row.werewolf_is_sheriff : undefined,
      personality: typeof row.personality === 'string' ? row.personality : undefined,
      style_hint: typeof row.style_hint === 'string' ? row.style_hint : undefined,
      judge_reasoning: typeof row.judge_reasoning === 'string' ? row.judge_reasoning : undefined,
    })
  }

  return normalized.sort((a, b) => b.score - a.score)
}

const mergeLeaderboards = (baseBoard: LeaderboardEntry[], incomingBoard: LeaderboardEntry[]): LeaderboardEntry[] => {
  if (incomingBoard.length === 0) return [...baseBoard]
  const merged = new Map<number, LeaderboardEntry>()
  for (const row of baseBoard) merged.set(row.player_id, row)
  for (const row of incomingBoard) {
    const previous = merged.get(row.player_id)
    merged.set(row.player_id, {
      player_id: row.player_id,
      name: row.name ?? previous?.name,
      display_name: row.display_name ?? previous?.display_name,
      score: row.score,
      flags_captured: row.flags_captured,
      flags_lost: row.flags_lost,
      sla_ok: row.sla_ok ?? previous?.sla_ok,
    })
  }
  return Array.from(merged.values()).sort((a, b) => b.score - a.score)
}

const computePlayerLabel = (row: LeaderboardEntry): string => row.display_name || row.name || `Player ${row.player_id}`

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

const formatPhaseLabel = (phase: string): string => {
  if (phase === 'defense') return '防御阶段'
  if (phase === 'attack') return '攻击阶段'
  if (phase === 'finished') return '已结束'
  if (phase === 'creating_containers') return '创建容器'
  if (phase === 'creating_werewolf_agents') return '创建狼人杀 Agent'
  if (phase === 'initializing_agents') return '初始化 Agent'
  if (phase === 'werewolf_training') return '赛前训练'
  if (phase === 'werewolf_sheriff') return '警长竞选'
  if (phase === 'werewolf_night') return '狼人杀夜晚'
  if (phase === 'werewolf_day') return '狼人杀白天'
  return phase || '初始化'
}

const phaseTone = (phase: string): 'neutral' | 'info' | 'success' | 'warning' | 'danger' => {
  if (phase === 'defense') return 'success'
  if (phase === 'attack') return 'danger'
  if (phase.startsWith('werewolf')) return 'warning'
  if (phase === 'finished') return 'neutral'
  return 'info'
}

const formatEvent = (event: EventItem): string => {
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
    case 'AGENT_NOT_READY':
      return `Agent 未就绪: P${String(data.player_id ?? '?')} (${String(data.reason ?? data.ready_reason ?? 'unknown')})`
    case 'FLAG_CAPTURED':
      return `夺旗: P${String(data.attacker_id ?? data.player_id ?? '?')} -> ${formatVictimLabel(data.victim_id)}${formatFlagEventSuffix(data)}`
    case 'FLAG_SUBMISSION':
      return `提交: P${String(data.attacker_id ?? '?')} -> ${formatVictimLabel(data.victim_id)}${formatFlagEventSuffix(data)} (${data.success ? '成功' : '失败'})`
    case 'FLAG_SUBMISSION_REJECTED':
      return `提交被拒: P${String(data.attacker_id ?? '?')} (${submissionReasonLabel(data.reason)})`
    case 'HEARTBEAT':
      return `心跳: 剩余 ${String(data.remaining_seconds ?? '?')} 秒`
    case 'NETWORK_OPENED':
      return `网络打开: ${String(data.arena_network ?? '')}`
    case 'AGENT_STREAM':
      return `Agent 输出: P${String(data.player_id ?? '?')}`
    case 'AGENT_LOGS_COLLECTED':
      return 'Agent 思考日志已归档'
    case 'AI_COMMENTARY':
      return `AI 解说: ${String(data.text ?? '')}`
    case 'WEREWOLF_TRAINING_STARTED':
      return '狼人杀赛前训练开始'
    case 'WEREWOLF_TRAINING_COMPLETED':
      return '狼人杀赛前训练完成'
    case 'WEREWOLF_GAME_STARTED':
      return '狼人杀开局'
    case 'WEREWOLF_PERSONALITIES_ASSIGNED':
      return '性格分配完成'
    case 'WEREWOLF_ROLES_REVEALED_TO_AUDIENCE':
      return '观众明牌身份已公开'
    case 'WEREWOLF_NIGHT_STARTED':
      return `第 ${String(data.day ?? '?')} 夜开始`
    case 'WEREWOLF_WOLF_CHAT_PUBLIC':
      return '狼队夜聊公开'
    case 'WEREWOLF_WOLF_KILL_VOTE_CAST':
      return `狼刀票: P${String(data.wolf_id ?? '?')} -> P${String(data.target_player_id ?? '?')}`
    case 'WEREWOLF_WOLF_KILL_DECIDED':
      return `狼队最终刀口: P${String(data.target_player_id ?? '?')}`
    case 'WEREWOLF_PLAYER_TURN_STARTED':
      return `行动开始: P${String(data.player_id ?? '?')}`
    case 'WEREWOLF_PLAYER_ACTION_RESOLVED':
      return `行动结果: P${String(data.player_id ?? '?')} ${String(data.action ?? 'pass')}`
    case 'WEREWOLF_DAY_STARTED':
      return `第 ${String(data.day ?? '?')} 天开始`
    case 'WEREWOLF_SHERIFF_ELECTION_STARTED':
      return '警长竞选开始'
    case 'WEREWOLF_SHERIFF_CANDIDATE_DECLARED':
      return `P${String(data.player_id ?? '?')} 上警`
    case 'WEREWOLF_SHERIFF_WITHDRAWN':
      return `P${String(data.player_id ?? '?')} 退水`
    case 'WEREWOLF_SHERIFF_VOTE_CAST':
      return `警长票: P${String(data.voter_id ?? '?')} -> P${String(data.target_player_id ?? '?')}`
    case 'WEREWOLF_SHERIFF_ASSIGNED':
      return `警长当选: P${String(data.player_id ?? '?')}`
    case 'WEREWOLF_SHERIFF_BADGE_PASSED':
      return `警徽移交: P${String(data.from_player_id ?? '?')} -> P${String(data.to_player_id ?? '?')}`
    case 'WEREWOLF_SHERIFF_BADGE_DESTROYED':
      return `警徽撕毁: ${String(data.reason ?? '')}`
    case 'WEREWOLF_PUBLIC_SPEECH':
      return `发言: P${String(data.player_id ?? '?')} ${String(data.text ?? '')}`
    case 'WEREWOLF_VOTE_CAST':
      return `放逐票: P${String(data.voter_id ?? '?')} -> P${String(data.target_player_id ?? '?')}`
    case 'WEREWOLF_EXILE_RESULT':
      return data.exiled_player_id == null ? '放逐结果: 无人出局' : `放逐结果: P${String(data.exiled_player_id)} 出局`
    case 'WEREWOLF_DEATH_RESOLVED':
      return `死亡结算: ${Array.isArray(data.dead_players) ? data.dead_players.map((pid) => `P${pid}`).join(', ') : String(data.death_count ?? 0)}`
    case 'WEREWOLF_REVEALED_SELF':
      return `狼人自爆: P${String(data.player_id ?? '?')}`
    case 'WEREWOLF_WHITE_WOLF_KING_REVEALED':
      return `白狼王自爆: P${String(data.player_id ?? '?')} -> P${String(data.target_player_id ?? '?')}`
    case 'WEREWOLF_KNIGHT_DUEL':
      return `骑士决斗: P${String(data.knight_id ?? '?')} -> P${String(data.target_player_id ?? '?')} (${data.hit_wolf ? '命中狼人' : '撞到好人'})`
    case 'WEREWOLF_HUNTER_SHOT':
      return `猎人开枪: P${String(data.hunter_id ?? '?')} -> P${String(data.target_player_id ?? '?')}`
    case 'WEREWOLF_GAME_FINISHED':
      return `狼人杀结束: ${String(data.winner_label ?? data.winner ?? '平局')}`
    case 'WEREWOLF_AI_JUDGEMENT':
      return 'AI 裁判评分完成'
    case 'MATCH_FINISHED':
      return '比赛结束'
    default:
      return JSON.stringify(data)
  }
}

const extractCommentary = (event: EventItem): CommentaryItem | null => {
  if (event.type !== 'AI_COMMENTARY') return null
  const data = event.data ?? {}
  const text = data.text
  if (typeof text !== 'string' || text.trim() === '') return null
  return {
    commentary_id: typeof data.commentary_id === 'string' ? data.commentary_id : undefined,
    timestamp: typeof data.timestamp === 'string' ? data.timestamp : event.timestamp,
    trigger: typeof data.trigger === 'string' ? data.trigger : undefined,
    style: typeof data.style === 'string' ? data.style : undefined,
    text,
  }
}

const formatCommentaryTrigger = (value?: string): string => {
  if (value === 'flag_captured') return '夺旗'
  if (value === 'phase_change') return '阶段'
  if (value === 'match_finished') return '收官'
  if (value === 'batch') return '赛况'
  return value || '赛况'
}

const roleTone = (team?: string): 'neutral' | 'info' | 'success' | 'warning' | 'danger' => {
  if (team === 'werewolf') return 'danger'
  if (team === 'good') return 'success'
  return 'neutral'
}

const buildWerewolfReplayObservations = (events: EventItem[], cursor: number) => {
  const speeches: WerewolfSpeechItem[] = []
  const wolfNight: WerewolfWolfNightItem[] = []
  const roleRows = new Map<number, LeaderboardEntry>()

  for (let i = 0; i < cursor; i += 1) {
    const event = events[i]
    const data = event.data ?? {}
    if (event.type === 'WEREWOLF_ROLES_REVEALED_TO_AUDIENCE') {
      const players = data.players
      if (players && typeof players === 'object') {
        for (const value of Object.values(players as Record<string, unknown>)) {
          if (!value || typeof value !== 'object') continue
          const row = value as Record<string, unknown>
          const pid = toNumber(row.player_id)
          if (pid == null) continue
          roleRows.set(pid, {
            player_id: pid,
            name: typeof row.name === 'string' ? row.name : undefined,
            score: 0,
            flags_captured: 0,
            flags_lost: 0,
            werewolf_role: typeof row.role === 'string' ? row.role : undefined,
            werewolf_role_label: typeof row.role_label === 'string' ? row.role_label : undefined,
            werewolf_team: typeof row.team === 'string' ? row.team : undefined,
            werewolf_alive: typeof row.alive === 'boolean' ? row.alive : undefined,
            werewolf_is_sheriff: typeof row.is_sheriff === 'boolean' ? row.is_sheriff : undefined,
            personality: typeof row.personality === 'string' ? row.personality : undefined,
            style_hint: typeof row.style_hint === 'string' ? row.style_hint : undefined,
          })
        }
      }
    } else if (event.type === 'WEREWOLF_PUBLIC_SPEECH') {
      const pid = toNumber(data.player_id ?? data.speaker_id)
      if (pid != null) {
        speeches.push({
          timestamp: event.timestamp,
          day: toNumber(data.day),
          stage: typeof data.stage === 'string' ? data.stage : undefined,
          player_id: pid,
          personality: typeof data.personality === 'string' ? data.personality : undefined,
          role_label: typeof data.role_label === 'string' ? data.role_label : undefined,
          team: typeof data.team === 'string' ? data.team : undefined,
          text: typeof data.text === 'string' ? data.text : '',
          claim_role: typeof data.claim_role === 'string' ? data.claim_role : undefined,
          suspects: Array.isArray(data.suspects) ? data.suspects.map(toNumber).filter((value): value is number => value != null) : undefined,
          vote_intent: toNumber(data.vote_intent),
        })
      }
    } else if (event.type === 'WEREWOLF_WOLF_CHAT_PUBLIC') {
      const messages = Array.isArray(data.messages) ? data.messages : []
      for (const message of messages) {
        if (!message || typeof message !== 'object') continue
        const row = message as Record<string, unknown>
        wolfNight.push({
          timestamp: event.timestamp,
          type: 'chat',
          day: toNumber(data.day),
          player_id: toNumber(row.player_id),
          personality: typeof row.personality === 'string' ? row.personality : undefined,
          text: typeof row.text === 'string' ? row.text : '',
        })
      }
    } else if (event.type === 'WEREWOLF_WOLF_KILL_VOTE_CAST') {
      wolfNight.push({
        timestamp: event.timestamp,
        type: 'vote',
        day: toNumber(data.day),
        player_id: toNumber(data.wolf_id),
        personality: typeof data.personality === 'string' ? data.personality : undefined,
        target_player_id: toNumber(data.target_player_id),
        reason: typeof data.reason === 'string' ? data.reason : undefined,
      })
    } else if (event.type === 'WEREWOLF_WOLF_KILL_DECIDED') {
      wolfNight.push({
        timestamp: event.timestamp,
        type: 'decision',
        day: toNumber(data.day),
        target_player_id: toNumber(data.target_player_id),
        reason: typeof data.reason === 'string' ? data.reason : undefined,
      })
    } else if (event.type === 'WEREWOLF_NIGHT_ACTION') {
      wolfNight.push({
        timestamp: event.timestamp,
        type: 'resolution',
        day: toNumber(data.day),
        target_player_id: toNumber(data.wolf_target_public),
        dead_players: Array.isArray(data.dead_players) ? data.dead_players.map(toNumber).filter((value): value is number => value != null) : [],
        reason: typeof data.action === 'string' ? data.action : undefined,
      })
    }
  }

  return {
    roleRows: [...roleRows.values()].sort((a, b) => a.player_id - b.player_id),
    speeches: speeches.reverse(),
    wolfNight: wolfNight.reverse(),
  }
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

const buildReplaySnapshot = (events: EventItem[], cursor: number, info: MatchInfo | null): ReplaySnapshot => {
  const isFinishedStatus = info?.status === 'finished'
  let phase = isFinishedStatus ? 'finished' : info?.status ?? 'initializing'
  let playerCount = info?.player_count ?? 0
  let initialRemaining = isFinishedStatus ? 0 : info?.remaining_seconds ?? 0

  if (isFinishedStatus) {
    for (const event of events) {
      if (event.type === 'MATCH_STARTED') {
        const defenseDuration = toNumber((event.data ?? {}).defense_duration)
        if (defenseDuration != null) {
          initialRemaining = defenseDuration
          break
        }
      }
    }
  }

  let remainingSeconds = initialRemaining
  const readyPlayers = new Set<number>()
  const agentLogs: Record<number, string[]> = {}
  const commentary: CommentaryItem[] = []
  let leaderboard: LeaderboardEntry[] = []

  const ensureLeaderboardRow = (pid: number) => {
    if (leaderboard.some((row) => row.player_id === pid)) return
    leaderboard = [...leaderboard, { player_id: pid, score: 0, flags_captured: 0, flags_lost: 0 }]
  }

  const applyCaptured = (attackerId: number | undefined, victimId: number | undefined, points: number | undefined) => {
    const gained = points ?? 100
    const lost = -Math.abs((points ?? 100) / 2)

    if (attackerId != null) {
      ensureLeaderboardRow(attackerId)
      leaderboard = leaderboard.map((row) => row.player_id === attackerId ? { ...row, score: row.score + gained, flags_captured: row.flags_captured + 1 } : row)
    }

    if (victimId != null) {
      ensureLeaderboardRow(victimId)
      leaderboard = leaderboard.map((row) => row.player_id === victimId ? { ...row, score: row.score + lost, flags_lost: row.flags_lost + 1 } : row)
    }

    leaderboard = [...leaderboard].sort((a, b) => b.score - a.score)
  }

  for (let i = 0; i < cursor; i++) {
    const event = events[i]
    const data = event.data ?? {}
    const commentaryItem = extractCommentary(event)
    if (commentaryItem) commentary.push(commentaryItem)

    if (event.type === 'STATUS') {
      if (typeof data.status === 'string') phase = data.status
    } else if (event.type === 'MATCH_STARTED') {
      phase = typeof data.status === 'string' ? data.status : 'defense'
      const maybePlayers = toNumber(data.player_count)
      if (maybePlayers != null) playerCount = maybePlayers
      const maybeDefense = toNumber(data.defense_duration)
      if (maybeDefense != null) remainingSeconds = maybeDefense
    } else if (event.type === 'CONTAINERS_CREATED') {
      const players = data.players
      if (players && typeof players === 'object') playerCount = Object.keys(players).length
    } else if (event.type === 'AGENT_READY') {
      const pid = toNumber(data.player_id)
      if (pid != null) readyPlayers.add(pid)
    } else if (event.type === 'PHASE_CHANGE') {
      if (typeof data.phase === 'string') phase = data.phase
      const maybeRemaining = toNumber(data.remaining_seconds)
      if (maybeRemaining != null) remainingSeconds = maybeRemaining
    } else if (event.type === 'HEARTBEAT') {
      if (typeof data.phase === 'string') phase = data.phase
      const maybeRemaining = toNumber(data.remaining_seconds)
      if (maybeRemaining != null) remainingSeconds = maybeRemaining
      const hbBoard = normalizeLeaderboard(data.leaderboard as MatchInfo['leaderboard'])
      if (hbBoard.length > 0) leaderboard = mergeLeaderboards(leaderboard, hbBoard)
    } else if (event.type === 'FLAG_CAPTURED') {
      const captureBoard = normalizeLeaderboard(data.leaderboard as MatchInfo['leaderboard'])
      if (captureBoard.length > 0) {
        leaderboard = mergeLeaderboards(leaderboard, captureBoard)
      } else {
        applyCaptured(toNumber(data.attacker_id ?? data.player_id), toNumber(data.victim_id), toNumber(data.points))
      }
    } else if (event.type === 'AGENT_STREAM') {
      const pid = toNumber(data.player_id)
      const content = data.content
      if (pid != null && typeof content === 'string') {
        agentLogs[pid] = [...(agentLogs[pid] ?? []), content]
        if (content.trim() !== '') readyPlayers.add(pid)
      }
    } else if (event.type === 'AGENT_LOGS_COLLECTED') {
      const persistedLogs = normalizePersistedAgentLogs(data.logs)
      for (const [pidRaw, lines] of Object.entries(persistedLogs)) agentLogs[Number(pidRaw)] = lines
    } else if (event.type === 'MATCH_FINISHED') {
      phase = 'finished'
      remainingSeconds = 0
      const finalBoard = normalizeLeaderboard(data.leaderboard as MatchInfo['leaderboard'])
      if (finalBoard.length > 0) leaderboard = finalBoard
    } else if (event.type === 'WEREWOLF_GAME_STARTED') {
      playerCount = toNumber(data.player_count) ?? playerCount
      phase = 'werewolf_night'
      for (let pid = 1; pid <= playerCount; pid += 1) ensureLeaderboardRow(pid)
    } else if (event.type === 'WEREWOLF_NIGHT_STARTED') {
      phase = 'werewolf_night'
    } else if (event.type === 'WEREWOLF_SHERIFF_ELECTION_STARTED') {
      phase = 'werewolf_sheriff'
    } else if (event.type === 'WEREWOLF_DAY_STARTED') {
      phase = 'werewolf_day'
    } else if (event.type === 'WEREWOLF_AI_JUDGEMENT') {
      const judgeBoard = normalizeLeaderboard(data.leaderboard as MatchInfo['leaderboard'])
      if (judgeBoard.length > 0) leaderboard = judgeBoard
    } else if (event.type === 'WEREWOLF_GAME_FINISHED') {
      phase = 'finished'
    }
  }

  return { phase, remainingSeconds, playerCount, readyPlayers, leaderboard, agentLogs, commentary: commentary.reverse() }
}

const ReplayPage: React.FC = () => {
  const { matchId } = useParams<{ matchId: string }>()
  const navigate = useNavigate()
  const [events, setEvents] = useState<EventItem[]>([])
  const [info, setInfo] = useState<MatchInfo | null>(null)
  const [submissions, setSubmissions] = useState<SubmissionItem[]>([])
  const [cursor, setCursor] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [selectedPlayerLog, setSelectedPlayerLog] = useState<number | null>(null)
  const [streamViewMode, setStreamViewMode] = useState<StreamViewMode>('cleaned')
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [exportingReport, setExportingReport] = useState(false)

  useEffect(() => {
    if (!matchId) return
    let cancelled = false

    const loadReplay = async () => {
      setLoading(true)
      setLoadError(null)
      try {
        const [matchInfo, submissionsData, raw] = await Promise.all([
          fetchJson<MatchInfo>(`${API_BASE}/api/matches/${matchId}`),
          fetchJson<{ submissions?: SubmissionItem[] }>(`${API_BASE}/api/matches/${matchId}/submissions`),
          fetchMatchEvents<EventItem>(matchId),
        ])
        if (cancelled) return

        setInfo(matchInfo)
        setSubmissions(Array.isArray(submissionsData.submissions) ? submissionsData.submissions : [])
        const sorted = [...raw].sort((a, b) => eventTime(a.timestamp) - eventTime(b.timestamp))
        setEvents(sorted)
        setCursor((current) => current === 0 ? sorted.length : current)

        // Werewolf finished matches reuse the full ArenaPage layout (round table, vote pool,
        // timeline, judge scoreboard) — no need for the AWD-style replay scrubber.
        if ((matchInfo as { mode?: string }).mode === 'werewolf' && matchId) {
          navigate(`/arena/${matchId}`, { replace: true })
        }
      } catch (error) {
        if (!cancelled) {
          setLoadError(error instanceof Error ? error.message : '加载回放数据失败')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadReplay()
    return () => {
      cancelled = true
    }
  }, [matchId, navigate])

  useEffect(() => {
    if (!playing) return
    if (cursor >= events.length) {
      setPlaying(false)
      return
    }

    const interval = window.setInterval(() => {
      setCursor((previous) => {
        if (previous >= events.length) {
          window.clearInterval(interval)
          return previous
        }
        return previous + 1
      })
    }, Math.max(120, Math.floor(1000 / speed)))

    return () => window.clearInterval(interval)
  }, [playing, speed, cursor, events.length])

  const snapshot = useMemo(() => buildReplaySnapshot(events, cursor, info), [events, cursor, info])
  const isWerewolf = info?.mode === 'werewolf'
  const werewolfReplay = useMemo(() => buildWerewolfReplayObservations(events, cursor), [events, cursor])
  const werewolfBoardLabel = info?.werewolf_board_label
    ?? (info?.werewolf_board === 'white_wolf_king_knight' ? '12 人白狼王骑士' : '12 人预女猎守')

  useEffect(() => {
    const players = Object.keys(snapshot.agentLogs).map(Number).filter(Number.isFinite).sort((a, b) => a - b)
    if (players.length === 0) {
      setSelectedPlayerLog(null)
      return
    }
    setSelectedPlayerLog((previous) => previous != null && players.includes(previous) ? previous : players[0])
  }, [snapshot.agentLogs])

  const mins = Math.floor(snapshot.remainingSeconds / 60).toString().padStart(2, '0')
  const secs = Math.floor(snapshot.remainingSeconds % 60).toString().padStart(2, '0')
  const readyCount = snapshot.readyPlayers.size
  const playerLabelById = new Map(snapshot.leaderboard.map((row) => [row.player_id, computePlayerLabel(row)]))
  const selectedPlayerBubbles = selectedPlayerLog != null && snapshot.agentLogs[selectedPlayerLog]
    ? buildAgentBubbles(snapshot.agentLogs[selectedPlayerLog], streamViewMode)
    : []
  const replayCutoffTime = cursor > 0 ? eventTime(events[Math.min(cursor, events.length) - 1]?.timestamp ?? '') : 0
  const visibleSubmissions = submissions
    .filter((submission) => {
      if (cursor === 0) return false
      const timestamp = typeof submission.timestamp === 'string' ? eventTime(submission.timestamp) : 0
      return timestamp > 0 && timestamp <= replayCutoffTime
    })
    .sort((a, b) => {
      const aTime = typeof a.timestamp === 'string' ? eventTime(a.timestamp) : 0
      const bTime = typeof b.timestamp === 'string' ? eventTime(b.timestamp) : 0
      return bTime - aTime
    })

  const handleReportExport = async () => {
    if (!matchId) return
    setExportingReport(true)
    setLoadError(null)
    try {
      const response = await fetchApi(`${API_BASE}/api/matches/${matchId}/report.md`)
      if (!response.ok) throw new Error(await readApiError(response))
      downloadBlob(await response.blob(), `match_${matchId}_report.md`)
    } catch (error) {
      setLoadError(error instanceof Error ? error.message : '导出复盘战报失败')
    } finally {
      setExportingReport(false)
    }
  }

  return (
    <div className="space-y-5">
      <header className="flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-cyan-300">Replay</div>
          <h1 className="mt-1 break-all text-2xl font-semibold text-white">回放 · {matchId}</h1>
          <div className="mt-2 flex flex-wrap gap-2">
            <StatusBadge tone={phaseTone(snapshot.phase)}>{formatPhaseLabel(snapshot.phase)}</StatusBadge>
            {isWerewolf && <StatusBadge tone="warning">{werewolfBoardLabel}</StatusBadge>}
            <StatusBadge tone="neutral">{cursor} / {events.length} 事件</StatusBadge>
            <StatusBadge tone="info">{readyCount} / {snapshot.playerCount || '?'} Agent 就绪</StatusBadge>
            {loading && <StatusBadge tone="warning">加载中</StatusBadge>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button
            variant="secondary"
            icon={<Download className="h-4 w-4" />}
            disabled={!matchId || exportingReport}
            onClick={handleReportExport}
          >
            {exportingReport ? '导出中' : '导出战报'}
          </Button>
          <Link className="inline-flex cursor-pointer items-center justify-center rounded-md border border-slate-600 bg-slate-800 px-3.5 py-2 text-sm font-medium text-slate-100 transition duration-200 hover:bg-slate-700 focus:outline-none focus:ring-2 focus:ring-cyan-400/30" to="/history">
            返回历史列表
          </Link>
        </div>
      </header>

      <ErrorBanner message={loadError} />

      <Panel>
        <div className="flex flex-col gap-3 md:flex-row md:items-center">
          <Button variant="primary" icon={playing ? <Pause className="h-4 w-4" /> : <Play className="h-4 w-4" />} disabled={events.length === 0} onClick={() => setPlaying((previous) => !previous)}>
            {playing ? '暂停' : '播放'}
          </Button>
          <Button variant="secondary" icon={<SkipBack className="h-4 w-4" />} onClick={() => {
            setPlaying(false)
            setCursor(0)
          }}>
            回到开场
          </Button>
          <Button variant="secondary" icon={<RotateCcw className="h-4 w-4" />} onClick={() => {
            setPlaying(false)
            setCursor(events.length)
          }}>
            跳到结尾
          </Button>
          <select className={cx(inputClassName, 'w-28')} value={speed} onChange={(event) => setSpeed(Number(event.target.value))}>
            <option value={0.5}>0.5x</option>
            <option value={1}>1x</option>
            <option value={2}>2x</option>
            <option value={4}>4x</option>
          </select>
          <div className="flex-1">
            <input
              type="range"
              min={0}
              max={events.length}
              value={cursor}
              onChange={(event) => {
                setPlaying(false)
                setCursor(Number(event.target.value))
              }}
              className="w-full cursor-pointer accent-cyan-400"
            />
          </div>
        </div>
      </Panel>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(360px,1fr)]">
        <div className="flex min-w-0 flex-col gap-4 xl:h-[calc(100vh-13rem)]">
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Panel className="flex flex-col items-center justify-center">
              <div className="text-xs uppercase tracking-wider text-slate-500">回放剩余时间</div>
              <div className="mt-2 font-mono text-6xl text-cyan-300 md:text-7xl">{mins}:{secs}</div>
            </Panel>
            <Panel className="lg:col-span-2" title={isWerewolf ? '狼人杀个人评分' : '回放排行榜'}>
              <div className="overflow-x-auto">
                <table className={cx(tableClassName, 'min-w-[620px]')}>
                  <thead>
                    <tr className="border-b border-slate-700 text-left text-xs uppercase tracking-wider text-slate-500">
                      <th className="px-3 py-2">#</th>
                      <th className="px-3 py-2">选手</th>
                      {isWerewolf && <th className="px-3 py-2">身份</th>}
                      <th className="px-3 py-2">得分</th>
                      {!isWerewolf && <th className="px-3 py-2">夺旗</th>}
                      {!isWerewolf && <th className="px-3 py-2">失旗</th>}
                      {!isWerewolf && <th className="px-3 py-2">SLA</th>}
                      {isWerewolf && <th className="px-3 py-2">点评</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {snapshot.leaderboard.map((row, index) => (
                      <tr key={row.player_id} className="border-b border-slate-800 transition duration-200 hover:bg-slate-800/50">
                        <td className="px-3 py-2 text-slate-500">{index + 1}</td>
                        <td className="px-3 py-2">{computePlayerLabel(row)}</td>
                        {isWerewolf && <td className="px-3 py-2">{row.werewolf_role_label ?? '-'}</td>}
                        <td className="px-3 py-2 font-mono text-cyan-300">{row.score}</td>
                        {!isWerewolf && <td className="px-3 py-2 text-emerald-300">{row.flags_captured}</td>}
                        {!isWerewolf && <td className="px-3 py-2 text-rose-300">{row.flags_lost}</td>}
                        {!isWerewolf && <td className="px-3 py-2">{typeof row.sla_ok === 'boolean' ? (row.sla_ok ? '在线' : '异常') : '-'}</td>}
                        {isWerewolf && <td className="max-w-[320px] px-3 py-2 text-sm text-slate-300">{row.judge_reasoning ?? '-'}</td>}
                      </tr>
                    ))}
                    {snapshot.leaderboard.length === 0 && <tr><td colSpan={isWerewolf ? 5 : 6} className="py-6 text-center text-slate-500">暂无积分数据</td></tr>}
                  </tbody>
                </table>
              </div>
            </Panel>
          </div>

          {!isWerewolf && <Panel title="提交记录（回放时点）">
            <div className="h-[15rem] overflow-auto">
              <table className={tableClassName}>
                <thead>
                  <tr className="border-b border-slate-700 text-left text-xs uppercase tracking-wider text-slate-500">
                    <th className="px-3 py-2">时间</th>
                    <th className="px-3 py-2">提交者</th>
                    <th className="px-3 py-2">目标</th>
                    <th className="px-3 py-2">Flag</th>
                    <th className="px-3 py-2">结果</th>
                    <th className="px-3 py-2">原因</th>
                  </tr>
                </thead>
                <tbody>
                  {visibleSubmissions.length === 0 ? (
                    <tr><td colSpan={6} className="py-6 text-center text-slate-500">当前回放时点暂无提交记录</td></tr>
                  ) : (
                    visibleSubmissions.map((submission, index) => {
                      const attackerId = toNumber(submission.attacker_id)
                      const success = Boolean(submission.success)
                      const timestamp = typeof submission.timestamp === 'string' ? new Date(submission.timestamp).toLocaleTimeString() : '-'
                      return (
                        <tr key={`${String(submission.timestamp ?? index)}-${index}`} className="border-b border-slate-800 transition duration-200 hover:bg-slate-800/50">
                          <td className="px-3 py-2 text-slate-300">{timestamp}</td>
                          <td className="px-3 py-2">P{attackerId ?? '?'}</td>
                          <td className="px-3 py-2">{formatVictimLabel(submission.victim_id)}</td>
                          <td className="px-3 py-2">
                            <div className="font-mono text-cyan-300">{formatFlagIndexLabel(submission.flag_index)}</div>
                            {typeof submission.flag_slot === 'string' && submission.flag_slot !== '' && <div className="text-xs text-slate-500">{submission.flag_slot}</div>}
                          </td>
                          <td className="px-3 py-2"><StatusBadge tone={success ? 'success' : 'danger'}>{success ? '成功' : '失败'}</StatusBadge></td>
                          <td className="px-3 py-2 text-slate-300">{submissionReasonLabel(submission.reason)}</td>
                        </tr>
                      )
                    })
                  )}
                </tbody>
              </table>
            </div>
          </Panel>}

          {isWerewolf && (
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
              <Panel title="明牌身份与性格">
                <div className="max-h-[320px] overflow-auto rounded-md border border-slate-800 bg-slate-950/70 p-3">
                  {werewolfReplay.roleRows.length === 0 && <div className="text-sm text-slate-500">等待观众明牌事件...</div>}
                  <div className="grid grid-cols-1 gap-2">
                    {werewolfReplay.roleRows.map((row) => (
                      <div key={row.player_id} className="rounded-md border border-slate-800 bg-slate-900/60 p-2">
                        <div className="flex flex-wrap items-center gap-2">
                          <StatusBadge tone="info">P{row.player_id}</StatusBadge>
                          <StatusBadge tone={roleTone(row.werewolf_team)}>{row.werewolf_role_label ?? '-'}</StatusBadge>
                          {row.personality && <StatusBadge tone="neutral">{row.personality}</StatusBadge>}
                        </div>
                        <div className="mt-1 truncate text-xs text-slate-400">{row.name ?? computePlayerLabel(row)}</div>
                      </div>
                    ))}
                  </div>
                </div>
              </Panel>

              <Panel title="白天发言席" className="xl:col-span-2">
                <div className="max-h-[320px] overflow-auto rounded-md border border-slate-800 bg-slate-950/70 p-3">
                  {werewolfReplay.speeches.length === 0 && <div className="text-sm text-slate-500">当前时点暂无白天发言</div>}
                  {werewolfReplay.speeches.map((speech, index) => (
                    <div key={`${speech.timestamp}-${index}`} className="border-b border-slate-800/80 py-3 first:pt-0 last:border-0 last:pb-0">
                      <div className="mb-2 flex flex-wrap items-center gap-2">
                        <StatusBadge tone="info">Day {speech.day ?? '?'}</StatusBadge>
                        <StatusBadge tone={roleTone(speech.team)}>P{speech.player_id} {speech.role_label ?? ''}</StatusBadge>
                        {speech.personality && <StatusBadge tone="neutral">{speech.personality}</StatusBadge>}
                        {speech.stage && <StatusBadge tone="warning">{speech.stage}</StatusBadge>}
                      </div>
                      <p className="text-sm leading-6 text-slate-200">{speech.text || '(pass)'}</p>
                    </div>
                  ))}
                </div>
              </Panel>
            </div>
          )}

          {isWerewolf && (
            <Panel title="夜晚狼队视角">
              <div className="max-h-[280px] overflow-auto rounded-md border border-slate-800 bg-slate-950/70 p-3">
                {werewolfReplay.wolfNight.length === 0 && <div className="text-sm text-slate-500">当前时点暂无狼队夜间行动</div>}
                {werewolfReplay.wolfNight.map((item, index) => (
                  <div key={`${item.timestamp}-${index}`} className="border-b border-slate-800/80 py-3 first:pt-0 last:border-0 last:pb-0">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <StatusBadge tone="danger">Night {item.day ?? '?'}</StatusBadge>
                      <StatusBadge tone="neutral">{item.type === 'chat' ? '夜聊' : item.type === 'vote' ? '投刀' : item.type === 'decision' ? '最终刀口' : '夜间结算'}</StatusBadge>
                      {item.player_id && <StatusBadge tone="danger">P{item.player_id}</StatusBadge>}
                      {item.personality && <StatusBadge tone="neutral">{item.personality}</StatusBadge>}
                    </div>
                    {item.text && <p className="text-sm leading-6 text-slate-200">{item.text}</p>}
                    <div className="text-sm text-slate-300">
                      {item.target_player_id && <span>目标 P{item.target_player_id}</span>}
                      {item.dead_players && item.dead_players.length > 0 && <span>死亡 {item.dead_players.map((pid) => `P${pid}`).join(', ')}</span>}
                      {item.reason && <span className="ml-2 text-slate-500">{item.reason}</span>}
                    </div>
                  </div>
                ))}
              </div>
            </Panel>
          )}

          <div className={cx('grid min-h-0 flex-1 grid-cols-1 gap-4', !isWerewolf && 'lg:grid-cols-2')}>
            {!isWerewolf && <Panel title="网络拓扑（回放时点）" className="flex min-h-[360px] flex-col">
              <div className="relative min-h-0 flex-1"><TopologyMap playerCount={snapshot.playerCount} phase={snapshot.phase} /></div>
            </Panel>}
            <Panel title="事件时间线" className="flex min-h-[360px] flex-col">
              <div className="min-h-0 flex-1 overflow-auto rounded-md border border-slate-800 bg-slate-950/70 p-3 font-mono text-xs">
                {events.length === 0 && <div className="text-slate-500">暂无事件</div>}
                {events.map((event, index) => {
                  const active = index === cursor - 1
                  const visible = index < cursor
                  return (
                    <div key={`${event.timestamp}_${index}`} className={cx('py-0.5', visible ? 'text-slate-300' : 'text-slate-600', active && 'text-cyan-300')}>
                      [{new Date(event.timestamp).toLocaleTimeString()}] {event.type}: {formatEvent(event)}
                    </div>
                  )
                })}
              </div>
            </Panel>
          </div>
        </div>

        <div className="flex min-h-[560px] flex-col gap-4 xl:sticky xl:top-24 xl:h-[calc(100vh-13rem)]">
          <Panel title="AI 解说" className="flex max-h-[18rem] flex-col">
            <div className="min-h-[8rem] overflow-auto rounded-md border border-slate-800 bg-slate-950/70 p-3">
              {snapshot.commentary.length === 0 && <div className="text-sm text-slate-500">当前回放时点暂无 AI 解说</div>}
              {snapshot.commentary.map((item, index) => (
                <div key={item.commentary_id ?? `${item.timestamp}-${index}`} className="border-b border-slate-800/80 py-3 first:pt-0 last:border-0 last:pb-0">
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <StatusBadge tone="info">{formatCommentaryTrigger(item.trigger)}</StatusBadge>
                    <span className="font-mono text-xs text-slate-500">{new Date(item.timestamp).toLocaleTimeString()}</span>
                  </div>
                  <p className="text-sm leading-6 text-slate-200">{item.text}</p>
                </div>
              ))}
            </div>
          </Panel>

          <Panel className="flex min-h-[360px] flex-1 flex-col">
          <div className="mb-3 flex items-center justify-between gap-3">
            <h2 className="text-lg font-semibold text-slate-100">Agent 思考流（回放）</h2>
            <div className="flex flex-wrap justify-end gap-2">
              {Object.keys(snapshot.agentLogs).length === 0 && <span className="text-xs text-slate-500">当前时点暂无数据</span>}
              {Object.keys(snapshot.agentLogs).map((pidStr) => {
                const pid = Number(pidStr)
                return (
                  <Button key={pid} size="sm" variant={selectedPlayerLog === pid ? 'primary' : 'secondary'} onClick={() => setSelectedPlayerLog(pid)}>
                    {playerLabelById.get(pid) ?? `Player ${pid}`}
                  </Button>
                )
              })}
            </div>
          </div>
          <AgentStreamView mode={streamViewMode} onModeChange={setStreamViewMode} bubbles={selectedPlayerBubbles} emptyText="等待 Agent 输出..." />
          </Panel>
        </div>
      </div>
    </div>
  )
}

export default ReplayPage
