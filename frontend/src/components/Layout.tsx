import React from 'react'
import { Link, useLocation } from 'react-router-dom'
import { CalendarClock, History, Plus, Swords } from 'lucide-react'
import { useAppStatus } from '../contexts/AppStatusContext'
import { cx } from './ui'

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
    <div className="flex h-screen overflow-hidden bg-slate-900 text-slate-200">
      {/* Sidebar */}
      <aside className="flex w-[200px] flex-col bg-slate-950 border-r border-slate-800">
        {/* Logo */}
        <div className="flex h-14 items-center gap-2.5 px-5 border-b border-slate-800">
          <Swords className="h-5 w-5 text-indigo-400" />
          <span className="text-sm font-bold text-slate-100">OpenClaw</span>
        </div>

        {/* Nav */}
        <nav className="flex-1 space-y-0.5 px-3 pt-4">
          {navItems.map((item) => {
            const Icon = item.icon
            const isActive = active(item.path)
            return (
              <Link
                key={item.path}
                to={item.path}
                className={cx(
                  'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors',
                  isActive
                    ? 'bg-indigo-500/15 text-indigo-300 font-medium'
                    : 'text-slate-400 hover:bg-slate-800 hover:text-slate-200',
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </Link>
            )
          })}
        </nav>

        {/* Footer */}
        <div className="border-t border-slate-800 px-4 py-3">
          <div className="flex items-center gap-2">
            <div className={cx(
              'h-2 w-2 rounded-full',
              health === 'online' ? 'bg-emerald-400' 
                : health === 'offline' ? 'bg-red-400' 
                : 'bg-slate-500',
            )} />
            <span className="text-xs text-slate-400">
              {health === 'online' ? '在线' : health === 'offline' ? '离线' : '...'}
            </span>
            {healthInfo?.active_matches ? (
              <span className="text-xs text-slate-500">· {healthInfo.active_matches} 场</span>
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
              className="mt-2 w-full rounded-md border border-slate-800 bg-slate-900 px-2.5 py-1.5 text-xs text-slate-300 placeholder:text-slate-600 focus:border-indigo-500/50 focus:outline-none focus:ring-1 focus:ring-indigo-500/20"
            />
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto bg-slate-900">
        <div className="mx-auto max-w-[900px] px-8 py-6">
          {children}
        </div>
      </main>
    </div>
  )
}

export default Layout
