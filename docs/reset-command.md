# Sentinel Reset Command

## Overview

The `sentinel reset` command provides an intuitive way to reset tickets or all Sentinel state to start fresh.

**Key features:**
- `sentinel reset <ticket-id>` - Removes worktree AND local branch for a specific ticket
- `sentinel reset --all` - Removes all worktrees, branches, bare repositories, and sessions

This replaces the previous `cleanup` and `deep_clean` commands with a single, clear mental model: **reset = fresh start**.

## Usage

### Reset Single Ticket

```bash
sentinel reset PROJ-123
```

This removes:
- Git worktree for PROJ-123
- Local branch `feature/PROJ-123` from the bare repository

### Reset Everything

```bash
sentinel reset --all
```

This removes:
- All git worktrees in workspace
- All local branches in bare repositories
- All bare git repositories (must be re-cloned)
- All Sentinel agent sessions

### Reset Single Project

```bash
sentinel reset --all --project PROJ
```

This resets only the specified project, leaving other projects intact.

## Options

| Option | Description |
|--------|-------------|
| `<ticket-id>` | Ticket ID to reset (e.g., PROJ-123) |
| `--all`, `-a` | Reset everything: all worktrees, branches, repositories, and sessions |
| `-p, --project` | Project key (e.g., ACME). Required with --all, optional with ticket_id |
| `-y, --yes` | Skip confirmation prompts |

## Output Examples

### Reset Single Ticket

```
🔄 Resetting ticket: PROJ-123

This will remove:
  • Worktree for PROJ-123
  • Local branch feature/PROJ-123

⚠️  Any uncommitted changes will be lost!
Continue? [y/N]: y

1️⃣  Removing worktree...
   ✓ Worktree removed

2️⃣  Deleting local branch...
   ✓ Branch feature/PROJ-123 deleted

✅ Reset complete for PROJ-123
```

### Reset All

```
🔄 Reset ALL Sentinel State

This will remove:
  • 3 worktree(s)
  • 1 bare repository(ies): PROJ
  • All local branches in those repositories
  • 2 Agent SDK session(s)

⚠️  WARNING: This cannot be undone!
⚠️  Repositories must be re-cloned after reset!
Are you sure? [y/N]: y

1️⃣  Resetting PROJ...
   ✓ Removed 3 worktree(s)
   ✓ Removed bare repository

2️⃣  Clearing Agent SDK sessions...
   ✓ Cleared 2 session(s)

✅ Reset complete - Sentinel is ready for a fresh start
```

## Safety Features

- **Confirmation required** for all destructive operations (bypass with `-y`)
- **Clear summary** of what will be removed before confirmation
- **Defaults to No** - must explicitly confirm with 'y'
- **Warning about uncommitted changes** before proceeding

## When to Use

### Reset Single Ticket

Use `sentinel reset <ticket-id>` when:
- You want to start fresh on a specific ticket
- Old local branch causes stale files to reappear after deleting remote branch
- Worktree is corrupted or in an inconsistent state

### Reset All

Use `sentinel reset --all` when:
- You need a complete fresh start
- Multiple worktrees or branches are causing issues
- Cleaning up after extensive testing
- Before archiving or backing up the workspace

## What Gets Removed

### Single Ticket Reset

| Item | Removed |
|------|---------|
| Git worktree | ✅ Yes |
| Local branch (`feature/<ticket-id>`) | ✅ Yes |
| Bare repository | ❌ No |
| Agent sessions | ❌ No |
| Configuration | ❌ No |

### Full Reset (--all)

| Item | Removed |
|------|---------|
| All git worktrees | ✅ Yes |
| All local branches | ✅ Yes |
| Bare repositories | ✅ Yes |
| Sentinel agent sessions | ✅ Yes |
| Configuration files | ❌ No |
| Claude Code settings | ❌ No |
| Your development sessions | ❌ No |

## Recovery

If you accidentally reset:

1. **Worktrees can be recreated:**
   ```bash
   poetry run sentinel plan PROJ-123
   ```

2. **Remote branches are preserved:**
   - Commits pushed to remote are safe
   - Only local branches are removed
   - Re-cloning will restore all remote branches

3. **Agent sessions rebuild automatically:**
   - New sessions created on next agent run

## Implementation Details

### CLI Command
Location: [src/cli.py](../src/cli.py)

### WorktreeManager Methods
Location: [src/worktree_manager.py](../src/worktree_manager.py)

Methods:
- `reset_ticket(ticket_id, project_key)` - Reset single ticket
- `reset_all(project_key)` - Reset all for a project
- `delete_local_branch(ticket_id, project_key)` - Delete a local branch

### Tests
Location: [tests/test_worktree_manager.py](../tests/test_worktree_manager.py)

## Related Commands

- `sentinel status` - View active worktrees without removing them
- `sentinel plan <ticket-id>` - Create/recreate worktree for a ticket
- `bd sync` - Sync Beads task tracking before resetting
