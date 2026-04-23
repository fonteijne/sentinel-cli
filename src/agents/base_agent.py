"""Base agent class for Sentinel agents."""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from src.config_loader import get_config
from src.prompt_loader import load_agent_prompt
from src.command_executor import execute_command
from src.agent_sdk_wrapper import AgentSDKWrapper

if TYPE_CHECKING:
    from src.core.events import EventBus, SentinelEvent


logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Base class for all Sentinel agents.

    Provides common functionality:
    - System prompt loading
    - LLM interaction via LLM Provider
    - Custom command execution
    - Configuration management
    """

    def __init__(
        self,
        agent_name: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        event_bus: Optional["EventBus"] = None,
        execution_id: Optional[str] = None,
    ) -> None:
        """Initialize base agent.

        Args:
            agent_name: Agent name for prompt loading (e.g., "plan_generator")
            model: LLM model to use (defaults to config)
            temperature: Sampling temperature (defaults to config)
            event_bus: Optional Command Center EventBus. When provided,
                agent activity is emitted as typed events in addition to
                the existing ``logger.info`` calls.
            execution_id: Optional execution id to stamp on emitted events.
                Required when ``event_bus`` is provided.
        """
        self.agent_name = agent_name
        self.config = get_config()
        self.agent_sdk = AgentSDKWrapper(agent_name, self.config)

        # Command Center event plumbing (opt-in; None keeps legacy behavior).
        self.event_bus = event_bus
        self.execution_id = execution_id
        # Propagate to the SDK wrapper so it can emit tool/cost/rate-limit events.
        if event_bus is not None and execution_id is not None:
            self.agent_sdk.event_bus = event_bus
            self.agent_sdk.execution_id = execution_id

        # Load agent configuration
        agent_config = self.config.get_agent_config(agent_name)

        # Use provided values or fall back to config
        self.model = model or agent_config.get("model", "claude-4-5-haiku")
        self.temperature = temperature if temperature is not None else agent_config.get("temperature", 0.2)

        # Load system prompt
        try:
            self.system_prompt = load_agent_prompt(agent_name)
            logger.info(f"Loaded system prompt for {agent_name} ({len(self.system_prompt)} chars)")
        except FileNotFoundError as e:
            logger.warning(f"System prompt not found for {agent_name}: {e}")
            self.system_prompt = ""

        # Message history for context
        self.messages: List[Dict[str, str]] = []

        # Session management for Agent SDK
        self.session_id: Optional[str] = None

        # Project key for session tracking
        self._project: Optional[str] = None

        logger.info(
            f"Initialized {agent_name} agent (model={self.model}, temp={self.temperature})"
        )

    def attach_events(
        self, event_bus: "EventBus", execution_id: str
    ) -> None:
        """Wire (or re-wire) Command Center events on an already-constructed agent.

        Subclasses that don't forward the ``event_bus``/``execution_id`` kwargs
        through their own ``__init__`` can still participate by calling this
        after construction.
        """
        self.event_bus = event_bus
        self.execution_id = execution_id
        self.agent_sdk.event_bus = event_bus
        self.agent_sdk.execution_id = execution_id

    def set_project(self, project: str) -> None:
        """Set the project key for session tracking.

        This should be called before running the agent to ensure
        sessions are correctly associated with the project.

        Args:
            project: Project key (e.g., "ACME")
        """
        self._project = project
        self.agent_sdk.set_project(project)

    def _add_system_message(self) -> None:
        """Add system prompt to message history if not already present."""
        if self.system_prompt and (
            not self.messages or self.messages[0].get("role") != "system"
        ):
            self.messages.insert(0, {"role": "system", "content": self.system_prompt})

    def _build_prompt(self) -> str:
        """Build full prompt with system message and conversation history.

        Returns:
            Combined prompt string

        Note: This method is kept for backwards compatibility but is no longer used.
        Use _build_user_prompt() instead for Agent SDK integration.
        """
        prompt_parts = []

        # Add system prompt if present
        if self.system_prompt:
            prompt_parts.append(f"SYSTEM: {self.system_prompt}")

        # Add message history
        for msg in self.messages:
            role = msg["role"].upper()
            content = msg["content"]
            prompt_parts.append(f"{role}: {content}")

        return "\n\n".join(prompt_parts)

    def _build_user_prompt(self) -> str:
        """Build user prompt from message history (without system prompt).

        System prompt is passed separately to Agent SDK.

        Returns:
            User prompt string with conversation history
        """
        # Just return the latest user message content
        # For multi-turn conversations, we'll rely on session_id to maintain context
        if self.messages:
            return self.messages[-1]["content"]
        return ""

    async def _send_message_async(
        self, content: str, role: str = "user", cwd: str | None = None,
        max_turns: int | None = None, timeout: int | None = None,
    ) -> str:
        """Internal async implementation using Agent SDK.

        Args:
            content: Message content
            role: Message role ("user" or "assistant")
            cwd: Working directory for tool execution
            max_turns: Optional max agentic turns
            timeout: Optional execution timeout in seconds

        Returns:
            Agent's response text
        """
        # Add message to history
        self.messages.append({"role": role, "content": content})

        # Build user prompt from message history
        user_prompt = self._build_user_prompt()

        # Execute via Agent SDK with system prompt passed separately
        t0 = time.monotonic()
        logger.info(
            f"[LLM] {self.agent_name}: sending request "
            f"(prompt={len(content)} chars, cwd={cwd}, session={self.session_id}, max_turns={max_turns})"
        )
        self._emit_message_sent(prompt_chars=len(content), cwd=cwd, max_turns=max_turns)
        response = await self.agent_sdk.execute_with_tools(
            prompt=user_prompt,
            session_id=self.session_id,
            system_prompt=self.system_prompt if self.system_prompt else None,
            cwd=cwd,
            max_turns=max_turns,
            timeout=timeout,
        )
        elapsed = time.monotonic() - t0
        logger.info(
            f"[LLM] {self.agent_name}: response received "
            f"({len(str(response['content']))} chars, {len(response.get('tool_uses', []))} tools, {elapsed:.1f}s)"
        )
        self._emit_response_received(
            response_chars=len(str(response["content"])),
            tool_uses_count=len(response.get("tool_uses", []) or []),
            elapsed_s=elapsed,
        )

        # Extract text content
        content_text: str = str(response["content"])

        # Update session for resumption
        self.session_id = response["session_id"]

        # Add to history
        self.messages.append({"role": "assistant", "content": content_text})

        return content_text

    def send_message(
        self, content: str, role: str = "user", cwd: str | None = None,
        max_turns: int | None = None, timeout: int | None = None,
    ) -> str:
        """Send a message to the agent and get a response.

        COMPATIBILITY: Maintains sync interface for existing agents.
        Internally uses async Agent SDK but presents sync API.

        Args:
            content: Message content
            role: Message role ("user" or "assistant")
            cwd: Working directory for tool execution (important for agents with tools)
            max_turns: Optional max agentic turns
            timeout: Optional execution timeout in seconds

        Returns:
            Agent's response text
        """
        # Run async execution in sync context
        logger.info(f"[LLM] {self.agent_name}: send_message called (cwd={cwd}, max_turns={max_turns})")
        result = asyncio.run(self._send_message_async(content, role, cwd, max_turns=max_turns, timeout=timeout))
        return result

    def execute_command(
        self, command_name: str, parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute a custom agent command.

        Args:
            command_name: Command name (e.g., "implement-tdd")
            parameters: Command parameters

        Returns:
            Execution result dictionary
        """
        return execute_command(command_name, parameters, agent_type=self.agent_name)

    def clear_history(self) -> None:
        """Clear the message history."""
        self.messages.clear()

    def get_history(self) -> List[Dict[str, str]]:
        """Get the message history.

        Returns:
            List of message dictionaries
        """
        return self.messages.copy()

    # ------------------------------------------------------------- events

    def _emit(self, event: "SentinelEvent") -> None:
        """Publish ``event`` if an :class:`EventBus` is wired; no-op otherwise."""
        if self.event_bus is None:
            return
        try:
            self.event_bus.publish(event)
        except Exception:
            logger.exception("failed to publish event %r", event)

    def _emit_message_sent(
        self, *, prompt_chars: int, cwd: Optional[str], max_turns: Optional[int]
    ) -> None:
        if self.event_bus is None or self.execution_id is None:
            return
        from src.core.events import AgentMessageSent  # local import: optional dep

        self._emit(
            AgentMessageSent(
                execution_id=self.execution_id,
                agent=self.agent_name,
                prompt_chars=prompt_chars,
                cwd=cwd,
                max_turns=max_turns,
            )
        )

    def _emit_response_received(
        self, *, response_chars: int, tool_uses_count: int, elapsed_s: float
    ) -> None:
        if self.event_bus is None or self.execution_id is None:
            return
        from src.core.events import AgentResponseReceived

        self._emit(
            AgentResponseReceived(
                execution_id=self.execution_id,
                agent=self.agent_name,
                response_chars=response_chars,
                tool_uses_count=tool_uses_count,
                elapsed_s=elapsed_s,
            )
        )

    def _append_operator_prompt(self, prompt: str, user_prompt: str | None) -> str:
        """Append operator instruction to a prompt if provided."""
        if not user_prompt:
            return prompt
        return f"{prompt}\n\n---\n## Operator Instruction\n\n{user_prompt}\n"

    @abstractmethod
    def run(self, **kwargs: Any) -> Any:
        """Run the agent's primary task.

        This method must be implemented by subclasses to define
        the agent's specific behavior.

        Args:
            **kwargs: Agent-specific parameters

        Returns:
            Agent-specific return value
        """
        pass


