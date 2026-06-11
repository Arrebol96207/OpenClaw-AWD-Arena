import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Eye, RefreshCw, RotateCcw, Square } from 'lucide-react'
import { API_BASE, fetchApi, fetchJson, readApiError } from '../api'
import { Button, ErrorBanner, Panel, StatusBadge, cx } from '../components/ui'
import { useProtectedApiAccess } from '../hooks/useProtectedApiAccess'

type LoopRow = {
  loop_id: string
  status: string
  name: string
  mode?: string
  repeat_count: number
  current_iteration: number
  completed_runs: number
  current_match_id?: string | null
  current_match_status?: string | null
  last_match_id?: string | null
  last_match_status?: string | null
  created_at?: string
  updated_at?: string
  stopped_at?: string | null
}

const modeLabel = (m?: string): string => m === 'werewolf' ? '狼人杀' : m === 'awd' ? 'AWD' : (m || '-')
const modeTone = (m?: string): 'neutral' | 'info' | 'success' | 'warning' | 'danger' =>
  m === 'werewolf' ? 'warning' : 'info'


const statusTone = (status: string): 'neutral' | 'info' | 'success' | 'warning' | 'danger' => {
  if (status === 'completed') return 'success'
  if (status === 'stopped') return 'warning'
  if (status === 'running') return 'info'
  if (status === 'error') return 'danger'
  return 'neutral'
}

const formatTime = (value?: string | null): string => {
  if (!value) return '-'
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? date.toLocaleString() : value
}

