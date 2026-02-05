# Sentinel API Documentation

Complete API reference for programmatic access to Sentinel agents, clients, and configuration.

## Overview

Sentinel provides a Python API for:
- **Agents**: BaseAgent, PlanGeneratorAgent, PythonDeveloperAgent, SecurityReviewerAgent
- **Clients**: JiraClient, GitLabClient, WorktreeManager, BeadsManager
- **Configuration**: ConfigLoader for accessing configuration
- **Utilities**: Command execution, prompt loading, ADF parsing

This documentation covers public interfaces and usage patterns for extending or integrating with Sentinel.

---

## Table of Contents

- [Agents API](#agents-api)
  - [BaseAgent](#baseagent)
  - [PlanGeneratorAgent](#plangeneratoragent)
  - [PythonDeveloperAgent](#pythondeveloperagent)
  - [SecurityReviewerAgent](#securityrevieweragent)
- [Client APIs](#client-apis)
  - [JiraClient](#jiraclient)
  - [GitLabClient](#gitlabclient)
  - [WorktreeManager](#worktreemanager)
  - [BeadsManager](#beadsmanager)
- [Configuration API](#configuration-api)
  - [ConfigLoader](#configloader)
- [Utility APIs](#utility-apis)

---

## Agents API

All agents inherit from `BaseAgent` and follow a common pattern.

### BaseAgent

**Location**: `src/agents/base_agent.py`

Base class for all Sentinel agents providing LLM interaction, session management, and command execution.

#### Class Definition

```python
class BaseAgent(ABC):
    """Base class for all Sentinel agents."""

    def __init__(
        self,
        agent_name: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> None:
        """Initialize base agent.

        Args:
            agent_name: Agent name for prompt loading (e.g., "plan_generator")
            model: LLM model to use (defaults to config)
            temperature: Sampling temperature (defaults to config)
        """
```

#### Methods

##### `send_message(content: str, role: str = "user") -> str`

Send a message to the agent and get a response.

**Parameters:**
- `content` (str): Message content to send
- `role` (str, optional): Message role ("user" or "assistant"). Default: "user"

**Returns:**
- `str`: Agent's response

**Example:**
```python
from src.agents.plan_generator import PlanGeneratorAgent

agent = PlanGeneratorAgent()
response = agent.send_message("What files are in src/agents/?")
print(response)
```

**Notes:**
- Uses Claude Agent SDK internally for tool use
- Maintains conversation history across calls
- Session persists for multi-turn conversations

##### `clear_history() -> None`

Clear the conversation history and reset session.

**Example:**
```python
agent.clear_history()  # Start fresh conversation
```

##### `execute_command(command_name: str, params: Dict[str, Any]) -> Dict[str, Any]`

Execute a custom command defined in YAML.

**Parameters:**
- `command_name` (str): Name of the command
- `params` (Dict): Command parameters

**Returns:**
- `Dict`: Command execution result

##### `run(**kwargs: Any) -> Any`

Execute the agent's main workflow (abstract method - must be implemented by subclasses).

**Parameters:**
- `**kwargs`: Agent-specific parameters

**Returns:**
- Agent-specific result dictionary

---

### PlanGeneratorAgent

**Location**: `src/agents/plan_generator.py`

Generates implementation plans for Jira tickets.

#### Class Definition

```python
class PlanGeneratorAgent(PlanningAgent):
    """Agent for generating implementation plans."""

    def __init__(self) -> None:
        super().__init__(
            agent_name="plan_generator",
            model="claude-4-5-opus",  # Uses Opus for planning
            temperature=0.3
        )
```

#### Methods

##### `run(ticket_id: str, worktree_path: Path) -> Dict[str, Any]`

Generate an implementation plan for a Jira ticket.

**Parameters:**
- `ticket_id` (str): Jira ticket ID (e.g., "PROJ-123")
- `worktree_path` (Path): Path to git worktree

**Returns:**
```python
{
    "plan_path": Path,        # Path to generated plan file
    "mr_url": str,            # GitLab merge request URL
    "mr_created": bool,       # True if new MR, False if existing
    "plan_updated": bool      # True if plan was modified
}
```

**Example:**
```python
from pathlib import Path
from src.agents.plan_generator import PlanGeneratorAgent

agent = PlanGeneratorAgent()
result = agent.run(
    ticket_id="PROJ-123",
    worktree_path=Path("/path/to/worktree")
)

print(f"Plan created: {result['plan_path']}")
print(f"MR URL: {result['mr_url']}")
```

##### `run_revision(ticket_id: str, worktree_path: Path) -> Dict[str, Any]`

Revise an existing plan based on MR feedback.

**Parameters:**
- `ticket_id` (str): Jira ticket ID
- `worktree_path` (Path): Path to git worktree

**Returns:**
```python
{
    "plan_path": Path,
    "mr_url": str,
    "feedback_count": int,      # Number of unresolved discussions
    "revision_type": str,       # "incremental" or "major_revision"
    "plan_updated": bool,
    "responses_posted": int     # Number of responses posted to MR
}
```

**Example:**
```python
result = agent.run_revision(
    ticket_id="PROJ-123",
    worktree_path=Path("/path/to/worktree")
)

if result['feedback_count'] > 0:
    print(f"Revised plan based on {result['feedback_count']} discussion(s)")
```

---

### PythonDeveloperAgent

**Location**: `src/agents/python_developer.py`

Implements code based on plans.

#### Class Definition

```python
class PythonDeveloperAgent(ImplementationAgent):
    """Agent for implementing Python code."""

    def __init__(self) -> None:
        super().__init__(
            agent_name="python_developer",
            model="claude-4-5-sonnet",
            temperature=0.2
        )
```

#### Methods

##### `run(plan_path: Path, worktree_path: Path) -> Dict[str, Any]`

Implement code based on a plan.

**Parameters:**
- `plan_path` (Path): Path to implementation plan
- `worktree_path` (Path): Path to git worktree

**Returns:**
```python
{
    "status": str,           # "success" or "error"
    "files_modified": List[str],
    "tests_written": bool,
    "commit_sha": str       # Git commit SHA
}
```

**Example:**
```python
from pathlib import Path
from src.agents.python_developer import PythonDeveloperAgent

agent = PythonDeveloperAgent()
result = agent.run(
    plan_path=Path(".agents/plans/plan.md"),
    worktree_path=Path("/path/to/worktree")
)

print(f"Modified {len(result['files_modified'])} files")
print(f"Commit: {result['commit_sha']}")
```

---

### SecurityReviewerAgent

**Location**: `src/agents/security_reviewer.py`

Reviews code for security vulnerabilities.

#### Class Definition

```python
class SecurityReviewerAgent(ReviewAgent):
    """Agent for security review with veto power."""

    def __init__(self) -> None:
        super().__init__(
            agent_name="security_review",
            model="claude-4-5-sonnet",
            temperature=0.1,
            veto_power=True
        )
```

#### Methods

##### `run(worktree_path: Path, implementation_summary: str) -> Dict[str, Any]`

Review code for security issues.

**Parameters:**
- `worktree_path` (Path): Path to git worktree
- `implementation_summary` (str): Summary of what was implemented

**Returns:**
```python
{
    "approved": bool,           # True if approved, False if vetoed
    "issues": List[Dict],       # List of security issues found
    "severity": str,            # "none", "low", "medium", "high", "critical"
    "feedback": str             # Detailed feedback
}
```

**Example:**
```python
from pathlib import Path
from src.agents.security_reviewer import SecurityReviewerAgent

agent = SecurityReviewerAgent()
result = agent.run(
    worktree_path=Path("/path/to/worktree"),
    implementation_summary="Implemented user authentication"
)

if result['approved']:
    print("Security review passed!")
else:
    print(f"Security issues found: {len(result['issues'])}")
    for issue in result['issues']:
        print(f"- {issue['description']} (severity: {issue['severity']})")
```

---

## Client APIs

### JiraClient

**Location**: `src/jira_client.py`

Interface for Jira API operations.

#### Class Definition

```python
class JiraClient:
    """Client for Jira API interactions."""

    def __init__(self) -> None:
        """Initialize Jira client from configuration."""
```

#### Methods

##### `get_ticket(ticket_id: str) -> Dict[str, Any]`

Fetch a Jira ticket by ID.

**Parameters:**
- `ticket_id` (str): Jira ticket ID (e.g., "PROJ-123")

**Returns:**
```python
{
    "key": str,             # Ticket key
    "summary": str,         # Ticket summary
    "description": str,     # Parsed description (ADF → markdown)
    "status": str,          # Current status
    "assignee": str,        # Assignee name or None
    "created": str,         # ISO timestamp
    "updated": str          # ISO timestamp
}
```

**Example:**
```python
from src.jira_client import JiraClient

client = JiraClient()
ticket = client.get_ticket("PROJ-123")

print(f"Summary: {ticket['summary']}")
print(f"Status: {ticket['status']}")
```

##### `create_comment(ticket_id: str, comment: str) -> Dict[str, Any]`

Add a comment to a Jira ticket.

**Parameters:**
- `ticket_id` (str): Jira ticket ID
- `comment` (str): Comment text (supports markdown)

**Returns:**
```python
{
    "id": str,           # Comment ID
    "created": str       # ISO timestamp
}
```

**Example:**
```python
client.create_comment(
    "PROJ-123",
    "Implementation plan generated. See MR: https://gitlab.com/..."
)
```

---

### GitLabClient

**Location**: `src/gitlab_client.py`

Interface for GitLab API operations.

#### Class Definition

```python
class GitLabClient:
    """Client for GitLab API interactions."""

    def __init__(self) -> None:
        """Initialize GitLab client from configuration."""
```

#### Methods

##### `create_merge_request(project_path: str, source_branch: str, target_branch: str, title: str, description: str, draft: bool = True) -> Dict[str, Any]`

Create a merge request.

**Parameters:**
- `project_path` (str): GitLab project path (e.g., "org/repo")
- `source_branch` (str): Source branch name
- `target_branch` (str): Target branch name
- `title` (str): MR title
- `description` (str): MR description (markdown)
- `draft` (bool, optional): Create as draft MR. Default: True

**Returns:**
```python
{
    "iid": int,          # MR internal ID
    "web_url": str,      # MR URL
    "created": bool      # True if created, False if already exists
}
```

**Example:**
```python
from src.gitlab_client import GitLabClient

client = GitLabClient()
mr = client.create_merge_request(
    project_path="acme/backend",
    source_branch="feature/PROJ-123",
    target_branch="main",
    title="[PROJ-123] Implement user authentication",
    description="# Implementation Plan\n\n...",
    draft=True
)

print(f"MR created: {mr['web_url']}")
```

##### `post_discussion_note(project_path: str, mr_iid: int, discussion_id: str, note: str) -> Dict[str, Any]`

Reply to an MR discussion.

**Parameters:**
- `project_path` (str): GitLab project path
- `mr_iid` (int): MR internal ID
- `discussion_id` (str): Discussion ID
- `note` (str): Reply text (markdown)

**Returns:**
```python
{
    "id": str,              # Note ID
    "created_at": str       # ISO timestamp
}
```

---

### WorktreeManager

**Location**: `src/worktree_manager.py`

Manages git worktrees for ticket isolation.

#### Class Definition

```python
class WorktreeManager:
    """Manager for git worktree operations."""

    def __init__(self) -> None:
        """Initialize worktree manager from configuration."""
```

#### Methods

##### `create_worktree(ticket_id: str, project_key: str, branch_name: Optional[str] = None) -> Path`

Create a git worktree for a ticket.

**Parameters:**
- `ticket_id` (str): Jira ticket ID
- `project_key` (str): Project key from config
- `branch_name` (str, optional): Custom branch name. Default: "feature/{ticket_id}"

**Returns:**
- `Path`: Path to created worktree

**Example:**
```python
from src.worktree_manager import WorktreeManager

manager = WorktreeManager()
worktree_path = manager.create_worktree(
    ticket_id="PROJ-123",
    project_key="ACME"
)

print(f"Worktree created: {worktree_path}")
# Output: ~/sentinel-workspaces/ACME/PROJ-123
```

##### `cleanup_worktree(ticket_id: str, project_key: str) -> bool`

Remove a git worktree and its branch.

**Parameters:**
- `ticket_id` (str): Jira ticket ID
- `project_key` (str): Project key

**Returns:**
- `bool`: True if successful

**Example:**
```python
manager.cleanup_worktree("PROJ-123", "ACME")
```

##### `list_worktrees(project_key: str) -> List[Dict[str, str]]`

List all active worktrees for a project.

**Parameters:**
- `project_key` (str): Project key

**Returns:**
```python
[
    {
        "path": str,        # Worktree path
        "branch": str,      # Branch name
        "commit": str       # Current commit SHA
    },
    ...
]
```

---

### BeadsManager

**Location**: `src/beads_manager.py`

Interface for Beads task tracking CLI.

#### Class Definition

```python
class BeadsManager:
    """Manager for Beads task tracking operations."""

    def __init__(self, working_dir: Optional[Path] = None) -> None:
        """Initialize Beads manager.

        Args:
            working_dir: Working directory for bd commands (default: current dir)
        """
```

#### Methods

##### `create_task(title: str, task_type: str = "task", priority: int = 2, description: Optional[str] = None) -> Dict[str, Any]`

Create a new task in Beads.

**Parameters:**
- `title` (str): Task title
- `task_type` (str, optional): "task", "bug", or "feature". Default: "task"
- `priority` (int, optional): Priority 0-4 (0=critical, 4=backlog). Default: 2
- `description` (str, optional): Task description

**Returns:**
```python
{
    "id": str,              # Task ID (e.g., "sentinel-abc")
    "title": str,
    "status": str,          # "open"
    "created": bool         # True
}
```

**Example:**
```python
from src.beads_manager import BeadsManager

beads = BeadsManager()
task = beads.create_task(
    title="Implement authentication",
    task_type="feature",
    priority=2,
    description="Add JWT-based authentication"
)

print(f"Created task: {task['id']}")
```

##### `update_task(task_id: str, status: Optional[str] = None, assignee: Optional[str] = None) -> bool`

Update a task's status or assignee.

**Parameters:**
- `task_id` (str): Task ID
- `status` (str, optional): New status ("open", "in_progress", "blocked", "closed")
- `assignee` (str, optional): Assignee name

**Returns:**
- `bool`: True if successful

**Example:**
```python
beads.update_task("sentinel-abc", status="in_progress")
```

##### `close_task(task_id: str, reason: Optional[str] = None) -> bool`

Close a task.

**Parameters:**
- `task_id` (str): Task ID
- `reason` (str, optional): Closure reason

**Returns:**
- `bool`: True if successful

---

## Configuration API

### ConfigLoader

**Location**: `src/config_loader.py`

Loads and provides access to configuration.

#### Class Definition

```python
class ConfigLoader:
    """Loads configuration from YAML and environment variables."""

    def __init__(self, config_path: Path) -> None:
        """Initialize configuration loader.

        Args:
            config_path: Path to config.yaml
        """
```

#### Methods

##### `get_agent_config(agent_name: str) -> Dict[str, Any]`

Get configuration for a specific agent.

**Parameters:**
- `agent_name` (str): Agent name (e.g., "plan_generator")

**Returns:**
```python
{
    "model": str,               # LLM model name
    "temperature": float,       # Sampling temperature
    "specializations": List[str] # Optional: agent specializations
}
```

**Example:**
```python
from src.config_loader import get_config

config = get_config()
agent_config = config.get_agent_config("plan_generator")

print(f"Model: {agent_config['model']}")
print(f"Temperature: {agent_config['temperature']}")
```

##### `get_jira_config() -> Dict[str, Any]`

Get Jira configuration with resolved environment variables.

**Returns:**
```python
{
    "base_url": str,        # Jira base URL
    "api_token": str,       # API token from env
    "email": str            # Email from env
}
```

##### `get_gitlab_config() -> Dict[str, Any]`

Get GitLab configuration with resolved environment variables.

**Returns:**
```python
{
    "base_url": str,        # GitLab base URL
    "api_token": str        # API token from env
}
```

##### `get_project_config(project_key: str) -> Dict[str, Any]`

Get configuration for a specific project.

**Parameters:**
- `project_key` (str): Project key (e.g., "ACME")

**Returns:**
```python
{
    "git_url": str,             # Git repository URL
    "default_branch": str,      # Default branch name
    "jira_project_key": str     # Jira project key
}
```

---

## Utility APIs

### Command Executor

**Location**: `src/command_executor.py`

Executes commands defined in YAML files.

```python
from src.command_executor import execute_command

result = execute_command("analyze_code", {"file_path": "src/main.py"})
```

### Prompt Loader

**Location**: `src/prompt_loader.py`

Loads agent prompts from markdown files.

```python
from src.prompt_loader import load_agent_prompt

prompt = load_agent_prompt("plan_generator")
print(prompt)  # System prompt for plan generator
```

### ADF Parser

**Location**: `src/utils/adf_parser.py`

Parses Atlassian Document Format to plain text.

```python
from src.utils.adf_parser import parse_adf_to_text

adf_json = {...}  # Jira description in ADF format
markdown_text = parse_adf_to_text(adf_json)
```

---

## Error Handling

All Sentinel APIs raise standard Python exceptions:

- `ValueError`: Invalid parameters
- `FileNotFoundError`: Missing configuration or files
- `RuntimeError`: Operation failed (e.g., git command failed)
- HTTP errors from requests library for API calls

**Example error handling:**
```python
from src.jira_client import JiraClient

client = JiraClient()

try:
    ticket = client.get_ticket("PROJ-123")
except ValueError as e:
    print(f"Invalid ticket ID: {e}")
except RuntimeError as e:
    print(f"Failed to fetch ticket: {e}")
```

---

## See Also

- **[Configuration Guide](CONFIGURATION.md)** - Configuration options and environment variables
- **[Development Guide](DEVELOPMENT.md)** - Extending Sentinel with custom agents
- **[Troubleshooting Guide](TROUBLESHOOTING.md)** - Common API issues and solutions
