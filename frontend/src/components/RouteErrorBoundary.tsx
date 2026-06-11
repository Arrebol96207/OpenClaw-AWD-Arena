import React from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'
import { Button, Panel } from './ui'

type RouteErrorBoundaryState = {
  error: Error | null
}

type RouteErrorBoundaryProps = React.PropsWithChildren<{
  resetKey?: string
}>

class RouteErrorBoundary extends React.Component<RouteErrorBoundaryProps, RouteErrorBoundaryState> {
  state: RouteErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): RouteErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    if (import.meta.env.DEV) {
      // Keep a concise breadcrumb during local development without polluting production consoles.
      console.error('Route render failed', error, info.componentStack)
    }
  }

  componentDidUpdate(previousProps: RouteErrorBoundaryProps) {
    if (this.state.error && previousProps.resetKey !== this.props.resetKey) {
      this.setState({ error: null })
    }
  }

  render() {
    if (!this.state.error) return this.props.children

    return (
      <Panel className="mx-auto max-w-2xl border-amber-500/40 bg-slate-950/80" role="alert" aria-live="assertive">
        <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
          <div className="inline-flex h-10 w-10 shrink-0 items-center justify-center rounded-md border border-amber-500/40 bg-amber-950/50 text-amber-200">
            <AlertTriangle className="h-5 w-5" />
          </div>
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold text-slate-100">页面加载失败</h2>
            <p className="mt-1 text-sm leading-6 text-slate-400">
              前端资源可能刚刚更新，当前浏览器还拿着旧入口。刷新后会重新拉取最新控制台资源。
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <Button variant="primary" icon={<RefreshCw className="h-4 w-4" />} onClick={() => window.location.reload()}>
                重新加载
              </Button>
              <Button variant="secondary" onClick={() => this.setState({ error: null })}>
                留在当前页
              </Button>
            </div>
          </div>
        </div>
      </Panel>
    )
  }
}

export default RouteErrorBoundary
