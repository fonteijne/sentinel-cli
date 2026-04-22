"""Extensible guardrail framework for Agent SDK tool call validation.

Uses the Claude Agent SDK's PreToolUse hooks to intercept and validate
tool calls before execution. Rules are loaded from config.yaml.

To add a new guardrail rule:
1. Add config key under guardrails.rules in config.yaml
2. Add a _check_<rule_name>() method to GuardrailEngine
3. Register it in _get_checkers()
"""

import fnmatch
import logging
import os
from typing import Any, Dict

from claude_agent_sdk import HookMatcher
from claude_agent_sdk.types import (
    HookContext,
    HookInput,
    HookJSONOutput,
    PreToolUseHookInput,
    PreToolUseHookSpecificOutput,
)

from src.config_loader import ConfigLoader

logger = logging.getLogger(__name__)

FILE_PATH_TOOLS = {"Read", "Write", "Edit", "NotebookEdit"}
SEARCH_TOOLS = {"Grep", "Glob"}
ALL_PATH_TOOLS = FILE_PATH_TOOLS | SEARCH_TOOLS

DEFAULT_BLOCKED_PATHS = [
    "/dev/*",
    "/proc/*",
    "/sys/*",
    "/etc/passwd",
    "/etc/shadow",
]

DEFAULT_TIMEOUT = 300
DEFAULT_MAX_CONSECUTIVE_REPEATS = 10


