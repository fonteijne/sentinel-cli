import React from 'react'
import { TrendingUp, TrendingDown, Minus } from 'lucide-react'
import clsx from 'clsx'

export default function StatCard({ title, value, subtitle, trend, trendLabel, icon: Icon, color = 'blue', loading = false }) {
  const colorMap = {
    blue: {
      icon: 'text-blue-400',
      iconBg: 'bg-blue-500/10 border-blue-500/20',
      accent: 'text-blue-400',
      bar: 'from-blue-500 to-blue-400',
    },
    cyan: {
      icon: 'text-cyan-400',
      iconBg: 'bg-cyan-500/10 border-cyan-500/20',
      accent: 'text-cyan-400',
      bar: 'from-cyan-500 to-cyan-400',
    },
    emerald: {
      icon: 'text-emerald-400',
      iconBg: 'bg-emerald-500/10 border-emerald-500/20',
      accent: 'text-emerald-400',
      bar: 'from-emerald-500 to-emerald-400',
    },
    amber: {
      icon: 'text-amber-400',
      iconBg: 'bg-amber-500/10 border-amber-500/20',
      accent: 'text-amber-400',
      bar: 'from-amber-500 to-amber-400',
    },
    purple: {
      icon: 'text-purple-400',
      iconBg: 'bg-purple-500/10 border-purple-500/20',
      accent: 'text-purple-400',
      bar: 'from-purple-500 to-purple-400',
    },
  }

  const c = colorMap[color] || colorMap.blue

  const TrendIcon = trend > 0 ? TrendingUp : trend < 0 ? TrendingDown : Minus
  const trendColor = trend > 0 ? 'text-emerald-400' : trend < 0 ? 'text-red-400' : 'text-slate-500'

  if (loading) {
    return (
      <div className="glass-card p-5 animate-pulse">
        <div className="flex items-start justify-between mb-4">
          <div className="w-10 h-10 rounded-lg bg-white/5" />
          <div className="w-16 h-4 rounded bg-white/5" />
        </div>
        <div className="w-20 h-8 rounded bg-white/5 mb-2" />
        <div className="w-32 h-3 rounded bg-white/5" />
      </div>
    )
  }

  return (
    <div className="glass-card glass-card-hover p-5 group cursor-default">
      {/* Header row */}
      <div className="flex items-start justify-between mb-4">
        <div className={clsx('w-10 h-10 rounded-xl border flex items-center justify-center flex-shrink-0', c.iconBg)}>
          {Icon && <Icon className={clsx('w-5 h-5', c.icon)} />}
        </div>

        {trend !== undefined && (
          <div className={clsx('flex items-center gap-1 text-xs font-medium', trendColor)}>
            <TrendIcon className="w-3.5 h-3.5" />
            <span>{Math.abs(trend)}%</span>
          </div>
        )}
      </div>

      {/* Value */}
      <div className="mb-1">
        <span className="text-2xl font-bold text-slate-100 tracking-tight tabular-nums">
          {value}
        </span>
      </div>

      {/* Title */}
      <div className="text-sm font-medium text-slate-400 mb-1">{title}</div>

      {/* Subtitle / trend label */}
      {(subtitle || trendLabel) && (
        <div className="text-xs text-slate-600">{trendLabel || subtitle}</div>
      )}
    </div>
  )
}
