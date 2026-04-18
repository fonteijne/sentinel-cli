# Task Context in SDK Stream Logs

## Problem

The SDK wrapper logs show which agent is running (after the identity logging change), but not *what* it's working on. When the developer agent opens 4+ streams in one iteration, they're indistinguishable without cross-referencing `base_developer` logs.

## Goal

Pass task context through to `execute_with_tools()` so each stream can be identified by both agent name and task. Designed with a future dashboard in mind — structured data, not just log strings.

## Approach

Add an optional `context` dict to `execute_with_tools()` that carries metadata (task name, iteration number, etc.) for logging and future dashboard use.

## Files to Modify

1. `/workspace/sentinel/src/agent_sdk_wrapper.py` — accept and log `context` param
2. `/workspace/sentinel/src/agents/base_developer.py` — pass task description in `implement_feature()`
3. `/workspace/sentinel/src/agents/security_reviewer.py` — pass review context

## Implementation

### Step 1: Add `context` parameter to `execute_with_tools()`

```python
async def execute_with_tools(
    self,
    prompt: str,
    session_id: str | None = None,
    system_prompt: str | None = None,
    cwd: str | None = None,
    max_turns: int | None = None,
    context: Dict[str, str] | None = None,  # NEW
) -> Dict[str, Any]:
```

Use it in the main log line:
```python
ctx_label = context.get("task", "") if context else ""
label = f"[{self.agent_name}]" + (f" ({ctx_label})" if ctx_label else "")
# Then use `label` in all log lines instead of f"[{self.agent_name}]"
```

Store `context` in the return dict so a future dashboard can consume it:
```python
return {
    "content": ...,
    "tool_uses": ...,
    "session_id": ...,
    "context": context or {},
}
```

### Step 2: Pass task from `implement_feature()`

In `base_developer.py`, wherever `execute_with_tools()` is called during task implementation, add:
```python
context={"task": task[:120]}
```

### Step 3: Pass context from security reviewer

Similar pattern — pass `{"task": "security_review", "ticket": ticket_id}`.

## Expected Log Output

```
[drupal_developer] (Create pricing_country paragraph type) Opening ClaudeSDKClient...
[drupal_developer] (Create pricing_country paragraph type) Tool use: Bash (4.5s)
[drupal_developer] (Create pricing_block_type paragraph type) Opening ClaudeSDKClient...
[security_review] (security_review) Opening ClaudeSDKClient...
```

## Dashboard Considerations

The `context` dict in the return value is intentionally generic. A future dashboard can:
- Display task name per stream
- Show iteration number
- Track timing per task (already available via stream start/end logs)
- Group streams by agent + task

No schema is enforced — callers pass whatever keys make sense for their agent type.
