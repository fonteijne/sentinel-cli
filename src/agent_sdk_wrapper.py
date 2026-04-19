"""Wrapper for Claude Agent SDK with unified LLM provider support."""

import os
import logging
import time
from typing import Any, Dict

from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions
from claude_agent_sdk.types import AssistantMessage, TextBlock, ToolUseBlock

from src.config_loader import ConfigLoader
from src.session_tracker import SessionTracker

logger = logging.getLogger(__name__)


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
        self.project: str | None = None  # Project key for session tracking
        self.llm_mode: str = "subscription"  # Default mode

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

    async def execute_with_tools(
        self,
        prompt: str,
        session_id: str | None = None,
        system_prompt: str | None = None,
        cwd: str | None = None,
        max_turns: int | None = None,
    ) -> Dict[str, Any]:
        """Execute agent with tool use enabled.

        Args:
            prompt: The prompt to send to the agent
            session_id: Optional session ID to resume
            system_prompt: Optional system prompt to set agent behavior
            cwd: Optional working directory for agent execution
            max_turns: Optional max agentic turns (prevents runaway exploration)

        Returns:
            Dictionary with:
                - content: Generated text
                - tool_uses: List of tool uses
                - session_id: Session ID for resumption
        """
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

        # Stderr callback to capture and log errors
        stderr_lines = []
        def stderr_callback(line: str) -> None:
            stderr_lines.append(line)
            # Write to file for debugging
            with open("/tmp/agent_sdk_stderr.log", "a") as f:
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

        # Build options
        options_kwargs: Dict[str, Any] = {
            "model": self.model,
            "allowed_tools": self.allowed_tools,
            "permission_mode": "acceptEdits",
            "system_prompt": system_prompt,
            "cwd": cwd,
            "env": subprocess_env,
            "resume": session_id if session_id else None,
            "stderr": stderr_callback,
            "extra_args": {"debug-to-stderr": None},
        }
        if max_turns is not None:
            options_kwargs["max_turns"] = max_turns
            logger.info(f"[SDK] max_turns={max_turns}")
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
            msg_count = 0
            async for message in client.receive_response():
                msg_count += 1
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            responses.append(block.text)
                        elif isinstance(block, ToolUseBlock):
                            tool_uses.append({
                                "tool": block.name,
                                "input": block.input
                            })
                            logger.info(f"[{self.agent_name}] Tool use: {block.name} ({time.monotonic() - stream_start:.1f}s into stream)")
                # Extract session ID from ResultMessage
                if hasattr(message, 'session_id'):
                    final_session_id = message.session_id

        logger.info(f"[{self.agent_name}] Stream complete: {msg_count} messages, {len(tool_uses)} tool uses, {time.monotonic() - sdk_start:.1f}s total")

        # Track the session ID if we got one (with project association)
        if final_session_id:
            self.session_tracker.track_session(final_session_id, project=self.project)
            logger.debug(f"Tracked session: {final_session_id} (project: {self.project})")

        return {
            "content": "\n".join(responses),
            "tool_uses": tool_uses,
            "session_id": final_session_id,
        }
