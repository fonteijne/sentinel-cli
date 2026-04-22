"""Unit tests for DrupalReviewerAgent."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from src.agents.drupal_reviewer import DrupalReviewerAgent


@pytest.fixture
def mock_config():
    """Mock configuration loader."""
    with patch("src.agents.base_agent.get_config") as mock:
        config = Mock()
        config.get_agent_config.return_value = {
            "model": "claude-4-5-sonnet",
            "temperature": 0.1,
        }
        config.get_llm_config.return_value = {
            "mode": "custom_proxy",
            "api_key": "test-api-key",
            "base_url": "https://test.api.com/v1",
        }
        config.get.return_value = ["Read", "Grep", "Glob"]
        config.get_project_config.return_value = {"default_branch": "main"}
        mock.return_value = config
        yield config


@pytest.fixture
def mock_agent_sdk():
    """Mock Agent SDK wrapper."""
    with patch("src.agents.base_agent.AgentSDKWrapper") as mock:
        wrapper = Mock()
        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": "Review response",
                "tool_uses": [],
                "session_id": "test-session-123",
            }
        wrapper.execute_with_tools = mock_execute
        wrapper.set_project = Mock()
        wrapper.agent_name = "drupal_reviewer"
        wrapper.model = "claude-4-5-sonnet"
        wrapper.llm_mode = "custom_proxy"
        wrapper.allowed_tools = ["Read", "Grep", "Glob", "Bash(git *)"]
        mock.return_value = wrapper
        yield wrapper


@pytest.fixture
def mock_prompt():
    """Mock prompt loader."""
    with patch("src.agents.base_agent.load_agent_prompt") as mock:
        mock.return_value = "Drupal reviewer system prompt"
        yield mock


@pytest.fixture
def temp_worktree():
    """Create a temporary directory for worktree."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestDrupalReviewerInit:
    """Test DrupalReviewerAgent initialization."""

    def test_init(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test agent initialization sets correct properties."""
        agent = DrupalReviewerAgent()

        assert agent.agent_name == "drupal_reviewer"
        assert agent.model == "claude-4-5-sonnet"
        assert agent.temperature == 0.1
        assert agent.veto_power is True

    def test_init_loads_overlay(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that overlay is appended to system prompt."""
        agent = DrupalReviewerAgent()

        overlay_path = Path(__file__).parent.parent / "prompts" / "overlays" / "drupal_reviewer.md"
        if overlay_path.exists():
            assert "DrupalSentinel" in agent.system_prompt

    def test_init_injects_environment_context(self, mock_agent_sdk, mock_prompt):
        """Test that config environment values replace {{ }} placeholders."""
        with patch("src.agents.base_agent.get_config") as mock_get_config:
            config = Mock()
            config.get_agent_config.return_value = {
                "model": "claude-4-5-sonnet",
                "temperature": 0.1,
            }
            config.get_llm_config.return_value = {
                "mode": "custom_proxy",
                "api_key": "test-api-key",
                "base_url": "https://test.api.com/v1",
            }
            env_data = {
                "core_version": "11.1.3",
                "php_version": "8.3",
                "hosting": "Lando",
                "shell": "fish",
                "key_contrib": "paragraphs, webform",
                "ci_pipeline": "GitLab CI",
                "compliance": "GDPR, WCAG 2.2 AA",
            }

            def config_get_side_effect(key, default=None):
                if key == "agents.drupal_reviewer.environment":
                    return env_data
                if key == "agent_sdk.default_tools":
                    return ["Read", "Grep", "Glob"]
                if key == "agent_sdk.auto_edits":
                    return True
                return default

            config.get.side_effect = config_get_side_effect
            config.get_project_config.return_value = {"default_branch": "main"}
            mock_get_config.return_value = config

            agent = DrupalReviewerAgent()

            assert "11.1.3" in agent.system_prompt
            assert "8.3" in agent.system_prompt
            assert "Lando" in agent.system_prompt
            assert "{{ core_version }}" not in agent.system_prompt
            assert "{{ php_version }}" not in agent.system_prompt

    def test_init_handles_missing_environment_config(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test agent initializes without error when no environment config exists."""
        agent = DrupalReviewerAgent()

        assert agent.agent_name == "drupal_reviewer"


class TestParseReviewResponse:
    """Test LLM response parsing."""

    def test_parse_valid_handover_json(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test parsing response with valid handover JSON."""
        agent = DrupalReviewerAgent()

        handover = {
            "verdict": "REQUEST_CHANGES",
            "reviewer": "DrupalSentinel",
            "target_agent": "drupal_developer",
            "metrics": {
                "blockers": 1,
                "majors": 0,
                "minors": 2,
                "nits": 1,
                "questions": 0,
                "praise": 1,
            },
            "findings": [
                {
                    "id": "F-001",
                    "severity": "BLOCKER",
                    "category": "di",
                    "file": "web/modules/custom/example/src/Controller/ExampleController.php",
                    "line": 42,
                    "title": "Global service wrapper in Controller",
                    "blocking": True,
                }
            ],
        }

        response = f"## 1. Verdict\n`REQUEST_CHANGES`\n\n## 8. Handover\n```json\n{json.dumps(handover, indent=2)}\n```"

        result = agent._parse_review_response(response)

        assert result["verdict"] == "REQUEST_CHANGES"
        assert result["metrics"]["blockers"] == 1
        assert len(result["findings"]) == 1
        assert result["findings"][0]["severity"] == "BLOCKER"

    def test_parse_malformed_json_falls_back(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test fallback parsing when JSON is malformed."""
        agent = DrupalReviewerAgent()

        response = """## 1. Verdict
`REQUEST_CHANGES`

## 3. Findings

### [BLOCKER] Missing DI in Controller
- **ID:** `F-001`
- **File:** `web/modules/custom/mymod/src/Controller/MyController.php:25`
- **Category:** `di`

### [MAJOR] Missing cache metadata
- **ID:** `F-002`
- **File:** `web/modules/custom/mymod/src/Plugin/Block/MyBlock.php:60`

### [PRAISE] Good service structure
- **ID:** `F-003`
- **File:** `web/modules/custom/mymod/src/Service/MyService.php:10`

## 8. Handover
```json
{invalid json here}
```"""

        result = agent._parse_review_response(response)

        assert result["verdict"] == "REQUEST_CHANGES"
        assert result["metrics"]["blockers"] >= 1
        assert result["metrics"]["majors"] >= 1
        assert result["metrics"]["praise"] >= 1
        assert len(result["findings"]) >= 2

    def test_parse_extracts_file_and_line(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test that file:line is parsed correctly in fallback mode."""
        agent = DrupalReviewerAgent()

        response = """## 1. Verdict
`APPROVE`

## 3. Findings

### [MINOR] Consider using typed properties
- **File:** `web/modules/custom/mymod/src/Service/Handler.php:15`
"""

        result = agent._fallback_parse(response)

        finding = result["findings"][0]
        assert finding["file"] == "web/modules/custom/mymod/src/Service/Handler.php"
        assert finding["line"] == 15


class TestApproveOrVeto:
    """Test verdict/approval logic."""

    def test_approve_no_blockers_no_majors(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test approval when no blockers or majors."""
        agent = DrupalReviewerAgent()

        review_data = {
            "verdict": "APPROVE",
            "metrics": {"blockers": 0, "majors": 0, "minors": 3, "nits": 2},
        }

        assert agent.approve_or_veto(review_data) is True

    def test_veto_on_blocker(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test veto when blockers present."""
        agent = DrupalReviewerAgent()

        review_data = {
            "verdict": "REQUEST_CHANGES",
            "metrics": {"blockers": 1, "majors": 0, "minors": 0, "nits": 0},
        }

        assert agent.approve_or_veto(review_data) is False

    def test_veto_on_major(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test veto when majors present."""
        agent = DrupalReviewerAgent()

        review_data = {
            "verdict": "REQUEST_CHANGES",
            "metrics": {"blockers": 0, "majors": 2, "minors": 0, "nits": 0},
        }

        assert agent.approve_or_veto(review_data) is False

    def test_comment_only_is_non_blocking(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test COMMENT_ONLY verdict is treated as non-blocking pass."""
        agent = DrupalReviewerAgent()

        review_data = {
            "verdict": "COMMENT_ONLY",
            "metrics": {"blockers": 0, "majors": 0},
        }

        assert agent.approve_or_veto(review_data) is True


class TestGetChangedFiles:
    """Test git diff file detection."""

    def test_get_changed_files_success(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test successful git diff returns changed Drupal files."""
        agent = DrupalReviewerAgent()

        php_file = temp_worktree / "web" / "modules" / "custom" / "mymod" / "src" / "Service" / "Handler.php"
        php_file.parent.mkdir(parents=True, exist_ok=True)
        php_file.write_text("<?php\n")

        module_file = temp_worktree / "web" / "modules" / "custom" / "mymod" / "mymod.module"
        module_file.write_text("<?php\n")

        with patch("src.agents.drupal_reviewer.subprocess.run") as mock_run:
            mock_run.side_effect = [
                Mock(returncode=0, stdout="abc123\n", stderr=""),
                Mock(
                    returncode=0,
                    stdout="web/modules/custom/mymod/src/Service/Handler.php\nweb/modules/custom/mymod/mymod.module\n",
                    stderr="",
                ),
            ]

            files = agent._get_changed_files(temp_worktree)

            assert files is not None
            assert len(files) == 2

    def test_get_changed_files_git_failure(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test None returned when git fails."""
        agent = DrupalReviewerAgent()

        with patch("src.agents.drupal_reviewer.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="fatal: not a git repo")

            files = agent._get_changed_files(temp_worktree)

            assert files is None

    def test_get_changed_files_filters_non_drupal(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that non-Drupal files are filtered out."""
        agent = DrupalReviewerAgent()

        php_file = temp_worktree / "src" / "Handler.php"
        php_file.parent.mkdir(parents=True, exist_ok=True)
        php_file.write_text("<?php\n")

        py_file = temp_worktree / "script.py"
        py_file.write_text("print('hello')\n")

        with patch("src.agents.drupal_reviewer.subprocess.run") as mock_run:
            mock_run.side_effect = [
                Mock(returncode=0, stdout="abc123\n", stderr=""),
                Mock(
                    returncode=0,
                    stdout="src/Handler.php\nscript.py\n",
                    stderr="",
                ),
            ]

            files = agent._get_changed_files(temp_worktree)

            assert files is not None
            assert len(files) == 1
            assert files[0].suffix == ".php"


class TestProvideFeedback:
    """Test feedback generation."""

    def test_feedback_groups_by_severity(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test that findings are grouped correctly in feedback."""
        agent = DrupalReviewerAgent()

        findings = [
            {"severity": "BLOCKER", "id": "F-001", "title": "Missing DI", "file": "Controller.php", "line": 10},
            {"severity": "MAJOR", "id": "F-002", "title": "No cache metadata", "file": "Block.php", "line": 20},
            {"severity": "MINOR", "id": "F-003", "title": "Missing docblock", "file": "Service.php", "line": 5},
        ]

        feedback = agent.provide_feedback(findings)

        assert any("BLOCKER" in line for line in feedback)
        assert any("MAJOR" in line for line in feedback)
        assert any("MINOR" in line for line in feedback)

    def test_feedback_empty_findings(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test empty feedback for no findings."""
        agent = DrupalReviewerAgent()

        feedback = agent.provide_feedback([])

        assert feedback == []


class TestRunWorkflow:
    """Test complete run workflow."""

    def test_run_approve_workflow(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test complete run that results in approval."""
        agent = DrupalReviewerAgent()

        handover = {
            "verdict": "APPROVE",
            "reviewer": "DrupalSentinel",
            "target_agent": "drupal_developer",
            "metrics": {"blockers": 0, "majors": 0, "minors": 1, "nits": 2, "questions": 0, "praise": 2},
            "findings": [
                {"id": "F-001", "severity": "MINOR", "file": "Handler.php", "line": 10, "title": "Minor issue", "blocking": False},
            ],
        }
        response_text = f"```json\n{json.dumps(handover)}\n```"

        async def mock_execute(prompt, system_prompt=None, cwd=None):
            return {"content": response_text}

        agent.agent_sdk.execute_with_tools = mock_execute

        with patch.object(agent, "_get_changed_files") as mock_files, \
             patch.object(agent, "_get_diff_content") as mock_diff:
            mock_files.return_value = [temp_worktree / "Handler.php"]
            mock_diff.return_value = "diff content"

            (temp_worktree / "Handler.php").write_text("<?php\n")

            result = agent.run(worktree_path=temp_worktree)

            assert result["approved"] is True
            assert result["veto"] is False
            assert len(result["findings"]) == 1

    def test_run_veto_workflow(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test complete run that results in veto."""
        agent = DrupalReviewerAgent()

        handover = {
            "verdict": "REQUEST_CHANGES",
            "reviewer": "DrupalSentinel",
            "target_agent": "drupal_developer",
            "metrics": {"blockers": 2, "majors": 1, "minors": 0, "nits": 0, "questions": 0, "praise": 1},
            "findings": [
                {"id": "F-001", "severity": "BLOCKER", "file": "Controller.php", "line": 42, "title": "DI violation", "blocking": True},
                {"id": "F-002", "severity": "BLOCKER", "file": "Block.php", "line": 30, "title": "Missing cache metadata", "blocking": True},
            ],
        }
        response_text = f"```json\n{json.dumps(handover)}\n```"

        async def mock_execute(prompt, system_prompt=None, cwd=None):
            return {"content": response_text}

        agent.agent_sdk.execute_with_tools = mock_execute

        with patch.object(agent, "_get_changed_files") as mock_files, \
             patch.object(agent, "_get_diff_content") as mock_diff:
            mock_files.return_value = [temp_worktree / "Controller.php"]
            mock_diff.return_value = "diff content"

            (temp_worktree / "Controller.php").write_text("<?php\n")

            result = agent.run(worktree_path=temp_worktree)

            assert result["approved"] is False
            assert result["veto"] is True
            assert len(result["findings"]) == 2

    def test_run_no_changed_files_skips(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that run skips review when no Drupal files changed."""
        agent = DrupalReviewerAgent()

        with patch.object(agent, "_get_changed_files") as mock_files:
            mock_files.return_value = []

            result = agent.run(worktree_path=temp_worktree)

            assert result["approved"] is True
            assert result["veto"] is False
            assert "skipped" in result["feedback"][0].lower()

    def test_run_llm_failure_graceful(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test graceful handling when LLM call fails."""
        agent = DrupalReviewerAgent()

        async def mock_execute_fail(prompt, system_prompt=None, cwd=None):
            raise RuntimeError("API timeout")

        agent.agent_sdk.execute_with_tools = mock_execute_fail

        with patch.object(agent, "_get_changed_files") as mock_files, \
             patch.object(agent, "_get_diff_content") as mock_diff:
            mock_files.return_value = [temp_worktree / "Handler.php"]
            mock_diff.return_value = "diff"

            (temp_worktree / "Handler.php").write_text("<?php\n")

            result = agent.run(worktree_path=temp_worktree)

            assert result["approved"] is True
            assert result["veto"] is False
            assert "failed" in result["feedback"][0].lower()


class TestBuildReviewPrompt:
    """Test review prompt construction."""

    def test_prompt_includes_diff(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that diff content is included in prompt."""
        agent = DrupalReviewerAgent()

        prompt = agent._build_review_prompt("--- a/file.php\n+++ b/file.php", "")

        assert "--- a/file.php" in prompt
        assert "11 review dimensions" in prompt

    def test_prompt_includes_description(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test that ticket description is included when provided."""
        agent = DrupalReviewerAgent()

        prompt = agent._build_review_prompt("diff", "", "Add webform handler")

        assert "Add webform handler" in prompt
        assert "MR Description" in prompt


class TestFormatDrupalFindingsComment:
    """Test _format_drupal_findings_comment MR comment formatter."""

    def test_formats_findings_grouped_by_severity(self):
        """Test that findings are grouped by severity in the comment."""
        from src.cli import _format_drupal_findings_comment

        drupal_result = {
            "review_data": {"verdict": "REQUEST_CHANGES"},
            "findings": [
                {"id": "F-001", "severity": "BLOCKER", "title": "Missing DI", "file": "Controller.php", "line": 42},
                {"id": "F-002", "severity": "MAJOR", "title": "No cache metadata", "file": "Block.php", "line": 30},
                {"id": "F-003", "severity": "MINOR", "title": "Missing docblock", "file": "Service.php", "line": 5},
                {"id": "F-004", "severity": "BLOCKER", "title": "SQL injection", "file": "Query.php", "line": 10},
            ],
        }

        comment = _format_drupal_findings_comment("PROJ-123", drupal_result, 5)

        assert "BLOCKER (2)" in comment
        assert "MAJOR (1)" in comment
        assert "MINOR (1)" in comment
        assert "**[F-001]** Missing DI (`Controller.php:42`)" in comment
        assert "**[F-004]** SQL injection (`Query.php:10`)" in comment

    def test_includes_ticket_and_attempts(self):
        """Test that ticket ID and attempt count are in the comment."""
        from src.cli import _format_drupal_findings_comment

        drupal_result = {
            "review_data": {"verdict": "REQUEST_CHANGES"},
            "findings": [
                {"id": "F-001", "severity": "BLOCKER", "title": "Issue", "file": "a.php", "line": 1},
            ],
        }

        comment = _format_drupal_findings_comment("ACME-456", drupal_result, 3)

        assert "`ACME-456`" in comment
        assert "**Attempts:** 3" in comment
        assert "REQUEST_CHANGES" in comment

    def test_handles_empty_findings(self):
        """Test comment with no findings."""
        from src.cli import _format_drupal_findings_comment

        drupal_result = {
            "review_data": {"verdict": "COMMENT_ONLY"},
            "findings": [],
        }

        comment = _format_drupal_findings_comment("PROJ-1", drupal_result, 2)

        assert "Unresolved Findings" in comment
        assert "BLOCKER" not in comment
        assert "MAJOR" not in comment

    def test_skips_empty_severity_groups(self):
        """Test that empty severity groups are omitted."""
        from src.cli import _format_drupal_findings_comment

        drupal_result = {
            "review_data": {"verdict": "REQUEST_CHANGES"},
            "findings": [
                {"id": "F-001", "severity": "MINOR", "title": "Small thing", "file": "a.php", "line": 1},
            ],
        }

        comment = _format_drupal_findings_comment("PROJ-1", drupal_result, 2)

        assert "MINOR (1)" in comment
        assert "BLOCKER" not in comment
        assert "MAJOR" not in comment

    def test_handles_finding_without_line(self):
        """Test finding with no line number."""
        from src.cli import _format_drupal_findings_comment

        drupal_result = {
            "review_data": {"verdict": "REQUEST_CHANGES"},
            "findings": [
                {"id": "F-001", "severity": "MAJOR", "title": "Issue", "file": "module.info.yml", "line": 0},
            ],
        }

        comment = _format_drupal_findings_comment("PROJ-1", drupal_result, 2)

        assert "(`module.info.yml`)" in comment
