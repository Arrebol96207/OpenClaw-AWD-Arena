import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { API_BASE, fetchApi } from '../api'

type LoopRow = {
  loop_id: string
  status: string
  name: string
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

const statusClassName = (status: string): string => {
  if (status === 'completed') return 'bg-emerald-900/50 text-emerald-300'
  if (status === 'stopped') return 'bg-amber-900/50 text-amber-300'
  return 'bg-cyan-900/50 text-cyan-300'
}

const LoopMatchesPage: React.FC = () => {
  const navigate = useNavigate()
  const [loops, setLoops] = useState<LoopRow[]>([])
  const [loading, setLoading] = useState(true)
  const [stoppingLoopId, setStoppingLoopId] = useState<string | null>(null)

  const loadLoops = async () => {
    setLoading(true)
    try {
      const response = await fetchApi(`${API_BASE}/api/loops`)
      const data = await response.json()
      setLoops(Array.isArray(data) ? data : data.loops ?? [])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadLoops()
    const timer = window.setInterval(loadLoops, 5000)
    return () => window.clearInterval(timer)
  }, [])

  const stopLoop = async (loopId: string) => {
    setStoppingLoopId(loopId)
    try {
      const response = await fetchApi(`${API_BASE}/api/loops/${loopId}/stop`, { method: 'POST' })
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`)
      }
      await loadLoops()
    } catch (error) {
      alert(`停止循环比赛失败：${error instanceof Error ? error.message : '未知错误'}`)
    } finally {
      setStoppingLoopId(null)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold">循环比赛管理</h2>
          <p className="mt-1 text-sm text-slate-400">查看循环比赛进度，并在需要时停止后续自动开赛。</p>
        </div>
        <button className="rounded-md bg-slate-700 px-3 py-2 text-sm hover:bg-slate-600" onClick={loadLoops}>刷新</button>
      </div>

      <section className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {loops.map((loop) => {
          const progressPercent = loop.repeat_count > 0 ? Math.min(100, (loop.completed_runs / loop.repeat_count) * 100) : 0
          const activeLabel = loop.current_match_id
            ? `第 ${loop.current_iteration} / ${loop.repeat_count} 场 · ${loop.current_match_status ?? 'running'}`
            : `已完成 ${loop.completed_runs} / ${loop.repeat_count} 场`

          return (
            <article key={loop.loop_id} className="rounded-xl border border-slate-700 bg-slate-800/60 p-5 shadow-lg shadow-black/10">
              <div className="flex items-start justify-between gap-4">
                <div className="space-y-2">
                  <div className="flex items-center gap-3">
                    <h3 className="text-lg font-semibold text-slate-100">{loop.name}</h3>
                    <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${statusClassName(loop.status)}`}>{loop.status}</span>
                  </div>
                  <div className="text-xs text-slate-400">循环 ID：{loop.loop_id}</div>
                  <div className="text-sm text-slate-300">{activeLabel}</div>
                </div>
                {loop.status === 'running' && (
                  <button
                    className="rounded-md bg-amber-700 px-3 py-2 text-xs font-medium text-white hover:bg-amber-600 disabled:opacity-50"
                    onClick={() => stopLoop(loop.loop_id)}
                    disabled={stoppingLoopId === loop.loop_id}
                  >
                    {stoppingLoopId === loop.loop_id ? '停止中...' : '停止后续比赛'}
                  </button>
                )}
              </div>

              <div className="mt-4 space-y-2">
                <div className="flex items-center justify-between text-xs text-slate-400">
                  <span>进度</span>
                  <span>{loop.completed_runs} / {loop.repeat_count}</span>
                </div>
                <div className="h-2 overflow-hidden rounded-full bg-slate-700">
                  <div className="h-full rounded-full bg-cyan-500 transition-all" style={{ width: `${progressPercent}%` }} />
                </div>
              </div>

              <dl className="mt-4 grid grid-cols-1 gap-3 text-sm text-slate-300 md:grid-cols-2">
                <div className="rounded-md border border-slate-700 bg-slate-900/40 p-3">
                  <dt className="text-xs uppercase tracking-wide text-slate-500">当前比赛</dt>
                  <dd className="mt-1 text-slate-100">{loop.current_match_id ?? '无'}</dd>
                </div>
                <div className="rounded-md border border-slate-700 bg-slate-900/40 p-3">
                  <dt className="text-xs uppercase tracking-wide text-slate-500">上一场比赛</dt>
                  <dd className="mt-1 text-slate-100">{loop.last_match_id ?? '无'}</dd>
                </div>
                <div className="rounded-md border border-slate-700 bg-slate-900/40 p-3">
                  <dt className="text-xs uppercase tracking-wide text-slate-500">创建时间</dt>
                  <dd className="mt-1 text-slate-100">{loop.created_at ? new Date(loop.created_at).toLocaleString() : '-'}</dd>
                </div>
                <div className="rounded-md border border-slate-700 bg-slate-900/40 p-3">
                  <dt className="text-xs uppercase tracking-wide text-slate-500">最近更新</dt>
                  <dd className="mt-1 text-slate-100">{loop.updated_at ? new Date(loop.updated_at).toLocaleString() : '-'}</dd>
                </div>
              </dl>

              <div className="mt-4 flex flex-wrap gap-2">
                {loop.current_match_id && (
                  <button
                    className="rounded-md bg-cyan-600 px-3 py-2 text-xs font-medium text-white hover:bg-cyan-500"
                    onClick={() => navigate(`/arena/${loop.current_match_id}`)}
                  >
                    进入当前观战
                  </button>
                )}
                {loop.last_match_id && (
                  <button
                    className="rounded-md bg-slate-700 px-3 py-2 text-xs font-medium text-white hover:bg-slate-600"
                    onClick={() => navigate(`/replay/${loop.last_match_id}`)}
                  >
                    查看上一场回放
                  </button>
                )}
              </div>
            </article>
          )
        })}
      </section>

      {!loading && loops.length === 0 && (
        <div className="rounded-xl border border-dashed border-slate-700 bg-slate-800/30 px-6 py-12 text-center text-slate-400">
          暂无循环比赛。前往“配置大厅”将循环次数设置为大于 1 后开始比赛。
        </div>
      )}

      {loading && (
        <div className="rounded-xl border border-slate-700 bg-slate-800/30 px-6 py-12 text-center text-slate-400">
          正在加载循环比赛信息...
        </div>
      )}
    </div>
  )
}

export default LoopMatchesPage
