# Claude Agent SDK Error Reference

## Overview

The Claude Agent SDK spawns a bundled Claude CLI as a subprocess. Errors from this CLI appear in stderr and are logged by `AgentSDKWrapper`.

## Error Categories

### 1. Telemetry Failures (Ignorable)

**Pattern:**
```
1P event logging: X events failed to export
Failed to export X events
```

**Cause:** The bundled CLI tries to send telemetry to Anthropic but fails due to:
- Network restrictions (firewall, proxy)
- Container environment limitations
- LLM Provider proxy not forwarding telemetry endpoints

**Impact:** None - telemetry failures don't affect functionality.

**Fix:** Disable telemetry via environment variable in `AgentSDKWrapper`:
```python
subprocess_env = {
    # ... auth vars ...
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
}
```

This bundles: `DISABLE_AUTOUPDATER`, `DISABLE_BUG_COMMAND`, `DISABLE_ERROR_REPORTING`, `DISABLE_TELEMETRY`.

**Status:** Fixed in `src/agent_sdk_wrapper.py` as of 2026-02-04.

---

### 2. Missing Skills Directory

**Pattern:**
```
ENOENT: no such file or directory, scandir '/etc/claude-code/.claude/skills'
```

**Cause:** The bundled CLI expects a system-wide skills directory that doesn't exist in the container.

**Impact:** Low - skills are optional and not used by Agent SDK.

**Fix (Dockerfile):**
```dockerfile
RUN mkdir -p /etc/claude-code/.claude/skills
```

**Fix (Runtime):**
```bash
sudo mkdir -p /etc/claude-code/.claude/skills
```

---

### 3. HTTP Client Errors

**Pattern:**
```
Error at <anonymous> (/$bunfs/root/claude:29:8379)
at <anonymous> (/$bunfs/root/claude:47:10263)
at emitError (node:events:43:23)
```

**Cause:** Network connectivity issues:
- API endpoint unreachable
- LLM Provider proxy rejecting requests
- Intermittent network failures
- SSL/TLS handshake failures

**Impact:** May cause request failures, but SDK typically retries.

**Debugging:**
1. Check LLM Provider proxy is running and accessible
2. Verify `ANTHROPIC_BASE_URL` is correct
3. Test connectivity: `curl -v $ANTHROPIC_BASE_URL/v1/models`
4. Check for rate limiting

---

### 4. Empty Response from Agent

**Pattern:**
```
Agent response received with 0 tool uses
```

**Cause:**
- System prompt too long (context overflow)
- Malformed prompt
- API error not properly surfaced

**Debugging:**
1. Check system prompt length in logs
2. Verify prompt content is valid
3. Check for truncation warnings

---

## Environment Variables

The Agent SDK requires these env vars for the subprocess:
```python
subprocess_env = {
    "ANTHROPIC_AUTH_TOKEN": os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
    "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL", ""),
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    # Model name overrides for LLM Provider compatibility
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-4-5-haiku",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-4-5-sonnet",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-5",
    "ANTHROPIC_SMALL_FAST_MODEL": "claude-4-5-haiku",
}
```

These are passed via `ClaudeAgentOptions(env=subprocess_env)`.

### Model Name Format Issue

The bundled Claude CLI uses Anthropic's standard model names (e.g., `claude-haiku-4-5-20251001`), but LLM Provider requires a different format (`claude-4-5-haiku`). Without the `ANTHROPIC_DEFAULT_*` overrides, you'll see:

```
Error in non-streaming fallback: 400 {"error":{"message":"Invalid model name passed in model=claude-haiku-4-5-20251001"}}
```

**Status:** Fixed in `src/agent_sdk_wrapper.py` as of 2026-02-04.

## Stderr Logging

All CLI stderr output is captured via callback in `AgentSDKWrapper.execute_with_tools()`:
- Written to `/tmp/agent_sdk_stderr.log` for debugging
- Logged at appropriate level based on content

---
**Created**: 2026-02-04
**Source**: Debugging session analyzing CLI stderr output during plan generation
