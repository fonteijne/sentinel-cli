import React from 'react'
import { Play, FileText, MessageSquare, Tag, Clock } from 'lucide-react'
import clsx from 'clsx'

const PRIORITY_CONFIG = {
  Highest: { color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/20' },
  High: { color: 'text-orange-400', bg: 'bg-orange-500/10', border: 'border-orange-500/20' },
  Medium: { color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/20' },
  Low: { color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/20' },
  Lowest: { color: 'text-slate-400', bg: 'bg-slate-500/10', border: 'border-slate-500/20' },
}

const STATUS_DOT = {
  plan: 'bg-cyan-400',
  execute: 'bg-blue-400',
  review: 'bg-amber-400',
  done: 'bg-emerald-400',
}

export default function TicketCard({ ticket, onExecute, onPlan, onDebrief, compact = false }) {
  const { id, summary, priority = 'Medium', status, assignee, labels = [], updated } = ticket
  const priorityCfg = PRIORITY_CONFIG[priority] || PRIORITY_CONFIG.Medium
  const dotColor = STATUS_DOT[status] || 'bg-slate-500'

  return (
    <div className={clsx(
      'glass-card glass-card-hover',
      compact ? 'p-3' : 'p-4'
    )}>
      {/* Ticket ID + status dot */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono font-bold text-blue-400/80 bg-blue-500/8 border border-blue-500/15 px-2 py-0.5 rounded-md">
            {id}
          </span>
          <div className={clsx('w-2 h-2 rounded-full flex-shrink-0', dotColor)} />
        </div>
        <div className={clsx('badge border text-[10px]', priorityCfg.bg, priorityCfg.border, priorityCfg.color)}>
          {priority}
        </div>
      </div>

      {/* Summary */}
      <p className={clsx(
        'text-slate-200 font-medium leading-snug mb-3',
        compact ? 'text-xs' : 'text-sm',
        compact && 'line-clamp-2'
      )}>
        {summary}
      </p>

      {/* Labels */}
      {labels.length > 0 && !compact && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {labels.slice(0, 3).map(label => (
            <span key={label} className="flex items-center gap-1 text-xs text-slate-500 bg-white/4 border border-white/8 px-2 py-0.5 rounded-full">
              <Tag className="w-2.5 h-2.5" />
              {label}
            </span>
          ))}
        </div>
      )}

      {/* Meta */}
      {!compact && (
        <div className="flex items-center gap-3 text-xs text-slate-600 mb-3">
          {assignee && (
            <span className="flex items-center gap-1">
              <div className="w-4 h-4 rounded-full bg-blue-500/30 flex items-center justify-center text-[9px] font-bold text-blue-400">
                {assignee.charAt(0).toUpperCase()}
              </div>
              {assignee}
            </span>
          )}
          {updated && (
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              {updated}
            </span>
          )}
        </div>
      )}

      {/* Actions */}
      {!compact && (
        <div className="flex items-center gap-2 pt-3 border-t border-blue-500/8">
          {onExecute && (
            <button
              onClick={() => onExecute(id)}
              className="btn-primary text-xs py-1.5 px-3 flex-1"
            >
              <Play className="w-3 h-3" />
              Execute
            </button>
          )}
          {onPlan && (
            <button
              onClick={() => onPlan(id)}
              className="btn-secondary text-xs py-1.5 px-3"
            >
              <FileText className="w-3 h-3" />
              Plan
            </button>
          )}
          {onDebrief && (
            <button
              onClick={() => onDebrief(id)}
              className="btn-secondary text-xs py-1.5 px-3"
            >
              <MessageSquare className="w-3 h-3" />
              Debrief
            </button>
          )}
        </div>
      )}
    </div>
  )
}
