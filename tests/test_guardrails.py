"""Unit tests for the guardrail engine."""

import asyncio
import pytest
from unittest.mock import MagicMock

from src.guardrails import GuardrailEngine, DEFAULT_BLOCKED_PATHS, DEFAULT_TIMEOUT, DEFAULT_MAX_CONSECUTIVE_REPEATS


def _make_config(guardrails_config=None):
    """Create a mock ConfigLoader with guardrails config."""
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: (
        guardrails_config if key == "guardrails" and guardrails_config is not None else default
    )
    return config


def _make_default_engine():
    """Create a GuardrailEngine with default config."""
    return GuardrailEngine(_make_config({
        "enabled": True,
        "timeout_seconds": 300,
        "agent_timeouts": {"plan_generator": 600},
        "rules": {
            "blocked_paths": ["/dev/*", "/proc/*", "/sys/*", "/etc/passwd"],
            "blocked_commands": ["rm -rf /"],
            "path_boundary": {"enabled": True, "extra_allowed": []},
        },
    }))


# --- Initialization ---

class TestInit:
    def test_enabled_by_default(self):
        engine = GuardrailEngine(_make_config({}))
        assert engine.enabled is True

    def test_disabled_when_configured(self):
        engine = GuardrailEngine(_make_config({"enabled": False}))
        assert engine.enabled is False

    def test_default_blocked_paths_when_no_rules(self):
        engine = GuardrailEngine(_make_config({}))
        assert engine.blocked_paths == DEFAULT_BLOCKED_PATHS

    def test_custom_blocked_paths(self):
        engine = GuardrailEngine(_make_config({
            "rules": {"blocked_paths": ["/tmp/*"]},
        }))
        assert engine.blocked_paths == ["/tmp/*"]

    def test_default_timeout(self):
        engine = GuardrailEngine(_make_config({}))
        assert engine.timeout_seconds == DEFAULT_TIMEOUT


# --- Blocked Paths ---

class TestBlockedPaths:
    def test_denies_dev_stdin(self):
        engine = _make_default_engine()
        result = engine._check_blocked_paths("Read", {"file_path": "/dev/stdin"}, "/work")
        assert result is not None
        assert "/dev/stdin" in result

    def test_denies_proc_path(self):
        engine = _make_default_engine()
        result = engine._check_blocked_paths("Read", {"file_path": "/proc/1/cmdline"}, "/work")
        assert result is not None

    def test_denies_etc_passwd(self):
        engine = _make_default_engine()
        result = engine._check_blocked_paths("Read", {"file_path": "/etc/passwd"}, "/work")
        assert result is not None

    def test_allows_normal_path(self):
        engine = _make_default_engine()
        result = engine._check_blocked_paths("Read", {"file_path": "/work/src/main.py"}, "/work")
        assert result is None

    def test_ignores_bash_tool(self):
        engine = _make_default_engine()
        result = engine._check_blocked_paths("Bash", {"command": "cat /dev/stdin"}, "/work")
        assert result is None

    def test_checks_path_key(self):
        engine = _make_default_engine()
        result = engine._check_blocked_paths("Glob", {"path": "/proc/self"}, "/work")
        assert result is not None

    def test_allows_empty_path(self):
        engine = _make_default_engine()
        result = engine._check_blocked_paths("Read", {}, "/work")
        assert result is None


# --- Blocked Commands ---

class TestBlockedCommands:
    def test_denies_rm_rf_root(self):
        engine = _make_default_engine()
        result = engine._check_blocked_commands("Bash", {"command": "rm -rf /"}, "/work")
        assert result is not None

    def test_allows_normal_command(self):
        engine = _make_default_engine()
        result = engine._check_blocked_commands("Bash", {"command": "git status"}, "/work")
        assert result is None

    def test_ignores_non_bash_tool(self):
        engine = _make_default_engine()
        result = engine._check_blocked_commands("Read", {"command": "rm -rf /"}, "/work")
        assert result is None

    def test_allows_empty_command(self):
        engine = _make_default_engine()
        result = engine._check_blocked_commands("Bash", {}, "/work")
        assert result is None


# --- Path Boundary ---

