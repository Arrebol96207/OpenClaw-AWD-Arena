import React from 'react'
import { Link, useLocation } from 'react-router-dom'
import { Activity, CalendarClock, History, Key, RefreshCw, Swords, Wifi } from 'lucide-react'
import { useAppStatus } from '../contexts/AppStatusContext'
import { Button, StatusBadge, cx, inputClassName } from './ui'

const navItems = [
  { path: '/config', label: '配置', icon: Swords },
  { path: '/history', label: '历史', icon: History },
  { path: '/loops', label: '循环', icon: CalendarClock },
]

const Layout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const location = useLocation()
  const { apiKey, setApiKey, health, healthInfo, authState } = useAppStatus()
  const isDevAuth = authState === 'dev'

  const active = (path: string) => location.pathname.startsWith(path)

  const handleApiKeyChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setApiKey(e.target.value)
  }

  return (
    <div className="min-h-screen bg-[#070b12] text-slate-100">
      <div className="relative min-h-screen">
        <header className="sticky top-0 z-20 border-b border-slate-800 bg-slate-950/95 backdrop-blur">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-2.5">
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-2">
                <Activity className="h-5 w-5 text-cyan-400" />
                <span className="text-sm font-semibold text-cyan-200">OpenClaw</span>
              </div>

              <nav className="flex items-center gap-1">
                {navItems.map((item) => {
                  const Icon = item.icon
                  return (
                    <Link
                      key={item.path}
                      to={item.path}
                      className={cx(
                        'inline-flex items-center gap-1.5 rounded px-2.5 py-1.5 text-sm transition',
                        active(item.path)
                          ? 'bg-cyan-500/15 text-cyan-100'
                          : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200',
                      )}
                    >
                      <Icon className="h-3.5 w-3.5" />
                      {item.label}
                    </Link>
                  )
                })}
              </nav>
            </div>

            <div className="flex items-center gap-2">
              <div
                className="flex items-center gap-1.5 rounded px-2 py-1 text-xs"
                title={
                  healthInfo
                    ? [
                        `活跃: ${healthInfo.active_matches ?? 0}`,
                        `历史: ${healthInfo.loaded_matches ?? 0}`,
                        `WS: ${healthInfo.ws_connections ?? 0}`,
                        healthInfo.version ? `v${healthInfo.version}` : '',
                      ].filter(Boolean).join(' | ')
                    : undefined
                }
              >
                <div className={cx(
                  'h-2 w-2 rounded-full',
                  health === 'online' ? 'bg-emerald-400' : health === 'offline' ? 'bg-rose-400' : 'bg-amber-400',
                )} />
                <span className="text-slate-400">
                  {health === 'online' ? '在线' : health === 'offline' ? '离线' : '...'}
                </span>
                {healthInfo?.active_matches ? (
                  <span className="text-cyan-400">{healthInfo.active_matches}</span>
                ) : null}
              </div>

              {!isDevAuth && (
                <input
                  type="password"
                  value={apiKey}
                  onChange={handleApiKeyChange}
                  placeholder="API Key"
                  aria-label="Referee API Key"
                  autoComplete="off"
                  className={cx(inputClassName, 'h-7 w-32 text-xs')}
                />
              )}

              <Button
                variant="ghost"
                size="sm"
                icon={<RefreshCw className="h-3.5 w-3.5" />}
                onClick={() => window.location.reload()}
                aria-label="刷新"
              />
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-6xl px-4 py-4">{children}</main>
      </div>
    </div>
  )
}

export default Layout
