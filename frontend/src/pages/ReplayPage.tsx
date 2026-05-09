import React, { useEffect, useMemo, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { API_BASE, fetchApi } from '../api'
import AgentStreamView, { buildAgentBubbles, StreamViewMode } from '../components/AgentStreamView'
import TopologyMap from '../components/TopologyMap'
import { submissionReasonLabel } from '../lib/submissionReason'

type EventItem = {
  timestamp: string
  type: string
  data?: Record<string, unknown>
}

type LeaderboardEntry = {
  player_id: number
  name?: string
  display_name?: string
  score: number
  flags_captured: number
  flags_lost: number
  sla_ok?: boolean
}

type MatchInfo = {
  match_id?: string
  status?: string
  finished_at?: string
  remaining_seconds?: number
  player_count?: number
  leaderboard?: Record<string, LeaderboardEntryLike> | LeaderboardEntryLike[]
  submissions?: SubmissionItem[]
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
}

type ReplaySnapshot = {
  phase: string
  remainingSeconds: number
  playerCount: number
  readyPlayers: Set<number>
  leaderboard: LeaderboardEntry[]
  agentLogs: Record<number, string[]>
}

const toNumber = (value: unknown): number | undefined => {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string') {
    const parsed = Number(value)
    if (Number.isFinite(parsed)) return parsed
  }
  return undefined
}

const normalizeLeaderboard = (raw: MatchInfo['leaderboard']): LeaderboardEntry[] => {
  if (!raw) return []

  const values: LeaderboardEntryLike[] = Array.isArray(raw) ? raw : Object.values(raw)
  const normalized: LeaderboardEntry[] = []

  for (const row of values) {
    const playerId = toNumber(row.player_id)
    if (playerId == null) continue

    const score = toNumber(row.score) ?? toNumber(row.total_score) ?? 0
    const flagsCaptured = toNumber(row.flags_captured) ?? 0
    const flagsLost = toNumber(row.flags_lost) ?? 0
    const slaOkRaw = row.sla_ok ?? row.sla_up

    normalized.push({
      player_id: playerId,
      name: typeof row.name === 'string' ? row.name : undefined,
      display_name: typeof row.display_name === 'string' ? row.display_name : undefined,
      score,
      flags_captured: flagsCaptured,
      flags_lost: flagsLost,
      sla_ok: typeof slaOkRaw === 'boolean' ? slaOkRaw : undefined,
    })
  }

  return normalized.sort((a, b) => b.score - a.score)
}

const mergeLeaderboards = (baseBoard: LeaderboardEntry[], incomingBoard: LeaderboardEntry[]): LeaderboardEntry[] => {
  if (incomingBoard.length === 0) return [...baseBoard]

  const merged = new Map<number, LeaderboardEntry>()
  for (const row of baseBoard) {
    merged.set(row.player_id, row)
  }

  for (const row of incomingBoard) {
    const prev = merged.get(row.player_id)
    merged.set(row.player_id, {
      player_id: row.player_id,
      name: row.name ?? prev?.name,
      display_name: row.display_name ?? prev?.display_name,
      score: row.score,
      flags_captured: row.flags_captured,
      flags_lost: row.flags_lost,
      sla_ok: row.sla_ok ?? prev?.sla_ok,
    })
  }

  return Array.from(merged.values()).sort((a, b) => b.score - a.score)
}

