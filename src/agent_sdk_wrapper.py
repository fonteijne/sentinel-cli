"""Wrapper for Claude Agent SDK with unified LLM provider support."""

import asyncio
import json as _json
import os
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions, ClaudeSDKError
from claude_agent_sdk.types import AssistantMessage, SystemPromptPreset, TextBlock, ToolUseBlock

from typing import TYPE_CHECKING

from src.config_loader import ConfigLoader
from src.guardrails import GuardrailEngine
from src.session_tracker import SessionTracker

if TYPE_CHECKING:
    from src.core.events import EventBus


class AgentTimeoutError(Exception):
    """Raised when an agent exceeds its configured execution timeout."""

logger = logging.getLogger(__name__)


_RATE_LIMIT_HINT = re.compile(r"\b(?:429|529|rate[ _-]?limit(?:ed)?)\b", re.IGNORECASE)
_RETRY_AFTER_HINT = re.compile(
    r"retry[ _-]?after[^0-9]{0,8}([0-9]+(?:\.[0-9]+)?)", re.IGNORECASE
)


def _classify_rate_limit(exc: BaseException) -> tuple[bool, float | None]:
    """Return (is_rate_limit, retry_after_s) for a SDK/CLI exception.

    The claude-agent-sdk does not expose a dedicated rate-limit exception
    class (all CLI failures bubble as ``ClaudeSDKError``/``ProcessError``).
    We sniff the message and, if present, the ``stderr`` attribute for the
    429/529 signatures plus any ``retry-after`` hint.
    """
    haystacks: list[str] = [str(exc)]
    stderr = getattr(exc, "stderr", None)
    if isinstance(stderr, str):
        haystacks.append(stderr)

    blob = "\n".join(haystacks)
    if not _RATE_LIMIT_HINT.search(blob):
        return False, None

    retry_after: float | None = None
    m = _RETRY_AFTER_HINT.search(blob)
    if m:
        try:
            retry_after = float(m.group(1))
        except ValueError:
            retry_after = None
    return True, retry_after


def _usage_field(usage: Any, name: str) -> int:
    """Pull a numeric field from an SDK usage object (attr or dict)."""
    if usage is None:
        return 0
    val = getattr(usage, name, None)
    if val is None and isinstance(usage, dict):
        val = usage.get(name, 0)
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def entry_dict(
    *,
    agent: str,
    event: str,
    data: Dict[str, Any],
    cwd: str | None = None,
) -> Dict[str, Any]:
    """Single shape used by both the JSONL diagnostic writer and the EventBus.

    Having one function back both paths prevents the two telemetry sinks from
    drifting; tests in ``tests/test_agent_sdk_wrapper.py`` assert parity.
    """
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "agent": agent,
        "event": event,
        "cwd": cwd,
        **data,
    }


