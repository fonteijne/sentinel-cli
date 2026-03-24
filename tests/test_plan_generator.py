"""Unit tests for PlanGeneratorAgent."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from src.agents.plan_generator import PlanGeneratorAgent


@pytest.fixture
def mock_config():
    """Mock configuration loader."""
    with patch("src.agents.base_agent.get_config") as mock:
        config = Mock()
        config.get_agent_config.return_value = {
            "model": "claude-opus-4-5",
            "temperature": 0.3,
        }
        config.get_project_config.return_value = {
            "git_url": "git@gitlab.com:test/project.git",
            "default_branch": "main",
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
        wrapper.agent_name = "plan_generator"
        wrapper.model = "claude-opus-4-5"
        wrapper.llm_mode = "custom_proxy"
        wrapper.allowed_tools = ["Read", "Grep", "Glob", "Bash(git *)"]
        mock.return_value = wrapper
        yield wrapper


@pytest.fixture
def mock_prompt():
    """Mock prompt loader."""
    with patch("src.agents.base_agent.load_agent_prompt") as mock:
        mock.return_value = "Plan generator system prompt"
        yield mock


@pytest.fixture
def mock_jira():
    """Mock Jira client."""
    with patch("src.agents.plan_generator.get_jira_client") as mock_factory:
        client = Mock()
        client.base_url = "https://test.atlassian.net"
        client.get_ticket.return_value = {
            "key": "TEST-123",
            "summary": "Implement user authentication",
            "description": "Add JWT-based authentication to the API",
            "type": "Story",
            "status": "To Do",
        }
        client.add_comment.return_value = {"id": "12345"}
        mock_factory.return_value = client
        yield mock_factory


@pytest.fixture
def mock_gitlab():
    """Mock GitLab client."""
    with patch("src.agents.plan_generator.GitLabClient") as mock_class:
        client = Mock()
        client.create_merge_request.return_value = {
            "id": 1,
            "iid": 42,
            "web_url": "https://gitlab.com/test/project/-/merge_requests/42",
            "title": "TEST-123: Implement user authentication",
        }
        mock_class.return_value = client
        yield mock_class


@pytest.fixture
def temp_worktree():
    """Create a temporary directory for worktree."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestPlanGeneratorAgent:
    """Test suite for PlanGeneratorAgent class."""

    def test_init(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab):
        """Test agent initialization."""
        agent = PlanGeneratorAgent()

        assert agent.agent_name == "plan_generator"
        assert agent.model == "claude-opus-4-5"
        assert agent.temperature == 0.3
        assert agent.jira is not None
        assert agent.gitlab is not None

    def test_analyze_ticket_basic(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab
    ):
        """Test basic ticket analysis."""
        agent = PlanGeneratorAgent()

        analysis = agent.analyze_ticket("TEST-123")

        assert "ticket_data" in analysis
        assert "requirements" in analysis
        assert "technical_approach" in analysis
        assert "risks" in analysis
        assert "estimated_complexity" in analysis

        # Verify Jira was called
        mock_jira.return_value.get_ticket.assert_called_once_with("TEST-123")

    def test_analyze_ticket_extracts_requirements(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab
    ):
        """Test that requirements are extracted from ticket."""
        agent = PlanGeneratorAgent()

        analysis = agent.analyze_ticket("TEST-123")
        requirements = analysis["requirements"]

        assert len(requirements) > 0
        assert any("Implement user authentication" in req for req in requirements)

    def test_extract_requirements_from_summary(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab
    ):
        """Test requirement extraction from summary."""
        agent = PlanGeneratorAgent()

        ticket_data = {
            "summary": "Add user registration API",
            "description": "",
        }

        requirements = agent._extract_requirements(ticket_data)

        assert len(requirements) >= 1
        assert any("Add user registration API" in req for req in requirements)

    def test_extract_requirements_from_description(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab
    ):
        """Test requirement extraction from description."""
        agent = PlanGeneratorAgent()

        ticket_data = {
            "summary": "API Update",
            "description": "Implement OAuth2 authentication with refresh tokens",
        }

        requirements = agent._extract_requirements(ticket_data)

        assert len(requirements) >= 2
        assert any("OAuth2 authentication" in req for req in requirements)

    def test_extract_requirements_empty_ticket(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab
    ):
        """Test requirement extraction with empty ticket data."""
        agent = PlanGeneratorAgent()

        ticket_data = {}

        requirements = agent._extract_requirements(ticket_data)

        assert isinstance(requirements, list)

    def test_generate_plan_creates_file(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test that plan generation creates a file."""
        agent = PlanGeneratorAgent()

        context = {
            "ticket_data": {
                "summary": "Test feature",
                "description": "Test description",
            },
            "requirements": ["Req 1", "Req 2"],
            "technical_approach": "TDD implementation",
            "risks": ["Risk 1", "Risk 2"],
        }

        plan_path = temp_worktree / "plans" / "TEST-123.md"
        plan_content = agent.generate_plan("TEST-123", context, plan_path)

        assert plan_path.exists()
        assert len(plan_content) > 0
        assert "TEST-123" in plan_content

    def test_generate_plan_content_structure(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test that generated plan has correct structure."""
        agent = PlanGeneratorAgent()

        context = {
            "ticket_data": {"summary": "Test feature"},
            "requirements": ["Req 1", "Req 2"],
            "technical_approach": "TDD",
            "risks": ["Risk 1"],
        }

        plan_path = temp_worktree / "plans" / "TEST-123.md"
        plan_content = agent.generate_plan("TEST-123", context, plan_path)

        # Check for key sections
        assert "# Implementation Plan: TEST-123" in plan_content
        assert "## Ticket Summary" in plan_content
        assert "## Requirements" in plan_content
        assert "## Technical Approach" in plan_content
        assert "## Implementation Steps" in plan_content
        assert "## Risks and Mitigation" in plan_content
        assert "## Success Criteria" in plan_content

        # Check that requirements are included
        assert "Req 1" in plan_content
        assert "Req 2" in plan_content

    def test_generate_plan_creates_directory(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test that plan generation creates parent directory if needed."""
        agent = PlanGeneratorAgent()

        context = {
            "ticket_data": {"summary": "Test"},
            "requirements": [],
            "technical_approach": "TDD",
            "risks": [],
        }

        # Use nested path
        plan_path = temp_worktree / "nested" / "plans" / "TEST-123.md"

        agent.generate_plan("TEST-123", context, plan_path)

        assert plan_path.parent.exists()
        assert plan_path.exists()

    def test_create_draft_mr_basic(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test creating a draft merge request."""
        agent = PlanGeneratorAgent()

        # Create a plan file
        plan_path = temp_worktree / "TEST-123.md"
        plan_path.write_text("# Test Plan\n\nImplementation details...")

        mr_url = agent.create_draft_mr("TEST-123", plan_path, "TEST")

        assert mr_url == "https://gitlab.com/test/project/-/merge_requests/42"

        # Verify GitLab MR was created
        mock_gitlab.return_value.create_merge_request.assert_called_once()

        # Verify Jira comment was added
        mock_jira.return_value.add_comment.assert_called_once()

    def test_create_draft_mr_parameters(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test MR creation with correct parameters."""
        agent = PlanGeneratorAgent()

        plan_path = temp_worktree / "TEST-123.md"
        plan_path.write_text("# Test Plan")

        agent.create_draft_mr("TEST-123", plan_path, "TEST")

        call_kwargs = mock_gitlab.return_value.create_merge_request.call_args[1]

        # The code extracts "test" from the git_url "git@gitlab.com:test/project.git"
        # and appends "/backend" when no colon delimiter is found in parsing
        assert "test" in call_kwargs["project_id"]
        assert call_kwargs["title"] == "TEST-123: Implement user authentication"
        assert call_kwargs["source_branch"] == "feature/TEST-123"
        assert call_kwargs["target_branch"] == "main"
        assert call_kwargs["draft"] is True

    def test_create_draft_mr_description(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test MR description includes necessary information."""
        agent = PlanGeneratorAgent()

        plan_path = temp_worktree / "TEST-123.md"
        plan_path.write_text("# Detailed Plan\n\nLots of details here...")

        agent.create_draft_mr("TEST-123", plan_path, "TEST")

        call_kwargs = mock_gitlab.return_value.create_merge_request.call_args[1]
        description = call_kwargs["description"]

        assert "TEST-123" in description
        assert "Implement user authentication" in description
        assert "View in Jira" in description
        assert "Implementation Plan" in description
        assert "Sentinel" in description

    def test_create_draft_mr_jira_comment(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test that Jira comment is added with MR link."""
        agent = PlanGeneratorAgent()

        plan_path = temp_worktree / "TEST-123.md"
        plan_path.write_text("# Test Plan")

        agent.create_draft_mr("TEST-123", plan_path, "TEST")

        # Check comment was added with link parameters
        mock_jira.return_value.add_comment.assert_called_once_with(
            "TEST-123",
            "Draft implementation plan ready: ",
            link_text="View Merge Request",
            link_url="https://gitlab.com/test/project/-/merge_requests/42",
        )

    def test_run_complete_workflow(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test the complete run workflow."""
        agent = PlanGeneratorAgent()

        # Mock git operations
        with patch("src.agents.plan_generator.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=b"", stderr=b"")

            result = agent.run(ticket_id="TEST-123", worktree_path=temp_worktree)

            # Check all steps completed
            assert "plan_path" in result
            assert "mr_url" in result
            assert "analysis" in result
            assert "plan_content" in result

            # Verify plan file was created
            plan_path = Path(result["plan_path"])
            assert plan_path.exists()

            # Verify MR was created
            assert result["mr_url"] == "https://gitlab.com/test/project/-/merge_requests/42"

            # Verify all methods were called
            mock_jira.return_value.get_ticket.assert_called()
            mock_gitlab.return_value.create_merge_request.assert_called_once()

    def test_run_creates_correct_plan_path(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test that run creates plan in correct location."""
        agent = PlanGeneratorAgent()

        # Mock git operations
        with patch("src.agents.plan_generator.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=b"", stderr=b"")

            result = agent.run(ticket_id="ACME-456", worktree_path=temp_worktree)

            plan_path = Path(result["plan_path"])

            assert ".agents" in str(plan_path)
            assert "plans" in str(plan_path)
            assert "ACME-456.md" in str(plan_path)

    def test_run_extracts_project_key(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test that project key is correctly extracted from ticket ID."""
        # Create a new mock to track calls within this test
        with patch("src.agents.plan_generator.get_config") as local_config_mock:
            local_config = Mock()
            local_config.get_agent_config.return_value = {"model": "claude-opus-4-5", "temperature": 0.3}
            local_config.get_project_config.return_value = {
                "git_url": "git@gitlab.com:acme/backend.git",
                "default_branch": "main",
            }
            local_config_mock.return_value = local_config

            agent = PlanGeneratorAgent()

            # Update mock for this ticket
            mock_jira.return_value.get_ticket.return_value = {
                "key": "ACME-789",
                "summary": "New feature",
                "description": "Details",
            }

            # Mock git operations
            with patch("src.agents.plan_generator.subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0, stdout=b"", stderr=b"")

                result = agent.run(ticket_id="ACME-789", worktree_path=temp_worktree)

                # Verify config was called with correct project key
                local_config.get_project_config.assert_called_with("ACME")

    def test_run_with_additional_kwargs(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test run accepts additional kwargs."""
        agent = PlanGeneratorAgent()

        # Mock git operations
        with patch("src.agents.plan_generator.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout=b"", stderr=b"")

            result = agent.run(
                ticket_id="TEST-123",
                worktree_path=temp_worktree,
                extra_param="value",
            )

            # Should complete successfully even with extra params
            assert result["mr_url"] is not None

    def test_git_url_parsing_ssh_format(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test parsing SSH git URL format."""
        # Update mock config with SSH URL
        mock_config.return_value.get_project_config.return_value = {
            "git_url": "git@gitlab.com:acme/backend.git",
            "default_branch": "main",
        }

        agent = PlanGeneratorAgent()
        plan_path = temp_worktree / "TEST-123.md"
        plan_path.write_text("# Test Plan")

        agent.create_draft_mr("TEST-123", plan_path, "ACME")

        call_kwargs = mock_gitlab.return_value.create_merge_request.call_args[1]
        assert call_kwargs["project_id"] == "acme/backend"

    def test_git_url_parsing_fallback(
        self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, mock_gitlab, temp_worktree
    ):
        """Test fallback when git URL doesn't have expected format."""
        # Update mock config with URL without colon
        mock_config.return_value.get_project_config.return_value = {
            "git_url": "invalid-url",
            "default_branch": "main",
        }

        agent = PlanGeneratorAgent()
        plan_path = temp_worktree / "TEST-123.md"
        plan_path.write_text("# Test Plan")

        agent.create_draft_mr("TEST-123", plan_path, "ACME")

        call_kwargs = mock_gitlab.return_value.create_merge_request.call_args[1]
        # Should fall back to project_key/backend
        assert call_kwargs["project_id"] == "acme/backend"
