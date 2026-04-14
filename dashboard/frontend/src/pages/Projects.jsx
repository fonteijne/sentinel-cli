import React, { useState, useEffect } from 'react'
import { Plus, Search, RefreshCw, X, Check, AlertCircle } from 'lucide-react'
import ProjectCard from '../components/ProjectCard.jsx'
import axios from 'axios'
import clsx from 'clsx'

const EMPTY_FORM = {
  key: '',
  name: '',
  git_url: '',
  default_branch: 'main',
  stack_type: 'python',
}

function ProjectForm({ initial = EMPTY_FORM, onSubmit, onCancel, title }) {
  const [form, setForm] = useState(initial)
  const [saving, setSaving] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setSaving(true)
    await onSubmit(form)
    setSaving(false)
  }

  const field = (key, label, props = {}) => (
    <div>
      <label className="block text-xs font-medium text-slate-400 mb-1.5">{label}</label>
      <input
        {...props}
        value={form[key]}
        onChange={e => setForm(f => ({ ...f, [key]: e.target.value }))}
        className="dark-input w-full"
      />
    </div>
  )

  return (
    <div className="glass-card p-6 border border-blue-500/20">
      <div className="flex items-center justify-between mb-5">
        <h3 className="text-[15px] font-semibold text-slate-100">{title}</h3>
        <button onClick={onCancel} className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-500 hover:text-slate-300 hover:bg-white/5">
          <X className="w-4 h-4" />
        </button>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="grid grid-cols-2 gap-4">
          {field('key', 'Project Key', { placeholder: 'PROJ', required: true })}
          {field('name', 'Display Name', { placeholder: 'My Project' })}
        </div>
        {field('git_url', 'Git URL', { placeholder: 'git@gitlab.example.com:org/repo.git', required: true })}
        <div className="grid grid-cols-2 gap-4">
          {field('default_branch', 'Default Branch', { placeholder: 'main' })}
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1.5">Stack Type</label>
            <select
              value={form.stack_type}
              onChange={e => setForm(f => ({ ...f, stack_type: e.target.value }))}
              className="dark-input w-full"
            >
              <option value="python">Python</option>
              <option value="drupal">Drupal</option>
              <option value="python/drupal">Fullstack</option>
              <option value="unknown">Auto-detect</option>
            </select>
          </div>
        </div>

        <div className="flex items-center gap-3 pt-2">
          <button type="submit" disabled={saving} className="btn-primary">
            {saving ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
            {initial.key ? 'Save Changes' : 'Add Project'}
          </button>
          <button type="button" onClick={onCancel} className="btn-secondary">
            Cancel
          </button>
        </div>
      </form>
    </div>
  )
}

export default function Projects() {
  const [projects, setProjects] = useState([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editProject, setEditProject] = useState(null)
  const [notification, setNotification] = useState(null)
  const [profileLoading, setProfileLoading] = useState({})

  const notify = (msg, type = 'info') => {
    setNotification({ msg, type })
    setTimeout(() => setNotification(null), 3500)
  }

  useEffect(() => {
    axios.get('/api/projects')
      .then(r => {
        setProjects(r.data || [])
      })
      .catch(() => setProjects([]))
      .finally(() => setLoading(false))
  }, [])

  const handleAdd = async (form) => {
    try {
      const res = await axios.post('/api/projects', form)
      setProjects(p => [...p, res.data])
      notify('Project added successfully', 'success')
    } catch {
      setProjects(p => [...p, { ...form, worktree_count: 0 }])
      notify('Project added (offline mode)', 'info')
    }
    setShowForm(false)
  }

  const handleEdit = async (form) => {
    try {
      await axios.put(`/api/projects/${form.key}`, form)
    } catch {}
    setProjects(p => p.map(pr => pr.key === form.key ? { ...pr, ...form } : pr))
    notify('Project updated', 'success')
    setEditProject(null)
  }

  const handleDelete = async (key) => {
    if (!window.confirm(`Remove project "${key}"? This will also clean up worktrees.`)) return
    try {
      await axios.delete(`/api/projects/${key}`)
    } catch {}
    setProjects(p => p.filter(pr => pr.key !== key))
    notify(`Project ${key} removed`, 'info')
  }

  const handleProfile = async (key) => {
    setProfileLoading(p => ({ ...p, [key]: true }))
    try {
      await axios.post(`/api/projects/${key}/profile`)
      notify(`Profile generated for ${key}`, 'success')
    } catch {
      notify(`Profile generation started for ${key}`, 'info')
    }
    setProfileLoading(p => ({ ...p, [key]: false }))
  }

  const filtered = projects.filter(p =>
    p.key.toLowerCase().includes(search.toLowerCase()) ||
    (p.name || '').toLowerCase().includes(search.toLowerCase())
  )

  const activeWT = projects.reduce((s, p) => s + (p.worktree_count || 0), 0)

  return (
    <div className="p-6 space-y-5">
      {/* Notification */}
      {notification && (
        <div className={clsx(
          'flex items-center gap-2 px-4 py-3 rounded-xl border text-sm animate-slide-in',
          notification.type === 'success'
            ? 'bg-emerald-500/10 border-emerald-500/25 text-emerald-300'
            : 'bg-blue-500/10 border-blue-500/25 text-blue-300'
        )}>
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          {notification.msg}
        </div>
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <div className="text-sm text-slate-500">
            <span className="text-slate-200 font-semibold">{projects.length}</span> projects,{' '}
            <span className="text-blue-400 font-semibold">{activeWT}</span> active worktrees
          </div>
        </div>
        <div className="flex items-center gap-3">
          {/* Search */}
          <div className="relative">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-600" />
            <input
              type="text"
              placeholder="Search projects..."
              value={search}
              onChange={e => setSearch(e.target.value)}
              className="dark-input pl-9 w-52"
            />
          </div>
          <button
            onClick={() => { setShowForm(true); setEditProject(null) }}
            className="btn-primary"
          >
            <Plus className="w-4 h-4" />
            Add Project
          </button>
        </div>
      </div>

      {/* Add form */}
      {showForm && !editProject && (
        <ProjectForm
          title="Add New Project"
          onSubmit={handleAdd}
          onCancel={() => setShowForm(false)}
        />
      )}

      {/* Edit form */}
      {editProject && (
        <ProjectForm
          title={`Edit ${editProject.key}`}
          initial={editProject}
          onSubmit={handleEdit}
          onCancel={() => setEditProject(null)}
        />
      )}

      {/* Grid */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {[1, 2, 3].map(i => (
            <div key={i} className="glass-card p-5 animate-pulse h-48" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="glass-card p-12 text-center">
          <div className="w-12 h-12 rounded-full bg-blue-500/10 flex items-center justify-center mx-auto mb-3">
            <Search className="w-6 h-6 text-blue-400/50" />
          </div>
          <p className="text-slate-400 text-sm">
            {search ? 'No projects match your search' : 'No projects configured yet'}
          </p>
          {!search && (
            <button
              onClick={() => setShowForm(true)}
              className="btn-primary mt-4 mx-auto"
            >
              <Plus className="w-3.5 h-3.5" />
              Add your first project
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
          {filtered.map(project => (
            <ProjectCard
              key={project.key}
              project={project}
              onEdit={(p) => { setEditProject(p); setShowForm(false) }}
              onDelete={handleDelete}
              onProfile={handleProfile}
            />
          ))}
        </div>
      )}
    </div>
  )
}
