#!/bin/bash
# Diagnostic script for the inner Claude Code CLI environment.
# Run inside sentinel-dev to discover what the CLI "sees" when it starts.
#
# Usage: docker compose exec sentinel-dev bash /app/diagnose-inner-cli.sh [cwd]
#
# Results are written to /app/logs/diagnostics/ (= /workspace/sentinel/logs/diagnostics/)

set -euo pipefail

CWD="${1:-/root/sentinel-workspaces}"
OUT_DIR="/app/logs/diagnostics"
mkdir -p "$OUT_DIR"
REPORT="$OUT_DIR/inner-cli-env-$(date +%Y%m%d_%H%M%S).txt"

echo "=== Inner CLI Environment Diagnostic ===" | tee "$REPORT"
echo "Run at: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$REPORT"
echo "CWD: $CWD" | tee -a "$REPORT"
echo "" | tee -a "$REPORT"

# 1. Check for .claude/ directories in the hierarchy
echo "--- .claude/ directories in cwd hierarchy ---" | tee -a "$REPORT"
dir="$CWD"
while [ "$dir" != "/" ]; do
    if [ -d "$dir/.claude" ]; then
        echo "FOUND: $dir/.claude/" | tee -a "$REPORT"
        ls -la "$dir/.claude/" 2>/dev/null | tee -a "$REPORT"
        # Dump settings if present
        if [ -f "$dir/.claude/settings.json" ]; then
            echo "  settings.json:" | tee -a "$REPORT"
            cat "$dir/.claude/settings.json" | tee -a "$REPORT"
        fi
        if [ -f "$dir/.claude/settings.local.json" ]; then
            echo "  settings.local.json:" | tee -a "$REPORT"
            cat "$dir/.claude/settings.local.json" | tee -a "$REPORT"
        fi
    fi
    dir=$(dirname "$dir")
done
# Check root
if [ -d "/.claude" ]; then
    echo "FOUND: /.claude/" | tee -a "$REPORT"
    ls -la "/.claude/" 2>/dev/null | tee -a "$REPORT"
fi
echo "" | tee -a "$REPORT"

# 2. Check HOME directory
echo "--- HOME directory ---" | tee -a "$REPORT"
echo "HOME=$HOME" | tee -a "$REPORT"
if [ -d "$HOME/.claude" ]; then
    echo "FOUND: $HOME/.claude/" | tee -a "$REPORT"
    ls -la "$HOME/.claude/" 2>/dev/null | tee -a "$REPORT"
    for f in settings.json settings.local.json CLAUDE.md; do
        if [ -f "$HOME/.claude/$f" ]; then
            echo "  $f:" | tee -a "$REPORT"
            cat "$HOME/.claude/$f" | tee -a "$REPORT"
        fi
    done
else
    echo "NO $HOME/.claude/ directory" | tee -a "$REPORT"
fi
echo "" | tee -a "$REPORT"

# 3. Check for CLAUDE.md files in hierarchy
echo "--- CLAUDE.md files in cwd hierarchy ---" | tee -a "$REPORT"
dir="$CWD"
while [ "$dir" != "/" ]; do
    if [ -f "$dir/CLAUDE.md" ]; then
        echo "FOUND: $dir/CLAUDE.md ($(wc -c < "$dir/CLAUDE.md") bytes)" | tee -a "$REPORT"
        head -20 "$dir/CLAUDE.md" | tee -a "$REPORT"
        echo "..." | tee -a "$REPORT"
    fi
    dir=$(dirname "$dir")
done
echo "" | tee -a "$REPORT"

# 4. Relevant environment variables
echo "--- Environment variables ---" | tee -a "$REPORT"
env | grep -iE "claude|anthropic|sdk|stdin|tty" 2>/dev/null | sort | tee -a "$REPORT"
echo "" | tee -a "$REPORT"

# 5. Check stdin state
echo "--- stdin properties ---" | tee -a "$REPORT"
if [ -t 0 ]; then
    echo "stdin is a TTY" | tee -a "$REPORT"
else
    echo "stdin is NOT a TTY (pipe or redirect)" | tee -a "$REPORT"
fi
ls -la /dev/stdin 2>/dev/null | tee -a "$REPORT"
ls -la /proc/self/fd/0 2>/dev/null | tee -a "$REPORT"
echo "" | tee -a "$REPORT"

# 6. Check bundled CLI
echo "--- Bundled CLI ---" | tee -a "$REPORT"
BUNDLED=$(python3 -c "
import claude_agent_sdk._internal.transport.subprocess_cli as t
import os
for d in [os.path.dirname(t.__file__) + '/../../_bundled/claude']:
    p = os.path.normpath(d)
    if os.path.exists(p):
        print(p)
        break
else:
    print('NOT FOUND')
" 2>/dev/null || echo "PYTHON ERROR")
echo "Path: $BUNDLED" | tee -a "$REPORT"
if [ -f "$BUNDLED" ]; then
    echo "Size: $(du -h "$BUNDLED" | cut -f1)" | tee -a "$REPORT"
fi
echo "" | tee -a "$REPORT"

# 7. List worktree contents (top-level files only)
echo "--- Worktree top-level files ---" | tee -a "$REPORT"
if [ -d "$CWD" ]; then
    ls -la "$CWD/" 2>/dev/null | head -30 | tee -a "$REPORT"
fi
echo "" | tee -a "$REPORT"

# 8. Recent diagnostics from agent_sdk_wrapper
echo "--- Recent agent_diagnostics.jsonl (last 20 lines) ---" | tee -a "$REPORT"
if [ -f "/app/logs/agent_diagnostics.jsonl" ]; then
    tail -20 /app/logs/agent_diagnostics.jsonl | tee -a "$REPORT"
else
    echo "No diagnostics file yet (run sentinel execute to generate)" | tee -a "$REPORT"
fi

echo "" | tee -a "$REPORT"
echo "=== Diagnostic complete ===" | tee -a "$REPORT"
echo "Report written to: $REPORT"
