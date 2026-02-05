# Debugging LLM Output Issues

## Overview

When an LLM agent produces unexpected output (wrong format, missing sections, completely off-task), use this guide to diagnose and fix.

## Symptoms

| Symptom | Likely Cause |
|---------|--------------|
| LLM returns README instead of plan | Lost context, wrong task in prompt |
| Missing required sections | Prompt-validator mismatch |
| Empty response | Context overflow, API error |
| JSON parse failures | Prompt doesn't emphasize JSON format |
| Repeated/looping output | Temperature too high, unclear instructions |

---

## Diagnostic Steps

### 1. Verify System Prompt is Loaded

Check logs for:
```
Loaded system prompt for {agent_name} (XXXXX chars)
```

**If chars = 0 or "not found" warning:**
- Prompt file missing from `.agents/prompts/{agent_name}.md`
- Path resolution issue in `prompt_loader.py`

### 2. Check What Prompt Was Sent

Add temporary debug logging:
```python
# In base_agent.py _send_message_async()
logger.info(f"System prompt preview: {self.system_prompt[:1000]}")
logger.info(f"User prompt: {user_prompt[:500]}")
```

### 3. Examine LLM Response

The error message includes a response preview:
```
LLM Response Preview:
  # Todo API

  A minimal FastAPI todo application...
```

**Ask:** Does this look like what the prompt requested?

### 4. Check for Context Confusion

If using session resumption, previous context may confuse the task:
```python
# Force fresh session before new task type
self.session_id = None
self.messages.clear()
```

**Example:** The `analyze_ticket()` call uses one conversation context, then `generate_plan()` resumes that session. The LLM may "remember" it was doing analysis and produce wrong output.

### 5. Check User Prompt References Correct Section Names

The user prompt may reference old section names that don't match the system prompt template:
```python
# BAD - references old names
"Return plan with: Implementation Steps, Security Considerations"

# GOOD - matches current template
"Your output MUST include: Step-by-Step Tasks, Risks and Mitigations"
```

### 6. Check Working Directory (cwd) for Tool-Using Agents

If an agent has tools enabled (Read, Grep, Glob, Bash), it will explore the filesystem. Without a proper `cwd`, it may explore the wrong directory and get confused.

**Symptom:** Random/unrelated output like ASCII art or content from wrong project.

**Fix:** Pass `cwd` to `send_message()`:
```python
response = self.send_message(prompt, cwd=str(worktree_path))
```

The `cwd` flows through: `send_message()` -> `_send_message_async()` -> `agent_sdk.execute_with_tools(cwd=...)`

---

## Common Issues and Fixes

### Issue: LLM Generates Wrong Document Type

**Example:** Asked for implementation plan, got README.

**Causes:**
1. User prompt mentions "README" or related terms
2. Previous conversation context confused the task
3. System prompt is too vague about output format

**Fix:** Make the system prompt explicit:
```markdown
**CRITICAL OUTPUT RULE**: Return the plan content directly in your response.
Do NOT generate README, documentation, or any other document type.
Your output must be an implementation plan with these exact sections: ...
```

### Issue: Missing Required Sections

**Cause:** Prompt template uses different section names than validator expects.

**Fix:** Align validator with prompt (see `updating-agent-prompts.md`).

### Issue: LLM Returns JSON When Markdown Expected (or vice versa)

**Cause:** Mixed instructions in prompt or conversation history.

**Fix:** Be explicit about format:
```markdown
Return your response as **markdown** (not JSON).
```
or
```markdown
Return your response as a **JSON object** with this exact structure: ...
```

### Issue: Partial/Truncated Output

**Cause:**
- Max tokens limit reached
- Context window overflow
- Network interruption

**Fix:**
- Increase `max_tokens` in agent config
- Reduce system prompt length
- Break task into smaller chunks

---

## Validation Pattern

Always validate LLM output before using:

```python
required_sections = {
    "Section Name": ["variant1", "variant2"],
}

content_lower = response.lower()
missing = []
for section, variants in required_sections.items():
    if not any(v in content_lower for v in variants):
        missing.append(section)

if missing:
    raise ValueError(f"Missing sections: {missing}")
```

## Testing Prompts

Before deploying prompt changes:

1. **Unit test the validator** against sample good/bad outputs
2. **Manual test** with a real ticket
3. **Check edge cases**: empty description, very long description, special characters

---

## Architecture: Agent Writes File, Orchestrator Validates

For complex multi-phase prompts, it's better to:
1. **Let the agent write the file** using the Write tool
2. **Orchestrator reads and validates** the file content
3. **Iterate with feedback** if validation fails

This is more robust than expecting the agent to return perfect content in one shot.

### Implementation Pattern

```python
max_iterations = 3
for iteration in range(max_iterations):
    response = self.send_message(prompt, cwd=worktree_cwd)

    if not output_path.exists():
        # Agent didn't write file - provide feedback
        prompt = f"You MUST use Write tool to save to: {output_path}"
        continue

    plan_content = output_path.read_text()
    missing = validate_sections(plan_content)

    if not missing:
        break  # Success

    # Provide feedback for next iteration
    prompt = f"Plan missing: {missing}. Please update the file."
```

### Benefits
- Agent can use multiple internal turns to complete phases
- Write tool forces complete document output
- Orchestrator has full control over validation and iteration

---
**Created**: 2026-02-04
**Source**: Debugging session where LLM returned README instead of implementation plan
