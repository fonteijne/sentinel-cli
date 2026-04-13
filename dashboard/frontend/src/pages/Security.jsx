import React, { useState } from 'react'
import { Shield, AlertTriangle, ShieldAlert, ShieldCheck, Info, ChevronRight, Clock, Code2, Bug } from 'lucide-react'
import { SeverityPieChart, SecurityTrendChart } from '../components/SecurityChart.jsx'
import clsx from 'clsx'

const MOCK_FINDINGS = [
  {
    id: 'SEC-001',
    severity: 'Critical',
    title: 'SQL injection vulnerability in user search endpoint',
    ticket: 'PROJ-142',
    file: 'src/api/users.py',
    line: 87,
    rule: 'A03:2021-Injection',
    status: 'open',
    found: '2h ago',
    description: 'User-supplied input is concatenated directly into SQL query without parameterization.',
  },
  {
    id: 'SEC-002',
    severity: 'High',
    title: 'Missing authentication on admin API endpoints',
    ticket: 'PROJ-140',
    file: 'src/api/admin.py',
    line: 34,
    rule: 'A01:2021-Access Control',
    status: 'resolved',
    found: '1d ago',
    description: 'Several admin routes do not require authentication headers.',
  },
  {
    id: 'SEC-003',
    severity: 'High',
    title: 'JWT secret stored in plaintext in config file',
    ticket: 'PROJ-138',
    file: 'config/config.yaml',
    line: 15,
    rule: 'A02:2021-Cryptographic',
    status: 'resolved',
    found: '3d ago',
    description: 'JWT signing secret is stored in config.yaml which is tracked by git.',
  },
  {
    id: 'SEC-004',
    severity: 'Medium',
    title: 'Weak password hashing algorithm (MD5)',
    ticket: 'PROJ-136',
    file: 'src/auth/password.py',
    line: 22,
    rule: 'A02:2021-Cryptographic',
    status: 'open',
    found: '5h ago',
    description: 'MD5 is used for password hashing — should use bcrypt or argon2.',
  },
  {
    id: 'SEC-005',
    severity: 'Medium',
    title: 'Exposed stack trace in error responses',
    ticket: 'PROJ-143',
    file: 'src/middleware/error_handler.py',
    line: 45,
    rule: 'A05:2021-Misconfiguration',
    status: 'open',
    found: '1h ago',
    description: 'Stack traces are included in 500 error responses in production mode.',
  },
  {
    id: 'SEC-006',
    severity: 'Low',
    title: 'Missing CORS configuration for API routes',
    ticket: 'PROJ-142',
    file: 'src/main.py',
    line: 12,
    rule: 'A05:2021-Misconfiguration',
    status: 'open',
    found: '2h ago',
    description: 'CORS headers allow all origins (*) without restriction.',
  },
  {
    id: 'SEC-007',
    severity: 'Low',
    title: 'Outdated dependency with known CVE',
    ticket: 'SHOP-88',
    file: 'composer.json',
    line: null,
    rule: 'A06:2021-Vulnerable Components',
    status: 'open',
    found: '4h ago',
    description: 'symfony/http-foundation 5.4.0 has CVE-2023-46733.',
  },
]

const SEVERITY_CONFIG = {
  Critical: { icon: ShieldAlert, color: 'text-red-400', bg: 'bg-red-500/10', border: 'border-red-500/25', dot: 'bg-red-400' },
  High: { icon: AlertTriangle, color: 'text-orange-400', bg: 'bg-orange-500/10', border: 'border-orange-500/25', dot: 'bg-orange-400' },
  Medium: { icon: Info, color: 'text-amber-400', bg: 'bg-amber-500/10', border: 'border-amber-500/25', dot: 'bg-amber-400' },
  Low: { icon: Info, color: 'text-blue-400', bg: 'bg-blue-500/10', border: 'border-blue-500/25', dot: 'bg-blue-400' },
}

const STATUS_CFG = {
  open: { color: 'text-red-400', bg: 'bg-red-500/8', border: 'border-red-500/20', label: 'Open' },
  resolved: { color: 'text-emerald-400', bg: 'bg-emerald-500/8', border: 'border-emerald-500/20', label: 'Resolved' },
  ignored: { color: 'text-slate-500', bg: 'bg-slate-500/8', border: 'border-slate-500/20', label: 'Ignored' },
}