class AgentSDKWrapper:
    """Wrapper for Claude Agent SDK with unified LLM provider support."""

    def __init__(self, agent_name: str, config: ConfigLoader) -> None:
        """Initialize Agent SDK wrapper.

        Args:
            agent_name: Name of the agent (e.g., "plan_generator")
            config: ConfigLoader instance
        """
        self.agent_name = agent_name
        self.config = config
        self.session_tracker = SessionTracker()
        self.guardrails = GuardrailEngine(config)
        self.project: str | None = None  # Project key for session tracking
        self.llm_mode: str = "subscription"  # Default mode

        # Command Center event plumbing — set by BaseAgent after construction
        # when an execution context is active. Kept optional so non-orchestrated
        # callers (legacy tests, ad-hoc scripts) keep working unchanged.
        self.event_bus: "EventBus | None" = None
        self.execution_id: str | None = None

        # Initialize model and tools with defaults (will be updated in set_project)
        agent_config = self.config.get_agent_config(self.agent_name)
        self.model = agent_config.get("model", "claude-4-5-haiku")
        self.temperature = agent_config.get("temperature", 0.2)
        self.allowed_tools = self._get_allowed_tools(self.agent_name)

    def set_project(self, project: str) -> None:
        """Set the project key for session tracking.

        Args:
            project: Project key (e.g., "ACME")
        """
        self.project = project

        # Get LLM config with auto-detected mode
        llm_config = self.config.get_llm_config()
        self.llm_mode = llm_config["mode"]

        # Set environment variables based on mode
        if self.llm_mode == "custom_proxy":
            # Custom proxy - use AUTH_TOKEN for proxy compatibility
            # Clear API_KEY so Claude CLI doesn't pick it up instead of AUTH_TOKEN
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ["ANTHROPIC_AUTH_TOKEN"] = llm_config["api_key"]
            os.environ["ANTHROPIC_BASE_URL"] = llm_config["base_url"]
            logger.info(f"Using custom proxy at {llm_config['base_url']}")
        elif self.llm_mode == "direct_api":
            # Direct Anthropic API
            os.environ["ANTHROPIC_API_KEY"] = llm_config["api_key"]
            # Clear any proxy settings
            os.environ.pop("ANTHROPIC_BASE_URL", None)
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            logger.info("Using direct Anthropic API")
        else:  # subscription
            # Claude Code subscription - clear API keys, let SDK use cached login
            os.environ.pop("ANTHROPIC_API_KEY", None)
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            os.environ.pop("ANTHROPIC_BASE_URL", None)
            logger.info("Using Claude Code subscription authentication")

        # Get agent-specific config
        agent_config = self.config.get_agent_config(self.agent_name)
        self.model = agent_config.get("model", "claude-4-5-haiku")
        self.temperature = agent_config.get("temperature", 0.2)

        # Configure allowed tools based on agent type
        self.allowed_tools = self._get_allowed_tools(self.agent_name)

        logger.info(f"Agent SDK initialized for {self.agent_name} with model: {self.model} (mode: {self.llm_mode})")
        logger.debug(f"Tools enabled: {', '.join(self.allowed_tools)}")

    def _get_allowed_tools(self, agent_name: str) -> list[str]:
        """Determine allowed tools based on agent type.

        Args:
            agent_name: Name of the agent

        Returns:
            List of allowed tool names
        """
        # Check for agent-specific tool configuration in config
        agent_config = self.config.get_agent_config(agent_name)
        if "allowed_tools" in agent_config:
            allowed: list[str] = agent_config["allowed_tools"]
            return allowed

        # Planning agents need exploration + Write for outputting plans
        if "plan" in agent_name.lower():
            planning_tools = self.config.get("agent_sdk.planning_agent_tools")
            if planning_tools:
                return list(planning_tools)
            return ["Read", "Write", "Grep", "Glob", "Bash(git *)"]

        # Review agents need read-only access
        if "review" in agent_name.lower():
            review_tools = self.config.get("agent_sdk.review_agent_tools")
            if review_tools:
                return list(review_tools)
            return ["Read", "Grep", "Glob", "Bash(git *)"]

        # Implementation agents need full write access
        if "developer" in agent_name.lower() or "implementation" in agent_name.lower():
            impl_tools = self.config.get("agent_sdk.implementation_agent_tools")
            if impl_tools:
                return list(impl_tools)
            return ["Read", "Write", "Edit", "Grep", "Glob", "Bash"]

        # Default: read-only
        default_tools = self.config.get("agent_sdk.default_tools")
        if default_tools:
            return list(default_tools)
        return ["Read", "Grep", "Glob"]

    def _write_diagnostic(
        self,
        event: str,
        data: Dict[str, Any],
        cwd: str | None = None,
    ) -> None:
        """Write diagnostic event to shared bind mount for external inspection.

        Writes JSONL to /app/logs/agent_diagnostics.jsonl (sentinel-dev)
        which maps to /workspace/sentinel/logs/agent_diagnostics.jsonl (sandbox).
        """
        entry = entry_dict(agent=self.agent_name, event=event, data=data, cwd=cwd)
        # Resolve log directory: /app/logs/ in sentinel-dev, fallback to local
        for base in ("/app/logs", "logs"):
            log_dir = Path(base)
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                log_file = log_dir / "agent_diagnostics.jsonl"
                with open(log_file, "a") as f:
                    f.write(_json.dumps(entry, default=str) + "\n")
                return
            except OSError:
                continue

    # --------------------------------------------------- event publication

    def _publish_tool_called(self, *, tool: str, args_summary: str) -> None:
        if self.event_bus is None or self.execution_id is None:
            return
        from src.core.events import ToolCalled

        try:
            self.event_bus.publish(
                ToolCalled(
                    execution_id=self.execution_id,
                    agent=self.agent_name,
                    tool=tool,
                    args_summary=args_summary,
                )
            )
        except Exception:
            logger.exception("failed to publish ToolCalled event")

    def _publish_cost_accrued(
        self, *, tokens_in: int, tokens_out: int, cents: int
    ) -> None:
        if self.event_bus is None or self.execution_id is None:
            return
        from src.core.events import CostAccrued

        try:
            self.event_bus.publish(
                CostAccrued(
                    execution_id=self.execution_id,
                    agent=self.agent_name,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cents=cents,
                )
            )
        except Exception:
            logger.exception("failed to publish CostAccrued event")

    def _publish_rate_limited(self, *, retry_after_s: float | None = None) -> None:
        if self.event_bus is None or self.execution_id is None:
            return
        from src.core.events import RateLimited

        try:
            self.event_bus.publish(
                RateLimited(
                    execution_id=self.execution_id,
                    agent=self.agent_name,
                    retry_after_s=retry_after_s,
                )
            )
        except Exception:
            logger.exception("failed to publish RateLimited event")

    async def execute_with_tools(
        self,
        prompt: str,
        session_id: str | None = None,
        system_prompt: str | None = None,
        cwd: str | None = None,
        max_turns: int | None = None,
        timeout: int | None = None,
    ) -> Dict[str, Any]:
        """Execute agent with tool use enabled.

        Args:
            prompt: The prompt to send to the agent
            session_id: Optional session ID to resume
            system_prompt: Optional system prompt to set agent behavior
            cwd: Optional working directory for agent execution
            max_turns: Optional max agentic turns (prevents runaway exploration)
            timeout: Optional execution timeout in seconds (overrides guardrail config)

        Returns:
            Dictionary with:
                - content: Generated text
                - tool_uses: List of tool uses
                - session_id: Session ID for resumption
        """
        effective_timeout = timeout or self.guardrails.get_timeout(self.agent_name)

        try:
            return await asyncio.wait_for(
                self._execute_sdk(prompt, session_id, system_prompt, cwd, max_turns),
                timeout=effective_timeout,
            )
        except asyncio.TimeoutError:
            raise AgentTimeoutError(
                f"Agent '{self.agent_name}' timed out after {effective_timeout}s"
            )

    async def _execute_sdk(
        self,
        prompt: str,
        session_id: str | None,
        system_prompt: str | None,
        cwd: str | None,
        max_turns: int | None,
    ) -> Dict[str, Any]:
        """Run the SDK client. Separated so execute_with_tools can wrap with timeout."""
        # Build subprocess environment based on LLM mode
        # The Agent SDK spawns the bundled Claude CLI as a subprocess,
        # so we need to explicitly pass environment variables.
        subprocess_env: Dict[str, str] = {
            # Disable telemetry to reduce noise and unnecessary network traffic
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }

        if self.llm_mode == "custom_proxy":
            # Custom proxy needs AUTH_TOKEN, BASE_URL, and model name overrides
            subprocess_env.update({
                "ANTHROPIC_AUTH_TOKEN": os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
                "ANTHROPIC_BASE_URL": os.environ.get("ANTHROPIC_BASE_URL", ""),
                # Model name overrides for proxy compatibility
                "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-4-5-haiku",
                "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-4-5-sonnet",
                "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-5",
                "ANTHROPIC_SMALL_FAST_MODEL": "claude-4-5-haiku",
            })
        elif self.llm_mode == "direct_api":
            # Direct Anthropic API - just needs the API key
            subprocess_env.update({
                "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
            })
        # subscription mode: no additional env vars needed, SDK handles auth

        logger.info(f"[{self.agent_name}] Agent SDK execute: model={self.model}, cwd={cwd}, session={session_id}, system_prompt_len={len(system_prompt) if system_prompt else 0}")
        logger.debug(f"System prompt preview: {system_prompt[:500] if system_prompt else 'None'}")
        logger.debug(f"User prompt: {prompt[:500] if prompt else 'None'}")

        self._write_diagnostic("exec_start", {
            "model": self.model,
            "session_id": session_id,
            "system_prompt_len": len(system_prompt) if system_prompt else 0,
            "user_prompt_len": len(prompt),
            "user_prompt_preview": prompt[:500],
            "allowed_tools": self.allowed_tools,
            "max_turns": max_turns,
            "llm_mode": self.llm_mode,
        }, cwd=cwd)

        # Stderr callback to capture and log errors
        stderr_lines = []
        # Write stderr to bind-mounted dir so it's readable from sandbox
        stderr_log_path = None
        for base in ("/app/logs", "logs"):
            try:
                Path(base).mkdir(parents=True, exist_ok=True)
                stderr_log_path = f"{base}/cli_stderr.log"
                break
            except OSError:
                continue

        def stderr_callback(line: str) -> None:
            stderr_lines.append(line)
            if stderr_log_path:
                with open(stderr_log_path, "a") as f:
                    f.write(f"{line}\n")
                    f.flush()

            # Filter log level based on message content
            # DEBUG messages from CLI should not be logged as errors
            if "[DEBUG]" in line:
                logger.debug(f"Claude CLI stderr: {line}")
            elif "Streaming stall" in line or "stall(s)" in line:
                # Stall messages indicate extended thinking - this is normal, not a warning
                logger.info(f"Claude CLI: Extended thinking in progress - {line}")
            elif "[WARN]" in line or "warning" in line.lower():
                logger.warning(f"Claude CLI stderr: {line}")
            elif "[ERROR]" in line or "error" in line.lower():
                logger.error(f"Claude CLI stderr: {line}")
            else:
                # Unknown level - log as info
                logger.info(f"Claude CLI stderr: {line}")

        # Log the exact model being used before passing to SDK
        logger.info(f"[{self.agent_name}] Using model: {self.model}")

        # Build system prompt: use preset mode with exclude_dynamic_sections
        # to prevent the CLI from injecting "read piped stdin" instructions.
        # The CLI detects non-TTY stdin and adds dynamic sections telling the
        # LLM to Read('/dev/stdin') — which is actually the SDK transport pipe.
        # exclude_dynamic_sections suppresses this while keeping the core prompt.
        if system_prompt:
            effective_system_prompt: str | SystemPromptPreset | None = SystemPromptPreset(
                type="preset",
                preset="claude_code",
                append=system_prompt,
                exclude_dynamic_sections=True,
            )
        else:
            effective_system_prompt = None

        # Build options
        options_kwargs: Dict[str, Any] = {
            "model": self.model,
            "allowed_tools": self.allowed_tools,
            "permission_mode": "acceptEdits",
            "system_prompt": effective_system_prompt,
            "cwd": cwd,
            "env": subprocess_env,
            "resume": session_id if session_id else None,
            "stderr": stderr_callback,
            "extra_args": {"debug-to-stderr": None},
            # Disable ambient settings discovery so the inner CLI ignores
            # .claude/settings.json and CLAUDE.md in the container hierarchy.
            # All instructions come via system_prompt; ambient files can cause
            # the CLI to inject stale/conflicting directives (e.g. read stdin).
            "setting_sources": [],
        }
        if max_turns is not None:
            options_kwargs["max_turns"] = max_turns
            logger.info(f"[SDK] max_turns={max_turns}")

        # Wire guardrail hooks into SDK options
        hooks = self.guardrails.build_hooks(cwd=cwd)
        if hooks:
            options_kwargs["hooks"] = hooks
            logger.info(f"[{self.agent_name}] Guardrail hooks attached")

        options = ClaudeAgentOptions(**options_kwargs)

        responses: list[str] = []
        tool_uses: list[Dict[str, Any]] = []
        final_session_id: str | None = None

        sdk_start = time.monotonic()
        logger.info(f"[{self.agent_name}] Opening ClaudeSDKClient (model={self.model})...")

        async with ClaudeSDKClient(options=options) as client:
            logger.info(f"[{self.agent_name}] Client opened ({time.monotonic() - sdk_start:.1f}s), sending query ({len(prompt)} chars)...")
            query_start = time.monotonic()
            await client.query(prompt)
            logger.info(f"[{self.agent_name}] Query sent ({time.monotonic() - query_start:.1f}s), waiting for response stream...")

            stream_start = time.monotonic()
            last_heartbeat = stream_start
            msg_count = 0
            text_chars = 0
            # Wrap the stream in a try/except: when the SDK surfaces a rate
            # limit (429/529 from upstream) we publish ``RateLimited`` as an
            # observation before re-raising. The SDK itself does retry/backoff
            # for transient blips; what reaches us here is the final failure
            # so the supervisor/worker can decide what to do.
            try:
                async for message in client.receive_response():
                    msg_count += 1
                    now = time.monotonic()

                    # Heartbeat: log progress every 30s during long generation
                    if now - last_heartbeat >= 30:
                        elapsed = now - stream_start
                        logger.info(
                            f"[{self.agent_name}] ♥ alive: {elapsed:.0f}s, "
                            f"{msg_count} msgs, {len(tool_uses)} tools, "
                            f"{text_chars} text chars so far"
                        )
                        last_heartbeat = now

                    # In-stream rate_limit signal: AssistantMessage.error is a
                    # soft marker the CLI emits when upstream throttles briefly.
                    # We publish the observation and let the stream keep going —
                    # raising here would short-circuit valid later content.
                    if (
                        isinstance(message, AssistantMessage)
                        and getattr(message, "error", None) == "rate_limit"
                    ):
                        self._publish_rate_limited(retry_after_s=None)

                    if isinstance(message, AssistantMessage):
                        for block in message.content:
                            if isinstance(block, TextBlock):
                                responses.append(block.text)
                                text_chars += len(block.text)
                                elapsed_in_stream = now - stream_start
                                logger.debug(
                                    f"[{self.agent_name}] Text block ({len(block.text)} chars, "
                                    f"{elapsed_in_stream:.1f}s into stream)"
                                )
                            elif isinstance(block, ToolUseBlock):
                                elapsed_in_stream = now - stream_start
                                tool_uses.append({
                                    "tool": block.name,
                                    "input": block.input
                                })
                                input_preview = str(block.input.get("command", block.input))[:200] if isinstance(block.input, dict) else str(block.input)[:200]
                                logger.info(f"[{self.agent_name}] Tool use: {block.name} ({elapsed_in_stream:.1f}s into stream) - {input_preview}")
                                self._write_diagnostic("tool_use", {
                                    "tool": block.name,
                                    "input": block.input if isinstance(block.input, dict) else str(block.input),
                                    "tool_index": len(tool_uses),
                                    "elapsed_s": round(elapsed_in_stream, 1),
                                }, cwd=cwd)
                                self._publish_tool_called(tool=block.name, args_summary=input_preview)
                    # Extract session ID from ResultMessage
                    if hasattr(message, 'session_id'):
                        final_session_id = message.session_id

                    # Best-effort cost accrual — not every ResultMessage carries usage,
                    # but when it does we forward tokens_in/out/cost to the bus so the
                    # orchestrator's subscriber can update executions.cost_cents.
                    usage = getattr(message, "usage", None)
                    total_cost_usd = getattr(message, "total_cost_usd", None)
                    if usage is not None or total_cost_usd is not None:
                        tokens_in = _usage_field(usage, "input_tokens")
                        tokens_out = _usage_field(usage, "output_tokens")
                        cents = int(round((total_cost_usd or 0) * 100))
                        self._publish_cost_accrued(
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                            cents=cents,
                        )
            except ClaudeSDKError as exc:
                # The SDK does not ship a dedicated RateLimitError; 429/529 from
                # upstream bubble up as ``ClaudeSDKError`` subclasses (notably
                # ``ProcessError`` with stderr set). We publish the observation
                # before re-raising so downstream failure handling is unchanged.
                is_rl, retry_after = _classify_rate_limit(exc)
                if is_rl:
                    self._publish_rate_limited(retry_after_s=retry_after)
                raise

        total_elapsed = time.monotonic() - sdk_start
        logger.info(f"[{self.agent_name}] Stream complete: {msg_count} messages, {len(tool_uses)} tool uses, {total_elapsed:.1f}s total")
        self._write_diagnostic("exec_complete", {
            "msg_count": msg_count,
            "tool_count": len(tool_uses),
            "total_elapsed_s": round(total_elapsed, 1),
            "response_len": len("\n".join(responses)),
            "session_id": final_session_id,
        }, cwd=cwd)

        # Track the session ID if we got one (with project association)
        if final_session_id:
            self.session_tracker.track_session(final_session_id, project=self.project)
            logger.debug(f"Tracked session: {final_session_id} (project: {self.project})")

        return {
            "content": "\n".join(responses),
            "tool_uses": tool_uses,
            "session_id": final_session_id,
        }
