import React, { useState, useEffect } from 'react'
import { RefreshCw, Cpu, Activity, CheckCircle, XCircle } from 'lucide-react'
import AgentCard from '../components/AgentCard.jsx'
import { AgentRunBarChart } from '../components/SecurityChart.jsx'
import axios from 'axios'

function AgentRunHistoryTable({ agentKey, history }) {
  if (!history || history.length === 0) {
    return <p className="text-xs text-slate-600 italic">No run history available</p>
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
  const [agents, setAgents] = useState({})
  const [loading, setLoading] = useState(true)
  const [selectedAgent, setSelectedAgent] = useState(null)

  useEffect(() => {
    axios.get('/api/agents')
      .then(r => {
        if (r.data && Object.keys(r.data).length > 0) {
          setAgents(r.data)
        } else {
          setAgents({})
        }
      })
      .catch(() => setAgents({}))
      .finally(() => setLoading(false))
  }, [])

  // Compute model usage from real agent data
  const modelCounts = {}
  Object.values(agents).forEach(a => {
    const m = a.model || 'unknown'
    modelCounts[m] = (modelCounts[m] || 0) + 1
  })

  const totalAgents = Object.keys(agents).length

  return (
    <div className="p-6 space-y-6">
      {/* Stats row */}
      <div className="grid grid-cols-3 gap-4">
        <div className="glass-card p-4 flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center">
            <Cpu className="w-5 h-5 text-blue-400" />
          </div>
          <div>
            <div className="text-2xl font-bold text-slate-100">{totalAgents}</div>
            <div className="text-xs text-slate-500">Total Agents</div>
          </div>
        </div>
        <div className="glass-card p-4 flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-emerald-500/10 border border-emerald-500/20 flex items-center justify-center">
            <Activity className="w-5 h-5 text-emerald-400" />
          </div>
          <div>
            <div className="text-2xl font-bold text-slate-100">0</div>
            <div className="text-xs text-slate-500">Total Runs</div>
          </div>
        </div>
        <div className="glass-card p-4 flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-cyan-500/10 border border-cyan-500/20 flex items-center justify-center">
            <CheckCircle className="w-5 h-5 text-cyan-400" />
          </div>
          <div>
            <div className="text-2xl font-bold text-slate-100">-</div>
            <div className="text-xs text-slate-500">Success Rate</div>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {[1, 2, 3].map(i => (
            <div key={i} className="glass-card p-5 animate-pulse h-40" />
          ))}
        </div>
      ) : totalAgents === 0 ? (
        <div className="glass-card p-16 text-center">
          <div className="w-16 h-16 rounded-full bg-blue-500/10 border border-blue-500/20 flex items-center justify-center mx-auto mb-4">
            <Cpu className="w-8 h-8 text-blue-400/60" />
          </div>
          <p className="text-slate-300 text-sm font-medium mb-1">No agents configured</p>
          <p className="text-slate-600 text-xs max-w-sm mx-auto leading-relaxed">
            Check <code className="text-blue-400/80 font-mono">config.yaml</code> to configure agents
          </p>
        </div>
      ) : (
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
                    runHistory={[]}
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
                  history={agents[selectedAgent]?.run_history || []}
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
              {Object.keys(modelCounts).length === 0 ? (
                <p className="text-xs text-slate-600 italic">No model data available</p>
              ) : (
                <div className="space-y-3">
                  {Object.entries(modelCounts).map(([model, count]) => (
                    <div key={model} className="space-y-1.5">
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-mono text-slate-400 truncate">{model}</span>
                        <span className="text-slate-500 flex-shrink-0 ml-2">{count} agent{count !== 1 ? 's' : ''}</span>
                      </div>
                      <div className="stat-bar">
                        <div
                          className="stat-bar-fill bg-blue-400"
                          style={{ width: `${(count / totalAgents) * 100}%` }}
                        />
                      </div>
                    </div>
                  ))}
                </div>
              )}

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
      )}
    </div>
  )
}
