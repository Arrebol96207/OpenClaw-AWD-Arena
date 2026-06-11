import React, { Suspense } from 'react'
import type { TopologyMapProps } from './TopologyMapCanvas'

const TopologyMapCanvas = React.lazy(() => import('./TopologyMapCanvas'))

const TopologyLoading: React.FC = () => (
  <div className="absolute inset-0 flex items-center justify-center">
    <div className="w-48 space-y-3">
      <div className="mx-auto h-12 w-12 animate-pulse rounded-full border border-cyan-500/40 bg-cyan-950/50" />
      <div className="h-2 animate-pulse rounded-full bg-slate-700" />
      <div className="mx-auto h-2 w-2/3 animate-pulse rounded-full bg-slate-800" />
    </div>
  </div>
)

const TopologyMap: React.FC<TopologyMapProps> = (props) => (
  <div className="relative h-full min-h-[300px] w-full rounded-md border border-slate-700 bg-slate-900/50">
    <Suspense fallback={<TopologyLoading />}>
      <TopologyMapCanvas {...props} />
    </Suspense>
    <div className="pointer-events-none absolute left-2 top-2 flex gap-2">
      <div className="flex items-center gap-1">
        <span className="inline-block h-3 w-3 rounded-full bg-cyan-500" />
        <span className="font-mono text-xs text-slate-300">Agent</span>
      </div>
      <div className="flex items-center gap-1">
        <span className="inline-block h-3 w-3 rounded-full bg-emerald-500" />
        <span className="font-mono text-xs text-slate-300">Target</span>
      </div>
    </div>
  </div>
)

export default TopologyMap
