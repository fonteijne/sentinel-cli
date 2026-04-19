"""Unit tests for PythonDeveloperAgent."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from src.agents.python_developer import PythonDeveloperAgent


@pytest.fixture
def mock_config():
    """Mock configuration loader."""
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
    """Mock Agent SDK wrapper."""
    with patch("src.agents.base_agent.AgentSDKWrapper") as mock:
        wrapper = Mock()
        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": "Test LLM response",
                "tool_uses": [],
                "session_id": "test-session-123"
            }
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
    """Mock prompt loader."""
    with patch("src.agents.base_agent.load_agent_prompt") as mock:
        mock.return_value = "Python developer system prompt"
        yield mock



@pytest.fixture
def temp_worktree():
    """Create a temporary directory for worktree."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_plan_file(temp_worktree):
    """Create a sample plan file with proper Implementation Steps section."""
    plan_path = temp_worktree / "plan.md"
    plan_content = """# Implementation Plan

## Overview
This is a sample implementation plan.

## Step-by-Step Tasks

### Task 1: Setup
- [ ] Create feature branch
- [ ] Set up test environment

### Task 2: Implementation
- [ ] Write failing tests
- [ ] Implement feature
- [ ] Refactor code

### Task 3: Validation
- [ ] All tests passing

## Completion Checklist
- [ ] Task 1 complete
- [ ] Task 2 complete
- [ ] Task 3 complete
- [ ] All validation checks passed

## Testing Strategy
Some notes about testing.
"""
    plan_path.write_text(plan_content)
    return plan_path


