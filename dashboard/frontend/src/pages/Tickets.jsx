import React, { useState, useEffect, useCallback } from 'react'
import { Search, RefreshCw, Play, FileText, MessageSquare, Tag, Radio, Layers } from 'lucide-react'
import TicketCard from '../components/TicketCard.jsx'
import clsx from 'clsx'
import axios from 'axios'

const MOCK_TICKETS = {
  plan: [
    { id: 'PROJ-145', summary: 'Refactor database connection pooling layer', priority: 'High', assignee: 'agent', labels: ['backend', 'performance'], updated: '2h ago', status: 'plan' },
    { id: 'SHOP-92', summary: 'Add multilingual content support to CMS', priority: 'Medium', assignee: 'agent', labels: ['drupal', 'i18n'], updated: '4h ago', status: 'plan' },
  ],
  execute: [
    { id: 'PROJ-142', summary: 'Implement OAuth2 authentication module', priority: 'Highest', assignee: 'agent', labels: ['security', 'auth'], updated: '25m ago', status: 'execute' },
    { id: 'PROJ-143', summary: 'Add rate limiting middleware to API endpoints', priority: 'High', assignee: 'agent', labels: ['backend', 'security'], updated: '1h ago', status: 'execute' },
    { id: 'SHOP-88', summary: 'Drupal content migration — products to new schema', priority: 'Medium', assignee: 'agent', labels: ['drupal', 'migration'], updated: '3h ago', status: 'execute' },
  ],
  review: [
    { id: 'PROJ-140', summary: 'WebSocket notification service', priority: 'High', assignee: 'agent', labels: ['backend', 'realtime'], updated: '1d ago', status: 'review' },
    { id: 'SHOP-85', summary: 'Product search indexing with Solr', priority: 'Medium', assignee: 'agent', labels: ['drupal', 'search'], updated: '2d ago', status: 'review' },
  ],
  done: [
    { id: 'PROJ-138', summary: 'User permission model refactor', priority: 'High', assignee: 'agent', labels: ['backend', 'security'], updated: '3d ago', status: 'done' },
    { id: 'PROJ-136', summary: 'Async task queue with Redis', priority: 'Medium', assignee: 'agent', labels: ['backend'], updated: '5d ago', status: 'done' },
    { id: 'SHOP-80', summary: 'Checkout flow payment integration', priority: 'Highest', assignee: 'agent', labels: ['drupal'], updated: '1w ago', status: 'done' },
  ],
}

const COLUMNS = [
  { key: 'plan', label: 'Plan', color: 'text-cyan-400', borderColor: 'border-cyan-500/20', dotColor: 'bg-cyan-400' },
  { key: 'execute', label: 'Execute', color: 'text-blue-400', borderColor: 'border-blue-500/20', dotColor: 'bg-blue-400' },
  { key: 'review', label: 'Review', color: 'text-amber-400', borderColor: 'border-amber-500/20', dotColor: 'bg-amber-400' },
  { key: 'done', label: 'Done', color: 'text-emerald-400', borderColor: 'border-emerald-500/20', dotColor: 'bg-emerald-400' },
]

