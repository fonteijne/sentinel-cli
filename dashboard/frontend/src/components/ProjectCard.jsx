import React from 'react'
import { GitBranch, GitMerge, Layers, Edit2, Trash2, RefreshCw, ExternalLink } from 'lucide-react'
import clsx from 'clsx'

const STACK_COLORS = {
  python: { bg: 'bg-yellow-500/10', border: 'border-yellow-500/25', text: 'text-yellow-400', label: 'Python' },
  drupal: { bg: 'bg-blue-500/10', border: 'border-blue-500/25', text: 'text-blue-400', label: 'Drupal' },
  'python/drupal': { bg: 'bg-purple-500/10', border: 'border-purple-500/25', text: 'text-purple-400', label: 'Fullstack' },
  unknown: { bg: 'bg-slate-500/10', border: 'border-slate-500/25', text: 'text-slate-400', label: 'Unknown' },
}

export default function ProjectCard({ project, onEdit, onDelete, onProfile }) {
  const { key, name, git_url, default_branch = 'main', stack_type = 'unknown', worktree_count = 0 } = project
  const stack = STACK_COLORS[stack_type] || STACK_COLORS.unknown

  const repoName = git_url
    ? git_url.replace(/.*[:/]/, '').replace(/\.git$/, '')
    : 'No repository'

  return (
    <div className="glass-card glass-card-hover p-5 group">
      {/* Header */}
      <div className="flex items-start justify-between mb-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-xs font-mono font-bold text-blue-400/80 bg-blue-500/8 border border-blue-500/15 px-2 py-0.5 rounded-md">
              {key}
            </span>
            <div className={clsx('badge border', stack.bg, stack.border, stack.text)}>
              {stack.label}
            </div>
          </div>
          <h3 className="text-[15px] font-semibold text-slate-100 mt-1.5 truncate">
            {name || key}
          </h3>
        </div>

        {/* Actions — visible on hover */}
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity ml-2 flex-shrink-0">
          <button
            onClick={() => onProfile?.(key)}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-500 hover:text-cyan-400 hover:bg-cyan-500/10 transition-all"
            title="Generate profile"
          >
            <RefreshCw className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => onEdit?.(project)}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-500 hover:text-blue-400 hover:bg-blue-500/10 transition-all"
            title="Edit project"
          >
            <Edit2 className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => onDelete?.(key)}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-500 hover:text-red-400 hover:bg-red-500/10 transition-all"
            title="Remove project"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Details */}
      <div className="space-y-2 mt-4">
        {git_url && (
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <GitMerge className="w-3.5 h-3.5 flex-shrink-0 text-slate-600" />
            <span className="truncate font-mono">{repoName}</span>
            <a
              href={git_url.replace(/^git@([^:]+):/, 'https://$1/')}
              target="_blank"
              rel="noopener noreferrer"
              className="ml-auto flex-shrink-0 hover:text-blue-400 transition-colors"
              onClick={e => e.stopPropagation()}
            >
              <ExternalLink className="w-3 h-3" />
            </a>
          </div>
        )}

        <div className="flex items-center gap-2 text-xs text-slate-500">
          <GitBranch className="w-3.5 h-3.5 flex-shrink-0 text-slate-600" />
          <span className="font-mono">{default_branch}</span>
        </div>

        <div className="flex items-center gap-2 text-xs text-slate-500">
          <Layers className="w-3.5 h-3.5 flex-shrink-0 text-slate-600" />
          <span>
            {worktree_count > 0
              ? `${worktree_count} active worktree${worktree_count !== 1 ? 's' : ''}`
              : 'No active worktrees'
            }
          </span>
        </div>
      </div>

      {/* Footer bar */}
      <div className="mt-4 pt-3 border-t border-blue-500/8 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <div className={clsx(
            'w-2 h-2 rounded-full',
            worktree_count > 0 ? 'bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.4)]' : 'bg-slate-600'
          )} />
          <span className="text-xs text-slate-600">
            {worktree_count > 0 ? 'Active' : 'Idle'}
          </span>
        </div>
        <span className="text-xs text-slate-600 font-mono">
          {worktree_count} WT
        </span>
      </div>
    </div>
  )
}