function FindingRow({ finding, isSelected, onClick }) {
  const sev = SEVERITY_CONFIG[finding.severity] || SEVERITY_CONFIG.Medium
  const SevIcon = sev.icon
  const stat = STATUS_CFG[finding.status] || STATUS_CFG.open

  return (
    <button
      onClick={onClick}
      className={clsx(
        'w-full flex items-start gap-4 px-4 py-3 text-left transition-all rounded-xl',
        isSelected
          ? 'bg-blue-500/10 border border-blue-500/20'
          : 'hover:bg-white/3 border border-transparent'
      )}
    >
      <div className={clsx('w-7 h-7 rounded-lg flex items-center justify-center flex-shrink-0 mt-0.5', sev.bg)}>
        <SevIcon className={clsx('w-3.5 h-3.5', sev.color)} />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <span className={clsx('badge border text-[10px]', sev.bg, sev.border, sev.color)}>
            {finding.severity}
          </span>
          <span className="text-[10px] font-mono text-slate-600">{finding.id}</span>
        </div>
        <p className="text-sm text-slate-200 leading-snug">{finding.title}</p>
        <div className="flex items-center gap-3 mt-1 text-xs text-slate-600">
          {finding.file && (
            <span className="flex items-center gap-1 font-mono truncate max-w-[160px]">
              <Code2 className="w-3 h-3 flex-shrink-0" />
              {finding.file}{finding.line ? `:${finding.line}` : ''}
            </span>
          )}
          <span className="flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {finding.found}
          </span>
        </div>
      </div>

      <div className="flex-shrink-0">
        <span className={clsx('badge border text-[10px]', stat.bg, stat.border, stat.color)}>
          {stat.label}
        </span>
      </div>
    </button>
  )
}

function FindingDetail({ finding, onClose }) {
  if (!finding) return (
    <div className="glass-card p-6 h-full flex items-center justify-center text-slate-600 text-sm">
      Select a finding to view details
    </div>
  )

  const sev = SEVERITY_CONFIG[finding.severity] || SEVERITY_CONFIG.Medium
  const stat = STATUS_CFG[finding.status] || STATUS_CFG.open

  return (
    <div className="glass-card p-5 h-full overflow-y-auto border border-blue-500/15">
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-2">
          <span className={clsx('badge border', sev.bg, sev.border, sev.color)}>{finding.severity}</span>
          <span className={clsx('badge border', stat.bg, stat.border, stat.color)}>{stat.label}</span>
        </div>
        <button
          onClick={onClose}
          className="w-6 h-6 flex items-center justify-center text-slate-600 hover:text-slate-300"
        >×</button>
      </div>

      <h3 className="text-[15px] font-semibold text-slate-100 leading-snug mb-4">{finding.title}</h3>

      <div className="space-y-3 text-sm mb-4">
        <div className="flex gap-3">
          <span className="text-slate-600 w-20 flex-shrink-0">Finding</span>
          <span className="font-mono text-blue-400">{finding.id}</span>
        </div>
        <div className="flex gap-3">
          <span className="text-slate-600 w-20 flex-shrink-0">Rule</span>
          <span className="text-slate-300 text-xs">{finding.rule}</span>
        </div>
        {finding.ticket && (
          <div className="flex gap-3">
            <span className="text-slate-600 w-20 flex-shrink-0">Ticket</span>
            <span className="font-mono text-xs text-cyan-400">{finding.ticket}</span>
          </div>
        )}
        {finding.file && (
          <div className="flex gap-3">
            <span className="text-slate-600 w-20 flex-shrink-0">Location</span>
            <span className="font-mono text-xs text-slate-400">
              {finding.file}{finding.line ? `:${finding.line}` : ''}
            </span>
          </div>
        )}
        <div className="flex gap-3">
          <span className="text-slate-600 w-20 flex-shrink-0">Found</span>
          <span className="text-slate-400">{finding.found}</span>
        </div>
      </div>

      <div className="divider" />

      <div className="mt-4">
        <h4 className="text-xs font-semibold text-slate-400 uppercase tracking-wider mb-2">Description</h4>
        <p className="text-sm text-slate-400 leading-relaxed">{finding.description}</p>
      </div>

      <div className="mt-4 flex gap-2">
        <button className="btn-danger text-xs flex-1 justify-center">
          <Bug className="w-3.5 h-3.5" />
          Mark Resolved
        </button>
        <button className="btn-secondary text-xs">
          Ignore
        </button>
      </div>
    </div>
  )
}

