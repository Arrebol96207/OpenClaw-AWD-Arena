import React from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Activity, CalendarClock, History, Key, RefreshCw, Swords, Wifi } from 'lucide-react'
import { useAppStatus } from '../contexts/AppStatusContext'
import { Button, StatusBadge, cx, inputClassName } from './ui'

const navItems = [
  { path: '/config', label: '配置大厅', icon: Swords },
  { path: '/history', label: '历史比赛', icon: History },
  { path: '/loops', label: '循环比赛', icon: CalendarClock },
]

const orchestratorLabel = (mode?: string): string => {
  if (mode === 'embedded') return '内置编排'
  if (mode === 'external_container_management') return '外部容器'
  return mode || '未知模式'
}

const healthAuthLabel = (mode?: string): string => {
  if (mode === 'dev_no_auth') return '本地免密'
  if (mode === 'api_key') return 'Key 模式'
  if (mode === 'unconfigured') return '未配置鉴权'
  return mode || '鉴权未知'
}

const exposureLabel = (mode?: string): string => {
  if (mode === 'local_only') return '仅本机'
  if (mode === 'shared_network') return '共享监听'
  if (mode === 'mixed') return '混合监听'
  return mode || '监听未知'
}

const authModeTone = (
  authMode?: string,
  exposure?: string,
): 'neutral' | 'info' | 'success' | 'warning' | 'danger' => {
  if (authMode === 'api_key') return 'success'
  if (authMode === 'dev_no_auth') {
    return exposure === 'local_only' ? 'success' : 'warning'
  }
  if (authMode === 'unconfigured') return 'danger'
  return 'neutral'
}

const Layout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const location = useLocation()
  const { apiKey, setApiKey, health, healthInfo, authState, authDetail } = useAppStatus()
  const isDevAuth = authState === 'dev'

  const active = (path: string) => location.pathname.startsWith(path)

  const handleApiKeyChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = e.target.value
    setApiKey(value)
  }

  return (
    <div className="min-h-screen bg-[#070b12] text-slate-100">
      <div className="pointer-events-none fixed inset-0 bg-[linear-gradient(180deg,rgba(15,23,42,0.94),rgba(2,6,23,0.98)),linear-gradient(90deg,rgba(34,211,238,0.04)_1px,transparent_1px),linear-gradient(180deg,rgba(34,211,238,0.035)_1px,transparent_1px)] bg-[size:auto,36px_36px,36px_36px]" />
      <div className="relative min-h-screen">
        <header className="sticky top-0 z-20 border-b border-slate-800/90 bg-slate-950/90 backdrop-blur">
          <div className="mx-auto flex max-w-7xl flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-md border border-cyan-400/40 bg-cyan-950/50">
                <Activity className="h-5 w-5 text-cyan-300" />
              </div>
              <div>
                <div className="text-sm font-semibold uppercase tracking-wider text-cyan-200">OpenClaw AWD Arena</div>
                <div className="text-xs text-slate-500">电竞赛事控制台</div>
              </div>
            </div>

            <nav className="flex flex-wrap items-center gap-2">
              {navItems.map((item) => {
                const Icon = item.icon
                return (
                  <Link
                    key={item.path}
                    to={item.path}
                    className={cx(
                      'inline-flex cursor-pointer items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition duration-200 focus:outline-none focus:ring-2 focus:ring-cyan-400/30',
                      active(item.path)
                        ? 'bg-cyan-500/15 text-cyan-100 ring-1 ring-cyan-400/40'
                        : 'text-slate-300 hover:bg-slate-800 hover:text-white',
                    )}
                  >
                    <Icon className="h-4 w-4" />
                    {item.label}
                  </Link>
                )
              })}
            </nav>

            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center sm:justify-end">
              <StatusBadge tone={health === 'online' ? 'success' : health === 'offline' ? 'danger' : 'warning'}>
                <Wifi className="h-3.5 w-3.5" />
                {health === 'online' ? 'API 在线' : health === 'offline' ? 'API 离线' : '检测中'}
              </StatusBadge>
              {healthInfo && (
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge tone={healthInfo.active_matches ? 'info' : 'neutral'}>
                    活跃 {healthInfo.active_matches ?? 0}
                  </StatusBadge>
                  <StatusBadge tone="neutral">
                    历史 {healthInfo.loaded_matches ?? 0}
                  </StatusBadge>
                  <StatusBadge tone="neutral">
                    WS {healthInfo.ws_connections ?? 0}
                  </StatusBadge>
                  <StatusBadge tone="info" title={healthInfo.orchestrator_mode}>
                    {orchestratorLabel(healthInfo.orchestrator_mode)}
                  </StatusBadge>
                  <StatusBadge
                    tone={authModeTone(healthInfo.auth_mode, healthInfo.deployment_exposure)}
                    title={healthInfo.auth_mode}
                  >
                    {healthAuthLabel(healthInfo.auth_mode)}
                  </StatusBadge>
                  <StatusBadge
                    tone={
                      healthInfo.deployment_exposure === 'local_only'
                        ? 'success'
                        : healthInfo.deployment_exposure === 'shared_network' || healthInfo.deployment_exposure === 'mixed'
                          ? 'warning'
                          : 'neutral'
                    }
                    title={healthInfo.deployment_exposure}
                  >
                    {exposureLabel(healthInfo.deployment_exposure)}
                  </StatusBadge>
                  {healthInfo.version && (
                    <StatusBadge tone="neutral">
                      v{healthInfo.version}
                    </StatusBadge>
                  )}
                </div>
              )}
              <StatusBadge
                tone={
                  authState === 'valid' || authState === 'dev'
                    ? 'success'
                    : authState === 'missing'
                      ? 'warning'
                      : authState === 'invalid'
                        ? 'danger'
                        : 'neutral'
                }
                title={authDetail}
              >
                <Key className="h-3.5 w-3.5" />
                {authState === 'valid'
                  ? 'Key 有效'
                  : authState === 'dev'
                    ? '开发免密'
                    : authState === 'missing'
                      ? 'Key 未填'
                      : authState === 'invalid'
                        ? 'Key 无效'
                        : 'Key 检测中'}
              </StatusBadge>
              {!isDevAuth && (
                <label className="flex min-w-0 items-center gap-2 text-sm text-slate-400">
                  <Key className="h-4 w-4 text-slate-500" />
                  <span className="sr-only">Referee API Key</span>
                  <input
                    type="password"
                    value={apiKey}
                    onChange={handleApiKeyChange}
                    placeholder="Referee API Key"
                    aria-label="Referee API Key"
                    autoComplete="off"
                    className={cx(inputClassName, 'h-9 w-full sm:w-56')}
                  />
                </label>
              )}
              <Button
                variant="ghost"
                size="sm"
                icon={<RefreshCw className="h-4 w-4" />}
                onClick={() => window.location.reload()}
                aria-label="刷新控制台"
              >
                刷新
              </Button>
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-7xl px-4 py-5">{children}</main>
      </div>
    </div>
  )
}

export default Layout