class TestPythonDeveloperAgent:
    """Test suite for PythonDeveloperAgent class."""

    def test_init(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test agent initialization."""
        agent = PythonDeveloperAgent()

        assert agent.agent_name == "python_developer"
        assert agent.model == "claude-4-5-sonnet"
        assert agent.temperature == 0.2
        assert agent.system_prompt is not None

    def test_break_down_plan_basic(
        self, mock_config, mock_agent_sdk, mock_prompt, sample_plan_file
    ):
        """Test breaking down a plan into tasks."""
        agent = PythonDeveloperAgent()

        tasks = agent.break_down_plan(sample_plan_file)

        assert len(tasks) == 6
        assert "Create feature branch" in tasks
        assert "Set up test environment" in tasks
        assert "Write failing tests" in tasks
        assert "Implement feature" in tasks
        assert "Refactor code" in tasks
        assert "All tests passing" in tasks

    def test_break_down_plan_empty_file(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test breaking down an empty plan."""
        agent = PythonDeveloperAgent()

        empty_plan = temp_worktree / "empty.md"
        empty_plan.write_text("")

        tasks = agent.break_down_plan(empty_plan)

        assert tasks == []

    def test_break_down_plan_missing_file(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test breaking down a non-existent plan."""
        agent = PythonDeveloperAgent()

        missing_plan = temp_worktree / "missing.md"

        tasks = agent.break_down_plan(missing_plan)

        assert tasks == []

    def test_break_down_plan_no_checkboxes(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test breaking down a plan with no checklist items."""
        agent = PythonDeveloperAgent()

        plan_path = temp_worktree / "plan.md"
        plan_path.write_text("# Plan\n\nSome text without checkboxes")

        tasks = agent.break_down_plan(plan_path)

        assert tasks == []

    def test_break_down_plan_mixed_content(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that plans without proper section headers return no tasks."""
        agent = PythonDeveloperAgent()

        plan_path = temp_worktree / "plan.md"
        plan_content = """# Plan

Some intro text

- [ ] Task 1
- Regular bullet
- [ ] Task 2

More text

- [ ] Task 3
"""
        plan_path.write_text(plan_content)

        tasks = agent.break_down_plan(plan_path)

        # Without proper "## Step-by-Step Tasks" or "## Implementation Steps" header,
        # should return empty list
        assert len(tasks) == 0

    def test_break_down_plan_extracts_only_implementation_section(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that only tasks from Implementation Steps section are extracted."""
        agent = PythonDeveloperAgent()

        plan_path = temp_worktree / "plan.md"
        plan_content = """# Implementation Plan

## Overview
This is the overview section.

## Step-by-Step Tasks

### Task 1: Setup environment
- [ ] Install dependencies
- [ ] Configure settings

### Task 2: Implement feature
- [ ] Write tests
- [ ] Write code

## Completion Checklist
- [ ] Task 1 complete
- [ ] Task 2 complete
- [ ] All tests pass
- [ ] Documentation updated

## Validation Commands
- [ ] Run pytest
- [ ] Run mypy
"""
        plan_path.write_text(plan_content)

        tasks = agent.break_down_plan(plan_path)

        # Should only extract from "Step-by-Step Tasks" section
        assert len(tasks) == 4
        assert "Install dependencies" in tasks
        assert "Configure settings" in tasks
        assert "Write tests" in tasks
        assert "Write code" in tasks

        # Should NOT extract from Completion Checklist or Validation Commands
        assert "Task 1 complete" not in tasks
        assert "Run pytest" not in tasks

    def test_break_down_plan_implementation_steps_variant(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test extraction works with 'Implementation Steps' header variant."""
        agent = PythonDeveloperAgent()

        plan_path = temp_worktree / "plan.md"
        plan_content = """# Implementation Plan

## Implementation Steps

- [ ] Step 1
- [ ] Step 2
- [ ] Step 3

## Testing
- [ ] Test 1
- [ ] Test 2
"""
        plan_path.write_text(plan_content)

        tasks = agent.break_down_plan(plan_path)

        assert len(tasks) == 3
        assert "Step 1" in tasks
        assert "Step 2" in tasks
        assert "Step 3" in tasks
        assert "Test 1" not in tasks

    def test_implement_feature_with_command(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test implementing a feature loads TDD command and runs Agent SDK."""
        agent = PythonDeveloperAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "run_tests") as mock_tests:
            mock_execute.return_value = {
                "success": True,
                "workflow": [{"name": "write_failing_test"}],
            }
            mock_tests.return_value = {"success": True, "output": "", "return_code": 0}

            result = agent.implement_feature("Add login endpoint", {}, temp_worktree)

            mock_execute.assert_called_once_with(
                "implement-tdd",
                {
                    "feature_description": "Add login endpoint",
                    "plan_step": "Add login endpoint",
                },
            )
            assert result["success"] is True

    def test_implement_feature_command_failure(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test implementing a feature when command fails."""
        agent = PythonDeveloperAgent()

        with patch.object(agent, "execute_command") as mock_execute:
            mock_execute.return_value = {
                "success": False,
                "errors": ["Test error"],
            }

            # Should raise RuntimeError when command validation fails
            with pytest.raises(RuntimeError, match="TDD command validation failed"):
                agent.implement_feature("Add feature", {}, temp_worktree)

            mock_execute.assert_called_once()

    def test_implement_feature_command_exception(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test implementing a feature when command raises exception."""
        agent = PythonDeveloperAgent()

        with patch.object(agent, "execute_command") as mock_execute:
            mock_execute.side_effect = Exception("Command error")

            # Should re-raise the exception
            with pytest.raises(Exception, match="Command error"):
                agent.implement_feature("Add feature", {}, temp_worktree)

    def test_write_tests_creates_file(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test writing tests creates a file."""
        agent = PythonDeveloperAgent()

        test_path = temp_worktree / "tests" / "test_feature.py"
        test_code = agent.write_tests("def feature():\n    pass", test_path)

        assert test_path.exists()
        assert len(test_code) > 0
        assert "import pytest" in test_code

    def test_write_tests_creates_directory(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test writing tests creates parent directory."""
        agent = PythonDeveloperAgent()

        test_path = temp_worktree / "nested" / "tests" / "test_feature.py"

        agent.write_tests("implementation", test_path)

        assert test_path.parent.exists()
        assert test_path.exists()

    def test_write_tests_content(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test generated test content structure."""
        agent = PythonDeveloperAgent()

        test_path = temp_worktree / "test_feature.py"
        test_code = agent.write_tests("implementation", test_path)

        # Check for test structure
        assert "def test_basic_functionality" in test_code
        assert "def test_edge_cases" in test_code
        assert "def test_error_handling" in test_code

    def test_run_tests_success(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test running tests successfully."""
        agent = PythonDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="test_example.py PASSED",
                stderr="",
            )

            result = agent.run_tests(temp_worktree)

            assert result["success"] is True
            assert result["return_code"] == 0
            assert "PASSED" in result["output"]

            mock_run.assert_called_once()
            call_args = mock_run.call_args
            assert call_args[0][0] == ["pytest", "-v", "--tb=short"]
            assert call_args[1]["cwd"] == temp_worktree

    def test_run_tests_failure(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test running tests with failures."""
        agent = PythonDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=1,
                stdout="test_example.py FAILED",
                stderr="AssertionError: Test failed",
            )

            result = agent.run_tests(temp_worktree)

            assert result["success"] is False
            assert result["return_code"] == 1
            assert "FAILED" in result["output"]

    def test_run_tests_timeout(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test running tests with timeout."""
        agent = PythonDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd="pytest", timeout=300
            )

            result = agent.run_tests(temp_worktree)

            assert result["success"] is False
            assert "timed out" in result["output"]
            assert result["return_code"] == -1

    def test_run_tests_exception(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test running tests with general exception."""
        agent = PythonDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_run:
            mock_run.side_effect = Exception("Test execution error")

            result = agent.run_tests(temp_worktree)

            assert result["success"] is False
            assert "Test execution error" in result["output"]
            assert result["return_code"] == -1

    def test_commit_changes_basic(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test committing changes."""
        agent = PythonDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)

            agent.commit_changes(
                "Add feature",
                ["file1.py", "file2.py"],
                temp_worktree,
            )

            # Should call git add for each file and git commit
            assert mock_run.call_count == 3

    def test_commit_changes_stages_files(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that files are staged correctly."""
        agent = PythonDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)

            agent.commit_changes(
                "Commit message",
                ["src/feature.py", "tests/test_feature.py"],
                temp_worktree,
            )

            # Check git add calls
            add_calls = [c for c in mock_run.call_args_list if "add" in c[0][0]]
            assert len(add_calls) == 2

            assert ["git", "add", "src/feature.py"] in [c[0][0] for c in add_calls]
            assert ["git", "add", "tests/test_feature.py"] in [c[0][0] for c in add_calls]

    def test_commit_changes_includes_coauthor(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that commit includes co-author."""
        agent = PythonDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)

            agent.commit_changes("Feature added", ["file.py"], temp_worktree)

            # Check commit call
            commit_calls = [c for c in mock_run.call_args_list if "commit" in c[0][0]]
            assert len(commit_calls) == 1

            commit_args = commit_calls[0][0][0]
            commit_message = commit_args[3]  # -m message

            assert "Feature added" in commit_message
            assert "Co-Authored-By: Claude Sonnet 4.5" in commit_message

    def test_commit_changes_failure(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test commit failure raises exception."""
        agent = PythonDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(1, "git")

            with pytest.raises(subprocess.CalledProcessError):
                agent.commit_changes("Message", ["file.py"], temp_worktree)

    def test_run_complete_workflow(
        self, mock_config, mock_agent_sdk, mock_prompt, sample_plan_file, temp_worktree
    ):
        """Test complete run workflow."""
        agent = PythonDeveloperAgent()

        with patch.object(agent, "implement_feature") as mock_implement, \
             patch.object(agent, "run_tests") as mock_test:

            mock_test.return_value = {
                "success": True,
                "return_code": 0,
                "output": "All tests passed",
            }

            result = agent.run(
                plan_file=sample_plan_file,
                worktree_path=temp_worktree,
            )

            # Check results
            assert "tasks_completed" in result
            assert "tasks_failed" in result
            assert "test_results" in result
            assert "results" in result

            # Verify implementation was attempted for each task
            assert mock_implement.call_count == 6

            # Verify tests were run
            mock_test.assert_called_once_with(temp_worktree)

    def test_run_handles_task_failures(
        self, mock_config, mock_agent_sdk, mock_prompt, sample_plan_file, temp_worktree
    ):
        """Test run handles task implementation failures."""
        agent = PythonDeveloperAgent()

        success_result = {
            "success": True,
            "files_created": [],
            "files_modified": [],
            "test_results": {"success": True},
            "commit_message": "feat: task",
            "agent_response": "",
        }

        with patch.object(agent, "implement_feature") as mock_implement, \
             patch.object(agent, "run_tests") as mock_test:

            # Simulate some failures (6 tasks total)
            mock_implement.side_effect = [
                success_result,  # Success - Create feature branch
                Exception("Implementation error"),  # Failure - Set up test environment
                success_result,  # Success - Write failing tests
                success_result,  # Success - Implement feature
                Exception("Another error"),  # Failure - Refactor code
                success_result,  # Success - All tests passing
            ]

            mock_test.return_value = {"success": True, "return_code": 0, "output": ""}

            result = agent.run(
                plan_file=sample_plan_file,
                worktree_path=temp_worktree,
            )

            assert result["tasks_completed"] == 4
            assert result["tasks_failed"] == 2

            # Check error details in results
            failed_results = [r for r in result["results"] if not r["success"]]
            assert len(failed_results) == 2
            assert "Implementation error" in failed_results[0]["error"]

    def test_run_returns_test_results(
        self, mock_config, mock_agent_sdk, mock_prompt, sample_plan_file, temp_worktree
    ):
        """Test that run returns test results."""
        agent = PythonDeveloperAgent()

        with patch.object(agent, "implement_feature"), \
             patch.object(agent, "run_tests") as mock_test:

            mock_test.return_value = {
                "success": True,
                "return_code": 0,
                "output": "5 passed in 2.5s",
            }

            result = agent.run(
                plan_file=sample_plan_file,
                worktree_path=temp_worktree,
            )

            assert result["test_results"]["success"] is True
            assert "5 passed" in result["test_results"]["output"]

    def test_run_with_empty_plan(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test run with empty plan file."""
        agent = PythonDeveloperAgent()

        empty_plan = temp_worktree / "empty.md"
        empty_plan.write_text("")

        with patch.object(agent, "run_tests") as mock_test:
            mock_test.return_value = {"success": True, "return_code": 0, "output": ""}

            result = agent.run(
                plan_file=empty_plan,
                worktree_path=temp_worktree,
            )

            assert result["tasks_completed"] == 0
            assert result["tasks_failed"] == 0

    def test_run_with_additional_kwargs(
        self, mock_config, mock_agent_sdk, mock_prompt, sample_plan_file, temp_worktree
    ):
        """Test run accepts additional kwargs."""
        agent = PythonDeveloperAgent()

        with patch.object(agent, "implement_feature"), \
             patch.object(agent, "run_tests") as mock_test:

            mock_test.return_value = {"success": True, "return_code": 0, "output": ""}

            # Should not raise exception with extra kwargs
            result = agent.run(
                plan_file=sample_plan_file,
                worktree_path=temp_worktree,
                extra_param="value",
            )

            assert result is not None

    def test_get_test_command(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test Python test command returns pytest."""
        agent = PythonDeveloperAgent()
        cmd = agent._get_test_command()
        assert cmd == ["pytest", "-v", "--tb=short"]

    def test_get_test_stub(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test Python test stub contains pytest imports."""
        agent = PythonDeveloperAgent()
        stub = agent._get_test_stub()
        assert "import pytest" in stub
        assert "def test_basic_functionality" in stub

    def test_build_tdd_prompt(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test Python TDD prompt references pytest and PEP 8."""
        agent = PythonDeveloperAgent()
        prompt = agent._build_tdd_prompt("Add login", {}, temp_worktree)
        assert "pytest" in prompt
        assert "PEP 8" in prompt
        assert "type hints" in prompt
        assert "TASK: Add login" in prompt


class TestFilterOutputFiles:
    """Test _filter_output_files junk and cross-stack filtering."""

    def test_filters_markdown_files(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that .md files are filtered out."""
        agent = PythonDeveloperAgent()
        files = [
            "/app/src/feature.py",
            "/app/TDD_DOCUMENTATION_INDEX.md",
            "/app/README.md",
        ]
        result = agent._filter_output_files(files)
        assert result == ["/app/src/feature.py"]

    def test_filters_txt_files(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that .txt files are filtered out."""
        agent = PythonDeveloperAgent()
        files = [
            "/app/src/service.py",
            "/app/TDD_EXECUTION_SUMMARY_FINAL.txt",
        ]
        result = agent._filter_output_files(files)
        assert result == ["/app/src/service.py"]

    def test_keeps_python_code_files(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that valid Python files are kept."""
        agent = PythonDeveloperAgent()
        files = [
            "/app/src/feature.py",
            "/app/tests/test_feature.py",
            "/app/config.yaml",
            "/app/setup.cfg",
        ]
        result = agent._filter_output_files(files)
        assert result == files

    def test_python_agent_rejects_php_files(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that Python agent rejects PHP/Drupal files (cross-stack)."""
        agent = PythonDeveloperAgent()
        files = [
            "/app/src/feature.py",
            "/app/web/modules/custom/mymod/Handler.php",
            "/app/web/modules/custom/mymod/mymod.module",
        ]
        result = agent._filter_output_files(files)
        assert result == ["/app/src/feature.py"]

    def test_filters_empty_strings(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that empty strings are filtered out."""
        agent = PythonDeveloperAgent()
        files = ["", "/app/src/feature.py", ""]
        result = agent._filter_output_files(files)
        assert result == ["/app/src/feature.py"]

    def test_empty_list(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test with empty file list."""
        agent = PythonDeveloperAgent()
        assert agent._filter_output_files([]) == []

    def test_all_junk_returns_empty(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that all-junk input returns empty list."""
        agent = PythonDeveloperAgent()
        files = [
            "/app/TDD_DOCUMENTATION_INDEX.md",
            "/app/EXECUTION_LOG.txt",
            "/app/SUMMARY.md",
        ]
        result = agent._filter_output_files(files)
        assert result == []