class PlanningAgent(BaseAgent):
    """Base class for planning/analysis agents."""

    def __init__(
        self,
        agent_name: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        event_bus: Optional["EventBus"] = None,
        execution_id: Optional[str] = None,
    ) -> None:
        """Initialize planning agent."""
        super().__init__(agent_name, model, temperature, event_bus, execution_id)


class ImplementationAgent(BaseAgent):
    """Base class for implementation/coding agents."""

    def __init__(
        self,
        agent_name: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        event_bus: Optional["EventBus"] = None,
        execution_id: Optional[str] = None,
    ) -> None:
        """Initialize implementation agent."""
        super().__init__(agent_name, model, temperature, event_bus, execution_id)


class ReviewAgent(BaseAgent):
    """Base class for review/validation agents."""

    def __init__(
        self,
        agent_name: str,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        veto_power: bool = False,
        event_bus: Optional["EventBus"] = None,
        execution_id: Optional[str] = None,
    ) -> None:
        """Initialize review agent.

        Args:
            agent_name: Agent name
            model: LLM model to use
            temperature: Sampling temperature
            veto_power: Whether agent can block progress
            event_bus: Optional Command Center EventBus.
            execution_id: Optional execution id to stamp on emitted events.
        """
        super().__init__(agent_name, model, temperature, event_bus, execution_id)
        self.veto_power = veto_power