class TestPathBoundary:
    def test_allows_within_cwd(self):
        engine = _make_default_engine()
        result = engine._check_path_boundary("Read", {"file_path": "/work/src/main.py"}, "/work")
        assert result is None

    def test_allows_cwd_itself(self):
        engine = _make_default_engine()
        result = engine._check_path_boundary("Read", {"file_path": "/work"}, "/work")
        assert result is None

    def test_allows_relative_path_within_cwd(self):
        engine = _make_default_engine()
        result = engine._check_path_boundary("Grep", {"path": "web/themes/custom/dp_theme"}, "/work")
        assert result is None

    def test_denies_relative_path_escaping_cwd(self):
        engine = _make_default_engine()
        result = engine._check_path_boundary("Read", {"file_path": "../../etc/shadow"}, "/work/project")
        assert result is not None

    def test_denies_outside_cwd(self):
        engine = _make_default_engine()
        result = engine._check_path_boundary("Read", {"file_path": "/other/file.py"}, "/work")
        assert result is not None
        assert "outside working directory" in result

    def test_allows_extra_allowed_path(self):
        engine = GuardrailEngine(_make_config({
            "enabled": True,
            "rules": {
                "path_boundary": {"enabled": True, "extra_allowed": ["/shared"]},
            },
        }))
        result = engine._check_path_boundary("Read", {"file_path": "/shared/data.json"}, "/work")
        assert result is None

    def test_disabled_boundary_allows_all(self):
        engine = GuardrailEngine(_make_config({
            "enabled": True,
            "rules": {
                "path_boundary": {"enabled": False},
            },
        }))
        result = engine._check_path_boundary("Read", {"file_path": "/other/file.py"}, "/work")
        assert result is None

    def test_ignores_bash_tool(self):
        engine = _make_default_engine()
        result = engine._check_path_boundary("Bash", {"command": "cat /etc/hosts"}, "/work")
        assert result is None


# --- Evaluate (integration of all checkers) ---

