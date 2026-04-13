import React from 'react'
import { CheckCircle2, XCircle, AlertCircle, Loader2, RefreshCw } from 'lucide-react'
import clsx from 'clsx'

const STATUS_CFG = {
  ok: {
    icon: CheckCircle2,
    label: 'Connected',
    color: 'text-emerald-400',
    bg: 'bg-emerald-500/8',
    border: 'border-emerald-500/20',
    dot: 'bg-emerald-400',
    pulse: true,
  },
  error: {
    icon: XCircle,
    label: 'Error',
    color: 'text-red-400',
    bg: 'bg-red-500/8',
    border: 'border-red-500/20',
    dot: 'bg-red-500',
    pulse: false,
  },
  warning: {
    icon: AlertCircle,
    label: 'Degraded',
    color: 'text-amber-400',
    bg: 'bg-amber-500/8',
    border: 'border-amber-500/20',
    dot: 'bg-amber-400',
    pulse: false,
  },
  checking: {
    icon: Loader2,
    label: 'Checking...',
    color: 'text-blue-400',
    bg: 'bg-blue-500/8',
    border: 'border-blue-500/20',
    dot: 'bg-blue-400',
    pulse: true,
  },
  unconfigured: {
    icon: AlertCircle,
    label: 'Not Configured',
    color: 'text-slate-500',
    bg: 'bg-slate-500/6',
    border: 'border-slate-500/15',
    dot: 'bg-slate-600',
    pulse: false,
  },
}

export default function ConnectionCard({ name, status = 'unconfigured', detail, url, icon: Icon, onTest }) {
  const cfg = STATUS_CFG[status] || STATUS_CFG.unconfigured
  const StatusIcon = cfg.icon

  return (
    <div className={clsx(
      'glass-card p-5 border transition-all',
      cfg.bg, cfg.border
    )}>
      {/* Header */}
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-white/5 border border-white/8 flex items-center justify-center flex-shrink-0">
            {Icon && <Icon className="w-5 h-5 text-slate-400" />}
          </div>
          <div>
            <h3 className="text-[15px] font-semibold text-slate-100">{name}</h3>
            {url && (
              <span className="text-xs text-slate-600 font-mono truncate block max-w-[200px]">{url}</span>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1.5">
          <div className={clsx(
            'w-2 h-2 rounded-full flex-shrink-0',
            cfg.dot,
            cfg.pulse && 'animate-pulse'
          )} />
        </div>
      </div>

      {/* Status row */}
      <div className="flex items-center justify-between">
        <div className={clsx('flex items-center gap-2 text-sm font-medium', cfg.color)}>
          <StatusIcon className={clsx('w-4 h-4', status === 'checking' && 'animate-spin')} />
          {cfg.label}
        </div>

        {onTest && (
          <button
            onClick={onTest}
            disabled={status === 'checking'}
            className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-blue-400 transition-colors disabled:opacity-50"
          >
            <RefreshCw className={clsx('w-3.5 h-3.5', status === 'checking' && 'animate-spin')} />
            Test
          </button>
        )}
      </div>

      {/* Detail */}
      {detail && (
        <div className="mt-3 pt-3 border-t border-white/5 text-xs text-slate-600 leading-relaxed">
          {detail}
        </div>
      )}
    </div>
  )
}
