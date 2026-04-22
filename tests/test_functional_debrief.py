"""Unit tests for FunctionalDebriefAgent."""

import json
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock

import pytest

from src.agents.functional_debrief import FunctionalDebriefAgent


@pytest.fixture
def mock_config():
    """Mock configuration loader."""
    with patch("src.agents.base_agent.get_config") as mock:
        config = Mock()
        config.get_agent_config.return_value = {
            "model": "claude-4-5-sonnet",
            "temperature": 0.3,
            "allowed_tools": [],
        }
        config.get_llm_config.return_value = {
            "mode": "custom_proxy",
            "api_key": "test-api-key",
            "base_url": "https://test.api.com/v1",
        }
        config.get.return_value = []
        mock.return_value = config
        yield config


@pytest.fixture
def mock_agent_sdk():
    """Mock Agent SDK wrapper."""
    with patch("src.agents.base_agent.AgentSDKWrapper") as mock:
        wrapper = Mock()

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None, max_turns=None, timeout=None):
            return {
                "content": "Test LLM response",
                "tool_uses": [],
                "session_id": "test-session-123",
            }

        wrapper.execute_with_tools = mock_execute
        wrapper.set_project = Mock()
        wrapper.agent_name = "functional_debrief"
        wrapper.model = "claude-4-5-sonnet"
        wrapper.llm_mode = "custom_proxy"
        wrapper.allowed_tools = []
        mock.return_value = wrapper
        yield wrapper


@pytest.fixture
def mock_prompt():
    """Mock prompt loader."""
    with patch("src.agents.base_agent.load_agent_prompt") as mock:
        mock.return_value = "Functional debrief system prompt"
        yield mock


@pytest.fixture
def mock_jira():
    """Mock Jira client."""
    with patch("src.agents.functional_debrief.get_jira_client") as mock:
        jira = Mock()
        jira.get_ticket.return_value = SAMPLE_TICKET_DATA
        jira.get_ticket_comments.return_value = []
        jira.add_comment.return_value = {"id": "comment-123"}
        mock.return_value = jira
        yield jira


SAMPLE_TICKET_DATA = {
    "key": "TEST-123",
    "summary": "Add PDF export to dashboard",
    "description": "Users should be able to export their dashboard data as a PDF.",
    "issuetype": {"name": "Story"},
    "priority": {"name": "Medium"},
}

SAMPLE_DEBRIEF_RESPONSE = json.dumps({
    "understanding": "You want users to export their dashboard as a PDF for sharing with stakeholders.",
    "assumptions": [
        "The PDF matches the current dashboard layout",
        "Export is triggered from the dashboard page",
    ],
    "gaps": [
        "Should the PDF include the current date range filter?",
        "Are there branding requirements for the PDF?",
    ],
    "questions": [
        "What date range should the PDF cover?",
        "Are there branding guidelines for exported documents?",
    ],
    "cta": "Could you help me clarify these points?",
    "gaps_resolved": False,
})

SAMPLE_FOLLOWUP_RESPONSE = json.dumps({
    "understanding": "The PDF export covers the currently selected date range. No branding needed.",
    "assumptions": [],
    "gaps": [],
    "questions": [],
    "cta": "Does this summary look correct? Please confirm so we can proceed.",
    "gaps_resolved": True,
})