const computePlayerLabel = (row: LeaderboardEntry): string => {
  if (row.display_name) return row.display_name
  if (row.name) return row.name
  return `Player ${row.player_id}`
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
    case 'AGENT_STREAM':
      return `Agent 输出: P${String(data.player_id ?? '?')}`
    case 'AGENT_LOGS_COLLECTED':
      return 'Agent 思考日志已归档'
    case 'MATCH_FINISHED':
      return '比赛结束'
    default:
      return JSON.stringify(data)
  }
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

  let initialRemaining = 0
  if (!isFinishedStatus) {
    initialRemaining = info?.remaining_seconds ?? 0
  } else {
    for (const ev of events) {
      if (ev.type === 'MATCH_STARTED') {
        const d = toNumber((ev.data ?? {}).defense_duration)
        if (d != null) { initialRemaining = d; break }
      }
    }
  }
  let remainingSeconds = initialRemaining

  const readyPlayers = new Set<number>()
  const agentLogs: Record<number, string[]> = {}
  let leaderboard: LeaderboardEntry[] = []

  const ensureLeaderboardRow = (pid: number) => {
    if (leaderboard.some((r) => r.player_id === pid)) return
    leaderboard = [...leaderboard, { player_id: pid, score: 0, flags_captured: 0, flags_lost: 0 }]
  }

  const applyCaptured = (attackerId: number | undefined, victimId: number | undefined, points: number | undefined) => {
    const gained = points ?? 100
    const lost = -Math.abs((points ?? 100) / 2)

    if (attackerId != null) {
      ensureLeaderboardRow(attackerId)
      leaderboard = leaderboard.map((row) =>
        row.player_id === attackerId
          ? { ...row, score: row.score + gained, flags_captured: row.flags_captured + 1 }
          : row
      )
    }

    if (victimId != null) {
      ensureLeaderboardRow(victimId)
      leaderboard = leaderboard.map((row) =>
        row.player_id === victimId
          ? { ...row, score: row.score + lost, flags_lost: row.flags_lost + 1 }
          : row
      )
    }

    leaderboard = [...leaderboard].sort((a, b) => b.score - a.score)
  }

  for (let i = 0; i < cursor; i++) {
    const ev = events[i]
    const data = ev.data ?? {}

    if (ev.type === 'STATUS') {
      if (typeof data.status === 'string') phase = data.status
    } else if (ev.type === 'MATCH_STARTED') {
      phase = typeof data.status === 'string' ? data.status : 'defense'
      const maybePlayers = toNumber(data.player_count)
      if (maybePlayers != null) playerCount = maybePlayers
      const maybeDefense = toNumber(data.defense_duration)
      if (maybeDefense != null) remainingSeconds = maybeDefense
    } else if (ev.type === 'CONTAINERS_CREATED') {
      const players = data.players
      if (players && typeof players === 'object') {
        playerCount = Object.keys(players).length
      }
    } else if (ev.type === 'AGENT_READY') {
      const pid = toNumber(data.player_id)
      if (pid != null) readyPlayers.add(pid)
    } else if (ev.type === 'PHASE_CHANGE') {
      if (typeof data.phase === 'string') phase = data.phase
      const maybeRemaining = toNumber(data.remaining_seconds)
      if (maybeRemaining != null) remainingSeconds = maybeRemaining
    } else if (ev.type === 'HEARTBEAT') {
      if (typeof data.phase === 'string') phase = data.phase
      const maybeRemaining = toNumber(data.remaining_seconds)
      if (maybeRemaining != null) remainingSeconds = maybeRemaining
      const hbBoard = normalizeLeaderboard(data.leaderboard as MatchInfo['leaderboard'])
      if (hbBoard.length > 0) leaderboard = mergeLeaderboards(leaderboard, hbBoard)
    } else if (ev.type === 'FLAG_CAPTURED') {
      const captureBoard = normalizeLeaderboard(data.leaderboard as MatchInfo['leaderboard'])
      if (captureBoard.length > 0) {
        leaderboard = mergeLeaderboards(leaderboard, captureBoard)
      } else {
        const attackerId = toNumber(data.attacker_id ?? data.player_id)
        const victimId = toNumber(data.victim_id)
        const points = toNumber(data.points)
        applyCaptured(attackerId, victimId, points)
      }
    } else if (ev.type === 'AGENT_STREAM') {
      const pid = toNumber(data.player_id)
      const content = data.content
      if (pid != null && typeof content === 'string') {
        const lines = agentLogs[pid] ?? []
        agentLogs[pid] = [...lines, content]
        if (!readyPlayers.has(pid) && content.trim() !== '') {
          readyPlayers.add(pid)
        }
      }
    } else if (ev.type === 'AGENT_LOGS_COLLECTED') {
      const persistedLogs = normalizePersistedAgentLogs(data.logs)
      for (const [pidRaw, lines] of Object.entries(persistedLogs)) {
        agentLogs[Number(pidRaw)] = lines
      }
    } else if (ev.type === 'MATCH_FINISHED') {
      phase = 'finished'
      remainingSeconds = 0
      const finalBoard = normalizeLeaderboard(data.leaderboard as MatchInfo['leaderboard'])
      if (finalBoard.length > 0) {
        const currentMaxScore = leaderboard.reduce((max, row) => Math.max(max, row.score), 0)
        const finalMaxScore = finalBoard.reduce((max, row) => Math.max(max, row.score), 0)
        if (finalMaxScore > 0 || currentMaxScore === 0) {
          leaderboard = finalBoard
        }
      }
    }
  }

  return { phase, remainingSeconds, playerCount, readyPlayers, leaderboard, agentLogs }
}

