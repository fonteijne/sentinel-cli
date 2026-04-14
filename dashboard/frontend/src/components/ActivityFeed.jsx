import React from 'react'
import {
  GitCommit,
  ShieldCheck,
  Bot,
  Ticket,
  FolderOpen,
  AlertTriangle,
  CheckCircle,
  XCircle,
  RefreshCw,
} from 'lucide-react'
import clsx from 'clsx'

const EVENT_ICONS = {
  execute: { icon: Bot, color: 'text-blue-400', bg: 'bg-blue-500/15' },
  plan: { icon: GitCommit, color: 'text-cyan-400', bg: 'bg-cyan-500/15' },
  security: { icon: ShieldCheck, color: 'text-emerald-400', bg: 'bg-emerald-500/15' },
  ticket: { icon: Ticket, color: 'text-purple-400', bg: 'bg-purple-500/15' },
  project: { icon: FolderOpen, color: 'text-amber-400', bg: 'bg-amber-500/15' },
  warning: { icon: AlertTriangle, color: 'text-amber-400', bg: 'bg-amber-500/15' },
  success: { icon: CheckCircle, color: 'text-emerald-400', bg: 'bg-emerald-500/15' },
  error: { icon: XCircle, color: 'text-red-400', bg: 'bg-red-500/15' },
  reset: { icon: RefreshCw, color: 'text-slate-400', bg: 'bg-slate-500/15' },
}

function timeAgo(ts) {
  const now = Date.now()
  const diff = now - new Date(ts).getTime()
  const mins = Math.floor(diff / 60000)
  const hours = Math.floor(mins / 60)
  const days = Math.floor(hours / 24)
  if (days > 0) return `${days}d ago`
  if (hours > 0) return `${hours}h ago`
  if (mins > 0) return `${mins}m ago`
  return 'Just now'
}

export default function ActivityFeed({ items = [], maxItems = 8 }) {
  const displayed = items.slice(0, maxItems)

  if (displayed.length === 0) {
    return (
      <div className="flex items-center justify-center py-8 text-slate-600 text-sm text-center">
        No activity yet — run a sentinel command to see events here
      </div>
    )
  }

  return (
    <div className="space-y-1">
      {displayed.map((item, i) => {
        const ev = EVENT_ICONS[item.type] || EVENT_ICONS.ticket
        const Icon = ev.icon
        const isLast = i === displayed.length - 1

        return (
          <div key={item.id} className={clsx('timeline-item pb-4', isLast && 'pb-0')}>
            <div className={clsx('timeline-dot', ev.bg)}>
              <Icon className={clsx('w-2 h-2', ev.color)} />
            </div>

            <div className="flex items-start justify-between gap-3 min-w-0">
              <div className="min-w-0 flex-1">
                <p className="text-sm text-slate-300 leading-snug">{item.text}</p>
                {item.ticket && (
                  <span className="inline-block mt-0.5 text-xs font-mono text-blue-400/80 bg-blue-500/8 border border-blue-500/15 px-1.5 py-0.5 rounded-md">
                    {item.ticket}
                  </span>
                )}
              </div>
              <span className="flex-shrink-0 text-xs text-slate-600 tabular-nums mt-0.5">
                {timeAgo(item.ts)}
              </span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
