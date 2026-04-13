import React from 'react'
import {
  PieChart, Pie, Cell, Tooltip, ResponsiveContainer,
  BarChart, Bar, XAxis, YAxis, CartesianGrid, LineChart, Line, Area, AreaChart,
} from 'recharts'

const SEVERITY_COLORS = {
  Critical: '#ef4444',
  High: '#f97316',
  Medium: '#f59e0b',
  Low: '#3b82f6',
}

const CUSTOM_TOOLTIP_STYLE = {
  background: 'rgba(13, 18, 50, 0.95)',
  border: '1px solid rgba(59, 130, 246, 0.2)',
  borderRadius: '8px',
  padding: '10px 14px',
  fontSize: '13px',
  color: '#f1f5f9',
}

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div style={CUSTOM_TOOLTIP_STYLE}>
      {label && <div className="text-slate-400 text-xs mb-1">{label}</div>}
      {payload.map((p, i) => (
        <div key={i} className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full" style={{ background: p.color || p.fill }} />
          <span className="font-medium">{p.value}</span>
          {p.name !== 'value' && <span className="text-slate-400">{p.name}</span>}
        </div>
      ))}
    </div>
  )
}

export function SeverityPieChart({ data }) {
  const pieData = Object.entries(data || { Critical: 2, High: 5, Medium: 12, Low: 8 }).map(([name, value]) => ({
    name, value, fill: SEVERITY_COLORS[name] || '#64748b',
  }))

  return (
    <div className="flex items-center gap-6">
      <ResponsiveContainer width={140} height={140}>
        <PieChart>
          <Pie
            data={pieData}
            cx="50%"
            cy="50%"
            innerRadius={42}
            outerRadius={62}
            paddingAngle={3}
            dataKey="value"
          >
            {pieData.map((entry, i) => (
              <Cell key={i} fill={entry.fill} stroke="none" />
            ))}
          </Pie>
          <Tooltip content={<CustomTooltip />} />
        </PieChart>
      </ResponsiveContainer>

      <div className="space-y-2 flex-1">
        {pieData.map(({ name, value, fill }) => (
          <div key={name} className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full" style={{ background: fill }} />
              <span className="text-xs text-slate-400">{name}</span>
            </div>
            <span className="text-xs font-semibold text-slate-200 tabular-nums">{value}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function SecurityTrendChart({ data }) {
  const chartData = data || [
    { week: 'W1', critical: 4, high: 8, medium: 15 },
    { week: 'W2', critical: 3, high: 7, medium: 13 },
    { week: 'W3', critical: 2, high: 6, medium: 14 },
    { week: 'W4', critical: 2, high: 5, medium: 12 },
    { week: 'W5', critical: 1, high: 4, medium: 10 },
    { week: 'W6', critical: 2, high: 5, medium: 12 },
  ]

  return (
    <ResponsiveContainer width="100%" height={180}>
      <AreaChart data={chartData} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
        <defs>
          <linearGradient id="critGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#ef4444" stopOpacity={0.3} />
            <stop offset="95%" stopColor="#ef4444" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="highGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#f97316" stopOpacity={0.2} />
            <stop offset="95%" stopColor="#f97316" stopOpacity={0} />
          </linearGradient>
          <linearGradient id="medGrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.15} />
            <stop offset="95%" stopColor="#f59e0b" stopOpacity={0} />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(59,130,246,0.08)" />
        <XAxis dataKey="week" tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} />
        <Tooltip content={<CustomTooltip />} />
        <Area type="monotone" dataKey="critical" stroke="#ef4444" fill="url(#critGrad)" strokeWidth={2} dot={false} />
        <Area type="monotone" dataKey="high" stroke="#f97316" fill="url(#highGrad)" strokeWidth={1.5} dot={false} />
        <Area type="monotone" dataKey="medium" stroke="#f59e0b" fill="url(#medGrad)" strokeWidth={1.5} dot={false} />
      </AreaChart>
    </ResponsiveContainer>
  )
}

export function AgentRunBarChart({ data }) {
  const chartData = data || [
    { name: 'Mon', runs: 4, success: 4 },
    { name: 'Tue', runs: 7, success: 6 },
    { name: 'Wed', runs: 5, success: 5 },
    { name: 'Thu', runs: 9, success: 8 },
    { name: 'Fri', runs: 6, success: 6 },
    { name: 'Sat', runs: 2, success: 2 },
    { name: 'Sun', runs: 3, success: 3 },
  ]

  return (
    <ResponsiveContainer width="100%" height={160}>
      <BarChart data={chartData} margin={{ top: 5, right: 10, left: -20, bottom: 0 }} barSize={20}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(59,130,246,0.08)" vertical={false} />
        <XAxis dataKey="name" tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} />
        <YAxis tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} />
        <Tooltip content={<CustomTooltip />} />
        <Bar dataKey="runs" fill="rgba(59,130,246,0.3)" radius={[4, 4, 0, 0]} />
        <Bar dataKey="success" fill="#3b82f6" radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  )
}

export function ConfidenceLineChart({ data }) {
  const chartData = data || [
    { tick: 'PROJ-138', score: 82 },
    { tick: 'PROJ-139', score: 91 },
    { tick: 'PROJ-140', score: 96 },
    { tick: 'PROJ-141', score: 88 },
    { tick: 'PROJ-142', score: 97 },
    { tick: 'PROJ-143', score: 94 },
    { tick: 'PROJ-144', score: 99 },
  ]

  return (
    <ResponsiveContainer width="100%" height={140}>
      <LineChart data={chartData} margin={{ top: 5, right: 10, left: -20, bottom: 0 }}>
        <defs>
          <linearGradient id="confGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#3b82f6" />
            <stop offset="100%" stopColor="#06b6d4" />
          </linearGradient>
        </defs>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(59,130,246,0.08)" />
        <XAxis dataKey="tick" tick={{ fill: '#64748b', fontSize: 10 }} axisLine={false} tickLine={false} />
        <YAxis domain={[70, 100]} tick={{ fill: '#64748b', fontSize: 11 }} axisLine={false} tickLine={false} />
        <Tooltip content={<CustomTooltip />} />
        {/* Threshold line at 95 */}
        <Line type="monotone" dataKey="score" stroke="url(#confGrad)" strokeWidth={2.5} dot={{ fill: '#3b82f6', r: 4, strokeWidth: 0 }} activeDot={{ r: 6 }} />
      </LineChart>
    </ResponsiveContainer>
  )
}
