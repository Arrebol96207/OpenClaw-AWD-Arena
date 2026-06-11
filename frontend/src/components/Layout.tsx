import React from 'react'
import { Link, useLocation } from 'react-router-dom'
import { CalendarClock, History, Plus, Swords } from 'lucide-react'
import { useAppStatus } from '../contexts/AppStatusContext'
import { cx, inputClassName } from './ui'

const navItems = [
  { path: '/config', label: '新建比赛', icon: Plus },
  { path: '/history', label: '历史比赛', icon: History },
  { path: '/loops', label: '循环比赛', icon: CalendarClock },
]

const Layout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const location = useLocation()
  const { apiKey, setApiKey, health, healthInfo, authState } = useAppStatus()
  const isDevAuth = authState === 'dev'

  const active = (path: string) => location.pathname.startsWith(path)

  return (
    <div className="flex h-screen overflow-hidden bg-neutral-950 text-neutral-100">
      {/* Sidebar - Linear style */}
      <aside className="flex w-[220px] flex-col bg-neutral-950 border-r border-neutral-900">
        {/* Logo */}
        <div className="flex h-12 items-center gap-2.5 px-4">
          <Swords className="h-4 w-4 text-neutral-500" />
          <span className="text-sm font-semibold text-neutral-200">OpenClaw</span>
        </div>

        {/* Nav */}
        <nav className="flex-1 space-y-0.5 px-2 pt-2">
          {navItems.map((item) => {
            const Icon = item.icon
            return (
              <Link
                key={item.path}
                to={item.path}
                className={cx(
                  'flex items-center gap-2 rounded-md px-2.5 py-1.5 text-[13px] transition-colors',
                  active(item.path)
                    ? 'bg-neutral-800/80 text-neutral-100 font-medium'
                    : 'text-neutral-500 hover:bg-neutral-900 hover:text-neutral-300',
                )}
              >
                <Icon className="h-3.5 w-3.5" />
                {item.label}
              </Link>
            )
          })}
        </nav>

        {/* Footer - Linear style */}
        <div className="border-t border-neutral-900 px-3 py-3">
          <div className="flex items-center gap-2 px-1">
            <div className={cx(
              'h-1.5 w-1.5 rounded-full',
              health === 'online' ? 'bg-emerald-500' : health === 'offline' ? 'bg-red-500' : 'bg-neutral-600',
            )} />
            <span className="text-xs text-neutral-600">
              {health === 'online' ? '在线' : health === 'offline' ? '离线' : '...'}
            </span>
            {healthInfo?.active_matches ? (
              <span className="text-xs text-neutral-700">{healthInfo.active_matches} 场进行中</span>
            ) : null}
          </div>
          {!isDevAuth && (
            <input
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="API Key"
              aria-label="Referee API Key"
              autoComplete="off"
              className="mt-2 w-full rounded-md border border-neutral-800 bg-neutral-900 px-2.5 py-1.5 text-xs text-neutral-300 placeholder:text-neutral-700 focus:border-neutral-600 focus:outline-none"
            />
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto">
        <div className="mx-auto max-w-[960px] px-8 py-6">
          {children}
        </div>
      </main>
    </div>
  )
}

export default Layout
