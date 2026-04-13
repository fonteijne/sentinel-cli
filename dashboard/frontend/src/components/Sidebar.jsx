import React, { useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import {
  LayoutDashboard,
  FolderOpen,
  Ticket,
  Bot,
  Shield,
  Settings,
  ScrollText,
  ChevronLeft,
  ChevronRight,
  Activity,
} from 'lucide-react'
import clsx from 'clsx'

const NAV_ITEMS = [
  { to: '/', icon: LayoutDashboard, label: 'Overview', exact: true },
  { to: '/projects', icon: FolderOpen, label: 'Projects' },
  { to: '/tickets', icon: Ticket, label: 'Tickets' },
  { to: '/agents', icon: Bot, label: 'Agents' },
  { to: '/security', icon: Shield, label: 'Security' },
  { to: '/logs', icon: ScrollText, label: 'Logs' },
]

const BOTTOM_ITEMS = [
  { to: '/settings', icon: Settings, label: 'Settings' },
]

function SentinelLogo({ collapsed }) {
  return (
    <div className={clsx(
      'flex items-center gap-3 px-4 py-5 border-b transition-all duration-200',
      'border-blue-500/10'
    )}>
      {/* SVG Shield Logo */}
      <svg
        viewBox="0 0 36 40"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        className="w-9 h-9 flex-shrink-0"
        aria-label="Sentinel shield logo"
      >
        {/* Outer shield */}
        <path
          d="M18 2L3 8v14c0 9.5 6.5 16.5 15 18 8.5-1.5 15-8.5 15-18V8L18 2z"
          fill="url(#shieldGrad)"
          opacity="0.15"
        />
        <path
          d="M18 2L3 8v14c0 9.5 6.5 16.5 15 18 8.5-1.5 15-8.5 15-18V8L18 2z"
          stroke="url(#shieldStroke)"
          strokeWidth="1.5"
          fill="none"
        />
        {/* Inner S mark */}
        <path
          d="M22.5 14.5c0-1.66-1.34-3-3-3h-3.5c-1.38 0-2.5 1.12-2.5 2.5s1.12 2.5 2.5 2.5h3c1.66 0 3 1.34 3 3s-1.34 3-3 3H15"
          stroke="url(#sStroke)"
          strokeWidth="2"
          strokeLinecap="round"
        />
        {/* Eye/scan line */}
        <line
          x1="12"
          y1="20"
          x2="24"
          y2="20"
          stroke="#06b6d4"
          strokeWidth="0.75"
          strokeDasharray="2 2"
          opacity="0.5"
        />
        <defs>
          <linearGradient id="shieldGrad" x1="3" y1="2" x2="33" y2="40" gradientUnits="userSpaceOnUse">
            <stop stopColor="#3b82f6" />
            <stop offset="1" stopColor="#06b6d4" />
          </linearGradient>
          <linearGradient id="shieldStroke" x1="3" y1="2" x2="33" y2="40" gradientUnits="userSpaceOnUse">
            <stop stopColor="#3b82f6" />
            <stop offset="1" stopColor="#06b6d4" />
          </linearGradient>
          <linearGradient id="sStroke" x1="13" y1="11.5" x2="25" y2="30.5" gradientUnits="userSpaceOnUse">
            <stop stopColor="#60a5fa" />
            <stop offset="1" stopColor="#22d3ee" />
          </linearGradient>
        </defs>
      </svg>

      {!collapsed && (
        <div className="overflow-hidden">
          <div className="text-[15px] font-700 tracking-tight text-slate-100 font-bold leading-tight">
            Sentinel
          </div>
          <div className="text-[11px] font-medium text-blue-400/80 tracking-widest uppercase">
            Command Center
          </div>
        </div>
      )}
    </div>
  )
}

export default function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const location = useLocation()

  return (
    <aside
      className={clsx(
        'flex flex-col h-screen sticky top-0 flex-shrink-0 transition-all duration-200',
        'bg-[#080c22] border-r border-blue-500/10',
        collapsed ? 'w-[60px]' : 'w-[220px]'
      )}
    >
      <SentinelLogo collapsed={collapsed} />

      {/* System status indicator */}
      {!collapsed && (
        <div className="mx-3 mt-3 mb-1 px-3 py-2 rounded-lg bg-emerald-500/5 border border-emerald-500/15 flex items-center gap-2">
          <div className="status-dot online" />
          <span className="text-xs text-emerald-400 font-medium">System Online</span>
          <Activity className="w-3 h-3 text-emerald-400 ml-auto" />
        </div>
      )}

      {/* Main nav */}
      <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
        {NAV_ITEMS.map(({ to, icon: Icon, label, exact }) => (
          <NavLink
            key={to}
            to={to}
            end={exact}
            className={({ isActive }) => clsx(
              'flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-150',
              'group relative',
              isActive
                ? 'bg-blue-500/15 text-blue-300 border border-blue-500/20'
                : 'text-slate-400 hover:bg-blue-500/8 hover:text-slate-200 border border-transparent',
              collapsed && 'justify-center px-2'
            )}
          >
            {({ isActive }) => (
              <>
                {/* Active indicator bar */}
                {isActive && (
                  <span className="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-5 bg-blue-400 rounded-r-full" />
                )}
                <Icon
                  className={clsx(
                    'flex-shrink-0 transition-colors duration-150',
                    isActive ? 'text-blue-400 w-[18px] h-[18px]' : 'text-slate-500 w-[18px] h-[18px] group-hover:text-slate-300'
                  )}
                />
                {!collapsed && (
                  <span className="truncate">{label}</span>
                )}
                {collapsed && (
                  <span className="sr-only">{label}</span>
                )}
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Bottom items */}
      <div className="px-2 py-2 space-y-0.5 border-t border-blue-500/8">
        {BOTTOM_ITEMS.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) => clsx(
              'flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all duration-150',
              'group border',
              isActive
                ? 'bg-blue-500/15 text-blue-300 border-blue-500/20'
                : 'text-slate-400 hover:bg-blue-500/8 hover:text-slate-200 border-transparent',
              collapsed && 'justify-center px-2'
            )}
          >
            {({ isActive }) => (
              <>
                <Icon
                  className={clsx(
                    'flex-shrink-0 w-[18px] h-[18px] transition-colors',
                    isActive ? 'text-blue-400' : 'text-slate-500 group-hover:text-slate-300'
                  )}
                />
                {!collapsed && <span className="truncate">{label}</span>}
              </>
            )}
          </NavLink>
        ))}

        {/* Collapse toggle */}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className={clsx(
            'w-full flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-all',
            'text-slate-500 hover:text-slate-300 hover:bg-white/5 border border-transparent',
            collapsed && 'justify-center px-2'
          )}
          aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed
            ? <ChevronRight className="w-4 h-4" />
            : <><ChevronLeft className="w-4 h-4" /><span className="text-xs">Collapse</span></>
          }
        </button>
      </div>
    </aside>
  )
}