const LoopMatchesPage: React.FC = () => {
  const navigate = useNavigate()
  const protectedApi = useProtectedApiAccess()
  const [loops, setLoops] = useState<LoopRow[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [stoppingLoopId, setStoppingLoopId] = useState<string | null>(null)

  const loadLoops = async (showLoading = true) => {
    if (!protectedApi.ready) {
      setLoops([])
      setError(null)
      if (showLoading) setLoading(false)
      return
    }
    if (showLoading) setLoading(true)
    setError(null)
    try {
      const data = await fetchJson<{ loops?: LoopRow[] } | LoopRow[]>(`${API_BASE}/api/loops`)
      setLoops(Array.isArray(data) ? data : data.loops ?? [])
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '加载循环比赛失败')
    } finally {
      if (showLoading) setLoading(false)
    }
  }

  useEffect(() => {
    if (protectedApi.loading) return
    if (!protectedApi.ready) {
      setLoops([])
      setError(null)
      setLoading(false)
      return
    }

    loadLoops()
    const timer = window.setInterval(() => loadLoops(false), 5000)
    return () => window.clearInterval(timer)
  }, [protectedApi.loading, protectedApi.ready])

  const stopLoop = async (loopId: string) => {
    setStoppingLoopId(loopId)
    setError(null)
    try {
      const response = await fetchApi(`${API_BASE}/api/loops/${loopId}/stop`, { method: 'POST' })
      if (!response.ok) throw new Error(await readApiError(response))
      await loadLoops(false)
    } catch (stopError) {
      setError(stopError instanceof Error ? stopError.message : '停止循环比赛失败')
    } finally {
      setStoppingLoopId(null)
    }
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-col justify-between gap-4 lg:flex-row lg:items-end">
        <div>
          <div className="text-xs font-semibold uppercase tracking-wider text-cyan-300">Automation</div>
          <h1 className="mt-1 text-2xl font-semibold text-white">循环比赛</h1>
          <p className="mt-1 text-sm text-slate-400">追踪连续赛程进度，必要时停止后续自动开赛。</p>
        </div>
        <Button
          variant="secondary"
          icon={<RefreshCw className={cx('h-4 w-4', loading && 'animate-spin')} />}
          disabled={loading}
          onClick={() => loadLoops()}
        >
          刷新
        </Button>
      </div>

      <ErrorBanner message={!protectedApi.loading && !protectedApi.ready ? protectedApi.message : null} />
      <ErrorBanner message={error} />

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {loops.map((loop) => {
          const progressPercent = loop.repeat_count > 0 ? Math.min(100, (loop.completed_runs / loop.repeat_count) * 100) : 0
          const isLoopRunning = loop.status === 'running'
          const hasRecordedCurrentMatch = Boolean(loop.current_match_id)
          const activeLabel = isLoopRunning && loop.current_match_id
            ? `第 ${loop.current_iteration} / ${loop.repeat_count} 场 · ${loop.current_match_status ?? 'running'}`
            : loop.status === 'stopped'
              ? `已停止 · 已完成 ${loop.completed_runs} / ${loop.repeat_count} 场`
              : `已完成 ${loop.completed_runs} / ${loop.repeat_count} 场`
          const currentMatchLabel = isLoopRunning ? loop.current_match_id ?? '无' : '无'
          const recordedMatchLabel = loop.last_match_id ?? (!isLoopRunning && loop.current_match_id ? loop.current_match_id : null)
          const recordedMatchTitle = loop.last_match_id ? '上一场比赛' : hasRecordedCurrentMatch && !isLoopRunning ? '停止时比赛' : '上一场比赛'

          return (
            <Panel key={loop.loop_id} className="space-y-4">
              <div className="-mt-2"><StatusBadge tone={modeTone(loop.mode)}>{modeLabel(loop.mode)}</StatusBadge></div>
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <h2 className="truncate text-lg font-semibold text-white">{loop.name}</h2>
                    <StatusBadge tone={statusTone(loop.status)}>{loop.status}</StatusBadge>
                  </div>
                  <div className="mt-1 font-mono text-xs text-slate-500">{loop.loop_id}</div>
                  <div className="mt-2 text-sm text-slate-300">{activeLabel}</div>
                </div>
                {loop.status === 'running' && (
                  <Button
                    size="sm"
                    variant="warning"
                    icon={<Square className="h-3.5 w-3.5" />}
                    disabled={stoppingLoopId === loop.loop_id}
                    onClick={() => stopLoop(loop.loop_id)}
                  >
                    {stoppingLoopId === loop.loop_id ? '停止中' : '停止后续'}
                  </Button>
                )}
              </div>

              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs text-slate-400">
                  <span>进度</span>
                  <span>{loop.completed_runs} / {loop.repeat_count}</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-slate-800">
                  <div className="h-full rounded-full bg-cyan-400 transition-all duration-300" style={{ width: `${progressPercent}%` }} />
                </div>
              </div>

              <dl className="grid grid-cols-1 gap-3 text-sm md:grid-cols-2">
                <div className="rounded-md border border-slate-800 bg-slate-950/50 p-3">
                  <dt className="text-xs uppercase tracking-wider text-slate-500">当前比赛</dt>
                  <dd className="mt-1 break-all text-slate-100">{currentMatchLabel}</dd>
                </div>
                <div className="rounded-md border border-slate-800 bg-slate-950/50 p-3">
                  <dt className="text-xs uppercase tracking-wider text-slate-500">{recordedMatchTitle}</dt>
                  <dd className="mt-1 break-all text-slate-100">{recordedMatchLabel ?? '无'}</dd>
                </div>
                <div className="rounded-md border border-slate-800 bg-slate-950/50 p-3">
                  <dt className="text-xs uppercase tracking-wider text-slate-500">创建时间</dt>
                  <dd className="mt-1 text-slate-100">{formatTime(loop.created_at)}</dd>
                </div>
                <div className="rounded-md border border-slate-800 bg-slate-950/50 p-3">
                  <dt className="text-xs uppercase tracking-wider text-slate-500">最近更新</dt>
                  <dd className="mt-1 text-slate-100">{formatTime(loop.updated_at)}</dd>
                </div>
              </dl>

              <div className="flex flex-wrap gap-2">
                {isLoopRunning && loop.current_match_id && (
                  <Button size="sm" variant="primary" icon={<Eye className="h-3.5 w-3.5" />} onClick={() => navigate(`/arena/${loop.current_match_id}`)}>
                    当前观战
                  </Button>
                )}
                {loop.last_match_id && (
                  <Button size="sm" variant="secondary" icon={<RotateCcw className="h-3.5 w-3.5" />} onClick={() => navigate(`/replay/${loop.last_match_id}`)}>
                    上一场回放
                  </Button>
                )}
              </div>
            </Panel>
          )
        })}
      </section>

      {!loading && loops.length === 0 && (
        <Panel className="border-dashed py-12 text-center text-sm text-slate-500">
          暂无循环比赛。到配置大厅把循环次数设为大于 1 后开始比赛。
        </Panel>
      )}

      {loading && loops.length === 0 && (
        <Panel className="py-12 text-center text-sm text-slate-500">
          正在加载循环比赛信息...
        </Panel>
      )}
    </div>
  )
}

export default LoopMatchesPage
