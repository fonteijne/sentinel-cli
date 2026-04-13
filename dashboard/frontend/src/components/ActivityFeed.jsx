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

const MOCK_ACTIVITY = [
  { id: 1, type: 'execute', text: 'Executed ticket PROJ-142 — Python authentication module', ts: Date.now() - 8 * 60000, ticket: 'PROJ-142' },
  { id: 2, type: 'security', text: 'Security review passed with 0 critical findings', ts: Date.now() - 25 * 60000 },
  { id: 3, type: 'plan', text: 'Plan generated for PROJ-145 — Refactor database layer', ts: Date.now() - 52 * 60000, ticket: 'PROJ-145' },
  { id: 4, type: 'warning', text: 'LLM rate limit reached, retried after backoff', ts: Date.now() - 1.5 * 3600000 },
  { id: 5, type: 'execute', text: 'Executed ticket SHOP-88 — Drupal content migration', ts: Date.now() - 2.2 * 3600000, ticket: 'SHOP-88' },
  { id: 6, type: 'project', text: 'Project profile regenerated for shop-platform', ts: Date.now() - 3.1 * 3600000 },
  { id: 7, type: 'success', text: 'PROJ-140 debrief validated — ticket resolved', ts: Date.now() - 5 * 3600000, ticket: 'PROJ-140' },
  { id: 8, type: 'ticket', text: 'Plan revision triggered by MR feedback on PROJ-139', ts: Date.now() - 7 * 3600000, ticket: 'PROJ-139' },
]

export default function ActivityFeed({ items = MOCK_ACTIVITY, maxItems = 8 }) {
  const displayed = items.slice(0, maxItems)

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
