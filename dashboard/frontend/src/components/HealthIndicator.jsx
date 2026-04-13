import React from 'react'
import { CheckCircle2, XCircle, AlertCircle, Loader2 } from 'lucide-react'
import clsx from 'clsx'

const STATUS_CONFIG = {
  ok: {
    icon: CheckCircle2,
    color: 'text-emerald-400',
    bg: 'bg-emerald-500/8',
    border: 'border-emerald-500/20',
    label: 'Connected',
    dot: 'bg-emerald-400',
  },
  error: {
    icon: XCircle,
    color: 'text-red-400',
    bg: 'bg-red-500/8',
    border: 'border-red-500/20',
    label: 'Error',
    dot: 'bg-red-400',
  },
  warning: {
    icon: AlertCircle,
    color: 'text-amber-400',
    bg: 'bg-amber-500/8',
    border: 'border-amber-500/20',
    label: 'Degraded',
    dot: 'bg-amber-400',
  },
  loading: {
    icon: Loader2,
    color: 'text-blue-400',
    bg: 'bg-blue-500/8',
    border: 'border-blue-500/15',
    label: 'Checking...',
    dot: 'bg-blue-400',
  },
  unknown: {
    icon: AlertCircle,
    color: 'text-slate-500',
    bg: 'bg-slate-500/8',
    border: 'border-slate-500/20',
    label: 'Unknown',
    dot: 'bg-slate-500',
  },
}

export default function HealthIndicator({ name, status = 'unknown', detail, icon: Icon }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.unknown
  const StatusIcon = cfg.icon

  return (
    <div className={clsx(
      'flex items-center gap-3 px-4 py-3 rounded-xl border transition-all',
      cfg.bg, cfg.border
    )}>
      {/* Service icon */}
      <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-white/5 flex-shrink-0">
        {Icon && <Icon className="w-4 h-4 text-slate-400" />}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium text-slate-200">{name}</span>
          <span className={clsx('w-1.5 h-1.5 rounded-full flex-shrink-0', cfg.dot)} />
        </div>
        {detail && (
          <span className="text-xs text-slate-600 truncate">{detail}</span>
        )}
      </div>

      <div className={clsx('flex items-center gap-1.5 text-xs font-medium flex-shrink-0', cfg.color)}>
        <StatusIcon className={clsx('w-3.5 h-3.5', status === 'loading' && 'animate-spin')} />
        {cfg.label}
      </div>
    </div>
  )
}