class TestEvaluate:
    def test_blocked_path_denied(self):
        engine = _make_default_engine()
        result = engine._evaluate({"tool_name": "Read", "tool_input": {"file_path": "/dev/stdin"}}, "/work")
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_allowed_path_returns_empty(self):
        engine = _make_default_engine()
        result = engine._evaluate({"tool_name": "Read", "tool_input": {"file_path": "/work/file.py"}}, "/work")
        assert result == {}

    def test_non_dict_input_allowed(self):
        engine = _make_default_engine()
        result = engine._evaluate("not a dict", "/work")
        assert result == {}

    def test_blocked_command_denied(self):
        engine = _make_default_engine()
        result = engine._evaluate({"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}, "/work")
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_out_of_boundary_denied(self):
        engine = _make_default_engine()
        result = engine._evaluate({"tool_name": "Write", "tool_input": {"file_path": "/tmp/evil.py"}}, "/work")
        assert "hookSpecificOutput" in result
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


# --- Timeout ---

class TestTimeout:
    def test_global_timeout(self):
        engine = _make_default_engine()
        assert engine.get_timeout() == 300

    def test_agent_specific_timeout(self):
        engine = _make_default_engine()
        assert engine.get_timeout("plan_generator") == 600

    def test_unknown_agent_falls_back_to_global(self):
        engine = _make_default_engine()
        assert engine.get_timeout("unknown_agent") == 300


# --- build_hooks ---

class TestBuildHooks:
    def test_returns_none_when_disabled(self):
        engine = GuardrailEngine(_make_config({"enabled": False}))
        assert engine.build_hooks() is None

    def test_returns_hooks_dict_when_enabled(self):
        engine = _make_default_engine()
        hooks = engine.build_hooks(cwd="/work")
        assert hooks is not None
        assert "PreToolUse" in hooks
        assert len(hooks["PreToolUse"]) == 1

    def test_hook_matcher_covers_all_tools(self):
        engine = _make_default_engine()
        hooks = engine.build_hooks(cwd="/work")
        matcher = hooks["PreToolUse"][0]
        for tool in ["Read", "Write", "Edit", "Grep", "Glob", "Bash", "NotebookEdit"]:
            assert tool in matcher.matcher

    def test_hook_callback_blocks_dev_stdin(self):
        engine = _make_default_engine()
        hooks = engine.build_hooks(cwd="/work")
        callback = hooks["PreToolUse"][0].hooks[0]

        result = asyncio.run(
            callback(
                {"tool_name": "Read", "tool_input": {"file_path": "/dev/stdin"}},
                "tool-123",
                {},
            )
        )
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_hook_callback_allows_valid_path(self):
        engine = _make_default_engine()
        hooks = engine.build_hooks(cwd="/work")
        callback = hooks["PreToolUse"][0].hooks[0]

        result = asyncio.run(
            callback(
                {"tool_name": "Read", "tool_input": {"file_path": "/work/src/main.py"}},
                "tool-456",
                {},
            )
        )
        assert result == {}


# --- Repetitive Call Detection ---

class TestRepetitiveCalls:
    def test_allows_first_call(self):
        engine = _make_default_engine()
        history: list[str] = []
        result = engine._check_repetitive_calls("Bash", {"command": "git status"}, history)
        assert result is None

    def test_allows_within_limit(self):
        engine = _make_default_engine()
        history: list[str] = []
        for _ in range(10):
            result = engine._check_repetitive_calls("Bash", {"command": "git status"}, history)
        assert result is None

    def test_denies_over_limit(self):
        engine = _make_default_engine()
        history: list[str] = []
        for _ in range(10):
            engine._check_repetitive_calls("Bash", {"command": "python3 fetch.py"}, history)
        result = engine._check_repetitive_calls("Bash", {"command": "python3 fetch.py"}, history)
        assert result is not None
        assert "11 consecutive times" in result

    def test_resets_on_different_call(self):
        engine = _make_default_engine()
        history: list[str] = []
        for _ in range(9):
            engine._check_repetitive_calls("Bash", {"command": "python3 fetch.py"}, history)
        engine._check_repetitive_calls("Read", {"file_path": "/work/file.py"}, history)
        result = engine._check_repetitive_calls("Bash", {"command": "python3 fetch.py"}, history)
        assert result is None

    def test_custom_limit(self):
        engine = GuardrailEngine(_make_config({
            "enabled": True,
            "rules": {"max_consecutive_repeats": 3},
        }))
        history: list[str] = []
        for _ in range(3):
            engine._check_repetitive_calls("Bash", {"command": "echo hi"}, history)
        result = engine._check_repetitive_calls("Bash", {"command": "echo hi"}, history)
        assert result is not None
        assert "4 consecutive times" in result

    def test_tracks_file_path_tools(self):
        engine = _make_default_engine()
        history: list[str] = []
        for _ in range(11):
            result = engine._check_repetitive_calls("Read", {"file_path": "/work/same.py"}, history)
        assert result is not None

    def test_default_max_repeats(self):
        engine = GuardrailEngine(_make_config({}))
        assert engine.max_consecutive_repeats == DEFAULT_MAX_CONSECUTIVE_REPEATS


# --- Evaluate with call_history ---

class TestEvaluateWithHistory:
    def test_repetitive_bash_denied_via_evaluate(self):
        engine = _make_default_engine()
        history: list[str] = []
        for _ in range(10):
            engine._evaluate(
                {"tool_name": "Bash", "tool_input": {"command": "python3 /app/tmp.py"}},
                "/work",
                call_history=history,
            )
        result = engine._evaluate(
            {"tool_name": "Bash", "tool_input": {"command": "python3 /app/tmp.py"}},
            "/work",
            call_history=history,
        )
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_no_history_skips_repetitive_check(self):
        engine = _make_default_engine()
        for _ in range(15):
            result = engine._evaluate(
                {"tool_name": "Bash", "tool_input": {"command": "python3 /app/tmp.py"}},
                "/work",
            )
        assert result == {}

    def test_hook_callback_tracks_history(self):
        engine = _make_default_engine()
        hooks = engine.build_hooks(cwd="/work")
        callback = hooks["PreToolUse"][0].hooks[0]

        for _ in range(10):
            asyncio.run(callback(
                {"tool_name": "Bash", "tool_input": {"command": "python3 loop.py"}},
                "tool-id",
                {},
            ))
        result = asyncio.run(callback(
            {"tool_name": "Bash", "tool_input": {"command": "python3 loop.py"}},
            "tool-id",
            {},
        ))
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"
