import React, { useState, useEffect, useCallback } from 'react'
import { Search, RefreshCw, Play, FileText, MessageSquare, Tag, Radio, Plus, X, RotateCcw, Trash2 } from 'lucide-react'
import TicketCard from '../components/TicketCard.jsx'
import clsx from 'clsx'
import axios from 'axios'

const COLUMNS = [
  { key: 'plan', label: 'Plan', color: 'text-cyan-400', borderColor: 'border-cyan-500/20', dotColor: 'bg-cyan-400' },
  { key: 'execute', label: 'Execute', color: 'text-blue-400', borderColor: 'border-blue-500/20', dotColor: 'bg-blue-400' },
  { key: 'review', label: 'Review', color: 'text-amber-400', borderColor: 'border-amber-500/20', dotColor: 'bg-amber-400' },
  { key: 'done', label: 'Done', color: 'text-emerald-400', borderColor: 'border-emerald-500/20', dotColor: 'bg-emerald-400' },
]

function StartTicketModal({ onClose, onSubmit }) {
  const [projects, setProjects] = useState([])
  const [ticketId, setTicketId] = useState('')
  const [projectKey, setProjectKey] = useState('')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    axios.get('/api/projects')
      .then(r => {
        const list = r.data || []
        setProjects(list)
        if (list.length > 0) setProjectKey(list[0].key)
      })
      .catch(() => setProjects([]))
  }, [])

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!ticketId.trim()) return
    setSubmitting(true)
    await onSubmit(ticketId.trim(), projectKey)
    setSubmitting(false)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm">
      <div className="glass-card p-6 border border-blue-500/25 w-full max-w-md mx-4">
        <div className="flex items-center justify-between mb-5">
          <h3 className="text-[15px] font-semibold text-slate-100">Start New Ticket</h3>
          <button
            onClick={onClose}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-500 hover:text-slate-300 hover:bg-white/5"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">Project</label>
            {projects.length > 0 ? (
              <select
                value={projectKey}
                onChange={e => setProjectKey(e.target.value)}
                className="dark-input w-full"
              >
                {projects.map(p => (
                  <option key={p.key} value={p.key}>{p.key} — {p.name || p.key}</option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                placeholder="Project key (e.g. PROJ)"
                value={projectKey}
                onChange={e => setProjectKey(e.target.value)}
                className="dark-input w-full"
                required
              />
            )}
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">Ticket ID</label>
            <input
              type="text"
              placeholder="e.g. PROJ-123"
              value={ticketId}
              onChange={e => setTicketId(e.target.value)}
              className="dark-input w-full font-mono"
              required
              autoFocus
            />
          </div>

          <div className="flex items-center gap-3 pt-2">
            <button
              type="submit"
              disabled={submitting || !ticketId.trim()}
              className="btn-primary flex-1 justify-center"
            >
              {submitting ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
              {submitting ? 'Starting...' : 'Start Ticket'}
            </button>
            <button type="button" onClick={onClose} className="btn-secondary">
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function TicketDetailPanel({ ticket, onClose, onExecute, onPlan, onDebrief, onReset, onRemoveWorktree }) {
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
          onClick={() => onPlan(ticket.id, ticket.project)}
          className="btn-secondary w-full justify-start"
        >
          <FileText className="w-4 h-4" />
          Generate / Revise Plan
        </button>
        <button
          onClick={() => onExecute(ticket.id, ticket.project)}
          className="btn-primary w-full justify-start"
        >
          <Play className="w-4 h-4" />
          Execute Implementation
        </button>
        <button
          onClick={() => onDebrief(ticket.id, ticket.project)}
          className="btn-secondary w-full justify-start"
        >
          <MessageSquare className="w-4 h-4" />
          Run Functional Debrief
        </button>

        <div className="pt-2 space-y-2">
          <p className="text-xs font-medium text-slate-600 uppercase tracking-wider">Worktree</p>
          <button
            onClick={() => onReset(ticket.id, ticket.project)}
            className="btn-secondary w-full justify-start text-amber-400 hover:text-amber-300"
          >
            <RotateCcw className="w-4 h-4" />
            Reset Ticket
          </button>
          <button
            onClick={() => onRemoveWorktree(ticket.id, ticket.project)}
            className="btn-danger w-full justify-start"
          >
            <Trash2 className="w-4 h-4" />
            Remove Worktree
          </button>
        </div>
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
  const [tickets, setTickets] = useState({ plan: [], execute: [], review: [], done: [] })
  const [selected, setSelected] = useState(null)
  const [search, setSearch] = useState('')
  const [projectFilter, setProjectFilter] = useState('all')
  const [running, setRunning] = useState({})
  const [notification, setNotification] = useState(null)
  const [loading, setLoading] = useState(false)
  const [isConnected, setIsConnected] = useState(false)
  const [showNewTicketModal, setShowNewTicketModal] = useState(false)

  const notify = (msg, type = 'info') => {
    setNotification({ msg, type })
    setTimeout(() => setNotification(null), 3000)
  }

  const fetchTickets = useCallback(async () => {
    setLoading(true)
    try {
      const res = await axios.get('/api/tickets')
      const data = res.data
      setTickets({
        plan: data.plan || [],
        execute: data.execute || [],
        review: data.review || [],
        done: data.done || [],
      })
      setIsConnected(true)
    } catch {
      setTickets({ plan: [], execute: [], review: [], done: [] })
      setIsConnected(false)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchTickets()
  }, [fetchTickets])

  const runAction = async (ticketId, action, project) => {
    setRunning(r => ({ ...r, [`${ticketId}-${action}`]: true }))
    try {
      const params = project ? `?project=${encodeURIComponent(project)}` : ''
      await axios.post(`/api/tickets/${ticketId}/${action}${params}`)
      notify(`${action} started for ${ticketId}`, 'success')
    } catch {
      notify(`${action} command sent for ${ticketId} — check Logs`, 'info')
    }
    setRunning(r => ({ ...r, [`${ticketId}-${action}`]: false }))
  }

  const handleReset = async (ticketId, project) => {
    try {
      const params = project ? `?project=${encodeURIComponent(project)}` : ''
      await axios.post(`/api/tickets/${ticketId}/reset${params}`)
      notify(`Reset started for ${ticketId}`, 'success')
      fetchTickets()
    } catch {
      notify(`Reset command sent for ${ticketId} — check Logs`, 'info')
    }
  }

  const handleRemoveWorktree = async (ticketId, project) => {
    try {
      const params = project ? `?project=${encodeURIComponent(project)}` : ''
      await axios.delete(`/api/tickets/${ticketId}/worktree${params}`)
      notify(`Worktree removed for ${ticketId}`, 'success')
      fetchTickets()
    } catch {
      notify(`Remove worktree command sent for ${ticketId}`, 'info')
    }
  }

  const handleStartNewTicket = async (ticketId, projectKey) => {
    try {
      const params = projectKey ? `?project=${encodeURIComponent(projectKey)}` : ''
      await axios.post(`/api/tickets/${ticketId}/worktree${params}`)
      notify(`Ticket ${ticketId} started`, 'success')
      setShowNewTicketModal(false)
      fetchTickets()
    } catch {
      notify(`Worktree creation started for ${ticketId} — check Logs`, 'info')
      setShowNewTicketModal(false)
    }
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
      {/* New Ticket Modal */}
      {showNewTicketModal && (
        <StartTicketModal
          onClose={() => setShowNewTicketModal(false)}
          onSubmit={handleStartNewTicket}
        />
      )}

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
        {/* Phase counts + connection badge */}
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
          {/* Connected / Offline badge */}
          {isConnected ? (
            <span className="flex items-center gap-1.5 text-xs font-semibold text-emerald-400 bg-emerald-500/10 border border-emerald-500/25 px-2 py-0.5 rounded-full">
              <Radio className="w-3 h-3" />
              Connected
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs font-semibold text-red-400 bg-red-500/10 border border-red-500/25 px-2 py-0.5 rounded-full">
              <Radio className="w-3 h-3" />
              Offline
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
          {/* Start New Ticket */}
          <button
            onClick={() => setShowNewTicketModal(true)}
            className="btn-primary px-3 py-1.5 flex items-center gap-1.5 text-xs"
          >
            <Plus className="w-3.5 h-3.5" />
            New Ticket
          </button>
        </div>
      </div>

      {/* Kanban + detail panel */}
      <div className="flex-1 flex gap-4 min-h-0">
        {/* Empty state when no tickets and not loading */}
        {!loading && totalCount === 0 ? (
          <div className="flex-1 flex items-center justify-center">
            <div className="text-center">
              <div className="w-16 h-16 rounded-full bg-blue-500/10 border border-blue-500/20 flex items-center justify-center mx-auto mb-4">
                <FileText className="w-8 h-8 text-blue-400/60" />
              </div>
              <p className="text-slate-300 text-sm font-medium mb-1">No active tickets</p>
              <p className="text-slate-600 text-xs mb-4">Start a new ticket to begin</p>
              <button
                onClick={() => setShowNewTicketModal(true)}
                className="btn-primary mx-auto"
              >
                <Plus className="w-4 h-4" />
                Start New Ticket
              </button>
            </div>
          </div>
        ) : (
          <>
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
                  onExecute={(id, project) => runAction(id, 'execute', project)}
                  onPlan={(id, project) => runAction(id, 'plan', project)}
                  onDebrief={(id, project) => runAction(id, 'debrief', project)}
                  onReset={handleReset}
                  onRemoveWorktree={handleRemoveWorktree}
                />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
