"""Unit tests for DrupalDeveloperAgent."""

import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from src.agents.drupal_developer import DrupalDeveloperAgent


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
        wrapper.agent_name = "drupal_developer"
        wrapper.model = "claude-4-5-sonnet"
        wrapper.llm_mode = "custom_proxy"
        wrapper.allowed_tools = ["Read", "Write", "Edit", "Grep", "Glob", "Bash"]
        mock.return_value = wrapper
        yield wrapper


@pytest.fixture
def mock_prompt():
    """Mock prompt loader."""
    with patch("src.agents.base_agent.load_agent_prompt") as mock:
        mock.return_value = "Developer system prompt"
        yield mock



@pytest.fixture
def temp_worktree():
    """Create a temporary directory for worktree."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_plan_file(temp_worktree):
    """Create a sample plan file for Drupal."""
    plan_path = temp_worktree / "plan.md"
    plan_content = """# Implementation Plan

## Overview
Implement webform submission handler.

## Step-by-Step Tasks

- [ ] Create hook_webform_submission_presave in webform_hooks.module
- [ ] Add service definition in webform_hooks.services.yml
- [ ] Write PHPUnit test for submission handler

## Validation Commands
- phpcs --standard=Drupal
- vendor/bin/phpunit
"""
    plan_path.write_text(plan_content)
    return plan_path


class TestDrupalDeveloperAgent:
    """Test suite for DrupalDeveloperAgent class."""

    def test_init(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test agent initialization."""
        agent = DrupalDeveloperAgent()

        assert agent.agent_name == "drupal_developer"
        assert agent.model == "claude-4-5-sonnet"
        assert agent.temperature == 0.2

    def test_init_loads_overlay(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that overlay is appended to system prompt."""
        agent = DrupalDeveloperAgent()

        # The overlay should be appended if the file exists
        overlay_path = Path(__file__).parent.parent / "prompts" / "overlays" / "drupal_developer.md"
        if overlay_path.exists():
            assert "Drupal Developer Overlay" in agent.system_prompt

    def test_get_test_command(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test Drupal test command returns phpunit scoped to custom modules.

        Path-based scope (web/modules/custom) instead of --testsuite=unit
        so contrib-test autoload errors (e.g. honeypot referencing rules
        classes that aren't installed) don't kill the verifier on
        every task.
        """
        agent = DrupalDeveloperAgent()
        cmd = agent._get_test_command()

        assert cmd == [
            "vendor/bin/phpunit",
            "web/modules/custom",
            "--no-coverage",
            "--log-junit=/tmp/phpunit-junit.xml",
        ]

    def test_get_test_command_with_paths(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """When paths are given, phpunit runs against just those files."""
        agent = DrupalDeveloperAgent()
        cmd = agent._get_test_command(
            paths=["web/modules/custom/foo/tests/src/Unit/FooTest.php"]
        )

        assert cmd == [
            "vendor/bin/phpunit",
            "web/modules/custom/foo/tests/src/Unit/FooTest.php",
            "--no-coverage",
            "--log-junit=/tmp/phpunit-junit.xml",
        ]

    def test_get_test_command_empty_paths_falls_back(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Empty paths list falls back to broad scope, like None."""
        agent = DrupalDeveloperAgent()
        cmd = agent._get_test_command(paths=[])

        assert cmd == [
            "vendor/bin/phpunit",
            "web/modules/custom",
            "--no-coverage",
            "--log-junit=/tmp/phpunit-junit.xml",
        ]

    def test_get_test_command_is_not_pytest(
        self, mock_config, mock_agent_sdk, mock_prompt    ):
        """Test Drupal test command does NOT return pytest."""
        agent = DrupalDeveloperAgent()
        cmd = agent._get_test_command()

        assert "pytest" not in cmd

    def test_get_test_stub(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test Drupal test stub is PHP, not Python."""
        agent = DrupalDeveloperAgent()
        stub = agent._get_test_stub()

        assert "<?php" in stub
        assert "UnitTestCase" in stub
        assert "public function testBasicFunctionality" in stub
        # Must NOT contain Python
        assert "import pytest" not in stub
        assert "def test_" not in stub

    def test_build_tdd_prompt_references_phpunit(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test Drupal TDD prompt references PHPUnit and Drupal concepts."""
        agent = DrupalDeveloperAgent()
        prompt = agent._build_tdd_prompt("Implement webform handler", {}, temp_worktree)

        assert "PHPUnit" in prompt
        assert "Drupal" in prompt
        assert "drush cr" in prompt
        assert "TASK: Implement webform handler" in prompt

    def test_build_tdd_prompt_no_python_references(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test Drupal TDD prompt does NOT reference Python concepts."""
        agent = DrupalDeveloperAgent()
        prompt = agent._build_tdd_prompt("Add feature", {}, temp_worktree)

        assert "pytest" not in prompt
        assert "PEP 8" not in prompt
        assert "type hints" not in prompt.lower() or "type hint" not in prompt.lower()

    def test_build_tdd_prompt_includes_di_guidance(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test Drupal TDD prompt includes dependency injection guidance."""
        agent = DrupalDeveloperAgent()
        prompt = agent._build_tdd_prompt("Add service", {}, temp_worktree)

        assert "dependency injection" in prompt.lower()
        assert "\\Drupal::service()" in prompt

    def test_run_tests_uses_phpunit(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that run_tests calls phpunit, not pytest."""
        agent = DrupalDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=0,
                stdout="OK (3 tests, 5 assertions)",
                stderr="",
            )

            result = agent.run_tests(temp_worktree)

            assert result["passed"] is True
            call_args = mock_run.call_args
            assert call_args[0][0] == [
                "vendor/bin/phpunit",
                "web/modules/custom",
                "--no-coverage",
                "--log-junit=/tmp/phpunit-junit.xml",
            ]
            assert call_args[1]["cwd"] == temp_worktree

    def test_run_tests_failure(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test running Drupal tests with failures."""
        agent = DrupalDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_run:
            mock_run.return_value = Mock(
                returncode=1,
                stdout="FAILURES!\nTests: 3, Assertions: 4, Failures: 1.",
                stderr="",
            )

            result = agent.run_tests(temp_worktree)

            assert result["passed"] is False
            assert result["return_code"] == 1

    def test_write_tests_creates_php_file(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that write_tests creates a PHP test file."""
        agent = DrupalDeveloperAgent()

        test_path = temp_worktree / "tests" / "src" / "Unit" / "BasicTest.php"
        test_code = agent.write_tests("implementation", test_path)

        assert test_path.exists()
        assert "<?php" in test_code
        assert "UnitTestCase" in test_code
        assert "import pytest" not in test_code

    def test_run_complete_workflow(
        self, mock_config, mock_agent_sdk, mock_prompt,
        sample_plan_file, temp_worktree
    ):
        """Test complete run workflow for Drupal."""
        agent = DrupalDeveloperAgent()

        with patch.object(agent, "implement_feature") as mock_implement, \
             patch.object(agent, "run_tests") as mock_test:

            mock_test.return_value = {
                "passed": True,
                "return_code": 0,
                "test_results": "OK (3 tests, 5 assertions)",
                "structured_errors": [],
            }

            result = agent.run(
                plan_file=sample_plan_file,
                worktree_path=temp_worktree,
            )

            assert "tasks_completed" in result
            assert "tasks_failed" in result
            assert "test_results" in result

            # Should attempt to implement each task
            assert mock_implement.call_count == 3

    def test_implement_feature_with_command(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test implementing a feature loads TDD command and runs Agent SDK."""
        agent = DrupalDeveloperAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "run_tests") as mock_tests:
            mock_execute.return_value = {
                "success": True,
                "workflow": [{"name": "write_failing_test"}],
            }
            mock_tests.return_value = {
                "passed": True,
                "test_results": "",
                "structured_errors": [],
                "return_code": 0,
            }

            result = agent.implement_feature("Add form handler", {}, temp_worktree)

            mock_execute.assert_called_once_with(
                "implement-tdd",
                {
                    "feature_description": "Add form handler",
                    "plan_step": "Add form handler",
                },
            )
            assert result["success"] is True


class TestContainerAwareTests:
    """Test container-aware test execution for Drupal projects."""

    def test_set_environment_stores_values(
        self, mock_config, mock_agent_sdk, mock_prompt    ):
        """Test that set_environment stores env_manager and ticket_id."""
        agent = DrupalDeveloperAgent()
        mock_env_mgr = Mock()

        agent.set_environment(mock_env_mgr, "TEST-123")

        assert agent._env_manager is mock_env_mgr
        assert agent._env_ticket_id == "TEST-123"

    def test_run_tests_uses_container_when_env_set(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that run_tests uses container exec when environment is attached."""
        agent = DrupalDeveloperAgent()
        mock_env_mgr = Mock()
        # All exec calls succeed (phpunit exists, config exists, suite found, tests pass)
        mock_env_mgr.exec.return_value = Mock(
            success=True,
            stdout="OK (3 tests, 5 assertions)",
            stderr="",
            returncode=0,
        )

        agent.set_environment(mock_env_mgr, "TEST-123")

        with patch("src.agents.base_developer.subprocess.run") as mock_subprocess:
            result = agent.run_tests(temp_worktree)

            calls = mock_env_mgr.exec.call_args_list
            # Calls: composer install, phpunit.xml check, grep testsuite, actual test run
            assert len(calls) >= 3

            # First call: ensure composer deps (phpunit binary is part of vendor/)
            assert calls[0].kwargs["command"] == [
                "composer", "install", "--no-interaction", "--no-progress"
            ]
            assert calls[0].kwargs["workdir"] == "/app"

            # Second call: check if phpunit.xml exists
            assert calls[1].kwargs["command"] == [
                "sh", "-c", "test -f phpunit.xml || test -f phpunit.xml.dist"
            ]

            # Last call: actual test execution, scoped to web/modules/custom
            # to avoid contrib-test autoload pollution (e.g. honeypot
            # referencing rules classes that aren't installed).
            assert calls[-1].kwargs["command"] == [
                "vendor/bin/phpunit",
                "web/modules/custom",
                "--no-coverage",
                "--log-junit=/tmp/phpunit-junit.xml",
            ]
            assert calls[-1].kwargs["workdir"] == "/app"

            # Host subprocess should NOT be called
            mock_subprocess.assert_not_called()

            assert result["passed"] is True
            assert result["return_code"] == 0

    def test_run_tests_falls_back_to_host_without_env(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that run_tests uses subprocess when no environment is attached."""
        agent = DrupalDeveloperAgent()

        with patch("src.agents.base_developer.subprocess.run") as mock_subprocess:
            mock_subprocess.return_value = Mock(
                returncode=0,
                stdout="OK (3 tests, 5 assertions)",
                stderr="",
            )

            result = agent.run_tests(temp_worktree)

            # Host subprocess should be called
            mock_subprocess.assert_called_once()
            call_args = mock_subprocess.call_args
            assert call_args[0][0] == [
                "vendor/bin/phpunit",
                "web/modules/custom",
                "--no-coverage",
                "--log-junit=/tmp/phpunit-junit.xml",
            ]
            assert call_args[1]["cwd"] == temp_worktree

            assert result["passed"] is True

    def test_run_tests_container_failure_returns_gracefully(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that container exec failures are handled gracefully."""
        agent = DrupalDeveloperAgent()
        mock_env_mgr = Mock()
        mock_env_mgr.exec.side_effect = RuntimeError("No active environment for TEST-123")

        agent.set_environment(mock_env_mgr, "TEST-123")

        result = agent.run_tests(temp_worktree)

        assert result["passed"] is False
        assert result["return_code"] == -1
        assert "No active environment" in result["test_results"]

    def test_run_tests_container_test_failure(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that failing tests in container are reported correctly."""
        agent = DrupalDeveloperAgent()
        mock_env_mgr = Mock()
        ok = Mock(success=True, stdout="", stderr="", returncode=0)
        # Calls: composer install (ensure deps), phpunit.xml exists,
        # actual test run (fails). Note: no testsuite-grep step now —
        # the Drupal command is path-scoped, not testsuite-based, so
        # _resolve_test_cmd_for_container skips that check.
        mock_env_mgr.exec.side_effect = [
            ok,  # composer install
            ok,  # phpunit.xml exists
            Mock(
                success=False,
                stdout="FAILURES!\nTests: 3, Assertions: 4, Failures: 1.",
                stderr="",
                returncode=1,
            ),
        ]

        agent.set_environment(mock_env_mgr, "TEST-123")

        result = agent.run_tests(temp_worktree)

        assert result["passed"] is False
        assert result["return_code"] == 1
        assert "FAILURES!" in result["test_results"]

    def test_run_tests_skips_when_no_config(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that tests are skipped gracefully when phpunit.xml is missing."""
        agent = DrupalDeveloperAgent()
        mock_env_mgr = Mock()
        ok = Mock(success=True, stdout="", stderr="", returncode=0)
        fail = Mock(success=False, stdout="", stderr="", returncode=1)
        # Calls: phpunit exists, phpunit.xml NOT found → skip
        mock_env_mgr.exec.side_effect = [
            ok,    # phpunit exists
            fail,  # phpunit.xml does NOT exist
        ]

        agent.set_environment(mock_env_mgr, "TEST-123")

        result = agent.run_tests(temp_worktree)

        # Should return success with skip message (no actual test run)
        assert result["passed"] is True
        assert "skipping" in result["test_results"].lower()
        assert result["return_code"] == 0
        # Only 2 exec calls — no test execution attempted
        assert mock_env_mgr.exec.call_count == 2

    # NOTE: previous test ``test_run_tests_strips_testsuite_when_suite_undefined``
    # was retired alongside switching the Drupal test command from
    # ``--testsuite=unit`` to a path-based scope (``web/modules/custom``).
    # The base-developer helper ``_resolve_test_cmd_for_container`` still
    # contains the strip-when-undefined logic for any future agent that
    # constructs a testsuite-based command, but this agent no longer
    # exercises that branch.


class TestEnvironmentContextInjection:
    """Test environment context injection into system prompt."""

    def test_init_injects_environment_context(
        self, mock_agent_sdk, mock_prompt
    ):
        """Test that config environment values replace {{ }} placeholders."""
        with patch("src.agents.base_agent.get_config") as mock_get_config:
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
            env_data = {
                "core_version": "11.1.3",
                "php_version": "8.3",
                "local_dev": "Lando",
                "key_contrib": "paragraphs, webform",
                "theme": "custom Starterkit-based",
                "ci_pipeline": "GitLab CI",
                "compliance": "GDPR, WCAG 2.2 AA",
            }

            def config_get_side_effect(key, default=None):
                if key == "agents.drupal_developer.environment":
                    return env_data
                if key == "agent_sdk.default_tools":
                    return ["Read", "Grep", "Glob"]
                if key == "agent_sdk.auto_edits":
                    return True
                return default

            config.get.side_effect = config_get_side_effect
            mock_get_config.return_value = config

            agent = DrupalDeveloperAgent()

            assert "11.1.3" in agent.system_prompt
            assert "8.3" in agent.system_prompt
            assert "Lando" in agent.system_prompt
            assert "paragraphs, webform" in agent.system_prompt
            assert "{{ core_version }}" not in agent.system_prompt
            assert "{{ php_version }}" not in agent.system_prompt

    def test_init_handles_missing_environment_config(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test agent initializes without error when no environment config exists."""
        agent = DrupalDeveloperAgent()

        assert agent.agent_name == "drupal_developer"
        overlay_path = Path(__file__).parent.parent / "prompts" / "overlays" / "drupal_developer.md"
        if overlay_path.exists():
            assert "Drupal Developer Overlay" in agent.system_prompt


class TestDrupalFilterOutputFiles:
    """Test per-stack allowlist filtering for Drupal projects."""

    def test_keeps_drupal_files(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that valid Drupal/PHP files are kept."""
        agent = DrupalDeveloperAgent()
        files = [
            "/app/web/modules/custom/mymod/mymod.module",
            "/app/web/modules/custom/mymod/src/Service/Handler.php",
            "/app/web/modules/custom/mymod/mymod.services.yml",
            "/app/web/modules/custom/mymod/templates/block.html.twig",
            "/app/web/modules/custom/mymod/mymod.install",
            "/app/web/themes/custom/mytheme/mytheme.theme",
            "/app/web/modules/custom/mymod/js/script.js",
            "/app/web/modules/custom/mymod/css/style.css",
        ]
        result = agent._filter_output_files(files)
        assert result == files

    def test_rejects_python_files(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that .py files are rejected in a Drupal project."""
        agent = DrupalDeveloperAgent()
        files = [
            "/app/web/modules/custom/mymod/src/Service/Handler.php",
            "/app/validate_pricing_block_library.py",
            "/app/test_runner.py",
        ]
        result = agent._filter_output_files(files)
        assert result == ["/app/web/modules/custom/mymod/src/Service/Handler.php"]

    def test_rejects_junk_and_python_combined(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test mixed junk + cross-stack files are all filtered."""
        agent = DrupalDeveloperAgent()
        files = [
            "/app/web/modules/custom/mymod/tests/src/Unit/HandlerTest.php",
            "/app/PRICING_BLOCK_JAVASCRIPT_TDD_IMPLEMENTATION.md",
            "/app/TDD_SUMMARY.txt",
            "/app/validate_pricing_block_library.py",
            "/app/web/modules/custom/mymod/mymod.module",
        ]
        result = agent._filter_output_files(files)
        assert result == [
            "/app/web/modules/custom/mymod/tests/src/Unit/HandlerTest.php",
            "/app/web/modules/custom/mymod/mymod.module",
        ]

    def test_rejects_markdown_files(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that .md files are still rejected by blocklist."""
        agent = DrupalDeveloperAgent()
        files = [
            "/app/web/modules/custom/mymod/mymod.module",
            "/app/TDD_DOCUMENTATION.md",
        ]
        result = agent._filter_output_files(files)
        assert result == ["/app/web/modules/custom/mymod/mymod.module"]


class TestChangedFilesScopedVerifier:
    """Tests for the per-task changed-files scope on phpunit.

    These exercise the helpers added to ``BaseDeveloperAgent``
    (``_capture_pretask_sha``, ``_derive_changed_test_paths``,
    ``_infer_module_test_dirs``) plus the integration through
    ``run_tests``. The fixture below builds a tiny git repo with a
    Drupal-shaped module so the helpers' fs walks can be exercised
    without mocking pathlib.
    """

    @pytest.fixture
    def drupal_worktree(self, tmp_path):
        """Init a git repo, create a Drupal module skeleton, return the path.

        The worktree starts with a baseline commit so callers can
        produce diffs against ``HEAD``. Test functions write further
        files and commit them; the SHA captured before those edits is
        the simulated pretask SHA.
        """
        subprocess.run(
            ["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=tmp_path, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"],
            cwd=tmp_path, check=True,
        )

        module = tmp_path / "web" / "modules" / "custom" / "foo"
        (module / "tests" / "src" / "Unit").mkdir(parents=True)
        (module / "foo.info.yml").write_text(
            "name: Foo\ntype: module\ncore_version_requirement: ^11\n"
        )
        (module / "foo.module").write_text("<?php\n// initial\n")
        (module / "tests" / "src" / "Unit" / "FooTest.php").write_text(
            "<?php\nclass FooTest {}\n"
        )

        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "baseline"], cwd=tmp_path, check=True
        )
        return tmp_path

    def test_capture_pretask_sha_returns_head(
        self, mock_config, mock_agent_sdk, mock_prompt, drupal_worktree
    ):
        agent = DrupalDeveloperAgent()
        sha = agent._capture_pretask_sha(drupal_worktree)
        assert sha is not None
        assert len(sha) == 40

    def test_capture_pretask_sha_returns_none_on_non_git(
        self, mock_config, mock_agent_sdk, mock_prompt, tmp_path
    ):
        """Fresh non-git dir → None → caller falls back to broad scope."""
        agent = DrupalDeveloperAgent()
        assert agent._capture_pretask_sha(tmp_path) is None

    def test_derive_changed_test_paths_picks_up_changed_test(
        self, mock_config, mock_agent_sdk, mock_prompt, drupal_worktree
    ):
        agent = DrupalDeveloperAgent()
        sha = agent._capture_pretask_sha(drupal_worktree)

        new_test = (
            drupal_worktree
            / "web/modules/custom/foo/tests/src/Unit/BarTest.php"
        )
        new_test.write_text("<?php\nclass BarTest {}\n")
        subprocess.run(["git", "add", "."], cwd=drupal_worktree, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "add bar test"],
            cwd=drupal_worktree, check=True,
        )

        paths = agent._derive_changed_test_paths(drupal_worktree, sha)
        assert paths == [
            "web/modules/custom/foo/tests/src/Unit/BarTest.php"
        ]

    def test_derive_changed_test_paths_implementation_only_infers_tests_dir(
        self, mock_config, mock_agent_sdk, mock_prompt, drupal_worktree
    ):
        """Impl file changed, no test changed → infer module's tests/ dir."""
        agent = DrupalDeveloperAgent()
        sha = agent._capture_pretask_sha(drupal_worktree)

        impl = drupal_worktree / "web/modules/custom/foo/foo.module"
        impl.write_text("<?php\n// changed\n")
        subprocess.run(["git", "add", "."], cwd=drupal_worktree, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "tweak module"],
            cwd=drupal_worktree, check=True,
        )

        paths = agent._derive_changed_test_paths(drupal_worktree, sha)
        assert paths == ["web/modules/custom/foo/tests"]

    def test_derive_changed_test_paths_no_sha_returns_empty(
        self, mock_config, mock_agent_sdk, mock_prompt, drupal_worktree
    ):
        agent = DrupalDeveloperAgent()
        assert agent._derive_changed_test_paths(drupal_worktree, None) == []

    def test_derive_changed_test_paths_no_diff_returns_empty(
        self, mock_config, mock_agent_sdk, mock_prompt, drupal_worktree
    ):
        """SHA == HEAD → no changes → fallback to broad scope."""
        agent = DrupalDeveloperAgent()
        sha = agent._capture_pretask_sha(drupal_worktree)
        assert agent._derive_changed_test_paths(drupal_worktree, sha) == []

    def test_run_tests_uses_changed_paths_in_container(
        self, mock_config, mock_agent_sdk, mock_prompt, drupal_worktree
    ):
        """run_tests with pretask_sha runs phpunit against just the diff."""
        agent = DrupalDeveloperAgent()
        sha = agent._capture_pretask_sha(drupal_worktree)

        new_test = (
            drupal_worktree
            / "web/modules/custom/foo/tests/src/Unit/NewTest.php"
        )
        new_test.write_text("<?php\nclass NewTest {}\n")
        subprocess.run(["git", "add", "."], cwd=drupal_worktree, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "new test"],
            cwd=drupal_worktree, check=True,
        )

        mock_env_mgr = Mock()
        ok = Mock(success=True, stdout="", stderr="", returncode=0)
        mock_env_mgr.exec.side_effect = [
            ok,  # composer install
            ok,  # phpunit.xml exists
            Mock(
                success=True,
                stdout="OK (1 test)",
                stderr="",
                returncode=0,
            ),
        ]
        agent.set_environment(mock_env_mgr, "TEST-1")

        result = agent.run_tests(drupal_worktree, pretask_sha=sha)

        last_cmd = mock_env_mgr.exec.call_args_list[-1].kwargs["command"]
        assert last_cmd == [
            "vendor/bin/phpunit",
            "web/modules/custom/foo/tests/src/Unit/NewTest.php",
            "--no-coverage",
            "--log-junit=/tmp/phpunit-junit.xml",
        ]
        assert result["passed"] is True

    def test_run_tests_falls_back_when_no_diff(
        self, mock_config, mock_agent_sdk, mock_prompt, drupal_worktree
    ):
        """pretask_sha set but no test files changed → broad scope."""
        agent = DrupalDeveloperAgent()
        sha = agent._capture_pretask_sha(drupal_worktree)

        mock_env_mgr = Mock()
        ok = Mock(success=True, stdout="", stderr="", returncode=0)
        mock_env_mgr.exec.side_effect = [
            ok,  # composer install
            ok,  # phpunit.xml exists
            Mock(
                success=True,
                stdout="OK",
                stderr="",
                returncode=0,
            ),
        ]
        agent.set_environment(mock_env_mgr, "TEST-1")

        agent.run_tests(drupal_worktree, pretask_sha=sha)

        last_cmd = mock_env_mgr.exec.call_args_list[-1].kwargs["command"]
        assert last_cmd == [
            "vendor/bin/phpunit",
            "web/modules/custom",
            "--no-coverage",
            "--log-junit=/tmp/phpunit-junit.xml",
        ]

    def test_run_tests_no_pretask_sha_uses_broad_scope(
        self, mock_config, mock_agent_sdk, mock_prompt, drupal_worktree
    ):
        """Backwards-compat: run_tests() without pretask_sha keeps the old
        broad-scope behavior."""
        agent = DrupalDeveloperAgent()
        mock_env_mgr = Mock()
        ok = Mock(success=True, stdout="", stderr="", returncode=0)
        mock_env_mgr.exec.side_effect = [ok, ok, ok]
        agent.set_environment(mock_env_mgr, "TEST-1")

        agent.run_tests(drupal_worktree)

        last_cmd = mock_env_mgr.exec.call_args_list[-1].kwargs["command"]
        assert last_cmd == [
            "vendor/bin/phpunit",
            "web/modules/custom",
            "--no-coverage",
            "--log-junit=/tmp/phpunit-junit.xml",
        ]

    def test_infer_module_test_dirs_skips_files_outside_modules(
        self, mock_config, mock_agent_sdk, mock_prompt, drupal_worktree
    ):
        """Files not under any module root produce no inferred test dirs."""
        agent = DrupalDeveloperAgent()
        # Path that isn't under any *.info.yml-bearing directory.
        result = agent._infer_module_test_dirs(
            drupal_worktree, ["docs/README.md"]
        )
        assert result == []

    def test_infer_module_test_dirs_skips_module_without_tests_dir(
        self, mock_config, mock_agent_sdk, mock_prompt, drupal_worktree
    ):
        """Module without a tests/ subdir → nothing inferred."""
        agent = DrupalDeveloperAgent()
        # Make a second module that has *no* tests/.
        bare = (
            drupal_worktree / "web" / "modules" / "custom" / "bare"
        )
        bare.mkdir(parents=True)
        (bare / "bare.info.yml").write_text("name: Bare\ntype: module\n")
        (bare / "bare.module").write_text("<?php\n")

        result = agent._infer_module_test_dirs(
            drupal_worktree, ["web/modules/custom/bare/bare.module"]
        )
        assert result == []