class GuardrailEngine:
    """Config-driven guardrail engine for Agent SDK tool calls."""

    def __init__(self, config: ConfigLoader) -> None:
        raw = config.get("guardrails", {})
        guardrails_config = raw if isinstance(raw, dict) else {}
        self.enabled = guardrails_config.get("enabled", True)
        self.rules = guardrails_config.get("rules", {}) or {}
        self.timeout_seconds = guardrails_config.get("timeout_seconds", DEFAULT_TIMEOUT)
        self.agent_timeouts: Dict[str, int] = guardrails_config.get("agent_timeouts", {}) or {}

        blocked = self.rules.get("blocked_paths", DEFAULT_BLOCKED_PATHS)
        self.blocked_paths: list[str] = blocked if blocked is not None else DEFAULT_BLOCKED_PATHS

        self.blocked_commands: list[str] = self.rules.get("blocked_commands", []) or []

        boundary_config = self.rules.get("path_boundary", {}) or {}
        self.path_boundary_enabled = boundary_config.get("enabled", True)
        self.extra_allowed: list[str] = boundary_config.get("extra_allowed", []) or []

        self.max_consecutive_repeats: int = self.rules.get(
            "max_consecutive_repeats", DEFAULT_MAX_CONSECUTIVE_REPEATS
        )

        if self.enabled:
            logger.info(
                f"Guardrails enabled: {len(self.blocked_paths)} blocked paths, "
                f"{len(self.blocked_commands)} blocked commands, "
                f"path_boundary={'on' if self.path_boundary_enabled else 'off'}, "
                f"max_repeats={self.max_consecutive_repeats}, "
                f"timeout={self.timeout_seconds}s"
            )

    def build_hooks(self, cwd: str | None = None) -> dict[str, list[HookMatcher]] | None:
        """Build SDK PreToolUse hooks from configured rules.

        Returns None if guardrails are disabled, otherwise a dict
        compatible with ClaudeAgentOptions.hooks.
        """
        if not self.enabled:
            return None

        resolved_cwd = cwd or os.getcwd()
        call_history: list[str] = []

        async def pre_tool_callback(
            input_data: HookInput,
            tool_use_id: str | None,
            context: HookContext,
        ) -> HookJSONOutput:
            return self._evaluate(input_data, resolved_cwd, call_history)

        tool_matcher = "|".join(sorted(ALL_PATH_TOOLS | {"Bash"}))
        return {
            "PreToolUse": [
                HookMatcher(
                    matcher=tool_matcher,
                    hooks=[pre_tool_callback],
                    timeout=10.0,
                )
            ],
        }

    def _evaluate(
        self, input_data: Any, cwd: str, call_history: list[str] | None = None,
    ) -> HookJSONOutput:
        """Run all applicable checks against a tool call."""
        if not isinstance(input_data, dict):
            return self._allow()

        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Stateful check: repetitive tool calls (only when history is tracked)
        if call_history is not None:
            reason = self._check_repetitive_calls(tool_name, tool_input, call_history)
            if reason:
                logger.warning(f"Guardrail DENIED {tool_name}: {reason}")
                return self._deny(reason)

        # Stateless checks
        for check in self._get_checkers():
            reason = check(tool_name, tool_input, cwd)
            if reason:
                logger.warning(f"Guardrail DENIED {tool_name}: {reason}")
                return self._deny(reason)

        return self._allow()

    def _get_checkers(self) -> list:
        """Return ordered list of check functions. Extend here for new rules."""
        return [
            self._check_blocked_paths,
            self._check_blocked_commands,
            self._check_path_boundary,
        ]

    def _check_blocked_paths(self, tool_name: str, tool_input: dict, cwd: str) -> str | None:
        """Deny file operations on blocked path patterns."""
        if tool_name not in ALL_PATH_TOOLS:
            return None

        path = tool_input.get("file_path") or tool_input.get("path") or ""
        if not path:
            return None

        for pattern in self.blocked_paths:
            if fnmatch.fnmatch(path, pattern):
                if path == "/dev/stdin":
                    return (
                        "BLOCKED: /dev/stdin is the SDK transport pipe, not user data. "
                        "It will hang if read. All input data is already in your prompt. "
                        "Do NOT retry this call."
                    )
                return (
                    f"BLOCKED: Path '{path}' is a restricted OS resource (matches '{pattern}'). "
                    f"Work only with files in the project working directory."
                )

        return None

    def _check_blocked_commands(self, tool_name: str, tool_input: dict, cwd: str) -> str | None:
        """Deny Bash commands matching blocked patterns."""
        if tool_name != "Bash":
            return None

        command = tool_input.get("command", "")
        if not command:
            return None

        for pattern in self.blocked_commands:
            if fnmatch.fnmatch(command, pattern):
                return f"Command matches blocked pattern '{pattern}'"

        return None

    def _check_path_boundary(self, tool_name: str, tool_input: dict, cwd: str) -> str | None:
        """Deny file operations outside the working directory."""
        if not self.path_boundary_enabled:
            return None
        if tool_name not in ALL_PATH_TOOLS:
            return None

        path = tool_input.get("file_path") or tool_input.get("path") or ""
        if not path:
            return None

        if not os.path.isabs(path):
            path = os.path.join(cwd, path)
        resolved = os.path.realpath(path)
        cwd_resolved = os.path.realpath(cwd)

        if resolved.startswith(cwd_resolved + os.sep) or resolved == cwd_resolved:
            return None

        for allowed in self.extra_allowed:
            allowed_resolved = os.path.realpath(allowed)
            if resolved.startswith(allowed_resolved + os.sep) or resolved == allowed_resolved:
                return None

        return f"Path '{path}' is outside working directory '{cwd}'"

    def _check_repetitive_calls(
        self, tool_name: str, tool_input: dict, call_history: list[str],
    ) -> str | None:
        """Deny when the same tool call is repeated too many consecutive times.

        Detects agents stuck in retry loops (e.g., running the same Bash
        command 100+ times). The call_history is scoped per build_hooks()
        invocation so it resets for each agent execution.
        """
        if tool_name == "Bash":
            key = f"Bash:{tool_input.get('command', '')}"
        else:
            key = f"{tool_name}:{tool_input.get('file_path') or tool_input.get('path', '')}"

        call_history.append(key)

        consecutive = 0
        for past_key in reversed(call_history):
            if past_key == key:
                consecutive += 1
            else:
                break

        if consecutive > self.max_consecutive_repeats:
            return (
                f"Tool '{tool_name}' called {consecutive} consecutive times "
                f"with same input (limit: {self.max_consecutive_repeats})"
            )

        return None

    def get_timeout(self, agent_name: str | None = None) -> int:
        """Get execution timeout in seconds for an agent."""
        if agent_name and agent_name in self.agent_timeouts:
            return self.agent_timeouts[agent_name]
        return self.timeout_seconds

    @staticmethod
    def _allow() -> HookJSONOutput:
        return {}

    @staticmethod
    def _deny(reason: str) -> HookJSONOutput:
        output: PreToolUseHookSpecificOutput = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
        return {
            "reason": f"Guardrail: {reason}",
            "hookSpecificOutput": output,
        }
