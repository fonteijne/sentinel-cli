import React, { useState, useEffect } from 'react'
import { RefreshCw, Cpu, Activity, CheckCircle, XCircle } from 'lucide-react'
import AgentCard from '../components/AgentCard.jsx'
import { AgentRunBarChart } from '../components/SecurityChart.jsx'
import axios from 'axios'

const MOCK_AGENTS = {
  plan_generator: {
    model: 'claude-opus-4-5',
    temperature: 0.3,
    specializations: [],
  },
  python_developer: {
    model: 'claude-4-5-sonnet',
    temperature: 0.2,
    specializations: ['python', 'pydantic-ai', 'fastapi', 'postgresql'],
  },
  drupal_developer: {
    model: 'claude-4-5-sonnet',
    temperature: 0.2,
    specializations: ['drupal', 'php', 'content-types'],
  },
  security_review: {
    model: 'claude-4-5-sonnet',
    temperature: 0.1,
    specializations: ['owasp', 'security'],
  },
  functional_debrief: {
    model: 'claude-4-5-sonnet',
    temperature: 0.3,
    specializations: [],
  },
  confidence_evaluator: {
    model: 'claude-4-5-sonnet',
    temperature: 0.1,
    specializations: [],
  },
  project_profiler: {
    model: 'claude-4-5-sonnet',
    temperature: 0.2,
    specializations: [],
  },
}

const MOCK_RUN_HISTORY = {
  plan_generator: [
    { label: 'PROJ-145', success: true, duration: '45s' },
    { label: 'SHOP-92', success: true, duration: '38s' },
    { label: 'PROJ-143', success: true, duration: '52s' },
    { label: 'PROJ-141', success: false, duration: '12s' },
    { label: 'SHOP-88', success: true, duration: '41s' },
  ],
  python_developer: [
    { label: 'PROJ-142 iter 1', success: true, duration: '3m 12s' },
    { label: 'PROJ-142 iter 2', success: true, duration: '2m 45s' },
    { label: 'PROJ-140', success: true, duration: '5m 3s' },
    { label: 'PROJ-138', success: true, duration: '4m 28s' },
  ],
  drupal_developer: [
    { label: 'SHOP-88 iter 1', success: true, duration: '4m 5s' },
    { label: 'SHOP-85', success: true, duration: '6m 12s' },
    { label: 'SHOP-80', success: true, duration: '8m 44s' },
  ],
  security_review: [
    { label: 'PROJ-142 review', success: true, duration: '48s' },
    { label: 'PROJ-140 review', success: true, duration: '52s' },
    { label: 'SHOP-88 review', success: true, duration: '45s' },
    { label: 'PROJ-138 review', success: true, duration: '38s' },
  ],
  functional_debrief: [
    { label: 'PROJ-140', success: true, duration: '1m 22s' },
    { label: 'PROJ-138', success: true, duration: '1m 5s' },
    { label: 'SHOP-80', success: true, duration: '55s' },
  ],
  confidence_evaluator: [
    { label: 'PROJ-145', success: true, duration: '12s' },
    { label: 'SHOP-92', success: true, duration: '10s' },
    { label: 'PROJ-143', success: true, duration: '11s' },
    { label: 'PROJ-141', success: false, duration: '8s' },
    { label: 'SHOP-88', success: true, duration: '9s' },
  ],
}

function AgentRunHistoryTable({ agentKey, history }) {
  if (!history || history.length === 0) {
    return <p className="text-xs text-slate-600 italic">No run history</p>
  }

  return (
    <div className="space-y-1.5">
      {history.slice(0, 5).map((run, i) => (
        <div key={i} className="flex items-center gap-3 text-xs">
          <div className={run.success
            ? 'w-3 h-3 rounded-full bg-emerald-400/20 flex items-center justify-center'
            : 'w-3 h-3 rounded-full bg-red-400/20 flex items-center justify-center'
          }>
            {run.success
              ? <CheckCircle className="w-2.5 h-2.5 text-emerald-400" />
              : <XCircle className="w-2.5 h-2.5 text-red-400" />
            }
          </div>
          <span className="font-mono text-slate-400 flex-1 truncate">{run.label}</span>
          <span className="text-slate-600 tabular-nums">{run.duration}</span>
        </div>
      ))}
    </div>
  )
}

