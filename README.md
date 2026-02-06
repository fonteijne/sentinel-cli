# Sentinel

Autonomous agent orchestration system for Jira ticket implementation.

**Quick start for new users:**
For running on your own machine:
```bash
cd sentinel && source init.sh  # Install deps + activate environment
```

For running the docker container
```bash
docker compose up -d
docker compose exec sentinel zsh
```

Edit config/.env with your API credentials
```bash
cp config/.env.example config/.env
```

Start commands
```bash
sentinel validate              # Verify setup
sentinel auth configure        # Configure Claude Code Auth
sentinel projects add          # Add your first project

sentinel info ACME-123         # Get info of Jira ticket
sentinel plan ACME-123         # Create plan for Jira ticket
sentinel plan ACME-123 --revise      # Work on feedback in MR
sentinel execute ACME-123      # Execute the plan
sentinel execute ACME-123 --revise   # Work on feedback in MR
```

---

## Overview

Sentinel is an AI-powered system that transforms Jira tickets into production-ready code automatically. Using a multi-agent architecture powered by Claude, Sentinel orchestrates specialized agents that plan implementations, write code, and perform security reviews—autonomously handling the entire development workflow from ticket to merge-ready code.

**Key capabilities:**
- **Autonomous Planning**: Analyzes Jira tickets and generates comprehensive implementation plans
- **Test-Driven Development**: Implements features with automated testing
- **Security Review**: Built-in security agent validates all code changes
- **GitLab Integration**: Creates merge requests with implementation and security review
- **Multi-Agent Workflow**: Specialized agents collaborate through iterative feedback loops
- **Feedback Iteration**: Automatically revises plans and implementations based on MR review comments
- **Claude Agent SDK**: Agents autonomously explore codebases, read files, run commands, and write code

Sentinel eliminates repetitive implementation work, allowing developers to focus on architecture, code review, and high-value problem-solving.

---

## Architecture

Sentinel uses a **multi-agent workflow** where specialized AI agents collaborate:

```
┌─────────────────────────────────────────────────────────────────┐
│                         Jira Ticket                             │
│                    (PROJ-123: Feature request)                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    1. Plan Generator Agent                      │
│  • Analyzes ticket requirements                                 │
│  • Explores codebase patterns                                   │
│  • Generates implementation plan                                │
│  • Creates draft merge request                                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  2. Python Developer Agent                      │
│  • Implements code based on plan                                │
│  • Writes tests                                                 │
│  • Iterates based on feedback                                   │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                 3. Security Reviewer Agent                      │
│  • Reviews code for vulnerabilities                             │
│  • Checks for security best practices                           │
│  • Can veto insecure implementations                            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│               Iteration Loop (if needed)                        │
│  Developer ←→ Security Reviewer until approval                  │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Merge-Ready Code                             │
│  • Implementation complete                                      │
│  • Tests passing                                                │
│  • Security approved                                            │
│  • Ready for human review                                       │
└─────────────────────────────────────────────────────────────────┘
```

Each agent uses the **Claude Agent SDK** for autonomous tool use (Read, Write, Grep, Glob, Bash), enabling context-aware decision-making without manual intervention.

---

## First-Time Setup

Follow these steps to get Sentinel running for the first time.

### Prerequisites

Before starting, ensure you have:

