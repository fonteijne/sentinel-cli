# Sentinel Configuration Guide

Complete reference for configuring Sentinel using `config.yaml` and environment variables.

## Overview

Sentinel uses a three-tier configuration system:

1. **`config/config.yaml`** - Shared configuration (agent settings, workspace paths, integration URLs)
2. **`config/config.local.yaml`** - Local overrides (machine-specific projects, not committed to git)
3. **Environment variables** - API credentials and sensitive data

This approach keeps sensitive credentials and local project configurations out of version control while maintaining flexible shared configuration.

---

## Quick Start

1. **Copy the environment template:**
   ```bash
   cp config/.env.example config/.env
   ```

2. **Edit `config/.env` with your credentials**

3. **Customize `config/config.yaml` for your projects**

4. **Validate configuration:**
   ```bash
   export $(cat config/.env | xargs)
   poetry run sentinel validate
   ```

---

## Table of Contents

- [Environment Variables](#environment-variables)
- [config.yaml Structure](#configyaml-structure)
  - [Projects](#projects)
  - [Workspace](#workspace)
  - [Jira Configuration](#jira-configuration)
  - [GitLab Configuration](#gitlab-configuration)
  - [Agent Configuration](#agent-configuration)
  - [Agent SDK Configuration](#agent-sdk-configuration)
  - [Iteration Limits](#iteration-limits)
  - [Beads Configuration](#beads-configuration)
  - [Logging](#logging)
- [Local Configuration Override](#local-configuration-override)
- [Adding New Projects](#adding-new-projects)
- [Customizing Agents](#customizing-agents)
- [Environment-Specific Configuration](#environment-specific-configuration)

---

## Environment Variables

Environment variables store sensitive credentials. Never commit these to git.

### Required Variables

#### Jira API Credentials

```bash
# Jira base URL (your company's Atlassian instance)
export JIRA_BASE_URL="https://your-company.atlassian.net"

# Jira API token (create at: https://id.atlassian.com/manage-profile/security/api-tokens)
export JIRA_API_TOKEN="your_jira_api_token_here"

# Your Jira account email
export JIRA_EMAIL="your.email@company.com"
```

**How to get:**
1. Go to https://id.atlassian.com/manage-profile/security/api-tokens
2. Click "Create API token"
3. Copy the token and add to `.env`

#### GitLab API Credentials

```bash
# GitLab base URL (use https://gitlab.com for gitlab.com)
export GITLAB_BASE_URL="https://gitlab.com"

# GitLab personal access token (create at: GitLab → Settings → Access Tokens)
export GITLAB_API_TOKEN="your_gitlab_token_here"
```

**Required scopes:**
- `api` - Full API access
- `write_repository` - Create merge requests

#### LLM Provider API

```bash
# LLM Provider API key (contact your LLM Provider administrator)
export LLM_PROVIDER_API_KEY="your_llm_provider_api_key"

# LLM Provider base URL
export LLM_PROVIDER_BASE_URL="https://api.llm-provider.example.com/v1"
```

### Loading Environment Variables

**Option 1: Export from .env file**
```bash
export $(cat config/.env | xargs)
poetry run sentinel validate
```

**Option 2: Use run-with-env.sh script**
```bash
./run-with-env.sh validate
```

**Option 3: Add to shell profile** (~/.bashrc, ~/.zshrc)
```bash
# Add to shell profile for persistent environment
source /path/to/sentinel/config/.env
```

---

## config.yaml Structure

**Location**: `sentinel/config/config.yaml`

Complete annotated configuration file:

### Full Example

```yaml
version: "1.0"

# Project Mappings
projects:
  ACME:
    git_url: "git@gitlab.com:acme/backend.git"
    default_branch: "main"
    jira_project_key: "ACME"
  SENTEST:
    git_url: "git@gitlab.com:vpl-test2/todo.git"
    default_branch: "main"
    jira_project_key: "SENTEST"

# Workspace Configuration
workspace:
  root_dir: "~/sentinel-workspaces"
  plans_dir: ".agents/plans"
  memory_dir: ".agents/memory"

# Jira Configuration
jira:
  base_url: "https://company.atlassian.net"
  api_token_env: "JIRA_API_TOKEN"
  email_env: "JIRA_EMAIL"

# GitLab Configuration
gitlab:
  base_url: "https://gitlab.com"
  api_token_env: "GITLAB_API_TOKEN"

# Agent Configuration
agents:
  plan_generator:
    model: "claude-4-5-opus"
    temperature: 0.3

  python_developer:
    model: "claude-4-5-sonnet"
    temperature: 0.2
    specializations:
      - "python"
      - "pydantic-ai"
      - "fastapi"
      - "postgresql"

  security_review:
    model: "claude-4-5-sonnet"
    temperature: 0.1
    strictness: 5
    veto_power: true

# Iteration Limits
iteration_limits:
  max_iterations: 5
  retry_on_failure: 2

# Beads Configuration
beads:
  auto_create: true
  task_prefix: "sentinel"

# Agent SDK Configuration
agent_sdk:
  default_tools:
    - "Read"
    - "Grep"
    - "Glob"
  auto_edits: true
  planning_agent_tools:
    - "Read"
    - "Grep"
    - "Glob"
    - "Bash(git *)"
  implementation_agent_tools:
    - "Read"
    - "Write"
    - "Edit"
    - "Grep"
    - "Glob"
    - "Bash"

# Logging
logging:
  level: "INFO"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

---

## Configuration Sections

### Projects

**Purpose**: Map project keys to Git repositories and Jira projects.

**Structure**:
```yaml
projects:
  PROJECT_KEY:              # Jira project key (uppercase)
    git_url: string         # Git repository URL (SSH or HTTPS)
    default_branch: string  # Default branch name
    jira_project_key: string # Jira project key (usually same as PROJECT_KEY)
```

**Example - Adding a new project**:
```yaml
projects:
  BACKEND:
    git_url: "git@gitlab.com:company/backend-api.git"
    default_branch: "develop"
    jira_project_key: "BACKEND"
```

**Notes**:
- Project keys must match Jira ticket prefixes (e.g., "BACKEND-123" → "BACKEND")
- `git_url` supports SSH (recommended) or HTTPS
- `default_branch` is used as the base branch for new worktrees

---

### Workspace

**Purpose**: Configure workspace directories for git worktrees and agent artifacts.

**Structure**:
```yaml
workspace:
  root_dir: string          # Root directory for all worktrees
  plans_dir: string         # Directory for implementation plans (within worktree)
  memory_dir: string        # Directory for agent memory (within worktree)
```

**Default**:
```yaml
workspace:
  root_dir: "~/sentinel-workspaces"
  plans_dir: ".agents/plans"
  memory_dir: ".agents/memory"
```

**Resulting structure**:
```
~/sentinel-workspaces/
├── ACME/                   # Bare git repository
│   └── ACME-123/           # Worktree for ticket ACME-123
│       ├── .agents/
│       │   ├── plans/      # Implementation plans
│       │   └── memory/     # Agent memory/context
│       └── src/            # Project source code
└── BACKEND/
    └── BACKEND-456/
```

**Customization**:
```yaml
workspace:
  root_dir: "/data/sentinel"  # Custom location
  plans_dir: "docs/plans"     # Custom plans directory
  memory_dir: ".sentinel"     # Custom memory directory
```

---

### Jira Configuration

**Purpose**: Configure Jira API connection.

**Structure**:
```yaml
jira:
  base_url: string          # Jira instance URL
  api_token_env: string     # Environment variable name for API token
  email_env: string         # Environment variable name for email
```

**Default**:
```yaml
jira:
  base_url: "https://company.atlassian.net"
  api_token_env: "JIRA_API_TOKEN"
  email_env: "JIRA_EMAIL"
```

**How it works**:
- `base_url` is read directly from config
- `api_token_env` tells Sentinel to read the token from environment variable `JIRA_API_TOKEN`
- `email_env` tells Sentinel to read the email from environment variable `JIRA_EMAIL`

This keeps credentials out of the config file.

---

### GitLab Configuration

**Purpose**: Configure GitLab API connection.

**Structure**:
```yaml
gitlab:
  base_url: string          # GitLab instance URL
  api_token_env: string     # Environment variable name for API token
```

**Default**:
```yaml
gitlab:
  base_url: "https://gitlab.com"
  api_token_env: "GITLAB_API_TOKEN"
```

**For self-hosted GitLab**:
```yaml
gitlab:
  base_url: "https://gitlab.company.com"
  api_token_env: "GITLAB_API_TOKEN"
```

---

### Agent Configuration

**Purpose**: Configure LLM models and parameters for each agent.

**Structure**:
```yaml
agents:
  agent_name:
    model: string           # Claude model name
    temperature: float      # Sampling temperature (0.0 - 1.0)
    specializations: list   # Optional: agent specializations
    strictness: int         # Optional: for review agents (1-10)
    veto_power: bool        # Optional: for review agents
```

**Available Models**:
- `claude-4-5-opus` - Most capable, best for planning
- `claude-4-5-sonnet` - Balanced, good for implementation
- `claude-4-5-haiku` - Fastest, good for simple tasks

**Agent Defaults**:
```yaml
agents:
  plan_generator:
    model: "claude-4-5-opus"      # Use Opus for complex planning
    temperature: 0.3              # Moderate creativity

  python_developer:
    model: "claude-4-5-sonnet"    # Balanced for code
    temperature: 0.2              # Low creativity (precise code)
    specializations:
      - "python"
      - "pydantic-ai"
      - "fastapi"
      - "postgresql"

  security_review:
    model: "claude-4-5-sonnet"
    temperature: 0.1              # Very low (strict review)
    strictness: 5                 # Medium strictness (1-10 scale)
    veto_power: true              # Can block insecure code
```

**Customization Examples**:

**Use Haiku for faster planning (trade capability for speed)**:
```yaml
agents:
  plan_generator:
    model: "claude-4-5-haiku"
    temperature: 0.3
```

**Increase security strictness**:
```yaml
agents:
  security_review:
    model: "claude-4-5-opus"   # Use Opus for thorough review
    temperature: 0.0           # Zero creativity
    strictness: 9              # Very strict
    veto_power: true
```

**Add agent specializations**:
```yaml
agents:
  python_developer:
    model: "claude-4-5-sonnet"
    temperature: 0.2
    specializations:
      - "python"
      - "django"              # Add Django
      - "celery"              # Add Celery
      - "redis"               # Add Redis
```

---

### Agent SDK Configuration

**Purpose**: Configure Claude Agent SDK tools and permissions.

**Structure**:
```yaml
agent_sdk:
  default_tools: list           # Default tools for all agents
  auto_edits: bool              # Auto-accept file edits
  planning_agent_tools: list    # Tools for planning agents
  implementation_agent_tools: list # Tools for implementation agents
```

**Default**:
```yaml
agent_sdk:
  default_tools:
    - "Read"                    # Read files
    - "Grep"                    # Search file contents
    - "Glob"                    # Find files by pattern
  auto_edits: true              # Agents can edit files without confirmation
  planning_agent_tools:
    - "Read"
    - "Grep"
    - "Glob"
    - "Bash(git *)"            # Git commands only
  implementation_agent_tools:
    - "Read"
    - "Write"                  # Create new files
    - "Edit"                   # Modify existing files
    - "Grep"
    - "Glob"
    - "Bash"                   # Full bash access
```

**Available Tools**:
- `Read` - Read file contents
- `Write` - Create new files
- `Edit` - Modify existing files
- `Grep` - Search file contents with regex
- `Glob` - Find files by pattern
- `Bash` - Execute bash commands
- `Bash(pattern)` - Limited bash commands (e.g., `Bash(git *)` allows only git commands)

**Security Note**: Be careful with `Bash` tool - it allows arbitrary command execution.

---

### Iteration Limits

**Purpose**: Control workflow iteration behavior.

**Structure**:
```yaml
iteration_limits:
  max_iterations: int       # Maximum developer-security iterations
  retry_on_failure: int     # Retries on transient failures
```

**Default**:
```yaml
iteration_limits:
  max_iterations: 5
  retry_on_failure: 2
```

**What these control**:
- `max_iterations`: Maximum rounds of developer → security review → developer feedback loop
- `retry_on_failure`: How many times to retry API calls or git operations on transient errors

**Customization**:
```yaml
iteration_limits:
  max_iterations: 10      # Allow more iterations for complex tickets
  retry_on_failure: 3     # More retries for unstable networks
```

---

### Beads Configuration

**Purpose**: Configure Beads task tracking integration.

**Structure**:
```yaml
beads:
  auto_create: bool         # Auto-create Beads tasks for tickets
  task_prefix: string       # Prefix for auto-created tasks
```

**Default**:
```yaml
beads:
  auto_create: true
  task_prefix: "sentinel"
```

**How it works**:
- When `auto_create: true`, Sentinel automatically creates Beads tasks for tracking
- Tasks are named: `{task_prefix}-{random_id}` (e.g., "sentinel-abc")

**Disable auto-tracking**:
```yaml
beads:
  auto_create: false
```

---

### Logging

**Purpose**: Configure logging level and format.

**Structure**:
```yaml
logging:
  level: string             # Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL
  format: string            # Log message format
```

**Default**:
```yaml
logging:
  level: "INFO"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

**Levels**:
- `DEBUG` - Verbose logging (all operations)
- `INFO` - Normal logging (major operations)
- `WARNING` - Warnings only
- `ERROR` - Errors only
- `CRITICAL` - Critical errors only

**Enable debug logging**:
```yaml
logging:
  level: "DEBUG"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

---

## Local Configuration Override

Sentinel supports a `config.local.yaml` file for machine-specific settings that should not be committed to version control.

### How It Works

1. **Base config**: `config/config.yaml` is loaded first
2. **Local override**: `config/config.local.yaml` is merged on top (if it exists)
3. **Deep merge**: Nested dictionaries are merged recursively, with local values taking precedence

### What Gets Stored Locally

When you use CLI commands to manage projects, changes are saved to `config.local.yaml`:

```bash
sentinel projects add      # Saves new project to config.local.yaml
sentinel projects remove   # Updates config.local.yaml
sentinel projects update   # Updates config.local.yaml
```

This ensures your personal test projects are not committed to the shared repository.

### Example

**Shared config (`config/config.yaml`)**:
```yaml
version: "1.0"
agents:
  plan_generator:
    model: "claude-opus-4-5"
    temperature: 0.3
workspace:
  root_dir: "~/sentinel-workspaces"
```

**Local override (`config/config.local.yaml`)** - git-ignored:
```yaml
projects:
  MY_TEST_PROJECT:
    git_url: "https://gitlab.com/myuser/my-repo.git"
    default_branch: "main"
    jira_project_key: "MY_TEST_PROJECT"
agents:
  plan_generator:
    model: "claude-4-5-haiku"  # Override to use faster model locally
```

**Resulting merged config**:
- `version`: "1.0" (from base)
- `agents.plan_generator.model`: "claude-4-5-haiku" (from local - overridden)
- `agents.plan_generator.temperature`: 0.3 (from base - preserved)
- `workspace.root_dir`: "~/sentinel-workspaces" (from base)
- `projects.MY_TEST_PROJECT`: (from local - added)

### Creating config.local.yaml

You can create the file manually:

```bash
touch config/config.local.yaml
```

Or it will be created automatically when you add a project via CLI:

```bash
sentinel projects add
```

---

## Adding New Projects

**Step-by-step guide to add a new project**:

1. **Edit `config/config.yaml`**:
   ```yaml
   projects:
     NEWPROJ:
       git_url: "git@gitlab.com:company/new-project.git"
       default_branch: "main"
       jira_project_key: "NEWPROJ"
   ```

2. **Ensure SSH key has access to the repository**:
   ```bash
   ssh -T git@gitlab.com
   ```

3. **Test configuration**:
   ```bash
   poetry run sentinel plan NEWPROJ-1  # Should create worktree
   ```

4. **Verify worktree creation**:
   ```bash
   ls ~/sentinel-workspaces/NEWPROJ/
   ```

---

## Customizing Agents

### Change Models

**Use different models for different agents**:
```yaml
agents:
  plan_generator:
    model: "claude-4-5-opus"      # Most capable for planning

  python_developer:
    model: "claude-4-5-haiku"     # Faster for simple implementations

  security_review:
    model: "claude-4-5-opus"      # Most thorough for security
```

### Adjust Temperature

**Temperature controls randomness (0.0 = deterministic, 1.0 = creative)**:

```yaml
agents:
  plan_generator:
    temperature: 0.5              # More creative planning

  python_developer:
    temperature: 0.1              # Very deterministic code

  security_review:
    temperature: 0.0              # No randomness in security
```

### Add Specializations

**Guide agents with domain expertise**:
```yaml
agents:
  python_developer:
    model: "claude-4-5-sonnet"
    temperature: 0.2
    specializations:
      - "python"
      - "async-programming"
      - "sqlalchemy"
      - "alembic-migrations"
      - "pytest"
```

---

## Environment-Specific Configuration

### Development vs Production

**Option 1: Multiple config files**:
```bash
# Development
config/config.dev.yaml

# Production
config/config.prod.yaml
```

Pass config path:
```python
from src.config_loader import ConfigLoader

config = ConfigLoader(Path("config/config.dev.yaml"))
```

**Option 2: Environment-specific .env files**:
```bash
config/.env.dev
config/.env.prod
```

Load appropriate env:
```bash
export $(cat config/.env.dev | xargs)
```

---

## Troubleshooting Configuration

### Verify Configuration

**Test all credentials**:
```bash
poetry run sentinel validate
```

**Check configuration loading**:
```python
from src.config_loader import get_config

config = get_config()
print(config.get_agent_config("plan_generator"))
```

### Common Issues

**Environment variables not loading**:
```bash
# Make sure to export
export $(cat config/.env | xargs)

# Verify
echo $JIRA_API_TOKEN
```

**Invalid config.yaml**:
```bash
# Validate YAML syntax
python -c "import yaml; yaml.safe_load(open('config/config.yaml'))"
```

**Git worktree failures**:
```bash
# Check SSH access
ssh -T git@gitlab.com

# Verify git_url in config
grep git_url config/config.yaml
```

---

## See Also

- **[Credentials Setup Guide](../CREDENTIALS.md)** - Detailed credential configuration
- **[Troubleshooting Guide](TROUBLESHOOTING.md)** - Configuration error solutions
- **[API Documentation](API.md)** - ConfigLoader API reference
