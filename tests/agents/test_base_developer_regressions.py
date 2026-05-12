"""Tests for cross-iteration regression carry (Plan B / verifier-cross-iteration).

Covers:
  - Pure helpers: ``RegressionContext``, ``_dedupe_structured_errors``,
    ``_render_regression_section``.
  - Prompt prepending: when ``regressions`` is supplied, every
    ``implement_feature`` call (single-shot and Loop A) prefixes the
    developer prompt with a "## Prior Iteration Regressions" section.
  - Iteration boundary: ``BaseDeveloperAgent.run`` collects the structured
    errors from every failed task into ``regression_errors`` (deduped) and
    threads any inbound ``regressions`` into per-task prompts.
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from src.agents._structured_errors import StructuredError
from src.agents.base_developer import (
    DeveloperCappedOutException,
    DeveloperTaskFailedException,
    RegressionContext,
    _dedupe_structured_errors,
    _render_regression_section,
)
from src.agents.python_developer import PythonDeveloperAgent


# ---------------------------------------------------------------------------
# Shared fixtures (same shape as test_base_developer_verifier_loop.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_config():
    with patch("src.agents.base_agent.get_config") as mock:
        config = Mock()
        config.get_agent_config.return_value = {
            "model": "claude-4-5-sonnet",
            "temperature": 0.2,
        }
        config.get_llm_config.return_value = {
            "mode": "custom_proxy",
            "api_key": "test-api-key",
            "base_url": "https://test.api.com/v1",
        }
        config.get.return_value = ["Read", "Grep", "Glob"]
        mock.return_value = config
        yield config


@pytest.fixture
def mock_agent_sdk():
    with patch("src.agents.base_agent.AgentSDKWrapper") as mock:
        wrapper = Mock()

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {"content": "ok", "tool_uses": [], "session_id": "test"}

        wrapper.execute_with_tools = mock_execute
        wrapper.set_project = Mock()
        wrapper.agent_name = "python_developer"
        wrapper.model = "claude-4-5-sonnet"
        wrapper.llm_mode = "custom_proxy"
        wrapper.allowed_tools = ["Read", "Write", "Edit", "Grep", "Glob", "Bash"]
        mock.return_value = wrapper
        yield wrapper


@pytest.fixture
def mock_prompt():
    with patch("src.agents.base_agent.load_agent_prompt") as mock:
        mock.return_value = "Developer system prompt"
        yield mock


@pytest.fixture
def temp_worktree():
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def _err(file: str, line: int, rule: str, message: str) -> StructuredError:
    return StructuredError(file=file, line=line, rule=rule, message=message)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_render_regression_section_empty_returns_blank():
    """Empty / None context renders to '' so the prepend is a no-op."""
    assert _render_regression_section(None) == ""
    assert _render_regression_section(RegressionContext(iteration_n=1, errors=[])) == ""


def test_render_regression_section_includes_header_count_and_errors():
    """Header advertises the iteration number, the count, and each error."""
    ctx = RegressionContext(
        iteration_n=2,
        errors=[
            _err("tests/test_a.py", 12, "test_failed", "AssertionError: nope"),
            _err("tests/test_b.py", 0, "test_error", "ImportError: kaboom"),
        ],
    )
    rendered = _render_regression_section(ctx)
    assert "## Prior Iteration Regressions" in rendered
    # Lead with prior iteration number and total failure count.
    assert "previous iteration (2)" in rendered
    assert "2 test(s) failing" in rendered
    # Both errors are present.
    assert "tests/test_a.py:12" in rendered
    assert "AssertionError: nope" in rendered
    assert "tests/test_b.py:0" in rendered
    assert "ImportError: kaboom" in rendered


def test_dedupe_collapses_identical_errors_preserving_order():
    """Same (file, line, rule, message) appearing N times collapses to one."""
    errs = [
        _err("a.py", 1, "test_failed", "boom"),
        _err("b.py", 2, "test_failed", "kaboom"),
        _err("a.py", 1, "test_failed", "boom"),  # duplicate of #1
        _err("a.py", 1, "test_failed", "boom"),  # duplicate of #1
        _err("c.py", 0, "test_error", "?"),
    ]
    out = _dedupe_structured_errors(errs)
    assert len(out) == 3
    assert out[0]["file"] == "a.py"
    assert out[1]["file"] == "b.py"
    assert out[2]["file"] == "c.py"


def test_dedupe_distinguishes_different_lines():
    """Same file+rule+message but different line numbers stay distinct."""
    errs = [
        _err("a.py", 1, "test_failed", "boom"),
        _err("a.py", 2, "test_failed", "boom"),
    ]
    assert len(_dedupe_structured_errors(errs)) == 2


# ---------------------------------------------------------------------------
# Prompt prepending — single-shot path
# ---------------------------------------------------------------------------


def test_single_shot_prepends_regression_block(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree, monkeypatch
):
    """Without DEV_VERIFIER_LOOP, regressions are still prepended to the prompt."""
    monkeypatch.delenv("DEV_VERIFIER_LOOP", raising=False)
    agent = PythonDeveloperAgent()

    sdk_calls: list[str] = []

    async def fake_execute(prompt, session_id=None, system_prompt=None, cwd=None):
        sdk_calls.append(prompt)
        return {"content": "ok", "tool_uses": []}

    agent.agent_sdk.execute_with_tools = fake_execute

    ctx = RegressionContext(
        iteration_n=1,
        errors=[_err("tests/test_a.py", 9, "test_failed", "carryover boom")],
    )

    with patch.object(agent, "execute_command") as exec_cmd, \
         patch.object(agent, "run_tests") as run_tests:
        exec_cmd.return_value = {"success": True, "workflow": []}
        run_tests.return_value = {
            "passed": True,
            "test_results": "ok",
            "structured_errors": [],
            "return_code": 0,
        }

        agent.implement_feature("task", {}, temp_worktree, regressions=ctx)

    assert len(sdk_calls) == 1
    prompt = sdk_calls[0]
    assert "## Prior Iteration Regressions" in prompt
    assert "carryover boom" in prompt
    assert "previous iteration (1)" in prompt


def test_single_shot_no_regressions_no_block(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree, monkeypatch
):
    """When no regressions are supplied, the prompt is unchanged."""
    monkeypatch.delenv("DEV_VERIFIER_LOOP", raising=False)
    agent = PythonDeveloperAgent()

    sdk_calls: list[str] = []

    async def fake_execute(prompt, session_id=None, system_prompt=None, cwd=None):
        sdk_calls.append(prompt)
        return {"content": "ok", "tool_uses": []}

    agent.agent_sdk.execute_with_tools = fake_execute

    with patch.object(agent, "execute_command") as exec_cmd, \
         patch.object(agent, "run_tests") as run_tests:
        exec_cmd.return_value = {"success": True, "workflow": []}
        run_tests.return_value = {
            "passed": True,
            "test_results": "ok",
            "structured_errors": [],
            "return_code": 0,
        }

        agent.implement_feature("task", {}, temp_worktree)

    assert len(sdk_calls) == 1
    assert "## Prior Iteration Regressions" not in sdk_calls[0]


def test_single_shot_failure_carries_structured_errors_on_exception(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree, monkeypatch
):
    """On test failure, the raised exception carries the parsed structured errors
    so the iteration loop can fold them into the next iteration's regressions."""
    monkeypatch.delenv("DEV_VERIFIER_LOOP", raising=False)
    agent = PythonDeveloperAgent()

    failing_errors = [_err("tests/test_a.py", 1, "test_failed", "boom")]

    with patch.object(agent, "execute_command") as exec_cmd, \
         patch.object(agent, "run_tests") as run_tests:
        exec_cmd.return_value = {"success": True, "workflow": []}
        run_tests.return_value = {
            "passed": False,
            "test_results": "FAILED ...",
            "structured_errors": failing_errors,
            "return_code": 1,
        }

        with pytest.raises(DeveloperTaskFailedException) as excinfo:
            agent.implement_feature("task", {}, temp_worktree)

    assert excinfo.value.structured_errors == failing_errors


