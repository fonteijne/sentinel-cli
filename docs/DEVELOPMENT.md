# Sentinel Development Guide

Complete guide for contributors and developers working on Sentinel.

## Overview

Sentinel is built with **Python 3.11+** using **Poetry** for dependency management and the **Claude Agent SDK** for autonomous agent orchestration. This guide covers everything you need to contribute effectively.

---

## Table of Contents

- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Development Workflow](#development-workflow)
- [Testing](#testing)
- [Adding New Agents](#adding-new-agents)
- [Adding New Integrations](#adding-new-integrations)
- [Code Standards](#code-standards)
- [Contributing](#contributing)
- [Debugging](#debugging)

---

## Development Setup

### Prerequisites

**Required:**
- **Python 3.11+** - Sentinel uses modern Python features
- **Poetry 1.6+** - Dependency and environment management
- **Git** - Version control
- **SSH key configured** - For GitLab repository access

**Optional:**
- **Claude API key** - For testing with Anthropic API directly
- **LLM Provider API key** - If using LLM Provider proxy
- **Jira account** - For testing Jira integration
- **GitLab account** - For testing MR creation

### Installation Steps

1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd sentinel
   ```

2. **Install dependencies with Poetry:**
   ```bash
   poetry install
   ```

   This creates a virtual environment and installs all dependencies from `pyproject.toml`.

3. **Activate the virtual environment:**
   ```bash
   poetry shell
   ```

   Or run commands with `poetry run`:
   ```bash
   poetry run sentinel --help
   ```

4. **Set up environment variables:**
   ```bash
   cp config/.env.example config/.env
   # Edit config/.env with your credentials
   ```

   See **[CREDENTIALS.md](../CREDENTIALS.md)** for detailed credential setup.

5. **Load environment variables:**
   ```bash
   export $(cat config/.env | xargs)
   ```

6. **Validate setup:**
   ```bash
   poetry run sentinel validate
   ```

   Expected output: All services (Jira, GitLab, LLM Provider, Beads) validated successfully.

---

## Project Structure

```
sentinel/
├── .agents/                    # Agent-generated artifacts (gitignored)
│   ├── plans/                 # Implementation plans
│   └── memory/                # Agent memory/context
├── config/                    # Configuration files
│   ├── config.yaml           # Project mappings and settings
│   ├── .env.example          # Environment variable template
│   └── .env                  # Your credentials (gitignored)
├── docs/                      # Documentation
│   ├── API.md                # API reference
│   ├── CONFIGURATION.md      # Configuration guide
│   ├── DEVELOPMENT.md        # This file
│   └── TROUBLESHOOTING.md    # Troubleshooting guide
├── prompts/                   # Agent system prompts
│   ├── plan_generator.txt    # Planning agent prompt
│   ├── python_developer.txt  # Developer agent prompt
│   └── security_reviewer.txt # Security agent prompt
├── src/                       # Source code
│   ├── agents/               # Agent implementations
│   │   ├── base_agent.py     # Base agent class
│   │   ├── plan_generator.py # Planning agent
│   │   ├── python_developer.py # Developer agent
│   │   └── security_reviewer.py # Security agent
│   ├── utils/                # Utility modules
│   │   └── adf_parser.py     # Atlassian Document Format parser
│   ├── agent_sdk_wrapper.py  # Claude Agent SDK integration
│   ├── beads_manager.py      # Beads task tracking
│   ├── cli.py                # Click CLI interface
│   ├── command_executor.py   # Git command execution
│   ├── config_loader.py      # Configuration loading
│   ├── gitlab_client.py      # GitLab API client
│   ├── jira_client.py        # Jira API client
│   ├── prompt_loader.py      # Prompt file loader
│   ├── session_tracker.py    # Agent session tracking
│   └── worktree_manager.py   # Git worktree management
├── tests/                     # Test suite
│   ├── test_base_agent.py    # Agent tests
│   ├── test_config_loader.py # Config tests
│   ├── test_jira_client.py   # Jira client tests
│   └── ...                   # Other test files
├── pyproject.toml            # Poetry dependencies and tool config
├── README.md                 # Main documentation
├── CREDENTIALS.md            # Credential setup guide
└── sentinel.log              # Runtime logs (gitignored)
```

### Module Organization

**Core Components:**
- **CLI (`cli.py`)**: Command-line interface using Click
- **Config (`config_loader.py`)**: YAML + environment variable configuration
- **Agents (`agents/`)**: Autonomous agent implementations
- **Clients (`*_client.py`)**: API integrations (Jira, GitLab)
- **Managers**: Worktree, Beads, Session tracking

**Agent SDK Integration:**
- **`agent_sdk_wrapper.py`**: Wraps Claude Agent SDK for Sentinel agents
- **`session_tracker.py`**: Tracks agent sessions for cleanup
- **`command_executor.py`**: Executes git commands with dry-run support

---

## Development Workflow

### Running Sentinel Locally

**View available commands:**
```bash
poetry run sentinel --help
```

**Run specific commands:**
```bash
# Validate credentials
poetry run sentinel validate

# Show status
poetry run sentinel status

# Plan a ticket (dry run - doesn't execute)
poetry run sentinel plan SENTEST-123 --dry-run

# Execute a ticket
poetry run sentinel execute SENTEST-123
```

### Quality Checks

**Type checking with mypy:**
```bash
poetry run mypy src/
```

Expected output: No errors (all code has type hints).

**Linting with ruff:**
```bash
# Check for issues
poetry run ruff check src/

# Auto-fix issues
poetry run ruff check src/ --fix

# Format code
poetry run ruff format src/
```

**Run all checks together:**
```bash
poetry run mypy src/ && poetry run ruff check src/
```

### Running Tests

**Run all tests:**
```bash
poetry run pytest
```

**Run with coverage:**
```bash
poetry run pytest --cov=src --cov-report=term-missing
```

**Run specific test file:**
```bash
poetry run pytest tests/test_base_agent.py
```

**Run specific test function:**
```bash
poetry run pytest tests/test_base_agent.py::test_agent_initialization
```

**Run with verbose output:**
```bash
poetry run pytest -v
```

---

## Testing

### Test File Organization

Tests mirror the source structure:
- `tests/test_base_agent.py` → `src/agents/base_agent.py`
- `tests/test_config_loader.py` → `src/config_loader.py`
- etc.

### Writing New Tests

**Pattern to follow** (from `tests/test_base_agent.py`):

```python
"""Tests for BaseAgent class."""

import pytest
from src.agents.base_agent import BaseAgent


class ConcreteAgent(BaseAgent):
    """Concrete implementation for testing."""

    def run(self, task: str) -> str:
        """Run the agent with a task."""
        return f"Executed: {task}"


def test_agent_initialization():
    """Test BaseAgent initialization."""
    agent = ConcreteAgent(agent_name="test_agent")
    assert agent.agent_name == "test_agent"
    assert agent.model is not None
    assert agent.temperature is not None


def test_agent_run():
    """Test agent run method."""
    agent = ConcreteAgent(agent_name="test_agent")
    result = agent.run("test task")
    assert "Executed: test task" in result
```

### Async Testing

For async code, use `pytest-asyncio`:

```python
import pytest


@pytest.mark.asyncio
async def test_async_function():
    """Test async function."""
    result = await some_async_function()
    assert result is not None
```

### Mocking External APIs

**Mock Jira API:**
```python
from unittest.mock import Mock, patch


def test_jira_get_ticket():
    """Test Jira ticket retrieval."""
    with patch('src.jira_client.JiraClient.session') as mock_session:
        mock_response = Mock()
        mock_response.json.return_value = {
            "key": "TEST-123",
            "fields": {"summary": "Test ticket"}
        }
        mock_session.get.return_value = mock_response

        client = JiraClient()
        ticket = client.get_ticket("TEST-123")
        assert ticket["key"] == "TEST-123"
```

### Test Coverage Goals

- **Minimum coverage**: 70% overall
- **Critical paths**: 90%+ (agents, CLI, config)
- **Integration code**: 50%+ (clients, managers)

Run coverage report:
```bash
poetry run pytest --cov=src --cov-report=html
open htmlcov/index.html  # View detailed coverage
```

---

## Adding New Agents

### Step 1: Create Agent Class

Extend `BaseAgent` in `src/agents/your_agent.py`:

```python
"""Your agent implementation."""

from typing import Optional
from src.agents.base_agent import BaseAgent


class YourAgent(BaseAgent):
    """Agent that does X."""

    def __init__(
        self,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> None:
        """Initialize YourAgent.

        Args:
            model: LLM model to use (defaults to config)
            temperature: Sampling temperature (defaults to config)
        """
        super().__init__(
            agent_name="your_agent",  # Used for prompt loading
            model=model,
            temperature=temperature,
        )

    def run(self, task: str) -> str:
        """Execute the agent's task.

        Args:
            task: Task description

        Returns:
            Agent's response/output
        """
        # Use self.send_message() to interact with LLM
        response = self.send_message(
            message=f"Task: {task}",
            agent_tools=["Read", "Grep", "Glob"],
        )
        return response
```

### Step 2: Create Agent Prompt

Create `prompts/your_agent.txt`:

```
You are YourAgent, an AI assistant specialized in [purpose].

Your responsibilities:
- [Responsibility 1]
- [Responsibility 2]

When given a task:
1. [Step 1]
2. [Step 2]

Output format:
[Expected output format]
```

### Step 3: Add to Configuration

Update `config/config.yaml`:

```yaml
agents:
  your_agent:
    model: "claude-4-5-sonnet"
    temperature: 0.2
```

### Step 4: Add to CLI

Update `src/cli.py`:

```python
@cli.command()
@click.argument("task")
def your_command(task: str) -> None:
    """Execute your agent."""
    from src.agents.your_agent import YourAgent

    agent = YourAgent()
    result = agent.run(task)
    click.echo(result)
```

### Step 5: Write Tests

Create `tests/test_your_agent.py`:

```python
"""Tests for YourAgent."""

import pytest
from src.agents.your_agent import YourAgent


def test_your_agent_initialization():
    """Test YourAgent initialization."""
    agent = YourAgent()
    assert agent.agent_name == "your_agent"


def test_your_agent_run():
    """Test YourAgent run method."""
    agent = YourAgent()
    result = agent.run("test task")
    assert result is not None
```

---

## Adding New Integrations

### Client Pattern

Follow the pattern from `JiraClient` and `GitLabClient`:

```python
"""Your API client."""

import os
from typing import Dict, Any
import requests


class YourClient:
    """Client for Your API."""

    def __init__(self) -> None:
        """Initialize client with credentials."""
        self.base_url = os.getenv("YOUR_BASE_URL")
        api_token = os.getenv("YOUR_API_TOKEN")

        if not self.base_url or not api_token:
            raise ValueError("YOUR_BASE_URL and YOUR_API_TOKEN must be set")

        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        })

    def get_resource(self, resource_id: str) -> Dict[str, Any]:
        """Get resource by ID.

        Args:
            resource_id: Resource identifier

        Returns:
            Resource data

        Raises:
            requests.HTTPError: If API request fails
        """
        response = self.session.get(
            f"{self.base_url}/api/resources/{resource_id}"
        )
        response.raise_for_status()
        return response.json()
```

### Configuration Integration

Add to `config/config.yaml`:

```yaml
your_service:
  base_url: "https://api.yourservice.com"
  api_token_env: "YOUR_API_TOKEN"
```

Add to `src/config_loader.py`:

```python
def get_your_service_config(self) -> Dict[str, Any]:
    """Get Your Service configuration."""
    return self.config.get("your_service", {})
```

### Error Handling

Use consistent error handling:

```python
try:
    result = client.get_resource(resource_id)
except requests.HTTPError as e:
    logger.error(f"API error: {e}")
    raise RuntimeError(f"Failed to fetch resource: {e}") from e
```

---

## Code Standards

### Type Hints Required

**All functions must have type hints:**

```python
def process_ticket(ticket_id: str, project: str) -> Dict[str, Any]:
    """Process a Jira ticket.

    Args:
        ticket_id: Jira ticket ID (e.g., "PROJ-123")
        project: Project key

    Returns:
        Processing result with status and details
    """
    pass
```

**Enforce with mypy:**
```bash
poetry run mypy src/
```

Configuration in `pyproject.toml`:
```toml
[tool.mypy]
python_version = "3.11"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
```

### Docstrings for Public APIs

**Use Google-style docstrings:**

```python
def create_merge_request(
    project_id: str,
    source_branch: str,
    target_branch: str,
    title: str,
) -> Dict[str, Any]:
    """Create a GitLab merge request.

    Args:
        project_id: GitLab project ID
        source_branch: Source branch name
        target_branch: Target branch name (usually "main")
        title: MR title

    Returns:
        MR data with web_url and iid

    Raises:
        requests.HTTPError: If MR creation fails
        ValueError: If branches are invalid

    Example:
        >>> client = GitLabClient()
        >>> mr = client.create_merge_request(
        ...     project_id="123",
        ...     source_branch="feature-x",
        ...     target_branch="main",
        ...     title="Add feature X"
        ... )
        >>> print(mr["web_url"])
    """
    pass
```

### Ruff Linting Rules

Configuration in `pyproject.toml`:
```toml
[tool.ruff]
line-length = 88
target-version = "py311"
```

**Run ruff:**
```bash
# Check
poetry run ruff check src/

# Fix
poetry run ruff check src/ --fix

# Format
poetry run ruff format src/
```

### Logging Patterns

**Use Python's logging module:**

```python
import logging

logger = logging.getLogger(__name__)


def process_task(task_id: str) -> None:
    """Process a task."""
    logger.info(f"Processing task {task_id}")
    try:
        # Do work
        logger.debug(f"Task {task_id} details: {details}")
    except Exception as e:
        logger.error(f"Failed to process {task_id}: {e}", exc_info=True)
        raise
```

**Logging levels:**
- `DEBUG`: Detailed diagnostic info
- `INFO`: General informational messages
- `WARNING`: Warning messages
- `ERROR`: Error messages
- `CRITICAL`: Critical errors

Configure in `config/config.yaml`:
```yaml
logging:
  level: "INFO"
  format: "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
```

---

## Contributing

### Branch Naming

**Format**: `{type}/{description}`

**Types:**
- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation changes
- `refactor/` - Code refactoring
- `test/` - Test additions/fixes

**Examples:**
- `feature/add-github-integration`
- `fix/worktree-cleanup-error`
- `docs/update-api-reference`

### Commit Message Format

**Format**:
```
<type>: <subject>

<body>

<footer>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation
- `refactor`: Code refactoring
- `test`: Test changes
- `chore`: Build/tooling changes

**Example:**
```
feat: add GitHub integration

- Implement GitHubClient for API access
- Add repository and PR management methods
- Include authentication via personal access token

Closes #123
```

### Pull Request Process

1. **Create feature branch:**
   ```bash
   git checkout -b feature/your-feature
   ```

2. **Make changes and test:**
   ```bash
   poetry run pytest
   poetry run mypy src/
   poetry run ruff check src/
   ```

3. **Commit changes:**
   ```bash
   git add .
   git commit -m "feat: add your feature"
   ```

4. **Push to remote:**
   ```bash
   git push origin feature/your-feature
   ```

5. **Create pull request** with:
   - Clear description of changes
   - Link to related issues
   - Test results (all passing)
   - Screenshots (if UI changes)

6. **Address review feedback:**
   - Make requested changes
   - Push updates to same branch
   - PR updates automatically

---

## Debugging

### Enable Debug Logging

**Set log level in config:**
```yaml
logging:
  level: "DEBUG"
```

**Or via environment variable:**
```bash
export LOG_LEVEL=DEBUG
poetry run sentinel plan TICKET-123
```

### View Agent Conversations

Agent conversations are logged to `sentinel.log`:

```bash
# Follow logs in real-time
tail -f sentinel.log

# Search for specific agent
grep "PlanGeneratorAgent" sentinel.log

# View last 100 lines
tail -n 100 sentinel.log
```

### Debug Git Worktrees

**List all worktrees:**
```bash
git worktree list
```

**Check worktree directory:**
```bash
ls -la ~/sentinel-workspaces/SENTEST/SENTEST-123/
```

**Manual cleanup if stuck:**
```bash
# Remove worktree directory
rm -rf ~/sentinel-workspaces/SENTEST/SENTEST-123

# Remove from git
git worktree prune
```

### Debug Agent SDK Sessions

**Check active sessions:**
```bash
# Sessions are tracked in .claude/
ls -la .claude/

# View session state
cat .agents/session_tracker.json
```

**Clean up sessions:**
```bash
poetry run sentinel deep-clean
```

### Common Debugging Commands

**Check configuration:**
```python
from src.config_loader import get_config

config = get_config()
print(config.get_agent_config("plan_generator"))
```

**Test API clients:**
```python
from src.jira_client import JiraClient

client = JiraClient()
ticket = client.get_ticket("SENTEST-1")
print(ticket)
```

**Dry run mode:**
```bash
# Test without executing
poetry run sentinel plan TICKET-123 --dry-run
```

---

## Additional Resources

- **[API Reference](API.md)** - Complete API documentation
- **[Configuration Guide](CONFIGURATION.md)** - Configuration options
- **[Troubleshooting Guide](TROUBLESHOOTING.md)** - Common issues and solutions
- **[Credentials Setup](../CREDENTIALS.md)** - API credential configuration

---

## Getting Help

**Issues and bugs:**
- Check [TROUBLESHOOTING.md](TROUBLESHOOTING.md) first
- Search existing issues in the repository
- Create a new issue with:
  - Clear description of problem
  - Steps to reproduce
  - Expected vs actual behavior
  - Logs from `sentinel.log`

**Questions:**
- Review documentation in `docs/`
- Check code comments and docstrings
- Ask in team chat or discussions

---

## See Also

- **[API Reference](API.md)** - Complete API documentation
- **[Configuration Guide](CONFIGURATION.md)** - Configuration options
- **[Troubleshooting Guide](TROUBLESHOOTING.md)** - Common issues and solutions
- **[Credentials Setup](../CREDENTIALS.md)** - API credential configuration

---

**Happy coding! 🚀**
