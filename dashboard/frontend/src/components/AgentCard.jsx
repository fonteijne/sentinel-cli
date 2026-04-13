import React from 'react'
import { Cpu, Thermometer, Zap, Clock, CheckCircle2, BarChart2 } from 'lucide-react'
import clsx from 'clsx'

const AGENT_COLORS = {
  plan_generator: { accent: 'text-cyan-400', border: 'border-cyan-500/20', glow: 'shadow-cyan-500/10' },
  python_developer: { accent: 'text-yellow-400', border: 'border-yellow-500/20', glow: 'shadow-yellow-500/10' },
  drupal_developer: { accent: 'text-blue-400', border: 'border-blue-500/20', glow: 'shadow-blue-500/10' },
  security_review: { accent: 'text-red-400', border: 'border-red-500/20', glow: 'shadow-red-500/10' },
  functional_debrief: { accent: 'text-purple-400', border: 'border-purple-500/20', glow: 'shadow-purple-500/10' },
  confidence_evaluator: { accent: 'text-emerald-400', border: 'border-emerald-500/20', glow: 'shadow-emerald-500/10' },
  project_profiler: { accent: 'text-orange-400', border: 'border-orange-500/20', glow: 'shadow-orange-500/10' },
}

const AGENT_LABELS = {
  plan_generator: 'Plan Generator',
  python_developer: 'Python Developer',
  drupal_developer: 'Drupal Developer',
  security_review: 'Security Reviewer',
  functional_debrief: 'Functional Debrief',
  confidence_evaluator: 'Confidence Evaluator',
  project_profiler: 'Project Profiler',
}

const AGENT_DESCRIPTIONS = {
  plan_generator: 'Creates implementation plans with confidence scoring and codebase research.',
  python_developer: 'Python/FastAPI/Pydantic implementation with TDD loop.',
  drupal_developer: 'Drupal CMS implementation, content types, and hooks.',
  security_review: 'OWASP security scanning with veto power over releases.',
  functional_debrief: 'Conversational functional analysis posted to Jira.',
  confidence_evaluator: 'Scores plan confidence against a 95% threshold.',
  project_profiler: 'Generates project profiles with codebase analysis and dependency mapping.',
}

function TempBar({ temperature }) {
  const pct = Math.min(temperature * 100, 100)
  const color = temperature < 0.2 ? 'bg-blue-400' : temperature < 0.4 ? 'bg-cyan-400' : 'bg-amber-400'
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1 bg-white/6 rounded-full overflow-hidden">
        <div
          className={clsx('h-full rounded-full transition-all', color)}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-xs font-mono text-slate-500 w-6 text-right">{temperature}</span>
    </div>
  )
}

export default function AgentCard({ agentKey, config, runHistory = [] }) {
  const colors = AGENT_COLORS[agentKey] || AGENT_COLORS.plan_generator
  const label = AGENT_LABELS[agentKey] || agentKey
  const description = AGENT_DESCRIPTIONS[agentKey] || ''
  const { model, temperature = 0.2, specializations = [] } = config || {}

  const successRate = runHistory.length > 0
    ? Math.round(runHistory.filter(r => r.success).length / runHistory.length * 100)
    : null

  const lastRun = runHistory.length > 0 ? runHistory[0] : null

  return (
    <div className={clsx(
      'glass-card glass-card-hover p-5',
      `border ${colors.border}`
    )}>
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1 min-w-0">
          <div className={clsx('text-xs font-mono font-bold mb-1', colors.accent)}>
            {agentKey}
          </div>
          <h3 className="text-[15px] font-semibold text-slate-100 leading-tight">{label}</h3>
        </div>

        <div className="flex items-center justify-center w-9 h-9 rounded-xl bg-white/4 border border-white/8 flex-shrink-0">
          <Cpu className={clsx('w-4 h-4', colors.accent)} />
        </div>
      </div>

      <p className="text-xs text-slate-500 leading-relaxed mb-4">{description}</p>

      {/* Model info */}
      {model && (
        <div className="flex items-center gap-2 text-xs bg-white/4 border border-white/6 rounded-lg px-3 py-2 mb-3">
          <Zap className={clsx('w-3.5 h-3.5 flex-shrink-0', colors.accent)} />
          <span className="font-mono text-slate-300 truncate">{model}</span>
        </div>
      )}

      {/* Temperature */}
      <div className="mb-3">
        <div className="flex items-center gap-1.5 text-xs text-slate-600 mb-1.5">
          <Thermometer className="w-3 h-3" />
          <span>Temperature</span>
        </div>
        <TempBar temperature={temperature} />
      </div>

      {/* Specializations */}
      {specializations.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mb-3">
          {specializations.map(s => (
            <span key={s} className={clsx(
              'text-[10px] font-medium px-2 py-0.5 rounded-full border',
              colors.accent,
              `bg-current/10`
            )}>
              {s}
            </span>
          ))}
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 gap-2 pt-3 border-t border-white/6">
        <div className="flex items-center gap-1.5 text-xs text-slate-600">
          <BarChart2 className="w-3 h-3" />
          <span>{runHistory.length} runs</span>
        </div>
        {successRate !== null && (
          <div className="flex items-center gap-1.5 text-xs text-emerald-400">
            <CheckCircle2 className="w-3 h-3" />
            <span>{successRate}% success</span>
          </div>
        )}
        {lastRun && (
          <div className="col-span-2 flex items-center gap-1.5 text-xs text-slate-600">
            <Clock className="w-3 h-3" />
            <span>Last: {lastRun.label}</span>
          </div>
        )}
      </div>
    </div>
  )
}