# ---------------------------------------------------------------------------
# Prompt prepending — Loop A path
# ---------------------------------------------------------------------------


def test_loop_path_prepends_regression_block(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree, monkeypatch
):
    """With Loop A enabled, the first-attempt prompt carries the regression block."""
    monkeypatch.setenv("DEV_VERIFIER_LOOP", "1")
    agent = PythonDeveloperAgent()

    sdk_calls: list[str] = []

    async def fake_execute(prompt, session_id=None, system_prompt=None, cwd=None):
        sdk_calls.append(prompt)
        return {"content": "ok", "tool_uses": []}

    agent.agent_sdk.execute_with_tools = fake_execute

    ctx = RegressionContext(
        iteration_n=1,
        errors=[_err("tests/test_x.py", 2, "test_failed", "loopcarry")],
    )

    with patch.object(agent, "execute_command") as exec_cmd, \
         patch.object(agent, "run_tests") as run_tests, \
         patch.object(agent, "run_static_checks") as run_static:
        exec_cmd.return_value = {"success": True, "workflow": []}
        run_tests.return_value = {
            "passed": True,
            "test_results": "ok",
            "structured_errors": [],
            "return_code": 0,
        }
        run_static.return_value = {
            "passed": True,
            "structured_errors": [],
            "ran": True,
        }

        agent.implement_feature("task", {}, temp_worktree, regressions=ctx)

    assert len(sdk_calls) == 1
    assert "## Prior Iteration Regressions" in sdk_calls[0]
    assert "loopcarry" in sdk_calls[0]