const ReplayPage: React.FC = () => {
  const { matchId } = useParams<{ matchId: string }>()
  const [events, setEvents] = useState<EventItem[]>([])
  const [info, setInfo] = useState<MatchInfo | null>(null)
  const [submissions, setSubmissions] = useState<SubmissionItem[]>([])
  const [cursor, setCursor] = useState<number>(0)
  const [playing, setPlaying] = useState<boolean>(false)
  const [speed, setSpeed] = useState<number>(1)
  const [selectedPlayerLog, setSelectedPlayerLog] = useState<number | null>(null)
  const [streamViewMode, setStreamViewMode] = useState<StreamViewMode>('cleaned')

  useEffect(() => {
    if (!matchId) return
    fetchApi(`${API_BASE}/api/matches/${matchId}`)
      .then((r) => r.json())
      .then((d: MatchInfo) => {
        setInfo(d)
      })
      .catch(() => {})

    fetchApi(`${API_BASE}/api/matches/${matchId}/submissions`)
      .then((r) => r.json())
      .then((d: { submissions?: SubmissionItem[] }) => {
        setSubmissions(Array.isArray(d.submissions) ? d.submissions : [])
      })
      .catch(() => {})

    fetchApi(`${API_BASE}/api/matches/${matchId}/events?limit=10000`)
      .then((r) => r.json())
      .then((resp: { events?: EventItem[] } | EventItem[]) => {
        const raw = Array.isArray(resp) ? resp : (resp as { events?: EventItem[] }).events ?? []
        const sorted = [...raw].sort((a, b) => eventTime(a.timestamp) - eventTime(b.timestamp))
        setEvents(sorted)
        setCursor((current) => (current === 0 ? sorted.length : current))
      })
      .catch(() => {})
  }, [matchId])

  useEffect(() => {
    if (!playing) return
    if (cursor >= events.length) {
      setPlaying(false)
      return
    }

    const interval = window.setInterval(() => {
      setCursor((prev) => {
        if (prev >= events.length) {
          window.clearInterval(interval)
          return prev
        }
        return prev + 1
      })
    }, Math.max(120, Math.floor(1000 / speed)))

    return () => window.clearInterval(interval)
  }, [playing, speed, cursor, events.length])

  const snapshot = useMemo(() => buildReplaySnapshot(events, cursor, info), [events, cursor, info])

  useEffect(() => {
    const players = Object.keys(snapshot.agentLogs)
      .map((key) => Number(key))
      .filter((key) => Number.isFinite(key))
      .sort((a, b) => a - b)

    if (players.length === 0) {
      setSelectedPlayerLog(null)
      return
    }

    setSelectedPlayerLog((prev) => {
      if (prev != null && players.includes(prev)) return prev
      return players[0]
    })
  }, [snapshot.agentLogs])

  const mins = Math.floor(snapshot.remainingSeconds / 60).toString().padStart(2, '0')
  const secs = Math.floor(snapshot.remainingSeconds % 60).toString().padStart(2, '0')
  const totalPlayers = snapshot.playerCount
  const readyCount = snapshot.readyPlayers.size
  const isInitPhase = snapshot.phase === 'creating_containers' || snapshot.phase === 'initializing_agents'
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
  const recentSubmissionsViewportClass = 'h-[15rem] overflow-x-auto overflow-y-auto overscroll-contain'

  return (
    <div className="min-h-screen bg-slate-900 text-slate-100 p-4">
      <div className="max-w-7xl mx-auto space-y-6">
        <header className="flex items-center justify-between gap-4">
          <h1 className="text-2xl font-semibold">回放 — {matchId}</h1>
          <div className="flex items-center gap-3">
            <span className={`px-3 py-1 rounded-full text-sm font-medium ${
              snapshot.phase === 'defense' ? 'bg-emerald-600' :
              snapshot.phase === 'attack' ? 'bg-red-600' :
              snapshot.phase === 'finished' ? 'bg-slate-500' :
              'bg-slate-700'
            }`}>
              {snapshot.phase === 'defense' ? '防御阶段' : snapshot.phase === 'attack' ? '攻击阶段' : snapshot.phase === 'finished' ? '已结束' : '初始化'}
            </span>
            <Link to="/history" className="px-3 py-2 rounded-md bg-slate-700 hover:bg-slate-600">返回历史列表</Link>
          </div>
        </header>

        <div className="bg-slate-800 border border-slate-700 rounded-md p-4 space-y-3">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setPlaying((prev) => !prev)}
              className="px-3 py-1.5 rounded-md bg-cyan-700 hover:bg-cyan-600 text-sm"
              disabled={events.length === 0}
            >
              {playing ? '暂停' : '播放'}
            </button>
            <button
              onClick={() => {
                setPlaying(false)
                setCursor(0)
              }}
              className="px-3 py-1.5 rounded-md bg-slate-700 hover:bg-slate-600 text-sm"
            >
              重置
            </button>
            <select
              value={speed}
              onChange={(e) => setSpeed(Number(e.target.value))}
              className="px-2 py-1.5 rounded-md bg-slate-700 border border-slate-600 text-sm"
            >
              <option value={0.5}>0.5x</option>
              <option value={1}>1x</option>
              <option value={2}>2x</option>
              <option value={4}>4x</option>
            </select>
            <span className="text-sm text-slate-400 font-mono">{cursor} / {events.length}</span>
          </div>

          <input
            type="range"
            min={0}
            max={events.length}
            value={cursor}
            onChange={(e) => {
              setPlaying(false)
              setCursor(Number(e.target.value))
            }}
            className="w-full"
          />
        </div>
        {isInitPhase && (
          <div className="bg-slate-800 border border-slate-700 rounded-md p-4">
            <div className="flex items-center justify-between mb-2">
              <span className="text-sm font-semibold text-slate-300">
                {snapshot.phase === 'creating_containers' ? '回放：创建容器阶段' : '回放：初始化 Agent 阶段'}
              </span>
              <span className="text-sm font-mono text-slate-400">{readyCount} / {totalPlayers || '?'} 就绪</span>
            </div>
            <div className="w-full bg-slate-700 rounded-full h-2 overflow-hidden">
              <div
                className="bg-cyan-500 h-2 rounded-full transition-all duration-300"
                style={{ width: totalPlayers > 0 ? `${(readyCount / totalPlayers) * 100}%` : '0%' }}
              />
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,2fr)_minmax(360px,1fr)] gap-4 items-stretch">
          <div className="flex flex-col gap-4 xl:h-[calc(100vh-8rem)]">
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 flex-shrink-0">
              <div className="bg-slate-800 border border-slate-700 rounded-md p-6 flex flex-col items-center justify-center">
                <div className="text-xs text-slate-400 mb-1">回放剩余时间</div>
                <div className="text-7xl font-mono text-cyan-400">{mins}:{secs}</div>
              </div>

              <div className="lg:col-span-2 bg-slate-800 border border-slate-700 rounded-md p-4">
                <h3 className="text-sm font-semibold text-slate-400 mb-3">回放排行榜</h3>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left border-b border-slate-700 text-slate-400">
                      <th className="pb-2">#</th>
                      <th className="pb-2">选手</th>
                      <th className="pb-2 font-mono">得分</th>
                      <th className="pb-2">夺旗</th>
                      <th className="pb-2">失旗</th>
                      <th className="pb-2">SLA</th>
                    </tr>
                  </thead>
                  <tbody>
                    {snapshot.leaderboard.map((row, i) => (
                      <tr key={row.player_id} className="border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors">
                        <td className="py-2 pr-3 text-slate-400">{i + 1}</td>
                        <td className="py-2 pr-3">{computePlayerLabel(row)}</td>
                        <td className="py-2 pr-3 font-mono text-cyan-400">{row.score}</td>
                        <td className="py-2 pr-3 text-emerald-400">{row.flags_captured}</td>
                        <td className="py-2 pr-3 text-red-400">{row.flags_lost}</td>
                        <td className="py-2">{typeof row.sla_ok === 'boolean' ? (row.sla_ok ? '✅' : '❌') : '-'}</td>
                      </tr>
                    ))}
                    {snapshot.leaderboard.length === 0 && (
                      <tr>
                        <td colSpan={6} className="py-4 text-center text-slate-500">暂无积分数据</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="bg-slate-800 border border-slate-700 rounded-md p-4 flex-shrink-0">
              <div className="mb-3 flex items-center justify-between gap-4">
                <h3 className="text-sm font-semibold text-slate-400">提交记录（回放时点）</h3>
                <span className="text-xs text-slate-500">同一选手对同一 Flag 仅第一次成功计分</span>
              </div>
              <div className={recentSubmissionsViewportClass}>
                <table className="w-full text-sm">
                  <thead>
                    <tr className="text-left border-b border-slate-700 text-slate-400">
                      <th className="pb-2">时间</th>
                      <th className="pb-2">提交者</th>
                      <th className="pb-2">目标</th>
                      <th className="pb-2">Flag</th>
                      <th className="pb-2">结果</th>
                      <th className="pb-2">原因</th>
                    </tr>
                  </thead>
                  <tbody>
                    {visibleSubmissions.length === 0 ? (
                      <tr>
                        <td colSpan={6} className="py-4 text-center text-slate-500">当前回放时点暂无提交记录</td>
                      </tr>
                    ) : (
                      visibleSubmissions.map((submission, index) => {
                        const attackerId = toNumber(submission.attacker_id)
                        const success = Boolean(submission.success)
                        const flagIndexLabel = formatFlagIndexLabel(submission.flag_index)
                        const timestamp = typeof submission.timestamp === 'string'
                          ? new Date(submission.timestamp).toLocaleTimeString()
                          : '-'

                        return (
                          <tr key={`${String(submission.timestamp ?? index)}-${index}`} className="border-b border-slate-700/50 hover:bg-slate-700/30 transition-colors">
                            <td className="py-2 pr-3 text-slate-300">{timestamp}</td>
                            <td className="py-2 pr-3">P{attackerId ?? '?'}</td>
                            <td className="py-2 pr-3">{formatVictimLabel(submission.victim_id)}</td>
                            <td className="py-2 pr-3">
                              <div className="font-mono text-cyan-300">{flagIndexLabel}</div>
                              {typeof submission.flag_slot === 'string' && submission.flag_slot !== '' && (
                                <div className="text-xs text-slate-500">{submission.flag_slot}</div>
                              )}
                            </td>
                            <td className={`py-2 pr-3 ${success ? 'text-emerald-400' : 'text-red-400'}`}>{success ? '成功' : '失败'}</td>
                            <td className="py-2 pr-3 text-slate-300">{submissionReasonLabel(submission.reason)}</td>
                          </tr>
                        )
                      })
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 flex-1 min-h-0">
              <div className="bg-slate-800 border border-slate-700 rounded-md p-4 flex flex-col h-full min-h-0">
                <h3 className="text-sm font-semibold text-slate-400 mb-3 flex-shrink-0">网络拓扑（回放时点）</h3>
                <div className="flex-grow relative min-h-0">
                  <TopologyMap playerCount={snapshot.playerCount} phase={snapshot.phase} />
                </div>
              </div>
              <div className="bg-slate-800 border border-slate-700 rounded-md p-4 flex flex-col h-full min-h-0">
                <h3 className="text-sm font-semibold text-slate-400 mb-3 flex-shrink-0">事件时间线</h3>
                <div className="flex-grow min-h-0 overflow-auto bg-slate-900/60 rounded-md p-3 font-mono text-xs space-y-1">
                  {events.length === 0 && <div className="text-slate-500">暂无事件</div>}
                  {events.map((e, idx) => {
                    const active = idx === cursor - 1
                    const visible = idx < cursor
                    return (
                      <div
                        key={`${e.timestamp}_${idx}`}
                        className={`${visible ? 'text-slate-300' : 'text-slate-600'} ${active ? 'text-cyan-400' : ''}`}
                      >
                        [{new Date(e.timestamp).toLocaleTimeString()}] {e.type}: {formatEvent(e)}
                      </div>
                    )
                  })}
                </div>
              </div>
            </div>
          </div>

          <div className="bg-slate-800 border border-slate-700 rounded-md p-4 flex flex-col min-h-[520px] xl:sticky xl:top-4 xl:h-[calc(100vh-8rem)] h-full min-h-0">
            <div className="flex items-center justify-between mb-3 flex-shrink-0">
              <h3 className="text-sm font-semibold text-slate-400">Agent 思考流（回放）</h3>
              <div className="flex gap-2 flex-wrap items-center justify-end">
                {Object.keys(snapshot.agentLogs).length === 0 && (
                  <span className="text-xs text-slate-500">当前时点暂无数据</span>
                )}
                {Object.keys(snapshot.agentLogs).map((pidStr) => {
                  const pid = Number(pidStr)
                  return (
                    <button
                      key={pid}
                      onClick={() => setSelectedPlayerLog(pid)}
                      className={`px-3 py-1 text-xs rounded-md font-mono transition-colors ${
                        selectedPlayerLog === pid
                          ? 'bg-cyan-600 text-white'
                          : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
                      }`}
                    >
                      {playerLabelById.get(pid) ?? `Player ${pid}`}
                    </button>
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
          </div>
        </div>
      </div>
    </div>
  )
}

export default ReplayPage
