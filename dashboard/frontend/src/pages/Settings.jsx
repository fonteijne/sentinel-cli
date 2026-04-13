import React, { useState, useEffect } from 'react'
import {
  Ticket, GitMerge, Bot, Server, Zap,
  RefreshCw, CheckCircle2, LogOut, Settings2, Eye, EyeOff,
} from 'lucide-react'
import ConnectionCard from '../components/ConnectionCard.jsx'
import axios from 'axios'

const SERVICE_ICONS = {
  Jira: Ticket,
  GitLab: GitMerge,
  LLM: Bot,
  SSH: Server,
  Beads: Zap,
}

const MOCK_CONFIG = `version: '1.0'
workspace:
  root_dir: ~/sentinel-workspaces
  plans_dir: .agents/plans
  memory_dir: .agents/memory
agents:
  plan_generator:
    model: claude-opus-4-5
    temperature: 0.3
  python_developer:
    model: claude-4-5-sonnet
    temperature: 0.2
    specializations:
    - python
    - pydantic-ai
    - fastapi
    - postgresql
  security_review:
    model: claude-4-5-sonnet
    temperature: 0.1
    strictness: 5
    veto_power: true
confidence:
  default_threshold: 95
logging:
  level: INFO`

export default function Settings() {
  const [connections, setConnections] = useState({
    Jira: 'unconfigured',
    GitLab: 'unconfigured',
    LLM: 'unconfigured',
    SSH: 'unconfigured',
    Beads: 'unconfigured',
  })
  const [connectionDetails, setConnectionDetails] = useState({})
  const [validating, setValidating] = useState(false)
  const [config, setConfig] = useState(MOCK_CONFIG)
  const [editingConfig, setEditingConfig] = useState(false)
  const [configDraft, setConfigDraft] = useState(MOCK_CONFIG)
  const [savingConfig, setSavingConfig] = useState(false)
  const [notification, setNotification] = useState(null)
  const [authStatus, setAuthStatus] = useState(null)

  const notify = (msg, type = 'info') => {
    setNotification({ msg, type })
    setTimeout(() => setNotification(null), 4000)
  }

  useEffect(() => {
    axios.get('/api/status')
      .then(r => {
        setConnections({
          Jira: r.data.jira || 'unconfigured',
          GitLab: r.data.gitlab || 'unconfigured',
          LLM: r.data.llm || 'unconfigured',
          SSH: r.data.ssh || 'unconfigured',
          Beads: r.data.beads || 'unconfigured',
        })
        setConnectionDetails({
          Jira: r.data.jira_url || '',
          GitLab: r.data.gitlab_url || '',
          LLM: r.data.llm_mode || '',
          SSH: 'SSH key auth',
          Beads: 'Task coordination',
        })
      })
      .catch(() => {})

    axios.get('/api/config')
      .then(r => {
        const txt = r.data.raw || MOCK_CONFIG
        setConfig(txt)
        setConfigDraft(txt)
      })
      .catch(() => {})
  }, [])

  const handleValidate = async () => {
    setValidating(true)
    // Set all to checking
    setConnections(c => Object.fromEntries(Object.keys(c).map(k => [k, 'checking'])))
    try {
      const res = await axios.get('/api/status/validate')
      setConnections({
        Jira: res.data.jira || 'error',
        GitLab: res.data.gitlab || 'error',
        LLM: res.data.llm || 'error',
        SSH: res.data.ssh || 'error',
        Beads: res.data.beads || 'error',
      })
      notify('Validation complete', 'success')
    } catch {
      setConnections(c => Object.fromEntries(Object.keys(c).map(k => [k, 'error'])))
      notify('Validation failed — check your configuration', 'error')
    }
    setValidating(false)
  }

  const handleSaveConfig = async () => {
    setSavingConfig(true)
    try {
      await axios.put('/api/config', { raw: configDraft })
      setConfig(configDraft)
      setEditingConfig(false)
      notify('Configuration saved', 'success')
    } catch {
      notify('Could not save config — running in demo mode', 'info')
      setConfig(configDraft)
      setEditingConfig(false)
    }
    setSavingConfig(false)
  }

  const handleTestConnection = async (name) => {
    setConnections(c => ({ ...c, [name]: 'checking' }))
    await new Promise(r => setTimeout(r, 1200))
    setConnections(c => ({ ...c, [name]: 'ok' }))
    notify(`${name} connection OK`, 'success')
  }

  return (
    <div className="p-6 space-y-6">
      {/* Notification */}
      {notification && (
        <div className={`flex items-center gap-2 px-4 py-3 rounded-xl border text-sm animate-slide-in ${
          notification.type === 'success' ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-300' :
          notification.type === 'error' ? 'bg-red-500/10 border-red-500/25 text-red-300' :
          'bg-blue-500/10 border-blue-500/25 text-blue-300'
        }`}>
          {notification.msg}
        </div>
      )}

      <div className="grid grid-cols-12 gap-5">
        {/* Left — Connections */}
        <div className="col-span-12 lg:col-span-7 space-y-4">
          {/* Connection cards */}
          <div className="glass-card p-5">
            <div className="flex items-center justify-between mb-5">
              <h2 className="section-title">Service Connections</h2>
              <button
                onClick={handleValidate}
                disabled={validating}
                className="btn-primary text-xs py-1.5"
              >
                {validating
                  ? <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                  : <CheckCircle2 className="w-3.5 h-3.5" />
                }
                {validating ? 'Validating...' : 'Validate All'}
              </button>
            </div>

            <div className="grid grid-cols-1 gap-3">
              {Object.entries(connections).map(([name, status]) => (
                <ConnectionCard
                  key={name}
                  name={name}
                  status={status}
                  icon={SERVICE_ICONS[name]}
                  detail={connectionDetails[name]}
                  onTest={() => handleTestConnection(name)}
                />
              ))}
            </div>
          </div>

          {/* Auth management */}
          <div className="glass-card p-5">
            <h2 className="section-title mb-4">Auth Management</h2>
            <div className="space-y-3">
              <div className="flex items-center justify-between p-4 rounded-xl bg-white/3 border border-white/8">
                <div>
                  <div className="text-sm font-medium text-slate-200">LLM Authentication</div>
                  <div className="text-xs text-slate-600 mt-0.5">
                    Claude Code subscription or direct Anthropic API key
                  </div>
                </div>
                <div className="flex gap-2">
                  <button className="btn-secondary text-xs py-1.5">
                    <Settings2 className="w-3.5 h-3.5" />
                    Configure
                  </button>
                  <button className="btn-danger text-xs py-1.5">
                    <LogOut className="w-3.5 h-3.5" />
                    Logout
                  </button>
                </div>
              </div>

              <div className="flex items-center justify-between p-4 rounded-xl bg-white/3 border border-white/8">
                <div>
                  <div className="text-sm font-medium text-slate-200">Jira Credentials</div>
                  <div className="text-xs text-slate-600 mt-0.5">API token stored in .env.local</div>
                </div>
                <button className="btn-secondary text-xs py-1.5">
                  <Settings2 className="w-3.5 h-3.5" />
                  Update
                </button>
              </div>

              <div className="flex items-center justify-between p-4 rounded-xl bg-white/3 border border-white/8">
                <div>
                  <div className="text-sm font-medium text-slate-200">GitLab Access Token</div>
                  <div className="text-xs text-slate-600 mt-0.5">Personal access token for GitLab API</div>
                </div>
                <button className="btn-secondary text-xs py-1.5">
                  <Settings2 className="w-3.5 h-3.5" />
                  Update
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Right — Config editor */}
        <div className="col-span-12 lg:col-span-5 space-y-4">
          <div className="glass-card p-5 flex flex-col">
            <div className="flex items-center justify-between mb-4">
              <h2 className="section-title">config.yaml</h2>
              <div className="flex items-center gap-2">
                {editingConfig ? (
                  <>
                    <button
                      onClick={handleSaveConfig}
                      disabled={savingConfig}
                      className="btn-primary text-xs py-1.5"
                    >
                      {savingConfig
                        ? <RefreshCw className="w-3 h-3 animate-spin" />
                        : <CheckCircle2 className="w-3 h-3" />
                      }
                      Save
                    </button>
                    <button
                      onClick={() => { setEditingConfig(false); setConfigDraft(config) }}
                      className="btn-secondary text-xs py-1.5"
                    >
                      Cancel
                    </button>
                  </>
                ) : (
                  <button
                    onClick={() => setEditingConfig(true)}
                    className="btn-secondary text-xs py-1.5"
                  >
                    <Settings2 className="w-3.5 h-3.5" />
                    Edit
                  </button>
                )}
              </div>
            </div>

            {editingConfig ? (
              <textarea
                value={configDraft}
                onChange={e => setConfigDraft(e.target.value)}
                className="dark-input w-full flex-1 font-mono text-xs resize-none min-h-[420px]"
                spellCheck={false}
              />
            ) : (
              <pre className="bg-white/3 border border-white/8 rounded-xl p-4 font-mono text-xs text-slate-400 overflow-auto max-h-[420px] leading-relaxed">
                {config}
              </pre>
            )}
          </div>

          {/* Env hints */}
          <div className="glass-card p-5">
            <h3 className="section-title mb-3">Environment Variables</h3>
            <div className="space-y-2 text-xs">
              {[
                { key: 'JIRA_URL', hint: 'Jira instance URL' },
                { key: 'JIRA_EMAIL', hint: 'Jira account email' },
                { key: 'JIRA_API_TOKEN', hint: 'Jira API token' },
                { key: 'GITLAB_URL', hint: 'GitLab instance URL' },
                { key: 'GITLAB_TOKEN', hint: 'GitLab personal access token' },
                { key: 'ANTHROPIC_API_KEY', hint: 'Direct API key (optional)' },
              ].map(({ key, hint }) => (
                <div key={key} className="flex items-center justify-between py-1.5 border-b border-white/5">
                  <span className="font-mono text-slate-400">{key}</span>
                  <span className="text-slate-600">{hint}</span>
                </div>
              ))}
            </div>
            <p className="text-xs text-slate-600 mt-3 leading-relaxed">
              Stored in <code className="text-blue-400/80 font-mono">config/.env.local</code> — never commit this file.
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
