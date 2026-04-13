import React, { useState } from 'react'
import { Terminal, Info } from 'lucide-react'
import LogViewer from '../components/LogViewer.jsx'

export default function Logs() {
  // In production, connect to real WebSocket
  const wsUrl = typeof window !== 'undefined'
    ? `ws://${window.location.host}/ws/logs`
    : null

  return (
    <div className="p-6 h-[calc(100vh-56px)] flex flex-col space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between flex-shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-blue-500/10 border border-blue-500/20 flex items-center justify-center">
            <Terminal className="w-4 h-4 text-blue-400" />
          </div>
          <div>
            <div className="text-sm font-semibold text-slate-200">Real-time Log Stream</div>
            <div className="text-xs text-slate-600">WebSocket: {wsUrl || 'demo mode'}</div>
          </div>
        </div>

        <div className="flex items-center gap-2 text-xs text-slate-600 bg-white/3 border border-white/8 rounded-xl px-3 py-2">
          <Info className="w-3.5 h-3.5 text-blue-400/60" />
          <span>Logs stream via WebSocket from the Sentinel CLI process</span>
        </div>
      </div>

      {/* Log viewer — takes remaining height */}
      <div className="flex-1 min-h-0">
        <LogViewer websocketUrl={null} />
      </div>
    </div>
  )
}
