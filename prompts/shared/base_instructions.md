# Shared Base Instructions for All Sentinel Agents

## General Behavior

### Communication Style
- Be concise and technical
- Avoid marketing language or excessive enthusiasm
- Focus on facts and actionable information
- Use professional terminology

### Error Handling
- Always validate inputs before processing
- Provide clear error messages with context
- Suggest fixes when errors occur
- Never fail silently

### File Operations
- Always verify file paths exist before reading
- Use absolute paths when possible
- Handle file encoding properly (UTF-8)
- Clean up temporary files

### Git Operations
- Never commit sensitive information
- Use conventional commit messages
- Keep commits atomic and focused
- Always pull before pushing

## Integration with Beads

All agents must use Beads for task coordination:

```bash
# View available tasks
bd ready

# Start work on a task
bd update <task-id> --status in_progress

# Complete a task
bd close <task-id>

# Create new task
bd add "Task description"
```

### Task Update Protocol
1. Query Beads for current task status before starting work
2. Update task to "in_progress" when beginning
3. Add subtasks as you discover them
4. Mark complete only when fully done
5. Never leave tasks in limbo state

## Working with Plans

### Reading Plans
Plans are located at `.agents/plans/{TICKET-ID}.md`

Parse plans for:
- Requirements and acceptance criteria
- Technical approach and architecture decisions
- Implementation steps (ordered list)
- Testing strategy
- Security considerations

### Referencing Plans
When implementing or reviewing:
```markdown
✓ Implemented step 3: "Create user authentication endpoint"
  See plan: .agents/plans/ACME-123.md#implementation-steps
```

## Memory Management

### Session Memory
Each agent maintains memory at `.agents/memory/{agent-name}-{ticket-id}.json`

Structure:
```json
{
  "ticket_id": "ACME-123",
  "agent": "python_developer",
  "session_start": "2026-01-23T10:00:00Z",
  "context": {
    "files_modified": ["src/api/users.py", "tests/test_users.py"],
    "decisions_made": ["Using JWT for authentication"],
    "blockers": []
  },
  "iterations": 1,
  "status": "in_progress"
}
```

### Persisting Context
Update memory after each significant action:
- File modifications
- Architectural decisions
- Blockers encountered
- Iteration completions

## Quality Standards

### Code Quality
- Use type hints on all Python functions
- Follow PEP 8 style guidelines
- Keep functions under 50 lines
- Maximum cyclomatic complexity: 10
- Avoid premature optimization

### Documentation
- Docstrings for public APIs only
- Inline comments only for non-obvious logic
- Keep README files up to date
- Document configuration options

### Testing
- Minimum 80% code coverage for new code
- Test happy path and edge cases
- Use descriptive test names
- One assertion per test (when possible)

## Escalation Protocol

### When to Escalate to Humans
1. **Blockers**: Can't proceed after 3 attempts
2. **Ambiguity**: Requirements are unclear or contradictory
3. **Complexity**: Task exceeds agent capabilities
4. **Time**: Iteration limit reached (5 iterations)
5. **Security**: Critical vulnerability can't be fixed safely

### How to Escalate
```markdown
## 🚨 HUMAN ESCALATION REQUIRED

**Ticket**: ACME-123
**Agent**: Python Developer
**Iteration**: 5/5
**Reason**: Cannot resolve security vulnerability without architectural change

**Context**:
The current authentication system stores passwords in plaintext.
Security Agent blocked merge (correctly).
Fixing this requires migrating to bcrypt, which impacts:
- Database schema
- User registration flow
- Password reset flow
- Session management

**Recommendation**:
1. Create new ticket for auth system refactor
2. Deprioritize ACME-123 until auth is secure
3. Human review of overall security architecture

**Artifacts**:
- Plan: .agents/plans/ACME-123.md
- Code: feature/ACME-123 branch
- Security report: .agents/memory/security-ACME-123.json
```

## Configuration Access

All agents can read configuration from `config/config.yaml`:

```python
from src.config_loader import load_config

config = load_config()
project_config = config['projects']['ACME']
git_url = project_config['git_url']
```

## Logging

Use structured logging for all operations:

```python
import logging

logger = logging.getLogger(__name__)

# INFO: Normal operations
logger.info("Starting plan generation for ACME-123")

# WARNING: Recoverable issues
logger.warning("Jira ticket missing acceptance criteria, proceeding anyway")

# ERROR: Failures that stop progress
logger.error("Failed to create worktree", exc_info=True)

# DEBUG: Detailed troubleshooting info
logger.debug(f"Parsed plan: {plan_data}")
```

---

**Version**: 1.0
**Last Updated**: 2026-01-23
