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
    <div className="flex h-screen overflow-hidden bg-[#13131f] text-[#D3D3D3]">
      {/* Sidebar */}
      <aside className="flex w-[200px] flex-col bg-[#1a1a2e] border-r border-[#2a2a3a]">
        {/* Logo */}
        <div className="flex h-14 items-center gap-2.5 px-5 border-b border-[#2a2a3a]">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#B19CD9]/20">
            <Swords className="h-4 w-4 text-[#B19CD9]" />
          </div>
          <div>
            <span className="text-sm font-bold text-white">OpenClaw</span>
            <span className="block text-[10px] text-[#B19CD9]/70 -mt-0.5">AWD Arena</span>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 space-y-1 px-3 pt-4">
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
                    ? 'bg-[#B19CD9]/15 text-[#B19CD9] font-medium'
                    : 'text-[#6a6a8a] hover:bg-[#2a2a3a] hover:text-[#D3D3D3]',
                )}
              >
                <Icon className="h-4 w-4" />
                {item.label}
              </Link>
            )
          })}
        </nav>

        {/* Footer */}
        <div className="border-t border-[#2a2a3a] px-4 py-3">
          <div className="flex items-center gap-2">
            <div className={cx(
              'h-2 w-2 rounded-full',
              health === 'online' ? 'bg-emerald-400' 
                : health === 'offline' ? 'bg-red-400' 
                : 'bg-[#6a6a8a]',
            )} />
            <span className="text-xs text-[#8888aa]">
              {health === 'online' ? '在线' : health === 'offline' ? '离线' : '...'}
            </span>
            {healthInfo?.active_matches ? (
              <span className="text-xs text-[#B19CD9] ml-1">{healthInfo.active_matches} 场</span>
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
              className="mt-2 w-full rounded-md border border-[#2a2a3a] bg-[#16162a] px-2.5 py-1.5 text-xs text-[#D3D3D3] placeholder:text-[#4a4a6a] focus:border-[#B19CD9]/50 focus:outline-none"
            />
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto bg-[#13131f]">
        <div className="mx-auto max-w-[900px] px-8 py-6">
          {children}
        </div>
      </main>
    </div>
  )
}

export default Layout
