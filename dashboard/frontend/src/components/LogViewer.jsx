import React, { useState, useEffect, useRef, useCallback } from 'react'
import { Search, Filter, ArrowDownToLine, Pause, Play, Trash2 } from 'lucide-react'
import clsx from 'clsx'

const LOG_LEVEL_COLORS = {
  ERROR: { text: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/20' },
  WARNING: { text: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/20' },
  INFO: { text: 'text-blue-400', bg: 'bg-blue-500/8', border: 'border-blue-500/15' },
  DEBUG: { text: 'text-slate-500', bg: 'bg-slate-500/8', border: 'border-slate-500/15' },
}

function LogLine({ log }) {
  const cfg = LOG_LEVEL_COLORS[log.level] || LOG_LEVEL_COLORS.INFO

  return (
    <div className="flex items-start gap-3 px-4 py-2 hover:bg-white/2 font-mono text-xs group">
      <span className="flex-shrink-0 text-slate-600 tabular-nums w-20">{log.timestamp}</span>
      <span className={clsx(
        'flex-shrink-0 badge text-[10px] w-16 justify-center border',
        cfg.text, cfg.bg, cfg.border
      )}>
        {log.level}
      </span>
      <span className="flex-shrink-0 text-cyan-400/60 w-28 truncate">{log.name}</span>
      <span className="text-slate-300 flex-1 break-all leading-relaxed">{log.message}</span>
    </div>
  )
}

// Mock log generator for demo
function generateMockLog(id) {
  const levels = ['INFO', 'INFO', 'INFO', 'DEBUG', 'WARNING', 'ERROR']
  const names = ['sentinel.plan', 'sentinel.execute', 'sentinel.security', 'sentinel.jira', 'sentinel.gitlab', 'sentinel.llm']
  const messages = [
    'Fetching Jira ticket PROJ-142...',
    'Generating implementation plan with confidence scoring',
    'LLM request sent — model: claude-opus-4-5',
    'Worktree created at /workspaces/project/worktrees/PROJ-142',
    'Security scan started — OWASP rules loaded',
    'Iteration 2/5 — running developer agent',
    'Git push completed: feature/PROJ-142',
    'MR created: !89 — Implement authentication module',
    'Rate limit hit — backing off 30s',
    'Security review PASSED — 0 critical, 2 medium findings',
    'Confidence score: 97/100 (threshold: 95) — PASS',
    'Debrief posted to Jira PROJ-142',
    'Container cleanup complete',
    'Beads task updated: sentinel-PROJ-142 → done',
  ]

  const now = new Date()
  const pad = n => String(n).padStart(2, '0')
  const ts = `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`

  return {
    id,
    timestamp: ts,
    level: levels[Math.floor(Math.random() * levels.length)],
    name: names[Math.floor(Math.random() * names.length)],
    message: messages[Math.floor(Math.random() * messages.length)],
  }
}

export default function LogViewer({ websocketUrl = null }) {
  const [logs, setLogs] = useState(() => Array.from({ length: 30 }, (_, i) => generateMockLog(i)))
  const [filter, setFilter] = useState('')
  const [levelFilter, setLevelFilter] = useState('ALL')
  const [autoScroll, setAutoScroll] = useState(true)
  const [paused, setPaused] = useState(false)
  const logsEndRef = useRef(null)
  const wsRef = useRef(null)
  const counterRef = useRef(30)
  const pausedRef = useRef(false)

  pausedRef.current = paused

  // Auto-scroll
  useEffect(() => {
    if (autoScroll) {
      logsEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, autoScroll])

  // Mock streaming when no WebSocket
  useEffect(() => {
    if (websocketUrl) {
      wsRef.current = new WebSocket(websocketUrl)
      wsRef.current.onmessage = (e) => {
        if (pausedRef.current) return
        try {
          const log = JSON.parse(e.data)
          setLogs(prev => [...prev.slice(-500), { ...log, id: counterRef.current++ }])
        } catch {}
      }
      return () => wsRef.current?.close()
    } else {
      // Demo mode: simulate incoming logs
      const interval = setInterval(() => {
        if (pausedRef.current) return
        setLogs(prev => [...prev.slice(-500), generateMockLog(counterRef.current++)])
      }, 2000)
      return () => clearInterval(interval)
    }
  }, [websocketUrl])

  const clearLogs = useCallback(() => setLogs([]), [])

  const LEVELS = ['ALL', 'INFO', 'WARNING', 'ERROR', 'DEBUG']

  const filteredLogs = logs.filter(log => {
    const levelOk = levelFilter === 'ALL' || log.level === levelFilter
    const textOk = !filter || log.message.toLowerCase().includes(filter.toLowerCase()) || log.name.toLowerCase().includes(filter.toLowerCase())
    return levelOk && textOk
  })

  return (
    <div className="flex flex-col h-full glass-card overflow-hidden">
      {/* Toolbar */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-blue-500/10 flex-shrink-0">
        {/* Search */}
        <div className="relative flex-1 max-w-sm">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-600" />
          <input
            type="text"
            placeholder="Filter logs..."
            value={filter}
            onChange={e => setFilter(e.target.value)}
            className="dark-input w-full pl-9 py-1.5 text-xs"
          />
        </div>

        {/* Level filter */}
        <div className="flex items-center gap-1">
          {LEVELS.map(lvl => {
            const cfg = LOG_LEVEL_COLORS[lvl]
            return (
              <button
                key={lvl}
                onClick={() => setLevelFilter(lvl)}
                className={clsx(
                  'text-[11px] font-medium px-2.5 py-1 rounded-lg border transition-all',
                  levelFilter === lvl
                    ? lvl === 'ALL'
                      ? 'bg-blue-500/20 border-blue-500/30 text-blue-300'
                      : `${cfg?.bg} ${cfg?.border} ${cfg?.text}`
                    : 'bg-transparent border-white/8 text-slate-600 hover:text-slate-400'
                )}
              >
                {lvl}
              </button>
            )
          })}
        </div>

        <div className="flex items-center gap-1 ml-auto">
          {/* Auto-scroll toggle */}
          <button
            onClick={() => setAutoScroll(!autoScroll)}
            className={clsx(
              'w-8 h-8 flex items-center justify-center rounded-lg border transition-all',
              autoScroll
                ? 'bg-blue-500/15 border-blue-500/25 text-blue-400'
                : 'bg-transparent border-white/8 text-slate-600 hover:text-slate-400'
            )}
            title="Auto-scroll"
          >
            <ArrowDownToLine className="w-3.5 h-3.5" />
          </button>

          {/* Pause/Resume */}
          <button
            onClick={() => setPaused(!paused)}
            className={clsx(
              'w-8 h-8 flex items-center justify-center rounded-lg border transition-all',
              paused
                ? 'bg-amber-500/15 border-amber-500/25 text-amber-400'
                : 'bg-transparent border-white/8 text-slate-600 hover:text-slate-400'
            )}
            title={paused ? 'Resume' : 'Pause'}
          >
            {paused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
          </button>

          {/* Clear */}
          <button
            onClick={clearLogs}
            className="w-8 h-8 flex items-center justify-center rounded-lg border border-white/8 text-slate-600 hover:text-red-400 hover:border-red-500/25 transition-all"
            title="Clear logs"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Status bar */}
      <div className="flex items-center gap-4 px-4 py-1.5 border-b border-blue-500/6 bg-white/1 flex-shrink-0">
        <span className="text-[11px] text-slate-600">
          {filteredLogs.length} entries
          {levelFilter !== 'ALL' && ` (${levelFilter})`}
          {filter && ` matching "${filter}"`}
        </span>
        <div className="ml-auto flex items-center gap-2">
          {paused ? (
            <span className="flex items-center gap-1.5 text-[11px] text-amber-400">
              <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
              Paused
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-[11px] text-emerald-400">
              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              Live
            </span>
          )}
        </div>
      </div>

      {/* Log lines */}
      <div className="flex-1 overflow-y-auto">
        {filteredLogs.length === 0 ? (
          <div className="flex items-center justify-center h-32 text-slate-600 text-sm">
            No log entries match your filters
          </div>
        ) : (
          <>
            {filteredLogs.map(log => (
              <LogLine key={log.id} log={log} />
            ))}
            <div ref={logsEndRef} />
          </>
        )}
      </div>
    </div>
  )
}