- **Python 3.11+** - Check with `python3 --version`
- **Poetry** - Install with `curl -sSL https://install.python-poetry.org | python3 -`
- **Git** - Check with `git --version`
- **API credentials** (see [Step 2: Configure Credentials](#step-2-configure-credentials) below)

### Step 1: Install and Initialize

```bash
# Clone and enter the directory
git clone <repository-url>
cd sentinel

# Run the initialization script (does everything!)
source init.sh
```

The `init.sh` script will:
- Check all prerequisites
- Install Python dependencies with Poetry
- Create `config/.env` from template (if missing)
- Load environment variables
- Activate the virtual environment
- Verify the installation

After sourcing, you can use `sentinel` directly instead of `poetry run sentinel`.

### Step 2: Configure Credentials

Edit `config/.env` with your API credentials:

```bash
# Open in your editor
$EDITOR config/.env
```

Required credentials:

| Variable | Description | How to Get |
|----------|-------------|------------|
| `JIRA_API_TOKEN` | Jira API token | [Atlassian API Tokens](https://id.atlassian.com/manage/api-tokens) |
| `JIRA_EMAIL` | Your Atlassian email | Your login email |
| `JIRA_BASE_URL` | Jira instance URL | e.g., `https://yourcompany.atlassian.net` |
| `GITLAB_API_TOKEN` | GitLab personal access token | GitLab → User Settings → Access Tokens |
| `GITLAB_BASE_URL` | GitLab instance URL | e.g., `https://gitlab.com` |
| `LLM_PROVIDER_API_KEY` | LLM API key | Your LLM Provider API key |
| `LLM_PROVIDER_BASE_URL` | LLM API endpoint | e.g., `https://api.llm-provider.example.com/v1` |

After editing, reload the environment:

```bash
source init.sh
```

### Step 3: Validate Setup

```bash
sentinel validate
```

You should see:

```
✅ Jira connected
✅ GitLab connected
✅ LLM Provider configured
✅ Beads CLI available
```

### Step 4: Add Your First Project

Add a project that links a Jira project to a GitLab repository:

```bash
sentinel projects add
```

You'll be prompted for:
- **JIRA project key** - e.g., `ACME` (the prefix of your ticket IDs like ACME-123)
- **Git origin URL** - HTTPS URL of your GitLab repository
- **Default branch** - Usually `main` or `master`

Example:
```
➕ Add New Project

JIRA project key: ACME
Git origin URL (use HTTPS, not SSH): https://gitlab.com/myorg/backend.git
Default branch [main]: main

✅ Project ACME added successfully
```

You can list configured projects with:
```bash
sentinel projects list
```

### Step 5: Test with a Ticket

Try fetching information about a real Jira ticket:

```bash
sentinel info ACME-123
```

If this works, you're ready to use Sentinel!

---

## Shell Setup (Optional)

### Per-Session Activation (Default)

Each time you open a new terminal and want to use Sentinel:

```bash
cd /path/to/sentinel
source init.sh
```

### Permanent Shell Function (Optional)

Add this to your `~/.bashrc` or `~/.zshrc` to automatically activate Sentinel when entering the directory:

```bash
# Sentinel auto-activation
sentinel_activate() {
    if [[ -f "init.sh" && -f "pyproject.toml" ]]; then
        if grep -q "name.*=.*\"sentinel\"" pyproject.toml 2>/dev/null; then
            source init.sh
        fi
    fi
}

# Auto-activate on directory change (optional)
cd() {
    builtin cd "$@" && sentinel_activate
}
```

After adding this, restart your shell or run `source ~/.bashrc` (or `~/.zshrc`).

---

## Usage

### Quick Reference

| Command | Description |
|---------|-------------|
| `sentinel info PROJ-123` | View Jira ticket details |
| `sentinel plan PROJ-123` | Generate implementation plan |
| `sentinel execute PROJ-123` | Execute the plan |
| `sentinel reset PROJ-123` | Clean up worktree and branch |
| `sentinel status` | Show active worktrees |
| `sentinel validate` | Check API credentials |
| `sentinel projects list` | List configured projects |

### Basic Workflow

**1. Generate a plan for a Jira ticket:**
```bash
sentinel plan PROJ-123
```

This will:
- Fetch the Jira ticket details
- Create a git worktree for isolated development
- Generate an implementation plan
- Create a draft merge request with the plan

**2. Execute the implementation:**
```bash
sentinel execute PROJ-123
```

This will:
- Implement the code based on the plan
- Write tests
- Run security review
- Iterate until security approval
- Push changes to the merge request

**3. Reset after merge (or to start fresh):**
```bash
sentinel reset PROJ-123
```

This removes the git worktree AND local branch, ensuring a clean slate.

### Additional Commands

**View ticket information:**
```bash
sentinel info PROJ-123
```

**Check worktree status:**
```bash
sentinel status
```

**Revise a plan based on MR feedback:**
```bash
sentinel plan PROJ-123 --revise
```

**Revise implementation based on code review feedback:**
```bash
sentinel execute PROJ-123 --revise
```

**Reset all worktrees and repositories:**
```bash
sentinel reset --all
```

See `sentinel --help` for all available commands.

---

## Documentation

### For Users

- **[Configuration Guide](docs/CONFIGURATION.md)** - Complete `config.yaml` and environment variable reference
- **[Troubleshooting Guide](docs/TROUBLESHOOTING.md)** - Solutions to common problems and debugging techniques
- **[Credentials Setup](CREDENTIALS.md)** - Detailed credential configuration for Jira, GitLab, and LLM Provider

### For Developers

- **[API Documentation](docs/API.md)** - Complete API reference for all agents, clients, and managers
- **[Development Guide](docs/DEVELOPMENT.md)** - Setup, testing, code standards, and contributing guidelines

### Advanced Topics

- **[Agent SDK Migration](docs/agent_sdk_migration.md)** - Understanding the Claude Agent SDK integration
- **[Reset Command](docs/reset-command.md)** - Worktree and session management details

---

## Project Status

**Current Status**: ✅ **MVP Complete** (70% of planned features)

### What Works

✅ **Core Infrastructure**
- Configuration system (YAML + environment variables)
- CLI with all major commands
- Git worktree management
- Jira and GitLab API integration
- Beads task tracking integration

✅ **Agents**
- Base agent architecture with Claude Agent SDK
- Plan Generator (autonomous codebase exploration)
- Python Developer (code implementation)
- Security Reviewer (vulnerability detection with veto power)

✅ **Workflows**
- Plan generation workflow
- Execute workflow with developer-security iteration loop
- Cleanup workflow
- Plan revision based on MR feedback

✅ **Quality Gates**
- Type checking (mypy): 0 errors
- Linting (ruff): 0 errors
- Tests: 267 passing
- Agent SDK integration: Fully functional

### What's Next

🔄 **In Progress**
- Comprehensive documentation (this guide and API docs)
- End-to-end testing with real tickets

📋 **Planned**
- CI/CD pipeline setup
- Pre-commit hooks
- Demo video and tutorials
- Performance benchmarks

---

## Development

This project uses modern Python tooling for quality and maintainability:

- **Poetry** - Dependency management and packaging
- **Ruff** - Fast Python linter
- **mypy** - Static type checking
- **pytest** - Testing framework with async support

### Running Tests

```bash
# Run all tests
poetry run pytest

# Run with coverage
poetry run pytest --cov=src

# Run specific test file
poetry run pytest tests/test_base_agent.py

# Run tests matching pattern
poetry run pytest -k "test_agent"
```

### Type Checking

```bash
# Type check entire codebase
poetry run mypy src/

# Type check specific module
poetry run mypy src/agents/
```

### Linting

```bash
# Check for linting issues
poetry run ruff check src/

# Auto-fix linting issues
poetry run ruff check --fix src/
```

### Project Structure

```
sentinel/
├── config/              # Configuration files
│   ├── config.yaml      # Main configuration
│   └── .env.example     # Environment variable template
├── docs/                # Documentation
├── src/                 # Source code
│   ├── agents/          # Agent implementations
│   │   ├── base_agent.py
│   │   ├── plan_generator.py
│   │   ├── python_developer.py
│   │   └── security_reviewer.py
│   ├── cli.py           # Click-based CLI
│   ├── config_loader.py
│   ├── jira_client.py
│   ├── gitlab_client.py
│   ├── worktree_manager.py
│   └── agent_sdk_wrapper.py
└── tests/               # Test suite (267 tests)
```

---

## Configuration

Sentinel uses a combination of YAML configuration (`config/config.yaml`) and environment variables.

**Quick configuration:**

1. Copy the example: `cp config/.env.example config/.env`
2. Edit `config/.env` with your credentials
3. Customize `config/config.yaml` for your projects

### Local Configuration Override

Sentinel supports a `config.local.yaml` file for machine-specific settings that should not be committed to version control. This file:

- **Extends** `config.yaml` with local overrides
- **Takes precedence** over values in `config.yaml` for any overlapping keys
- **Is automatically git-ignored** (added to `.gitignore`)
- **Is used for project storage** - when you add/update/remove projects via CLI, changes are saved to `config.local.yaml`

**Example use cases:**

**1. Local test projects:** Each developer has their own projects they're testing with:

```yaml
# config/config.local.yaml (not committed to git)
projects:
  MY_LOCAL_PROJECT:
    git_url: https://gitlab.com/myorg/my-repo.git
    default_branch: main
    jira_project_key: MY_LOCAL_PROJECT
```

**2. Custom LLM provider models:** If your LLM provider uses different model names than the defaults in `config.yaml`, override them locally:

```yaml
# config/config.local.yaml
agents:
  plan_generator:
    model: gpt-4-turbo  # Your provider's model name
  python_developer:
    model: gpt-4
  security_review:
    model: gpt-4
```

This lets teams share a common `config.yaml` while individual developers can adapt to their specific LLM provider or use faster/cheaper models for local development.

See the [Configuration Guide](docs/CONFIGURATION.md) for complete details on all options.

---

## Contributing

We welcome contributions! To get started:

1. Read the [Development Guide](docs/DEVELOPMENT.md)
2. Set up your development environment
3. Run tests to ensure everything works
4. Make your changes
5. Submit a pull request

Please ensure:
- All tests pass (`poetry run pytest`)
- Type checking passes (`poetry run mypy src/`)
- Linting passes (`poetry run ruff check src/`)
- New features include tests
- Public APIs have docstrings

---

## License

Sentinel is dual-licensed:

- **Non-commercial use**: [PolyForm Noncommercial License 1.0.0](LICENSE)
- **Commercial use**: [Commercial License](COMMERCIAL-LICENSE.md) required

For commercial licensing inquiries, see [COMMERCIAL-LICENSE.md](COMMERCIAL-LICENSE.md).

---

## Support

### Getting Help

1. Check the [Troubleshooting Guide](docs/TROUBLESHOOTING.md)
2. Review the [API Documentation](docs/API.md)
3. Open an issue on GitHub

### Useful Resources

- **Logs**: Check `sentinel.log` for detailed execution logs
- **Validate**: Run `sentinel validate` to check credentials and connectivity
- **Status**: Run `sentinel status` to see active worktrees

---

**Questions?** See the [full documentation](docs/) or open an issue.
