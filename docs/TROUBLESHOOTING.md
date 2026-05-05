# Sentinel Troubleshooting Guide

Solutions to common issues and debugging techniques for Sentinel.

## Overview

This guide covers the most common problems users encounter when working with Sentinel and provides step-by-step solutions. If you don't find your issue here, check the **[Getting Help](#getting-help)** section.

---

## Table of Contents

- [Common Issues](#common-issues)
  - [Credential Problems](#credential-problems)
  - [Git Worktree Issues](#git-worktree-issues)
  - [Agent SDK Issues](#agent-sdk-issues)
  - [Installation Problems](#installation-problems)
  - [Configuration Errors](#configuration-errors)
- [Command Center (`sentinel serve`) operator notes](#command-center-sentinel-serve-operator-notes)
- [Debugging Techniques](#debugging-techniques)
- [Reset Command](#reset-command)
- [Getting Help](#getting-help)

---

## Common Issues

### Credential Problems

#### Error: "Jira configuration incomplete"

**Full error:**
```
ValueError: Jira configuration incomplete. Ensure JIRA_BASE_URL, JIRA_API_TOKEN, and JIRA_EMAIL are set.
```

**Cause:** Environment variables not loaded or missing from `.env` file.

**Fix:**

1. **Check if `.env` exists:**
   ```bash
   ls -la config/.env
   ```

   If missing, create from template:
   ```bash
   cp config/.env.example config/.env
   ```

2. **Edit `config/.env` with your credentials:**
   ```bash
   # Open in your editor
   nano config/.env
   ```

   Required variables:
   ```bash
   JIRA_BASE_URL=https://your-company.atlassian.net
   JIRA_API_TOKEN=your_token_here
   JIRA_EMAIL=your.email@company.com
   ```

3. **Load environment variables:**
   ```bash
   export $(cat config/.env | xargs)
   ```

4. **Verify:**
   ```bash
   echo $JIRA_API_TOKEN  # Should show your token
   poetry run sentinel validate
   ```

---

#### Error: "401 Unauthorized"

**Full error:**
```
requests.exceptions.HTTPError: 401 Client Error: Unauthorized for url: https://...
```

**Cause:** Invalid or expired API token.

**Fix:**

1. **Generate a new API token:**
   - Jira: https://id.atlassian.com/manage-profile/security/api-tokens
   - GitLab: Settings → Access Tokens

2. **Update `config/.env`:**
   ```bash
   JIRA_API_TOKEN=new_token_here
   # or
   GITLAB_API_TOKEN=new_token_here
   ```

3. **Reload environment variables:**
   ```bash
   export $(cat config/.env | xargs)
   ```

4. **Test:**
   ```bash
   poetry run sentinel validate
   ```

**Still failing?**
- Check that email matches Jira account (JIRA_EMAIL)
- Verify token has correct permissions (Jira: read/write issues, GitLab: api, write_repository)
- Try token in browser: `https://your-company.atlassian.net/rest/api/3/myself` with token in Authorization header

---

#### Error: "Connection refused"

**Full error:**
```
requests.exceptions.ConnectionError: Connection refused
```

**Cause:** Wrong base URL or network issue.

**Fix:**

1. **Verify base URL in `config/.env`:**
   ```bash
   echo $JIRA_BASE_URL
   echo $GITLAB_BASE_URL
   ```

   Common mistakes:
   - ❌ `http://...` → ✅ `https://...`
   - ❌ Trailing slash → ✅ No trailing slash
   - ❌ Wrong subdomain

2. **Test URL in browser:**
   - Navigate to `$JIRA_BASE_URL`
   - Should show Jira login page

3. **Check network:**
   ```bash
   ping your-company.atlassian.net
   curl -I https://your-company.atlassian.net
   ```

4. **Check firewall/VPN:**
   - Some companies require VPN for Jira/GitLab access
   - Check with IT if you can't reach the URLs

---

### Git Worktree Issues

#### Error: "Worktree already exists"

**Full error:**
```
fatal: '/path/to/sentinel-workspaces/PROJ/PROJ-123' already exists
```

**Cause:** Previous worktree wasn't cleaned up.

**Fix:**

**Option 1: Use built-in reset**
```bash
poetry run sentinel reset PROJ-123
```

**Option 2: Manual cleanup**
```bash
# Remove worktree directory
rm -rf ~/sentinel-workspaces/PROJ/PROJ-123

# Prune git worktree references
cd ~/sentinel-workspaces/PROJ
git worktree prune

# Verify cleanup
git worktree list
```

**Prevention:**
Always use `sentinel reset` after finishing work:
```bash
poetry run sentinel reset PROJ-123
```

---

#### Error: "Branch already exists"

**Full error:**
```
fatal: a branch named 'worktree/PROJ-123' already exists
```

**Cause:** Branch from previous worktree still exists.

**Fix:**

1. **Check if branch is in use:**
   ```bash
   git worktree list | grep PROJ-123
   ```

2. **If worktree exists, remove it first:**
   ```bash
   poetry run sentinel reset PROJ-123
   ```

3. **If no worktree but branch exists, delete branch:**
   ```bash
   cd ~/sentinel-workspaces/PROJ
   git branch -D worktree/PROJ-123
   ```

4. **Retry:**
   ```bash
   poetry run sentinel plan PROJ-123
   ```

---

#### Error: "Cannot create worktree: Permission denied"

**Cause:** Insufficient permissions on workspace directory.

**Fix:**

1. **Check directory permissions:**
   ```bash
   ls -ld ~/sentinel-workspaces
   ```

2. **Create directory with correct permissions:**
   ```bash
   mkdir -p ~/sentinel-workspaces
   chmod 755 ~/sentinel-workspaces
   ```

3. **If using custom root_dir in config, ensure it's writable:**
   ```yaml
   # config/config.yaml
   workspace:
     root_dir: "/path/you/can/write/to"
   ```

---

### Agent SDK Issues

#### Error: "Session not found"

**Cause:** Agent session tracking got out of sync.

**Fix:**

1. **Check active sessions:**
   ```bash
   ls -la .agents/
   ```

2. **Clean up stale sessions:**
   ```bash
   poetry run sentinel reset --all
   ```

   This removes:
   - All worktrees and local branches
   - Bare repositories
   - Orphaned agent sessions

3. **Verify cleanup:**
   ```bash
   ls -la .agents/
   # Should be minimal or empty
   ```

---

#### Error: "Tool use failed: Permission denied"

**Cause:** Agent trying to use a tool not granted in configuration.

**Fix:**

1. **Check agent tool permissions in `config/config.yaml`:**
   ```yaml
   agent_sdk:
     planning_agent_tools:
       - "Read"
       - "Grep"
       - "Glob"
       - "Bash(git *)"  # Only git commands allowed
   ```

2. **Grant necessary tools:**
   ```yaml
   agent_sdk:
     implementation_agent_tools:
       - "Read"
       - "Write"
       - "Edit"
       - "Grep"
       - "Glob"
       - "Bash"  # Full bash access
   ```

3. **Restart sentinel after config changes:**
   ```bash
   poetry run sentinel plan TICKET-123
   ```

**Security Note:** Be careful granting `Bash` without restrictions - it allows arbitrary command execution.

---

#### Error: "Model not found: claude-4-5-opus"

**Cause:** Invalid model name in configuration.

**Fix:**

1. **Check model names in `config/config.yaml`:**
   ```yaml
   agents:
     plan_generator:
       model: "claude-4-5-opus"  # Correct format
   ```

2. **Valid model names:**
   - `claude-4-5-opus`
   - `claude-4-5-sonnet`
   - `claude-4-5-haiku`

3. **Verify LLM Provider API key if using LLM Provider:**
   ```bash
   echo $LLM_PROVIDER_API_KEY
   ```

4. **Test model access:**
   ```bash
   poetry run sentinel validate
   ```

---

### Installation Problems

#### Error: "Poetry not found"

**Cause:** Poetry not installed or not in PATH.

**Fix:**

1. **Install Poetry:**
   ```bash
   curl -sSL https://install.python-poetry.org | python3 -
   ```

2. **Add to PATH (add to ~/.bashrc or ~/.zshrc):**
   ```bash
   export PATH="$HOME/.local/bin:$PATH"
   ```

3. **Reload shell:**
   ```bash
   source ~/.bashrc  # or source ~/.zshrc
   ```

4. **Verify:**
   ```bash
   poetry --version
   ```

---

#### Error: "Python version not supported"

**Cause:** Python version is older than 3.11.

**Fix:**

1. **Check Python version:**
   ```bash
   python3 --version
   ```

2. **Install Python 3.11+ using pyenv:**
   ```bash
   # Install pyenv
   curl https://pyenv.run | bash

   # Install Python 3.11
   pyenv install 3.11.7

   # Set as local version for sentinel
   cd sentinel
   pyenv local 3.11.7
   ```

3. **Verify:**
   ```bash
   python3 --version  # Should show 3.11+
   ```

4. **Reinstall dependencies:**
   ```bash
   poetry install
   ```

---

#### Error: "Dependency conflict"

**Full error:**
```
Because sentinel depends on both package-a (^1.0) and package-b (^2.0) which depends on package-a (^0.9), version solving failed.
```

**Fix:**

1. **Update Poetry:**
   ```bash
   poetry self update
   ```

2. **Clear cache:**
   ```bash
   poetry cache clear pypi --all
   ```

3. **Remove lock file and reinstall:**
   ```bash
   rm poetry.lock
   poetry install
   ```

4. **If still failing, check for conflicting dependencies:**
   ```bash
   poetry show --tree
   ```

---

### Configuration Errors

#### Error: "Invalid YAML syntax"

**Cause:** Malformed `config.yaml`.

**Fix:**

1. **Validate YAML syntax:**
   ```bash
   python3 -c "import yaml; yaml.safe_load(open('config/config.yaml'))"
   ```

2. **Common YAML mistakes:**
   ```yaml
   # ❌ Wrong - inconsistent indentation
   projects:
     ACME:
      git_url: "..."

   # ✅ Correct - consistent 2-space indentation
   projects:
     ACME:
       git_url: "..."

   # ❌ Wrong - mixing tabs and spaces
   agents:
   	plan_generator:
       model: "..."

   # ✅ Correct - spaces only
   agents:
     plan_generator:
       model: "..."
   ```

3. **Use YAML validator:**
   - Online: https://www.yamllint.com/
   - Or install yamllint: `pip install yamllint`

---

## Command Center (`sentinel serve`) operator notes

Short, non-error notes about the HTTP service that are easy to misread if
you're wiring it into monitoring for the first time. Not error conditions —
just behaviour that looks surprising without context.

### `/health` is a deep probe, not a liveness check

`GET /health` is intentionally unauthenticated and executes `SELECT 1` against
the SQLite database via a real request-scoped connection. That means:

- If the DB file is missing, locked, or otherwise unreachable, `/health`
  returns **500** even though the Python process is alive and serving.
- If you use `/health` as a Kubernetes readiness probe or docker-compose
  `healthcheck:`, this is the desired behaviour — the service isn't useful
  without the DB, so it shouldn't receive traffic.
- If you use `/health` as a **liveness** probe, a transient DB lock will
  restart the process unnecessarily. For liveness, use a plain TCP port
  check on the bound port, not `/health`.

Bottom line: `/health` == "ready to serve requests"; TCP check == "process
is up". They are not interchangeable.

### Every audit line means "authorised attempt", not "confirmed write"

Authenticated writes (POST to `/executions*`) emit a structured log line:

```
INFO audit write user=<token-prefix> ip=<client> method=POST path=/executions
```

The line is emitted AFTER the bearer check and rate-limit reservation but
BEFORE the handler runs. Which means:

- A 4xx (e.g. pydantic-422 for a malformed body, 409 for a write on a
  terminal execution) still produces an audit line — the client had a
  valid token and passed the rate limit, they just sent a request the
  handler rejected.
- A 5xx from inside the handler also produces an audit line, because the
  attempt itself is what we audit.

When reconciling audit logs with state changes, correlate on the response
status (access logs) or on the corresponding `execution.started` event in
the DB — a successful write is "audit line + 202 Accepted + execution row".
A line without the 202 is a rejected or failed attempt.

### Token file lives at `~/.sentinel/service_token`

Created mode `0600` on first `sentinel serve` if neither the env var nor an
existing file is present. Safe to delete when you want to rotate — the next
`sentinel serve` generates a fresh one. On a shared host, prefer the env
var (`SENTINEL_SERVICE_TOKEN`) so the token isn't at rest on disk, but note
that env vars are visible to other processes via `ps -eww` on some systems.

### `--host 0.0.0.0` is refused by default

`sentinel serve --host 0.0.0.0` (or `::`) exits non-zero. Use the Docker
network IP or `127.0.0.1` for local work. The escape hatch is
`--i-know-what-im-doing`; don't use it without understanding that this
exposes execution control on the network.

---

## Debugging Techniques

### Enable Debug Logging

**Method 1: Configuration file**

Edit `config/config.yaml`:
```yaml
logging:
  level: "DEBUG"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

**Method 2: Environment variable**
```bash
export LOG_LEVEL=DEBUG
poetry run sentinel plan TICKET-123
```

**Method 3: Command-line flag**
```bash
poetry run sentinel --debug plan TICKET-123
```

### View Logs

**Tail logs in real-time:**
```bash
tail -f sentinel.log
```

**Search for specific agent:**
```bash
grep "PlanGeneratorAgent" sentinel.log
```

**View last 100 lines:**
```bash
tail -n 100 sentinel.log
```

**Filter by error level:**
```bash
grep "ERROR" sentinel.log
grep "WARNING" sentinel.log
```

### Use Validation Command

**Test all credentials and services:**
```bash
poetry run sentinel validate
```

Expected output:
```
🔐 Validating API Credentials

1️⃣  Testing Jira API...
   ✅ Jira connected: Your Name

2️⃣  Testing GitLab API...
   ✅ GitLab connected: Your Name

3️⃣  Testing LLM Provider API...
   ✅ LLM call successful

4️⃣  Testing Beads CLI...
   ✅ Beads CLI available

✅ All credentials validated successfully!
```

If any step fails, it shows exactly what's wrong.

### Check Status Command

**View current Sentinel state:**
```bash
poetry run sentinel status
```

Shows:
- Active worktrees
- Open Beads tasks
- Configuration summary

### Manual Worktree Inspection

**List all worktrees:**
```bash
git worktree list
```

**Check specific worktree directory:**
```bash
ls -la ~/sentinel-workspaces/PROJ/PROJ-123/
```

**Check git status in worktree:**
```bash
cd ~/sentinel-workspaces/PROJ/PROJ-123
git status
git log -1
```

### Debug Agent Conversations

**Agent SDK creates detailed logs** in `sentinel.log`:

```bash
# View full agent conversation
grep -A 20 "Agent: plan_generator" sentinel.log

# See tool use
grep "Tool:" sentinel.log

# Check for errors
grep -i "error\|exception\|failed" sentinel.log
```

### Dry Run Mode

**Test without executing:**
```bash
poetry run sentinel plan TICKET-123 --dry-run
```

This:
- Shows what would be done
- Doesn't create worktrees
- Doesn't call agents
- Useful for testing configuration

---

## Reset Command

### When to Use

Use `sentinel reset` when:
- Worktrees are stuck or corrupted
- Agent sessions won't start
- "Already exists" errors persist
- Starting fresh after many iterations
- Old local branches cause stale files to reappear

### What It Does

`sentinel reset <ticket-id>` removes:
- ✅ Git worktree for the ticket
- ✅ Local branch `feature/<ticket-id>`
- ❌ Does NOT delete: bare repository, configuration, logs

`sentinel reset --all` removes:
- ✅ All git worktrees in workspace
- ✅ All local branches in bare repositories
- ✅ All bare repositories (must re-clone)
- ✅ Agent session tracking files

### Usage

**Reset single ticket:**
```bash
poetry run sentinel reset PROJ-123
```

**Reset single project:**
```bash
poetry run sentinel reset --all --project PROJ
```

**Reset everything (use with caution):**
```bash
poetry run sentinel reset --all
```

### Safety Features

- **Confirmation required** for all destructive operations
- **Clear summary** of what will be removed before confirmation
- **Warning about uncommitted changes** before proceeding

### Recovery

If you accidentally reset:

1. **Worktrees can be recreated:**
   ```bash
   poetry run sentinel plan PROJ-123
   ```

2. **Remote branches are preserved:**
   - Commits pushed to remote are safe
   - Only local branches are removed

3. **Agent sessions rebuild automatically:**
   - New sessions created on next run

---

## Getting Help

### Before Asking for Help

1. **Check this troubleshooting guide**
2. **Read relevant documentation:**
   - [CONFIGURATION.md](CONFIGURATION.md) - Configuration issues
   - [DEVELOPMENT.md](DEVELOPMENT.md) - Development problems
   - [CREDENTIALS.md](../CREDENTIALS.md) - Credential setup
3. **Run validation:**
   ```bash
   poetry run sentinel validate
   ```
4. **Check logs:**
   ```bash
   tail -n 100 sentinel.log
   ```

### Log File Locations

Provide these when requesting help:

- **Main log**: `sentinel.log`
- **Configuration**: `config/config.yaml` (redact credentials!)
- **Environment**: `config/.env` (DO NOT share - sensitive!)
- **Session state**: `.agents/session_tracker.json`

### Information to Provide

When creating an issue, include:

1. **Error message** (full text, not paraphrased)
2. **Command that caused error:**
   ```bash
   poetry run sentinel plan PROJ-123
   ```
3. **Relevant logs:**
   ```bash
   tail -n 50 sentinel.log
   ```
4. **Environment:**
   ```bash
   python3 --version
   poetry --version
   uname -a  # OS info
   ```
5. **Configuration** (redacted):
   ```yaml
   # Relevant section from config.yaml
   agents:
     plan_generator:
       model: "claude-4-5-opus"
   ```

### GitHub Issues

**Create an issue:**
1. Go to repository issues page
2. Click "New Issue"
3. Use template if available
4. Include information from above

**Search existing issues first:**
```bash
# Your issue may already be reported/solved
```

### Community Support

- **Documentation**: Start with [README.md](../README.md)
- **API Reference**: [API.md](API.md)
- **Development**: [DEVELOPMENT.md](DEVELOPMENT.md)

---

## Additional Resources

- **[Configuration Guide](CONFIGURATION.md)** - Complete config.yaml reference
- **[Development Guide](DEVELOPMENT.md)** - Contributing and testing
- **[API Documentation](API.md)** - Programmatic API reference
- **[Credentials Setup](../CREDENTIALS.md)** - Detailed credential configuration
- **[Reset Command](reset-command.md)** - Detailed reset command documentation

---

**Still stuck?** Create an issue with detailed information above.
