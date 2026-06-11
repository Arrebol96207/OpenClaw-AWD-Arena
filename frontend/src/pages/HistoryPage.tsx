import React, { useEffect, useRef, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { AlertTriangle, Download, FileArchive, FileText, PlayCircle, RefreshCw, Square, Trash2, X } from 'lucide-react'
import { API_BASE, downloadBlob, fetchApi, fetchJson, fetchMatchEvents, readApiError } from '../api'
import { Button, ErrorBanner, Panel, StatusBadge, cx, inputClassName, tableClassName } from '../components/ui'
import { useProtectedApiAccess } from '../hooks/useProtectedApiAccess'

type MatchRow = {
  match_id: string
  id?: string
  name?: string
  mode?: string
  status: string
  player_count?: number
  playerCount?: number
  created_at?: string
  createdAt?: string
  duration?: number
  resource_destroyed?: boolean
  resourceDestroyed?: boolean
  can_end?: boolean
  canEnd?: boolean
  finished_at?: string
  finishedAt?: string
  werewolf_board?: string | null
  werewolf_board_label?: string | null
  werewolf_winner?: string | null
  werewolf_winner_label?: string | null
  werewolf_finished_reason?: string | null
  werewolf_final_day?: number | null
  werewolf_final_sheriff_id?: number | null
  player_code_export_status?: string
  player_code_export_available?: boolean
  player_code_export_downloadable?: boolean
  player_code_export_partial?: boolean
  player_code_export_error?: string
  player_code_export_generated_at?: string
  player_code_export_profile?: string
  player_code_export_result_status?: string
  player_code_export_incomplete_player_count?: number
}

const modeLabel = (mode?: string): string => {
  if (mode === 'werewolf') return '狼人杀'
  if (mode === 'awd') return 'AWD'
  return mode || '-'
}

const werewolfWinnerLabel = (row: MatchRow): string => {
  if (row.werewolf_winner_label) return row.werewolf_winner_label
  if (row.werewolf_winner === 'werewolf') return '狼人胜'
  if (row.werewolf_winner === 'good') return '好人胜'
  return '-'
}

const werewolfWinnerTone = (row: MatchRow): 'neutral' | 'info' | 'success' | 'warning' | 'danger' => {
  if (row.werewolf_winner === 'werewolf') return 'danger'
  if (row.werewolf_winner === 'good') return 'success'
  return 'neutral'
}

const werewolfBoardLabel = (row: MatchRow): string => {
  if (row.werewolf_board_label) return row.werewolf_board_label
  if (row.werewolf_board === 'white_wolf_king_knight') return '12 人白狼王骑士'
  return '12 人预女猎守'
}

const terminalStatuses = new Set(['finished', 'error', 'aborted'])

const isTerminalStatus = (status: string): boolean => terminalStatuses.has(status)

const statusTone = (status: string): 'neutral' | 'info' | 'success' | 'warning' | 'danger' => {
  if (status === 'finished') return 'success'
  if (status === 'aborted') return 'warning'
  if (status === 'error') return 'danger'
  if (status === 'defense' || status === 'attack') return 'info'
  return 'neutral'
}

const canDownloadPlayerCodeExport = (match: MatchRow): boolean => {
  if ((match.mode ?? 'awd') !== 'awd' || match.status !== 'finished') return false
  if (typeof match.player_code_export_downloadable === 'boolean') return match.player_code_export_downloadable
  return true
}

const playerCodeExportLabel = (match: MatchRow): string => {
  if (match.player_code_export_status === 'generatable') return '生成代码包'
  if (match.player_code_export_partial) return '选手代码(部分)'
  return '选手代码'
}

const playerCodeExportHint = (match: MatchRow): string => {
  const status = match.player_code_export_status
  if (status === 'generatable') return '导出包尚未生成，点击后会尝试从历史记录补生成复盘材料。'
  if (status === 'partial') return '导出包已生成，但部分选手材料不完整。'
  if (status === 'failed') return match.player_code_export_error || '选手代码导出生成失败。'
  if (status === 'ready') return '选手代码导出包可下载。'
  return '选手代码导出包状态未知。'
}

const playerCodeExportUnavailableTone = (match: MatchRow): 'neutral' | 'info' | 'success' | 'warning' | 'danger' => {
  if (match.player_code_export_status === 'failed') return 'danger'
  return 'warning'
}

const playerCodeExportUnavailableLabel = (match: MatchRow): string => {
  if (match.player_code_export_status === 'failed') return '代码导出失败'
  return '代码不可用'
}

const formatDuration = (seconds?: number): string => {
  if (!seconds || seconds <= 0) return '-'
  return `${Math.round(seconds / 60)} 分钟`
}

const formatTime = (value?: string): string => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : value
}

