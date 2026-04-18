# Agent Identity Logging in SDK Wrapper

## Problem

The `AgentSDKWrapper` logs model, cwd, and tool uses — but never identifies *which agent* is running. When multiple agents (plan_generator, drupal_developer, security_review, confidence_evaluator) execute in sequence, the logs all look identical. The only way to tell them apart is by inferring from the model name or surrounding context in the caller's logs.

## Goal

Add the `agent_name` to key log lines in `AgentSDKWrapper.execute_with_tools()` so operators can instantly see which agent is active at any point in the log.

## Files to Modify

1. `/workspace/sentinel/src/agent_sdk_wrapper.py` — the only file that needs changes

## Implementation

### Changes to `execute_with_tools()` log lines

The wrapper already stores `self.agent_name` (set in `__init__`). Thread it into the existing log messages.

**Line 175** — main execute log:
```python
# Before:
logger.info(f"Agent SDK execute: model={self.model}, cwd={cwd}, session={session_id}, system_prompt_len={len(system_prompt) if system_prompt else 0}")

# After:
logger.info(f"[{self.agent_name}] Agent SDK execute: model={self.model}, cwd={cwd}, session={session_id}, system_prompt_len={len(system_prompt) if system_prompt else 0}")
```

**Line 204** — model log:
```python
# Before:
logger.info(f"Using model: {self.model} (before SDK translation)")

# After:
logger.info(f"[{self.agent_name}] Using model: {self.model}")
```

**Line 228** — client opening:
```python
# Before:
logger.info(f"[SDK] Opening ClaudeSDKClient (model={self.model})...")

# After:
logger.info(f"[{self.agent_name}] Opening ClaudeSDKClient (model={self.model})...")
```

**Line 231** — query sent:
```python
# Before:
logger.info(f"[SDK] Client opened ({time.monotonic() - sdk_start:.1f}s), sending query ({len(prompt)} chars)...")

# After:
logger.info(f"[{self.agent_name}] Client opened ({time.monotonic() - sdk_start:.1f}s), sending query ({len(prompt)} chars)...")
```

**Line 234** — waiting for stream:
```python
# Before:
logger.info(f"[SDK] Query sent ({time.monotonic() - query_start:.1f}s), waiting for response stream...")

# After:
logger.info(f"[{self.agent_name}] Query sent ({time.monotonic() - query_start:.1f}s), waiting for response stream...")
```

**Line 249** — tool use:
```python
# Before:
logger.info(f"[SDK] Tool use: {block.name} ({time.monotonic() - stream_start:.1f}s into stream)")

# After:
logger.info(f"[{self.agent_name}] Tool use: {block.name} ({time.monotonic() - stream_start:.1f}s into stream)")
```

**Line 254** — stream complete:
```python
# Before:
logger.info(f"[SDK] Stream complete: {msg_count} messages, {len(tool_uses)} tool uses, {time.monotonic() - sdk_start:.1f}s total")

# After:
logger.info(f"[{self.agent_name}] Stream complete: {msg_count} messages, {len(tool_uses)} tool uses, {time.monotonic() - sdk_start:.1f}s total")
```

### Expected result

Before:
```
2026-04-18 06:11:10,533 - src.agent_sdk_wrapper - INFO - Using model: claude-4-5-sonnet (before SDK translation)
2026-04-18 06:11:10,533 - src.agent_sdk_wrapper - INFO - [SDK] Opening ClaudeSDKClient (model=claude-4-5-sonnet)...
2026-04-18 06:11:11,300 - src.agent_sdk_wrapper - INFO - [SDK] Client opened (0.8s), sending query (2935 chars)...
2026-04-18 06:11:15,799 - src.agent_sdk_wrapper - INFO - [SDK] Tool use: Bash (4.5s into stream)
```

After:
```
2026-04-18 06:11:10,533 - src.agent_sdk_wrapper - INFO - [drupal_developer] Using model: claude-4-5-sonnet
2026-04-18 06:11:10,533 - src.agent_sdk_wrapper - INFO - [drupal_developer] Opening ClaudeSDKClient (model=claude-4-5-sonnet)...
2026-04-18 06:11:11,300 - src.agent_sdk_wrapper - INFO - [drupal_developer] Client opened (0.8s), sending query (2935 chars)...
2026-04-18 06:11:15,799 - src.agent_sdk_wrapper - INFO - [drupal_developer] Tool use: Bash (4.5s into stream)
```

## Scope

This is a logging-only change. No behavior changes, no new dependencies, no test updates required. Existing tests that mock `AgentSDKWrapper` won't be affected since they mock `execute_with_tools` entirely.