class TestInit:
    """Test agent initialization."""

    def test_init(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        assert agent.agent_name == "functional_debrief"
        assert agent.model == "claude-4-5-sonnet"
        assert agent.temperature == 0.3


class TestIsDebriefComment:
    """Test debrief comment detection."""

    def test_detects_initial_debrief(self):
        assert FunctionalDebriefAgent.is_debrief_comment(
            "h2. 💬 Sentinel Functional Debrief\n\nSome content..."
        )

    def test_detects_followup(self):
        assert FunctionalDebriefAgent.is_debrief_comment(
            "h2. 💬 Sentinel Functional Debrief — Follow-up\n\nMore content..."
        )

    def test_detects_summary(self):
        assert FunctionalDebriefAgent.is_debrief_comment(
            "h2. 💬 Sentinel Functional Debrief — Summary\n\nSummary content..."
        )

    def test_rejects_confidence_report(self):
        assert not FunctionalDebriefAgent.is_debrief_comment(
            "h2. 🤖 Sentinel Confidence Report — 85/100"
        )

    def test_rejects_plain_comment(self):
        assert not FunctionalDebriefAgent.is_debrief_comment(
            "This is a regular comment from a user."
        )


class TestDetectDebriefState:
    """Test state detection logic."""

    def test_initial_no_state_file(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        with patch.object(agent, "_load_state", return_value=None):
            result = agent._detect_debrief_state("TEST-123")
        assert result["state"] == "initial"

    def test_awaiting_reply_no_client_response(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        mock_jira.get_ticket_comments.return_value = [
            {"author": "Sentinel", "body": "h2. 💬 Sentinel Functional Debrief\n\nDebrief content"},
        ]
        existing_state = {"status": "awaiting_reply", "posted_at": "2026-04-10T14:00:00+00:00", "iteration_count": 1}
        with patch.object(agent, "_load_state", return_value=existing_state):
            result = agent._detect_debrief_state("TEST-123")
        assert result["state"] == "awaiting_reply"

    def test_has_reply_client_responded(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        mock_jira.get_ticket_comments.return_value = [
            {"author": "Sentinel", "body": "h2. 💬 Sentinel Functional Debrief\n\nDebrief content"},
            {"author": "Client User", "body": "Yes, the PDF should include current date range."},
        ]
        existing_state = {"status": "awaiting_reply", "posted_at": "2026-04-10T14:00:00+00:00", "iteration_count": 1}
        with patch.object(agent, "_load_state", return_value=existing_state):
            result = agent._detect_debrief_state("TEST-123")
        assert result["state"] == "has_reply"
        assert len(result["client_replies"]) == 1

    def test_validated_state(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        mock_jira.get_ticket_comments.return_value = [
            {"author": "Sentinel", "body": "h2. 💬 Sentinel Functional Debrief — Summary\n\nSummary"},
            {"author": "Client", "body": "Confirmed!"},
        ]
        existing_state = {
            "status": "validated",
            "validated_at": "2026-04-10T15:00:00+00:00",
            "iteration_count": 2,
        }
        with patch.object(agent, "_load_state", return_value=existing_state):
            result = agent._detect_debrief_state("TEST-123")
        assert result["state"] == "validated"

    def test_validated_resets_when_comments_deleted(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        """If debrief comments are deleted from Jira, reset to initial."""
        agent = FunctionalDebriefAgent()
        mock_jira.get_ticket_comments.return_value = []  # All comments deleted
        existing_state = {
            "status": "validated",
            "validated_at": "2026-04-10T15:00:00+00:00",
            "iteration_count": 2,
        }
        with patch.object(agent, "_load_state", return_value=existing_state):
            result = agent._detect_debrief_state("TEST-123")
        assert result["state"] == "initial"

    def test_pending_confirmation_no_reply(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        mock_jira.get_ticket_comments.return_value = [
            {"author": "Sentinel", "body": "h2. 💬 Sentinel Functional Debrief — Summary\n\nSummary content"},
        ]
        existing_state = {"status": "pending_confirmation", "iteration_count": 2}
        with patch.object(agent, "_load_state", return_value=existing_state):
            result = agent._detect_debrief_state("TEST-123")
        assert result["state"] == "pending_confirmation"
        assert result["client_replied"] is False

    def test_pending_confirmation_client_confirmed(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        mock_jira.get_ticket_comments.return_value = [
            {"author": "Sentinel", "body": "h2. 💬 Sentinel Functional Debrief — Summary\n\nSummary"},
            {"author": "Client", "body": "Confirmed, looks good!"},
        ]
        existing_state = {"status": "pending_confirmation", "iteration_count": 2}
        with patch.object(agent, "_load_state", return_value=existing_state):
            result = agent._detect_debrief_state("TEST-123")
        assert result["state"] == "pending_confirmation"
        assert result["client_replied"] is True

    def test_reset_to_initial_when_debrief_deleted(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        mock_jira.get_ticket_comments.return_value = [
            {"author": "Client", "body": "Some unrelated comment"},
        ]
        existing_state = {"status": "awaiting_reply", "iteration_count": 1}
        with patch.object(agent, "_load_state", return_value=existing_state):
            result = agent._detect_debrief_state("TEST-123")
        assert result["state"] == "initial"


class TestRun:
    """Test the main run() workflow."""

    def test_run_initial_posts_debrief(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()

        # Mock LLM to return structured debrief
        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None, max_turns=None, timeout=None):
            return {
                "content": SAMPLE_DEBRIEF_RESPONSE,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        with patch.object(agent, "_load_state", return_value=None), \
             patch.object(agent, "_save_state") as mock_save:
            result = agent.run(ticket_id="TEST-123", project="TEST")

        assert result["action"] == "posted"
        assert result["iteration_count"] == 1
        mock_jira.add_comment.assert_called_once()
        mock_save.assert_called_once()
        saved_state = mock_save.call_args[0][1]
        assert saved_state["status"] == "awaiting_reply"

    def test_run_initial_with_worktree(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        """When worktree_path is provided, cwd is passed to send_message."""
        agent = FunctionalDebriefAgent()

        captured_cwd = {}

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None, max_turns=None, timeout=None):
            captured_cwd["value"] = cwd
            return {
                "content": SAMPLE_DEBRIEF_RESPONSE,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        with patch.object(agent, "_load_state", return_value=None), \
             patch.object(agent, "_save_state"):
            result = agent.run(ticket_id="TEST-123", project="TEST", worktree_path="/tmp/worktree")

        assert result["action"] == "posted"
        assert captured_cwd["value"] == "/tmp/worktree"

    def test_run_initial_without_worktree(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        """When no worktree_path, cwd is None."""
        agent = FunctionalDebriefAgent()

        captured_cwd = {}

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None, max_turns=None, timeout=None):
            captured_cwd["value"] = cwd
            return {
                "content": SAMPLE_DEBRIEF_RESPONSE,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        with patch.object(agent, "_load_state", return_value=None), \
             patch.object(agent, "_save_state"):
            result = agent.run(ticket_id="TEST-123", project="TEST")

        assert result["action"] == "posted"
        assert captured_cwd["value"] is None

    def test_run_awaiting_reply(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        mock_jira.get_ticket_comments.return_value = [
            {"author": "Sentinel", "body": "h2. 💬 Sentinel Functional Debrief\n\nContent"},
        ]
        existing_state = {"status": "awaiting_reply", "posted_at": "2026-04-10T14:00:00+00:00", "iteration_count": 1}
        with patch.object(agent, "_load_state", return_value=existing_state):
            result = agent.run(ticket_id="TEST-123")
        assert result["action"] == "awaiting_reply"

    def test_run_has_reply_followup(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        mock_jira.get_ticket_comments.return_value = [
            {"author": "Sentinel", "body": "h2. 💬 Sentinel Functional Debrief\n\nContent"},
            {"author": "Client", "body": "The date range should be the current selection."},
        ]

        # Mock LLM to return follow-up (not resolved)
        followup_response = json.dumps({
            "understanding": "Updated understanding with date range info.",
            "assumptions": ["No branding needed"],
            "gaps": ["Max file size?"],
            "questions": ["Is there a max file size for the PDF?"],
            "cta": "One more question...",
            "gaps_resolved": False,
        })

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None, max_turns=None, timeout=None):
            return {
                "content": followup_response,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        existing_state = {"status": "awaiting_reply", "posted_at": "2026-04-10T14:00:00+00:00", "iteration_count": 1}
        with patch.object(agent, "_load_state", return_value=existing_state), \
             patch.object(agent, "_save_state") as mock_save:
            result = agent.run(ticket_id="TEST-123")

        assert result["action"] == "followed_up"
        assert result["iteration_count"] == 2
        mock_jira.add_comment.assert_called_once()

    def test_run_has_reply_proposes_closure(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        mock_jira.get_ticket_comments.return_value = [
            {"author": "Sentinel", "body": "h2. 💬 Sentinel Functional Debrief\n\nContent"},
            {"author": "Client", "body": "All clear, date range is current selection, no branding."},
        ]

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None, max_turns=None, timeout=None):
            return {
                "content": SAMPLE_FOLLOWUP_RESPONSE,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        existing_state = {"status": "awaiting_reply", "posted_at": "2026-04-10T14:00:00+00:00", "iteration_count": 1}
        with patch.object(agent, "_load_state", return_value=existing_state), \
             patch.object(agent, "_save_state") as mock_save:
            result = agent.run(ticket_id="TEST-123")

        assert result["action"] == "proposed_closure"
        assert result["iteration_count"] == 2
        saved_state = mock_save.call_args[0][1]
        assert saved_state["status"] == "pending_confirmation"

    def test_run_validated_after_confirmation(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        """Client confirms after summary — LLM determines gaps_resolved=true."""
        agent = FunctionalDebriefAgent()
        mock_jira.get_ticket_comments.return_value = [
            {"author": "Sentinel", "body": "h2. 💬 Sentinel Functional Debrief — Summary\n\nSummary"},
            {"author": "Client", "body": "Confirmed!"},
        ]

        # LLM confirms this is indeed a confirmation
        confirmation_response = json.dumps({
            "understanding": "Final confirmed understanding.",
            "assumptions": [],
            "gaps": [],
            "questions": [],
            "cta": "Bedankt voor de bevestiging.",
            "gaps_resolved": True,
        })

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None, max_turns=None, timeout=None):
            return {
                "content": confirmation_response,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        existing_state = {"status": "pending_confirmation", "iteration_count": 2}
        with patch.object(agent, "_load_state", return_value=existing_state), \
             patch.object(agent, "_save_state") as mock_save:
            result = agent.run(ticket_id="TEST-123")

        assert result["action"] == "validated"
        saved_state = mock_save.call_args[0][1]
        assert saved_state["status"] == "validated"
        assert saved_state["validated_at"] is not None

    def test_run_correction_after_summary(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        """Client corrects after summary — reopens conversation."""
        agent = FunctionalDebriefAgent()
        mock_jira.get_ticket_comments.return_value = [
            {"author": "Sentinel", "body": "h2. 💬 Sentinel Functional Debrief — Summary\n\nSummary"},
            {"author": "Client", "body": "nee, ook de prefix ABS moet erbij"},
        ]

        # LLM detects this is a correction, not a confirmation
        correction_response = json.dumps({
            "understanding": "Updated understanding including ABS prefix.",
            "assumptions": [],
            "gaps": [],
            "questions": ["Zijn er nog andere prefixes die we missen?"],
            "cta": "Klopt dit nu?",
            "gaps_resolved": False,
        })

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None, max_turns=None, timeout=None):
            return {
                "content": correction_response,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        existing_state = {"status": "pending_confirmation", "iteration_count": 2}
        with patch.object(agent, "_load_state", return_value=existing_state), \
             patch.object(agent, "_save_state") as mock_save:
            result = agent.run(ticket_id="TEST-123")

        assert result["action"] == "followed_up"
        assert result["iteration_count"] == 3
        saved_state = mock_save.call_args[0][1]
        assert saved_state["status"] == "awaiting_reply"
        mock_jira.add_comment.assert_called_once()  # Follow-up posted


class TestPostDebriefComment:
    """Test wiki markup comment assembly."""

    def test_initial_comment_format(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        debrief_data = {
            "understanding": "You want PDF export from the dashboard.",
            "assumptions": ["Layout matches dashboard"],
            "gaps": ["Date range unclear"],
            "questions": ["What date range?"],
            "cta": "Could you clarify?",
        }

        agent._post_debrief_comment("TEST-123", debrief_data, comment_type="initial")

        mock_jira.add_comment.assert_called_once()
        comment_body = mock_jira.add_comment.call_args[0][1]

        assert "Sentinel Functional Debrief" in comment_body
        assert "Follow-up" not in comment_body
        assert "Summary" not in comment_body
        assert "You want PDF export" in comment_body
        assert "h3. Assumptions" in comment_body
        assert "Layout matches dashboard" in comment_body
        assert "h3. Information Gaps" in comment_body
        assert "h3. Questions" in comment_body
        assert "sentinel debrief TEST-123" in comment_body

    def test_followup_comment_format(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        debrief_data = {
            "understanding": "Updated understanding.",
            "assumptions": [],
            "gaps": ["One remaining gap"],
            "questions": ["Remaining question?"],
            "cta": "Please answer this.",
        }

        agent._post_debrief_comment("TEST-123", debrief_data, comment_type="followup")

        comment_body = mock_jira.add_comment.call_args[0][1]
        assert "Follow-up" in comment_body
        assert "sentinel debrief TEST-123" in comment_body
        # No assumptions section when empty
        assert "h3. Assumptions" not in comment_body

    def test_summary_comment_format(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        debrief_data = {
            "understanding": "Final agreed understanding.",
            "assumptions": [],
            "gaps": [],
            "questions": [],
            "cta": "Please confirm.",
        }

        agent._post_debrief_comment("TEST-123", debrief_data, comment_type="summary")

        comment_body = mock_jira.add_comment.call_args[0][1]
        assert "Summary" in comment_body
        assert "sentinel plan TEST-123" in comment_body  # Summary points to plan
        # No assumptions/gaps/questions sections when all empty
        assert "h3. Assumptions" not in comment_body
        assert "h3. Information Gaps" not in comment_body
        assert "h3. Questions" not in comment_body


class TestStatePersistence:
    """Test state file read/write."""

    def test_save_and_load_state(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, tmp_path):
        agent = FunctionalDebriefAgent()

        state = {
            "status": "awaiting_reply",
            "ticket_id": "TEST-123",
            "posted_at": "2026-04-10T14:00:00+00:00",
            "iteration_count": 1,
            "validated_at": None,
        }

        # Override home dir for test
        with patch("src.agents.functional_debrief.Path.home", return_value=tmp_path):
            agent._save_state("TEST-123", state)
            loaded = agent._load_state("TEST-123")

        assert loaded == state
        assert (tmp_path / ".sentinel" / "debriefs" / "TEST-123.json").exists()

    def test_load_state_no_file(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira, tmp_path):
        agent = FunctionalDebriefAgent()

        with patch("src.agents.functional_debrief.Path.home", return_value=tmp_path):
            loaded = agent._load_state("NONEXISTENT-999")

        assert loaded is None


class TestJsonExtraction:
    """Test JSON extraction from LLM responses."""

    def test_pure_json(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        response = '{"understanding": "test", "gaps_resolved": false}'
        result = agent._extract_json_from_response(response)
        assert result["understanding"] == "test"

    def test_json_in_code_block(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        response = 'Here is the result:\n```json\n{"understanding": "test"}\n```'
        result = agent._extract_json_from_response(response)
        assert result["understanding"] == "test"

    def test_json_with_surrounding_text(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        response = 'Some preamble {"understanding": "test"} some trailing text'
        result = agent._extract_json_from_response(response)
        assert result["understanding"] == "test"

    def test_invalid_response(self, mock_config, mock_agent_sdk, mock_prompt, mock_jira):
        agent = FunctionalDebriefAgent()
        result = agent._extract_json_from_response("This is not JSON at all")
        assert result is None


class TestDefaultDebrief:
    """Test fallback debrief when LLM fails."""

    def test_default_debrief_structure(self):
        result = FunctionalDebriefAgent._default_debrief()
        assert "understanding" in result
        assert "assumptions" in result
        assert "gaps" in result
        assert "questions" in result
        assert "cta" in result
        assert result["gaps_resolved"] is False
