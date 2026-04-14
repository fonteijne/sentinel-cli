import React, { useState, useEffect } from 'react'
import {
  FolderOpen, Ticket, Shield, Bot, Zap,
  ExternalLink, ArrowRight, RefreshCw,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import StatCard from '../components/StatCard.jsx'
import ActivityFeed from '../components/ActivityFeed.jsx'
import HealthIndicator from '../components/HealthIndicator.jsx'
import { AgentRunBarChart, ConfidenceLineChart } from '../components/SecurityChart.jsx'
import axios from 'axios'

const QUICK_ACTIONS = [
  { label: 'Plan Ticket', description: 'Generate plan for a Jira ticket', icon: Ticket, color: 'text-cyan-400', bg: 'bg-cyan-500/10 border-cyan-500/20', route: '/tickets' },
  { label: 'Execute Ticket', description: 'Run implementation for a ticket', icon: Zap, color: 'text-blue-400', bg: 'bg-blue-500/10 border-blue-500/20', route: '/tickets' },
  { label: 'Validate Connections', description: 'Test all API connections', icon: Shield, color: 'text-emerald-400', bg: 'bg-emerald-500/10 border-emerald-500/20', route: '/settings' },
  { label: 'View Projects', description: 'Manage configured projects', icon: FolderOpen, color: 'text-amber-400', bg: 'bg-amber-500/10 border-amber-500/20', route: '/projects' },
]

function QuickActionCard({ label, description, icon: Icon, color, bg, onClick }) {
  return (
    <button onClick={onClick} className={`glass-card glass-card-hover p-4 text-left w-full group border ${bg}`}>
      <div className="flex items-start justify-between">
        <div className={`w-9 h-9 rounded-xl flex items-center justify-center ${bg} mb-3`}>
          <Icon className={`w-4 h-4 ${color}`} />
        </div>
        <ArrowRight className="w-4 h-4 text-slate-700 group-hover:text-slate-400 transition-colors" />
      </div>
      <div className="text-sm font-semibold text-slate-100">{label}</div>
      <div className="text-xs text-slate-500 mt-0.5">{description}</div>
    </button>
  )
}

export default function Overview() {
  const navigate = useNavigate()
  const [status, setStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [activity, setActivity] = useState([])
  const [ticketCount, setTicketCount] = useState(0)
  const [refreshingHealth, setRefreshingHealth] = useState(false)

  useEffect(() => {
    axios.get('/api/status')
      .then(r => setStatus(r.data))
      .catch(() => setStatus(null))
      .finally(() => setLoading(false))

    axios.get('/api/activity')
      .then(r => setActivity(r.data || []))
      .catch(() => setActivity([]))

    axios.get('/api/tickets')
      .then(r => {
        const data = r.data || {}
        const count = Object.values(data).reduce((sum, arr) => sum + (Array.isArray(arr) ? arr.length : 0), 0)
        setTicketCount(count)
      })
      .catch(() => setTicketCount(0))
  }, [])

  const handleRefreshHealth = async () => {
    setRefreshingHealth(true)
    try {
      const res = await axios.get('/api/status/validate')
      setStatus(prev => ({ ...prev, ...res.data }))
    } catch {}
    setRefreshingHealth(false)
  }

  const raw = status?.stats || {}

  const healthItems = [
    { name: 'Jira', status: status?.jira || 'unknown', detail: status?.jira_url || 'Not configured', icon: Ticket },
    { name: 'GitLab', status: status?.gitlab || 'unknown', detail: status?.gitlab_url || 'Not configured', icon: ExternalLink },
    { name: 'LLM', status: status?.llm || 'unknown', detail: status?.llm_mode || 'Not configured', icon: Bot },
    { name: 'SSH', status: status?.ssh || 'unknown', detail: 'SSH key authentication', icon: Shield },
    { name: 'Beads', status: status?.beads || 'unknown', detail: 'Task coordination', icon: Zap },
  ]

  return (
    <div className="p-6 space-y-6">
      {/* KPI Row */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <StatCard
          title="Active Projects"
          value={raw.active_projects ?? 0}
          icon={FolderOpen}
          color="blue"
          loading={loading}
        />
        <StatCard
          title="Active Tickets"
          value={ticketCount}
          icon={Ticket}
          color="cyan"
          loading={loading}
        />
        <StatCard
          title="Security Score"
          value={raw.security_score != null ? `${raw.security_score}%` : '-'}
          icon={Shield}
          color="emerald"
          loading={loading}
        />
        <StatCard
          title="Agent Runs Today"
          value={raw.agent_runs_today ?? 0}
          icon={Bot}
          color="purple"
          loading={loading}
        />
      </div>

      {/* Main 3-column grid */}
      <div className="grid grid-cols-12 gap-4">
        {/* Activity Feed — 5 cols */}
        <div className="col-span-12 lg:col-span-5 glass-card p-5">
          <div className="section-header">
            <h2 className="section-title">Recent Activity</h2>
            <button className="text-xs text-slate-600 hover:text-blue-400 flex items-center gap-1 transition-colors">
              View all <ArrowRight className="w-3 h-3" />
            </button>
          </div>
          <ActivityFeed items={activity} maxItems={8} />
        </div>

        {/* Charts — 7 cols split */}
        <div className="col-span-12 lg:col-span-7 space-y-4">
          {/* Agent runs */}
          <div className="glass-card p-5">
            <div className="section-header">
              <h2 className="section-title">Agent Runs This Week</h2>
              <div className="flex items-center gap-3 text-xs text-slate-600">
                <span className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-blue-500/30" />Total
                </span>
                <span className="flex items-center gap-1.5">
                  <span className="w-2 h-2 rounded-full bg-blue-500" />Success
                </span>
              </div>
            </div>
            <AgentRunBarChart />
          </div>

          {/* Confidence scores */}
          <div className="glass-card p-5">
            <div className="section-header">
              <h2 className="section-title">Plan Confidence Scores</h2>
              <span className="text-xs text-slate-600">Threshold: 95%</span>
            </div>
            <ConfidenceLineChart />
            <div className="mt-2 flex items-center gap-2">
              <div className="flex-1 h-px bg-amber-400/30 relative">
                <span className="absolute right-0 -top-2.5 text-xs text-amber-400/70">95% threshold</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Bottom row: Health + Quick Actions */}
      <div className="grid grid-cols-12 gap-4">
        {/* System Health */}
        <div className="col-span-12 lg:col-span-7 glass-card p-5">
          <div className="section-header">
            <h2 className="section-title">System Health</h2>
            <button
              onClick={handleRefreshHealth}
              disabled={refreshingHealth}
              className="flex items-center gap-1.5 text-xs text-slate-600 hover:text-blue-400 transition-colors"
            >
              <RefreshCw className={`w-3 h-3 ${refreshingHealth ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {healthItems.map(item => (
              <HealthIndicator
                key={item.name}
                name={item.name}
                status={item.status}
                detail={item.detail}
                icon={item.icon}
              />
            ))}
          </div>
        </div>

        {/* Quick Actions */}
        <div className="col-span-12 lg:col-span-5 glass-card p-5">
          <div className="section-header">
            <h2 className="section-title">Quick Actions</h2>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {QUICK_ACTIONS.map(action => (
              <QuickActionCard
                key={action.label}
                {...action}
                onClick={() => navigate(action.route)}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