export default function Security() {
  const [selected, setSelected] = useState(null)
  const [severityFilter, setSeverityFilter] = useState('ALL')

  const findings = MOCK_FINDINGS
  const openFindings = findings.filter(f => f.status === 'open')
  const sevCounts = {
    Critical: findings.filter(f => f.severity === 'Critical').length,
    High: findings.filter(f => f.severity === 'High').length,
    Medium: findings.filter(f => f.severity === 'Medium').length,
    Low: findings.filter(f => f.severity === 'Low').length,
  }

  const score = Math.max(0, 100 - (sevCounts.Critical * 20) - (sevCounts.High * 8) - (sevCounts.Medium * 3) - (sevCounts.Low * 1))

  const filtered = findings.filter(f => severityFilter === 'ALL' || f.severity === severityFilter)

  return (
    <div className="p-6 space-y-5">
      {/* Stats row */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4">
        <div className="glass-card p-4 border border-emerald-500/15">
          <div className="flex items-center justify-between mb-2">
            <ShieldCheck className="w-5 h-5 text-emerald-400" />
            <span className="text-xs text-slate-600">Score</span>
          </div>
          <div className="text-3xl font-bold text-emerald-400 tabular-nums">{score}%</div>
          <div className="text-xs text-slate-500 mt-1">Security Score</div>
        </div>
        {Object.entries(sevCounts).map(([sev, count]) => {
          const cfg = SEVERITY_CONFIG[sev]
          const Icon = cfg.icon
          return (
            <div key={sev} className={clsx('glass-card p-4 border', cfg.bg, cfg.border)}>
              <div className="flex items-center justify-between mb-2">
                <Icon className={clsx('w-5 h-5', cfg.color)} />
                <span className="text-xs text-slate-600">{sev}</span>
              </div>
              <div className={clsx('text-3xl font-bold tabular-nums', cfg.color)}>{count}</div>
              <div className="text-xs text-slate-500 mt-1">Findings</div>
            </div>
          )
        })}
      </div>

      <div className="grid grid-cols-12 gap-5">
        {/* Charts */}
        <div className="col-span-12 lg:col-span-4 space-y-4">
          <div className="glass-card p-5">
            <h3 className="section-title mb-4">Severity Breakdown</h3>
            <SeverityPieChart data={sevCounts} />
          </div>
          <div className="glass-card p-5">
            <h3 className="section-title mb-4">Findings Trend</h3>
            <SecurityTrendChart />
          </div>
        </div>

        {/* Findings list + detail */}
        <div className="col-span-12 lg:col-span-8 grid grid-cols-12 gap-4">
          {/* List */}
          <div className="col-span-12 lg:col-span-7 glass-card overflow-hidden flex flex-col">
            {/* Filter bar */}
            <div className="flex items-center gap-2 px-4 py-3 border-b border-blue-500/8 flex-shrink-0">
              {['ALL', 'Critical', 'High', 'Medium', 'Low'].map(sev => {
                const cfg = SEVERITY_CONFIG[sev]
                return (
                  <button
                    key={sev}
                    onClick={() => setSeverityFilter(sev)}
                    className={clsx(
                      'text-[11px] font-medium px-2.5 py-1 rounded-lg border transition-all',
                      severityFilter === sev
                        ? sev === 'ALL'
                          ? 'bg-blue-500/20 border-blue-500/30 text-blue-300'
                          : `${cfg.bg} ${cfg.border} ${cfg.color}`
                        : 'bg-transparent border-white/8 text-slate-600 hover:text-slate-400'
                    )}
                  >
                    {sev} {sev !== 'ALL' && `(${sevCounts[sev] || 0})`}
                  </button>
                )
              })}
            </div>

            <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
              {filtered.length === 0 ? (
                <div className="text-center py-8 text-slate-600 text-sm">
                  No findings for this severity level
                </div>
              ) : (
                filtered.map(f => (
                  <FindingRow
                    key={f.id}
                    finding={f}
                    isSelected={selected?.id === f.id}
                    onClick={() => setSelected(selected?.id === f.id ? null : f)}
                  />
                ))
              )}
            </div>
          </div>

          {/* Detail */}
          <div className="col-span-12 lg:col-span-5">
            <FindingDetail finding={selected} onClose={() => setSelected(null)} />
          </div>
        </div>
      </div>
    </div>
  )
}
