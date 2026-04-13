import React from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from './Sidebar.jsx'

const PAGE_TITLES = {
  '/': 'Overview',
  '/projects': 'Projects',
  '/tickets': 'Tickets',
  '/agents': 'Agents',
  '/security': 'Security',
  '/settings': 'Settings',
  '/logs': 'Logs',
}

export default function Layout() {
  const location = useLocation()
  const title = PAGE_TITLES[location.pathname] ?? 'Sentinel'

  return (
    <div className="flex h-screen overflow-hidden bg-[#0a0e27]">
      <Sidebar />

      {/* Main content */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* Top bar */}
        <header className="flex-shrink-0 h-14 flex items-center justify-between px-6 border-b border-blue-500/10 bg-[#0a0e27]/80 backdrop-blur-sm">
          <div className="flex items-center gap-3">
            <h1 className="text-[15px] font-semibold text-slate-100 tracking-tight">{title}</h1>
          </div>

          <div className="flex items-center gap-3">
            {/* Live indicator */}
            <div className="flex items-center gap-1.5 text-xs text-slate-500">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse-slow" />
              Live
            </div>

            {/* Version badge */}
            <span className="text-xs font-medium text-blue-400/70 bg-blue-500/8 border border-blue-500/15 px-2 py-0.5 rounded-full">
              v1.0.0
            </span>
          </div>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-y-auto">
          <div className="page-enter">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  )
}