function TicketDetailPanel({ ticket, onClose, onExecute, onPlan, onDebrief }) {
  if (!ticket) return null

  return (
    <div className="glass-card p-5 border border-blue-500/20 h-full">
      <div className="flex items-center justify-between mb-4">
        <span className="text-xs font-mono font-bold text-blue-400 bg-blue-500/10 border border-blue-500/20 px-2 py-1 rounded-md">
          {ticket.id}
        </span>
        <button
          onClick={onClose}
          className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-500 hover:text-slate-300 hover:bg-white/5"
        >
          ×
        </button>
      </div>

      <h3 className="text-[15px] font-semibold text-slate-100 leading-snug mb-4">
        {ticket.summary}
      </h3>

      <div className="space-y-3 mb-5">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-600 w-24">Status</span>
          <span className={clsx(
            'capitalize font-medium',
            ticket.status === 'done' ? 'text-emerald-400' :
            ticket.status === 'execute' ? 'text-blue-400' :
            ticket.status === 'review' ? 'text-amber-400' : 'text-cyan-400'
          )}>
            {ticket.status}
          </span>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-600 w-24">Priority</span>
          <span className="text-slate-300">{ticket.priority}</span>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-600 w-24">Updated</span>
          <span className="text-slate-400">{ticket.updated}</span>
        </div>
        {ticket.project && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-slate-600 w-24">Project</span>
            <span className="text-slate-300 font-mono text-xs">{ticket.project}</span>
          </div>
        )}
        {ticket.branch && (
          <div className="flex items-center gap-2 text-sm">
            <span className="text-slate-600 w-24">Branch</span>
            <span className="text-slate-400 font-mono text-xs truncate max-w-[180px]" title={ticket.branch}>{ticket.branch}</span>
          </div>
        )}
        {ticket.labels?.length > 0 && (
          <div className="flex items-start gap-2 text-sm">
            <span className="text-slate-600 w-24 mt-0.5">Labels</span>
            <div className="flex flex-wrap gap-1.5">
              {ticket.labels.map(l => (
                <span key={l} className="text-xs text-slate-400 bg-white/5 border border-white/8 px-2 py-0.5 rounded-full flex items-center gap-1">
                  <Tag className="w-2.5 h-2.5" />
                  {l}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="divider" />

      <div className="space-y-2">
        <p className="text-xs font-medium text-slate-500 uppercase tracking-wider mb-3">Actions</p>
        <button
          onClick={() => onPlan(ticket.id)}
          className="btn-secondary w-full justify-start"
        >
          <FileText className="w-4 h-4" />
          Generate / Revise Plan
        </button>
        <button
          onClick={() => onExecute(ticket.id)}
          className="btn-primary w-full justify-start"
        >
          <Play className="w-4 h-4" />
          Execute Implementation
        </button>
        <button
          onClick={() => onDebrief(ticket.id)}
          className="btn-secondary w-full justify-start"
        >
          <MessageSquare className="w-4 h-4" />
          Run Functional Debrief
        </button>
      </div>

      <div className="mt-4 p-3 rounded-xl bg-blue-500/5 border border-blue-500/10">
        <p className="text-xs text-slate-600 leading-relaxed">
          Actions trigger sentinel CLI commands and stream output to the Logs page in real-time.
        </p>
      </div>
    </div>
  )
}

export default function Tickets() {
  const [tickets, setTickets] = useState(MOCK_TICKETS)
  const [selected, setSelected] = useState(null)
  const [search, setSearch] = useState('')
  const [projectFilter, setProjectFilter] = useState('all')
  const [running, setRunning] = useState({})
  const [notification, setNotification] = useState(null)
  const [loading, setLoading] = useState(false)
  const [isLive, setIsLive] = useState(false)

  const notify = (msg, type = 'info') => {
    setNotification({ msg, type })
    setTimeout(() => setNotification(null), 3000)
  }

  const fetchTickets = useCallback(async () => {
    setLoading(true)
    try {
      const res = await axios.get('/api/tickets')
      const data = res.data
      const hasLiveData = Object.values(data).some(arr => arr.length > 0)
      if (hasLiveData) {
        setTickets(data)
        setIsLive(true)
      } else {
        // API returned but all empty — keep mock done items, clear others
        setTickets({ ...MOCK_TICKETS, plan: [], execute: [], review: [] })
        setIsLive(false)
      }
    } catch {
      // Network/API failure — fall back to full mock data
      setTickets(MOCK_TICKETS)
      setIsLive(false)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchTickets()
  }, [fetchTickets])

  const runAction = async (ticketId, action) => {
    setRunning(r => ({ ...r, [`${ticketId}-${action}`]: true }))
    try {
      await axios.post(`/api/tickets/${ticketId}/${action}`)
      notify(`${action} started for ${ticketId}`, 'success')
    } catch {
      notify(`${action} command sent for ${ticketId} — check Logs`, 'info')
    }
    setRunning(r => ({ ...r, [`${ticketId}-${action}`]: false }))
  }

  const allTickets = Object.values(tickets).flat()
  const totalCount = allTickets.length

  // Collect unique project keys for filter dropdown
  const allProjects = [...new Set(allTickets.map(t => t.project).filter(Boolean))]

  const filterTickets = (list) =>
    list.filter(t => {
      const matchesSearch = !search ||
        t.id.toLowerCase().includes(search.toLowerCase()) ||
        t.summary.toLowerCase().includes(search.toLowerCase())
      const matchesProject = projectFilter === 'all' || t.project === projectFilter
      return matchesSearch && matchesProject
    })

  return (
    <div className="p-6 h-[calc(100vh-56px)] flex flex-col space-y-4">
      {/* Notification */}
      {notification && (
        <div className={clsx(
          'flex items-center gap-2 px-4 py-3 rounded-xl border text-sm animate-slide-in flex-shrink-0',
          notification.type === 'success'
            ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-300'
            : 'bg-blue-500/10 border-blue-500/25 text-blue-300'
        )}>
          {notification.msg}
        </div>
      )}

      {/* Stats + toolbar */}
      <div className="flex items-center justify-between flex-shrink-0 gap-3">
        {/* Phase counts + live/demo badge */}
        <div className="flex items-center gap-4">
          {COLUMNS.map(col => {
            const count = tickets[col.key]?.length || 0
            return (
              <div key={col.key} className="flex items-center gap-2">
                <div className={clsx('w-2 h-2 rounded-full', col.dotColor)} />
                <span className="text-xs text-slate-500">{col.label}</span>
                <span className={clsx('text-xs font-bold', col.color)}>{count}</span>
              </div>
            )
          })}
          {/* Live / Demo badge */}
          {isLive ? (
            <span className="flex items-center gap-1.5 text-xs font-semibold text-emerald-400 bg-emerald-500/10 border border-emerald-500/25 px-2 py-0.5 rounded-full">
              <Radio className="w-3 h-3" />
              Live
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 bg-white/5 border border-white/10 px-2 py-0.5 rounded-full">
              <Layers className="w-3 h-3" />
              Demo
            </span>
          )}
        </div>

        {/* Right-side controls */}
        <div className="flex items-center gap-2">
          {/* Project filter */}
          {allProjects.length > 0 && (
            <select
              value={projectFilter}
              onChange={e => setProjectFilter(e.target.value)}
              className="dark-input text-xs pr-8 w-36"
            >
              <option value="all">All projects</option>
              {allProjects.map(p => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          )}
          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-600" />
            <input
              type="text"
              placeholder="Search tickets..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="dark-input pl-9 w-48"
            />
          </div>
          {/* Refresh button */}
          <button
            onClick={fetchTickets}
            disabled={loading}
            className="btn-secondary px-3 py-1.5 flex items-center gap-1.5 text-xs"
            title="Sync worktrees"
          >
            <RefreshCw className={clsx('w-3.5 h-3.5', loading && 'animate-spin')} />
            {loading ? 'Syncing...' : 'Sync'}
          </button>
        </div>
      </div>

      {/* Kanban + detail panel */}
      <div className="flex-1 flex gap-4 min-h-0">
        {/* Kanban board */}
        <div className={clsx(
          'flex-1 grid gap-3 min-h-0 overflow-hidden',
          selected ? 'grid-cols-4' : 'grid-cols-4'
        )}>
          {COLUMNS.map(col => {
            const colTickets = filterTickets(tickets[col.key] || [])
            return (
              <div key={col.key} className={clsx(
                'pipeline-column flex flex-col border overflow-hidden',
                col.borderColor
              )}>
                {/* Column header */}
                <div className="flex items-center justify-between mb-3 flex-shrink-0">
                  <div className="flex items-center gap-2">
                    <div className={clsx('w-2 h-2 rounded-full', col.dotColor)} />
                    <span className={clsx('text-sm font-semibold', col.color)}>{col.label}</span>
                  </div>
                  <span className="text-xs font-bold text-slate-500 bg-white/5 w-5 h-5 flex items-center justify-center rounded-full">
                    {colTickets.length}
                  </span>
                </div>

                {/* Cards */}
                <div className="flex-1 space-y-2 overflow-y-auto pr-1">
                  {loading ? (
                    // Loading skeleton
                    [0, 1].map(i => (
                      <div key={i} className="rounded-xl border border-white/5 bg-white/3 p-3 animate-pulse space-y-2">
                        <div className="h-2.5 bg-white/8 rounded w-16" />
                        <div className="h-2 bg-white/5 rounded w-full" />
                        <div className="h-2 bg-white/5 rounded w-3/4" />
                      </div>
                    ))
                  ) : colTickets.length === 0 ? (
                    <div className="flex items-center justify-center h-20 text-xs text-slate-700">
                      No tickets
                    </div>
                  ) : (
                    colTickets.map(t => (
                      <div
                        key={t.id}
                        onClick={() => setSelected(selected?.id === t.id ? null : t)}
                        className={clsx(
                          'cursor-pointer transition-all',
                          selected?.id === t.id && 'ring-1 ring-blue-500/50 rounded-xl'
                        )}
                      >
                        <TicketCard
                          ticket={t}
                          compact={true}
                        />
                      </div>
                    ))
                  )}
                </div>
              </div>
            )
          })}
        </div>

        {/* Detail panel */}
        {selected && (
          <div className="w-72 flex-shrink-0">
            <TicketDetailPanel
              ticket={selected}
              onClose={() => setSelected(null)}
              onExecute={(id) => runAction(id, 'execute')}
              onPlan={(id) => runAction(id, 'plan')}
              onDebrief={(id) => runAction(id, 'debrief')}
            />
          </div>
        )}
      </div>
    </div>
  )
}
