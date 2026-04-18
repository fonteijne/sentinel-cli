# Fix Beads/Dolt Server Unreachable from Worktrees

## Problem

`bd` commands fail when run from git worktree paths. The Dolt server that backs beads is unreachable. 2 hours of debugging on 2026-04-18 didn't resolve it; beads is temporarily disabled.

## Hypothesis

Git worktrees live under `.claude/worktrees/` or similar paths, not under the main repo root. Dolt likely discovers its database via one of:

1. **Socket/port binding** — Dolt server starts bound to the main repo path and worktrees can't find the socket
2. **`.dolt/` directory resolution** — Dolt looks for `.dolt/` in `cwd` or ancestors; worktrees don't have this directory (they have `.git` as a file pointing back to main repo)
3. **`bd init` path** — beads was initialized in the main repo; the Dolt database path is absolute or relative to that location

## Investigation Steps

1. **Where does Dolt store its data?**
   ```bash
   # From main repo (where bd works):
   find . -name ".dolt" -type d
   bd dolt status
   bd dolt config
   ```

2. **How does bd discover the Dolt server?**
   ```bash
   # Check bd config for server connection details:
   bd config
   cat .beads/config.yml  # or similar
   ```

3. **What's different in a worktree?**
   ```bash
   # From a worktree:
   pwd
   cat .git  # Should show "gitdir: /path/to/main/.git/worktrees/..."
   ls -la .dolt  # Probably missing
   bd stats  # Reproduce the error
   ```

4. **How does Dolt resolve its working directory?**
   - Check if Dolt uses `git rev-parse --show-toplevel` (returns worktree root, not main repo)
   - Check if Dolt uses `git rev-parse --git-common-dir` (returns main repo's .git)

## Potential Fixes

### Option A: Symlink `.dolt` into worktrees
After creating a worktree, symlink `.dolt` from main repo:
```bash
ln -s /path/to/main/repo/.dolt /path/to/worktree/.dolt
```
Quick fix, but fragile.

### Option B: Set Dolt working dir explicitly
If `bd` or Dolt supports a `--data-dir` or environment variable, point it to the main repo's Dolt database regardless of cwd.

### Option C: Run bd from main repo with --cwd
Wrap all `bd` calls in `BeadsManager` to always execute from the main repo root rather than the worktree path. The `cwd` parameter already exists in most methods.

### Option D: Start Dolt server on a fixed port
If the issue is socket-based, configure Dolt to listen on `localhost:PORT` instead of a Unix socket. All worktrees connect to the same port.

## Priority

Low — beads is disabled and Sentinel works without it. Revisit when task tracking becomes a bottleneck again.