export default function Agents() {
  const [agents, setAgents] = useState(MOCK_AGENTS)
  const [loading, setLoading] = useState(false)
  const [selectedAgent, setSelectedAgent] = useState(null)

  useEffect(() => {
    axios.get('/api/agents')
      .then(r => {
        // Merge API data with mock data to ensure all known agents are shown
        if (r.data && Object.keys(r.data).length > 0) {
          setAgents({ ...MOCK_AGENTS, ...r.data })
        }
      })
      .catch(() => {})
  }, [])

  // Aggregate stats
  const allRuns = Object.values(MOCK_RUN_HISTORY).flat()
  const totalRuns = allRuns.length
  const successRuns = allRuns.filter(r => r.success).length
  const successRate = Math.round(successRuns / totalRuns * 100)

  return (
    <div className="p-6 space-y-6">
      {/* Stats row */}
      <div className="grid grid-cols-3 gap-4">
        <div className="glass-card p-4 flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center">
            <Cpu className="w-5 h-5 text-blue-400" />
          </div>
          <div>
            <div className="text-2xl font-bold text-slate-100">{Object.keys(agents).length}</div>
            <div className="text-xs text-slate-500">Total Agents</div>
          </div>
        </div>
        <div className="glass-card p-4 flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center">
            <Activity className="w-5 h-5 text-emerald-400" />
          </div>
          <div>
            <div className="text-2xl font-bold text-slate-100">{totalRuns}</div>
            <div className="text-xs text-slate-500">Total Runs</div>
          </div>
        </div>
        <div className="glass-card p-4 flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center">
            <CheckCircle className="w-5 h-5 text-cyan-400" />
          </div>
          <div>
            <div className="text-2xl font-bold text-slate-100">{successRate}%</div>
            <div className="text-xs text-slate-500">Success Rate</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-12 gap-5">
        {/* Agent cards */}
        <div className="col-span-12 lg:col-span-8">
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
            {Object.entries(agents).map(([key, config]) => (
              <div
                key={key}
                onClick={() => setSelectedAgent(selectedAgent === key ? null : key)}
                className="cursor-pointer"
              >
                <AgentCard
                  agentKey={key}
                  config={config}
                  runHistory={MOCK_RUN_HISTORY[key] || []}
                />
              </div>
            ))}
          </div>
        </div>

        {/* Right panel: run history + chart */}
        <div className="col-span-12 lg:col-span-4 space-y-4">
          {/* Run history detail */}
          {selectedAgent && (
            <div className="glass-card p-5 border border-blue-500/20">
              <h3 className="section-title mb-4">
                {selectedAgent.replace(/_/g, ' ')} — Run History
              </h3>
              <AgentRunHistoryTable
                agentKey={selectedAgent}
                history={MOCK_RUN_HISTORY[selectedAgent]}
              />
            </div>
          )}

          {/* Weekly runs chart */}
          <div className="glass-card p-5">
            <div className="section-header">
              <h3 className="section-title">Runs This Week</h3>
            </div>
            <AgentRunBarChart />
          </div>

          {/* Model usage breakdown */}
          <div className="glass-card p-5">
            <h3 className="section-title mb-4">Model Usage</h3>
            <div className="space-y-3">
              {[
                { model: 'claude-opus-4-5', count: 1, color: 'bg-purple-400' },
                { model: 'claude-4-5-sonnet', count: 5, color: 'bg-blue-400' },
              ].map(({ model, count, color }) => (
                <div key={model} className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-mono text-slate-400 truncate">{model}</span>
                    <span className="text-slate-500 flex-shrink-0 ml-2">{count} agents</span>
                  </div>
                  <div className="stat-bar">
                    <div
                      className={`stat-bar-fill ${color}`}
                      style={{ width: `${(count / Object.keys(agents).length) * 100}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>

            {/* Config note */}
            <div className="mt-4 pt-4 border-t border-white/5">
              <p className="text-xs text-slate-600 leading-relaxed">
                Agent models and temperatures are configured in <code className="text-blue-400/80 font-mono">config.yaml</code>.
                Changes require a sentinel restart.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