const HistoryPage: React.FC = () => {
  const protectedApi = useProtectedApiAccess()
  const [matches, setMatches] = useState<MatchRow[]>([])
  const [statusFilter, setStatusFilter] = useState('All')
  const [modeFilter, setModeFilter] = useState<'All' | 'werewolf' | 'awd'>('All')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [endingMatchId, setEndingMatchId] = useState<string | null>(null)
  const [exportingJsonMatchId, setExportingJsonMatchId] = useState<string | null>(null)
  const [exportingReportMatchId, setExportingReportMatchId] = useState<string | null>(null)
  const [exportingCodeMatchId, setExportingCodeMatchId] = useState<string | null>(null)
  const [deletingMatchId, setDeletingMatchId] = useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<{ matchId: string; label: string } | null>(null)
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const highlightedMatchId = searchParams.get('matchId')
  const highlightedRowRef = useRef<HTMLTableRowElement | null>(null)

  const loadMatches = async () => {
    if (!protectedApi.ready) {
      setMatches([])
      setError(null)
      setLoading(false)
      return
    }
    setLoading(true)
    setError(null)
    try {
      const data = await fetchJson<{ matches?: MatchRow[] } | MatchRow[]>(`${API_BASE}/api/matches`)
      const list = Array.isArray(data) ? data : data.matches ?? []
      const sorted = [...list].sort((a, b) => {
        const aTime = new Date(a.created_at ?? a.createdAt ?? 0).getTime()
        const bTime = new Date(b.created_at ?? b.createdAt ?? 0).getTime()
        return bTime - aTime
      })
      setMatches(sorted)
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '加载历史比赛失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (protectedApi.loading) return
    if (protectedApi.ready) {
      loadMatches()
    } else {
      setMatches([])
      setError(null)
      setLoading(false)
    }
  }, [protectedApi.loading, protectedApi.ready])

  useEffect(() => {
    if (!highlightedMatchId || matches.length === 0) return
    const hasMatch = matches.some((match) => (match.match_id ?? match.id ?? '') === highlightedMatchId)
    if (hasMatch) highlightedRowRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  }, [highlightedMatchId, matches])

  const filteredMatches = matches.filter((match) => {
    if (statusFilter !== 'All' && match.status !== statusFilter) return false
    if (modeFilter !== 'All' && (match.mode ?? 'awd') !== modeFilter) return false
    return true
  })
  const uniqueStatuses = Array.from(new Set(matches.map((match) => match.status))).sort()

  const handleExport = async (event: React.MouseEvent, matchId: string) => {
    event.stopPropagation()
    setExportingJsonMatchId(matchId)
    setError(null)
    try {
      const matchData = await fetchJson<Record<string, unknown>>(`${API_BASE}/api/matches/${matchId}`)
      const eventsData = await fetchMatchEvents<unknown>(matchId)
      const fullData = {
        ...matchData,
        events: eventsData,
      }
      downloadBlob(new Blob([JSON.stringify(fullData, null, 2)], { type: 'application/json' }), `match_${matchId}.json`)
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : '导出 JSON 失败')
    } finally {
      setExportingJsonMatchId(null)
    }
  }

  const handleEndMatch = async (event: React.MouseEvent, matchId: string, isActive: boolean) => {
    event.stopPropagation()
    setEndingMatchId(matchId)
    setError(null)
    try {
      const action = isActive ? 'end' : 'destroy'
      const response = await fetchApi(`${API_BASE}/api/matches/${matchId}/${action}`, { method: 'POST' })
      if (!response.ok) throw new Error(await readApiError(response))
      await loadMatches()
    } catch (endError) {
      setError(endError instanceof Error ? endError.message : isActive ? '结束比赛失败' : '清理比赛资源失败')
    } finally {
      setEndingMatchId(null)
    }
  }

  const handleDeleteMatch = async (event: React.MouseEvent, matchId: string, matchName?: string) => {
    event.stopPropagation()
    const label = matchName ? `${matchName} (${matchId})` : matchId
    setDeleteTarget({ matchId, label })
  }

  const confirmDeleteMatch = async () => {
    if (!deleteTarget) return
    const { matchId } = deleteTarget
    setDeletingMatchId(matchId)
    setError(null)
    try {
      const response = await fetchApi(`${API_BASE}/api/matches/${matchId}`, { method: 'DELETE' })
      if (!response.ok) throw new Error(await readApiError(response))
      await loadMatches()
      setDeleteTarget(null)
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : '删除比赛失败')
    } finally {
      setDeletingMatchId(null)
    }
  }

  const handlePlayerCodeExport = async (event: React.MouseEvent, matchId: string) => {
    event.stopPropagation()
    setExportingCodeMatchId(matchId)
    setError(null)
    try {
      const response = await fetchApi(`${API_BASE}/api/matches/${matchId}/player-code-export`)
      if (!response.ok) throw new Error(await readApiError(response))
      downloadBlob(await response.blob(), `match_${matchId}_player_code_export.zip`)
      await loadMatches()
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : '导出选手代码/复盘材料失败')
    } finally {
      setExportingCodeMatchId(null)
    }
  }

  const handleReportExport = async (event: React.MouseEvent, matchId: string) => {
    event.stopPropagation()
    setExportingReportMatchId(matchId)
    setError(null)
    try {
      const response = await fetchApi(`${API_BASE}/api/matches/${matchId}/report.md`)
      if (!response.ok) throw new Error(await readApiError(response))
      downloadBlob(await response.blob(), `match_${matchId}_report.md`)
    } catch (exportError) {
      setError(exportError instanceof Error ? exportError.message : '导出复盘战报失败')
    } finally {
      setExportingReportMatchId(null)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-cyan-300">Archive</div>
          <h1 className="mt-1 text-2xl font-semibold text-white">历史比赛</h1>
          <p className="mt-1 text-sm text-slate-400">查看比赛结果、导出复盘数据，并重新进入仍在运行的赛场。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <select className={cx(inputClassName, 'w-36')} value={modeFilter} onChange={(e) => setModeFilter(e.target.value as 'All' | 'werewolf' | 'awd')}>
            <option value="All">所有模式</option>
            <option value="werewolf">狼人杀</option>
            <option value="awd">AWD</option>
          </select>
          <select className={cx(inputClassName, 'w-44')} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
            <option value="All">所有状态</option>
            {uniqueStatuses.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
          <Button variant="secondary" icon={<RefreshCw className="h-4 w-4" />} loading={loading} onClick={loadMatches}>
            刷新
          </Button>
        </div>
      </div>

      <ErrorBanner message={!protectedApi.loading && !protectedApi.ready ? protectedApi.message : null} />
      <ErrorBanner message={error} />

      <Panel>
        <div className="overflow-x-auto">
          <table className={tableClassName}>
            <thead>
              <tr className="border-b border-slate-700 text-left text-xs uppercase tracking-wider text-slate-500">
                <th className="px-3 py-3">名称</th>
                <th className="px-3 py-3">模式</th>
                <th className="px-3 py-3">状态</th>
                <th className="px-3 py-3">胜方 / 选手</th>
                <th className="px-3 py-3">时长</th>
                <th className="px-3 py-3">创建时间</th>
                <th className="px-3 py-3">完成时间</th>
                <th className="px-3 py-3">操作</th>
              </tr>
            </thead>
            <tbody>
              {filteredMatches.map((match) => {
                const rowId = match.match_id ?? match.id ?? ''
                const isActive = !isTerminalStatus(match.status)
                const resourcesDestroyed = (match.resource_destroyed ?? match.resourceDestroyed) ?? false
                const canEnd = match.can_end ?? match.canEnd ?? !resourcesDestroyed
                const finishedAt = match.finished_at ?? match.finishedAt
                const showPlayerCodeExport = (match.mode ?? 'awd') === 'awd' && match.status === 'finished'
                const playerCodeExportDownloadable = canDownloadPlayerCodeExport(match)

                return (
                  <tr
                    key={rowId}
                    ref={rowId === highlightedMatchId ? highlightedRowRef : null}
                    className={cx(
                      'cursor-pointer border-b border-slate-800 transition duration-200 hover:bg-slate-800/60',
                      rowId === highlightedMatchId && 'bg-cyan-950/50 ring-1 ring-cyan-400/40',
                    )}
                    onClick={() => navigate(isActive ? `/arena/${rowId}` : `/replay/${rowId}`)}
                  >
                    <td className="px-3 py-3">
                      <div className="font-medium text-slate-100">{match.name || rowId}</div>
                      <div className="mt-1 font-mono text-xs text-slate-500">{rowId}</div>
                    </td>
                    <td className="px-3 py-3">
                      <StatusBadge tone={match.mode === 'werewolf' ? 'warning' : 'info'}>{modeLabel(match.mode)}</StatusBadge>
                    </td>
                    <td className="px-3 py-3">
                      <StatusBadge tone={statusTone(match.status)}>
                        {match.status}
                        {isTerminalStatus(match.status) && (resourcesDestroyed ? ' / 已清理' : ' / 待清理')}
                      </StatusBadge>
                    </td>
                    <td className="px-3 py-3 text-slate-300">
                      {match.mode === 'werewolf' ? (
                        <div className="flex flex-col gap-1">
                          {match.werewolf_winner ? (
                            <StatusBadge tone={werewolfWinnerTone(match)}>{werewolfWinnerLabel(match)}</StatusBadge>
                          ) : (
                            <span className="text-xs text-slate-500">{match.status === 'finished' ? '-' : '进行中'}</span>
                          )}
                          <div className="text-xs text-slate-500">
                            {werewolfBoardLabel(match)}{match.werewolf_final_day != null ? ` · Day ${match.werewolf_final_day}` : ''}
                            {match.werewolf_final_sheriff_id ? ` · 警长 P${match.werewolf_final_sheriff_id}` : ''}
                          </div>
                          {match.werewolf_finished_reason && (
                            <div className="text-xs text-slate-500">{match.werewolf_finished_reason}</div>
                          )}
                        </div>
                      ) : (
                        <>{match.player_count ?? match.playerCount ?? '-'} 选手</>
                      )}
                    </td>
                    <td className="px-3 py-3 text-slate-300">{formatDuration(match.duration)}</td>
                    <td className="px-3 py-3 text-slate-300">{formatTime(match.created_at ?? match.createdAt)}</td>
                    <td className="px-3 py-3 text-slate-300">{formatTime(finishedAt)}</td>
                    <td className="px-3 py-3">
                      <div className="flex flex-wrap gap-2">
                        <Button size="sm" variant="secondary" icon={<Download className="h-3.5 w-3.5" />} loading={exportingJsonMatchId === rowId} onClick={(e) => handleExport(e, rowId)}>
                          JSON
                        </Button>
                        <Button size="sm" variant="secondary" icon={<FileText className="h-3.5 w-3.5" />} loading={exportingReportMatchId === rowId} onClick={(e) => handleReportExport(e, rowId)}>
                          战报
                        </Button>
                        {showPlayerCodeExport && (
                          playerCodeExportDownloadable ? (
                            <Button
                              size="sm"
                              variant={match.player_code_export_status === 'generatable' ? 'primary' : 'secondary'}
                              icon={<FileArchive className="h-3.5 w-3.5" />}
                              loading={exportingCodeMatchId === rowId}
                              title={playerCodeExportHint(match)}
                              onClick={(e) => handlePlayerCodeExport(e, rowId)}
                            >
                              {playerCodeExportLabel(match)}
                            </Button>
                          ) : (
                            <StatusBadge
                              tone={playerCodeExportUnavailableTone(match)}
                              title={playerCodeExportHint(match)}
                              className="cursor-help"
                              onClick={(event) => event.stopPropagation()}
                            >
                              {playerCodeExportUnavailableLabel(match)}
                            </StatusBadge>
                          )
                        )}
                        {isActive && (
                          <Button size="sm" variant="primary" icon={<PlayCircle className="h-3.5 w-3.5" />} onClick={(e) => {
                            e.stopPropagation()
                            navigate(`/arena/${rowId}`)
                          }}>
                            观战
                          </Button>
                        )}
                        {canEnd && (
                          <Button size="sm" variant="warning" icon={isActive ? <Square className="h-3.5 w-3.5" /> : <Trash2 className="h-3.5 w-3.5" />} loading={endingMatchId === rowId} onClick={(e) => handleEndMatch(e, rowId, isActive)}>
                            {endingMatchId === rowId ? '处理中' : isActive ? '结束' : '清理'}
                          </Button>
                        )}
                        {/* 永久删除（不可恢复） */}
                        {isTerminalStatus(match.status) && (
                          <Button
                            size="sm"
                            variant="danger"
                            icon={<Trash2 className="h-3.5 w-3.5" />}
                            loading={deletingMatchId === rowId}
                            onClick={(e) => handleDeleteMatch(e, rowId, match.name)}
                          >
                            {deletingMatchId === rowId ? '删除中' : '删除'}
                          </Button>
                        )}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>

        {!loading && filteredMatches.length === 0 && (
          <div className="py-12 text-center text-sm text-slate-500">暂无比赛数据</div>
        )}
        {loading && filteredMatches.length === 0 && (
          <div className="py-12 text-center text-sm text-slate-500">正在加载历史比赛...</div>
        )}
      </Panel>

      {deleteTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 p-4 backdrop-blur-sm">
          <Panel className="w-full max-w-lg border-rose-500/40 bg-slate-950 shadow-2xl shadow-black/40">
            <div className="mb-4 flex items-start justify-between gap-4">
              <div className="flex items-start gap-3">
                <div className="rounded-md border border-rose-500/40 bg-rose-950/50 p-2 text-rose-200">
                  <AlertTriangle className="h-5 w-5" />
                </div>
                <div>
                  <h2 className="text-lg font-semibold text-slate-100">永久删除比赛</h2>
                  <p className="mt-1 text-sm text-slate-400">这个操作会删除比赛记录与相关运行痕迹，无法撤销。</p>
                </div>
              </div>
              <Button
                size="sm"
                variant="ghost"
                icon={<X className="h-4 w-4" />}
                onClick={() => setDeleteTarget(null)}
                aria-label="关闭删除确认"
                disabled={deletingMatchId === deleteTarget.matchId}
              />
            </div>
            <div className="rounded-md border border-slate-800 bg-slate-900/70 p-3">
              <div className="text-xs font-medium uppercase tracking-wider text-slate-500">目标比赛</div>
              <div className="mt-1 break-all font-mono text-sm text-slate-200">{deleteTarget.label}</div>
            </div>
            <div className="mt-4 grid gap-2 text-sm text-slate-300">
              <div className="flex items-center gap-2"><StatusBadge tone="danger">删除</StatusBadge>比赛记录与所有事件</div>
              <div className="flex items-center gap-2"><StatusBadge tone="danger">删除</StatusBadge>提交/投放记录</div>
              <div className="flex items-center gap-2"><StatusBadge tone="warning">清理</StatusBadge>仍在运行时会先销毁容器资源</div>
            </div>
            <div className="mt-5 flex flex-wrap justify-end gap-2">
              <Button variant="secondary" onClick={() => setDeleteTarget(null)} disabled={deletingMatchId === deleteTarget.matchId}>
                取消
              </Button>
              <Button
                variant="danger"
                icon={<Trash2 className="h-4 w-4" />}
                onClick={confirmDeleteMatch}
                loading={deletingMatchId === deleteTarget.matchId}
              >
                {deletingMatchId === deleteTarget.matchId ? '删除中' : '确认删除'}
              </Button>
            </div>
          </Panel>
        </div>
      )}
    </div>
  )
}

export default HistoryPage