def test_loop_capout_exception_carries_last_errors(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree, monkeypatch
):
    """On cap-out, the raised exception exposes the last structured errors so
    the iteration loop can carry them forward."""
    monkeypatch.setenv("DEV_VERIFIER_LOOP", "1")
    agent = PythonDeveloperAgent()

    async def fake_execute(prompt, session_id=None, system_prompt=None, cwd=None):
        return {"content": "ok", "tool_uses": []}

    agent.agent_sdk.execute_with_tools = fake_execute

    failing_errors = [_err("tests/test_a.py", 0, "test_failed", "kaboom")]

    with patch.object(agent, "execute_command") as exec_cmd, \
         patch.object(agent, "run_tests") as run_tests, \
         patch.object(agent, "run_static_checks") as run_static:
        exec_cmd.return_value = {"success": True, "workflow": []}
        run_tests.return_value = {
            "passed": False,
            "test_results": "FAILED ...",
            "structured_errors": failing_errors,
            "return_code": 1,
        }
        run_static.return_value = {
            "passed": True,
            "structured_errors": [],
            "ran": True,
        }

        with pytest.raises(DeveloperCappedOutException) as excinfo:
            agent.implement_feature("task", {}, temp_worktree)

    # Cap-out should expose the same structured errors that produced the
    # final failed verifier — not necessarily a strict subset since
    # static_errors get appended too, but at minimum our test failure must
    # be present.
    err_keys = {(e["file"], e["rule"], e["message"]) for e in excinfo.value.structured_errors}
    assert ("tests/test_a.py", "test_failed", "kaboom") in err_keys


# ---------------------------------------------------------------------------
# Iteration boundary — BaseDeveloperAgent.run()
# ---------------------------------------------------------------------------


def _make_plan_file(tmp: Path, tasks: list[str]) -> Path:
    """Write a tiny plan file the default break_down_plan can chew on.
    The break_down_plan logic is mocked out below; this just gives ``run``
    a real file path to stem-parse a project key from."""
    f = tmp / "PROJ-123-plan.md"
    f.write_text("\n".join(f"- [ ] {t}" for t in tasks))
    return f


def test_run_collects_regression_errors_from_failed_tasks(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree
):
    """Tasks that raise with structured_errors land in ``regression_errors``,
    deduped, on the dict returned by ``run``."""
    agent = PythonDeveloperAgent()

    plan_file = _make_plan_file(temp_worktree, ["task one", "task two", "task three"])

    err_a = _err("a.py", 1, "test_failed", "boom")
    err_b = _err("b.py", 2, "test_failed", "kaboom")

    def fake_implement(task, ctx, wt, **kwargs):
        if task.startswith("task one"):
            # Same error appears in both failing tasks — dedup must collapse.
            raise DeveloperTaskFailedException("fail-1", structured_errors=[err_a])
        if task.startswith("task two"):
            raise DeveloperTaskFailedException(
                "fail-2", structured_errors=[err_a, err_b]
            )
        return {"success": True, "files_created": [], "files_modified": []}

    with patch.object(agent, "implement_feature", side_effect=fake_implement), \
         patch.object(agent, "validate_config") as validate, \
         patch.object(agent, "run_tests") as run_tests, \
         patch.object(agent, "break_down_plan") as break_down:
        validate.return_value = {"success": True, "output": "", "environment_issue": False}
        run_tests.return_value = {
            "passed": True,
            "test_results": "ok",
            "structured_errors": [],
            "return_code": 0,
        }
        break_down.return_value = ["task one", "task two", "task three"]

        result = agent.run(plan_file=plan_file, worktree_path=temp_worktree)

    assert result["tasks_completed"] == 1
    assert result["tasks_failed"] == 2

    regs = result["regression_errors"]
    # err_a was raised twice across the two failed tasks but must appear once.
    assert len(regs) == 2
    keys = {(e["file"], e["rule"], e["message"]) for e in regs}
    assert ("a.py", "test_failed", "boom") in keys
    assert ("b.py", "test_failed", "kaboom") in keys

    # And per-failed-task results carry their own structured_errors slice.
    failed = [r for r in result["results"] if not r["success"]]
    assert all("structured_errors" in r for r in failed)


def test_run_threads_inbound_regressions_into_each_task(
    mock_config, mock_agent_sdk, mock_prompt, temp_worktree
):
    """When ``run`` is called with ``regressions``, every per-task
    ``implement_feature`` call gets the same context as a kwarg."""
    agent = PythonDeveloperAgent()

    plan_file = _make_plan_file(temp_worktree, ["t1", "t2"])

    inbound = RegressionContext(
        iteration_n=1,
        errors=[_err("tests/test_x.py", 7, "test_failed", "from-iter-1")],
    )

    seen_kwargs: list[dict] = []

    def fake_implement(task, ctx, wt, **kwargs):
        seen_kwargs.append(kwargs)
        return {"success": True, "files_created": [], "files_modified": []}

    with patch.object(agent, "implement_feature", side_effect=fake_implement), \
         patch.object(agent, "validate_config") as validate, \
         patch.object(agent, "run_tests") as run_tests, \
         patch.object(agent, "break_down_plan") as break_down:
        validate.return_value = {"success": True, "output": "", "environment_issue": False}
        run_tests.return_value = {
            "passed": True,
            "test_results": "ok",
            "structured_errors": [],
            "return_code": 0,
        }
        break_down.return_value = ["t1", "t2"]

        agent.run(
            plan_file=plan_file,
            worktree_path=temp_worktree,
            regressions=inbound,
        )

    assert len(seen_kwargs) == 2
    for kwargs in seen_kwargs:
        assert kwargs.get("regressions") is inbound
