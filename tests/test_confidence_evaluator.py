"""Unit tests for ConfidenceEvaluatorAgent."""

import json
from unittest.mock import Mock, patch

import pytest

from src.agents.confidence_evaluator import ConfidenceEvaluatorAgent


@pytest.fixture
def mock_config():
    """Mock configuration loader."""
    with patch("src.agents.base_agent.get_config") as mock:
        config = Mock()
        config.get_agent_config.return_value = {
            "model": "claude-4-5-sonnet",
            "temperature": 0.1,
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

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": "Test LLM response",
                "tool_uses": [],
                "session_id": "test-session-123",
            }

        wrapper.execute_with_tools = mock_execute
        wrapper.set_project = Mock()
        wrapper.agent_name = "confidence_evaluator"
        wrapper.model = "claude-4-5-sonnet"
        wrapper.llm_mode = "custom_proxy"
        wrapper.allowed_tools = []
        mock.return_value = wrapper
        yield wrapper


@pytest.fixture
def mock_prompt():
    """Mock prompt loader."""
    with patch("src.agents.base_agent.load_agent_prompt") as mock:
        mock.return_value = "Confidence evaluator system prompt"
        yield mock


SAMPLE_TICKET_DATA = {
    "key": "TEST-123",
    "summary": "Add user notification system",
    "description": "Users should receive email notifications when their order ships.",
    "issuetype": {"name": "Story"},
    "priority": {"name": "Medium"},
}

SAMPLE_ANALYSIS = {
    "ticket_data": SAMPLE_TICKET_DATA,
    "requirements": ["Send email on order shipment", "Support HTML templates"],
    "risks": ["Email delivery latency"],
    "estimated_complexity": "medium",
    "comments": [],
}

HIGH_CONFIDENCE_RESPONSE = json.dumps({
    "confidence_score": 98,
    "gaps": [],
    "assumptions": [],
    "questions": [],
    "invest_evaluation": {
        "independent": {"score": 5, "note": "Self-contained"},
        "negotiable": {"score": 5, "note": "Flexible approach"},
        "valuable": {"score": 5, "note": "Clear user value"},
        "estimatable": {"score": 5, "note": "Well-scoped"},
        "small": {"score": 4, "note": "2 days work"},
        "testable": {"score": 5, "note": "Clear acceptance criteria"},
    },
    "summary": "Plan is well-specified and ready for implementation",
    "scope_suggestion": None,
})

LOW_CONFIDENCE_RESPONSE = json.dumps({
    "confidence_score": 45,
    "gaps": [
        "No email template specification",
        "Missing error handling for bounced emails",
    ],
    "assumptions": [
        "Assumes SendGrid as email provider",
        "Assumes order events are published via message queue",
    ],
    "questions": [
        "Which email service provider should be used?",
        "Should failed deliveries be retried? If so, how many times?",
        "What should the email template look like?",
    ],
    "invest_evaluation": {
        "independent": {"score": 3, "note": "Depends on order service events"},
        "negotiable": {"score": 4, "note": "Implementation approach is flexible"},
        "valuable": {"score": 5, "note": "Direct user value"},
        "estimatable": {"score": 2, "note": "Missing provider details"},
        "small": {"score": 3, "note": "Could be smaller if scoped to one channel"},
        "testable": {"score": 3, "note": "Missing acceptance criteria in ticket"},
    },
    "summary": "Plan makes 2 assumptions about email infrastructure that need validation",
    "scope_suggestion": None,
})


class TestConfidenceEvaluatorAgent:
    """Test suite for ConfidenceEvaluatorAgent class."""

    def test_init(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test agent initialization."""
        agent = ConfidenceEvaluatorAgent()

        assert agent.agent_name == "confidence_evaluator"
        assert agent.model == "claude-4-5-sonnet"
        assert agent.temperature == 0.1
        assert agent.veto_power is True

    def test_evaluate_returns_valid_schema(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that evaluate returns all expected keys."""
        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": HIGH_CONFIDENCE_RESPONSE,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        agent = ConfidenceEvaluatorAgent()
        result = agent.evaluate("# Plan content", SAMPLE_TICKET_DATA, SAMPLE_ANALYSIS)

        required_keys = [
            "confidence_score", "gaps", "assumptions",
            "questions", "invest_evaluation", "summary", "scope_suggestion",
        ]
        for key in required_keys:
            assert key in result, f"Missing key: {key}"

    def test_evaluate_invest_criteria_complete(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that all 6 INVEST criteria are present."""
        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": HIGH_CONFIDENCE_RESPONSE,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        agent = ConfidenceEvaluatorAgent()
        result = agent.evaluate("# Plan content", SAMPLE_TICKET_DATA, SAMPLE_ANALYSIS)

        invest = result["invest_evaluation"]
        expected_criteria = ["independent", "negotiable", "valuable", "estimatable", "small", "testable"]
        for criterion in expected_criteria:
            assert criterion in invest, f"Missing INVEST criterion: {criterion}"
            assert "score" in invest[criterion]
            assert "note" in invest[criterion]

    def test_evaluate_no_codebase_access(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that evaluator is called with cwd=None (no codebase access)."""
        calls = []

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            calls.append({"cwd": cwd})
            return {
                "content": HIGH_CONFIDENCE_RESPONSE,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        agent = ConfidenceEvaluatorAgent()
        agent.evaluate("# Plan content", SAMPLE_TICKET_DATA, SAMPLE_ANALYSIS)

        assert len(calls) > 0
        assert calls[0]["cwd"] is None, "Evaluator should not have codebase access"

    def test_evaluate_high_confidence(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test high confidence score for well-specified plan."""
        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": HIGH_CONFIDENCE_RESPONSE,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        agent = ConfidenceEvaluatorAgent()
        result = agent.evaluate("# Plan content", SAMPLE_TICKET_DATA, SAMPLE_ANALYSIS)

        assert result["confidence_score"] >= 95
        assert len(result["gaps"]) == 0
        assert len(result["questions"]) == 0

    def test_evaluate_low_confidence(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test low confidence score for vague plan."""
        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": LOW_CONFIDENCE_RESPONSE,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        agent = ConfidenceEvaluatorAgent()
        result = agent.evaluate("# Plan content", SAMPLE_TICKET_DATA, SAMPLE_ANALYSIS)

        assert result["confidence_score"] < 95
        assert len(result["gaps"]) > 0
        assert len(result["questions"]) > 0
        assert len(result["assumptions"]) > 0

    def test_evaluate_handles_invalid_json(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test graceful handling of invalid JSON response."""
        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": "This is not valid JSON at all",
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        agent = ConfidenceEvaluatorAgent()
        result = agent.evaluate("# Plan content", SAMPLE_TICKET_DATA, SAMPLE_ANALYSIS)

        # Should return conservative default
        assert result["confidence_score"] == 0
        assert len(result["gaps"]) > 0

    def test_evaluate_handles_missing_fields(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that missing fields get safe defaults."""
        partial_response = json.dumps({
            "confidence_score": 75,
            "gaps": ["Missing info"],
        })

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": partial_response,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        agent = ConfidenceEvaluatorAgent()
        result = agent.evaluate("# Plan content", SAMPLE_TICKET_DATA, SAMPLE_ANALYSIS)

        assert result["confidence_score"] == 75
        assert "assumptions" in result
        assert "questions" in result
        assert "invest_evaluation" in result

    def test_evaluate_confidence_score_is_int(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that confidence score is always an integer."""
        response_with_float = json.dumps({
            "confidence_score": 72.5,
            "gaps": [],
            "assumptions": [],
            "questions": [],
            "invest_evaluation": {},
            "summary": "Test",
        })

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": response_with_float,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        agent = ConfidenceEvaluatorAgent()
        result = agent.evaluate("# Plan content", SAMPLE_TICKET_DATA, SAMPLE_ANALYSIS)

        assert isinstance(result["confidence_score"], int)

    def test_evaluate_includes_comments_in_prompt(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that existing ticket comments are included in evaluation prompt."""
        prompts_received = []

        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            prompts_received.append(prompt)
            return {
                "content": HIGH_CONFIDENCE_RESPONSE,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        analysis_with_comments = {
            **SAMPLE_ANALYSIS,
            "comments": [
                {"author": "PO User", "body": "We should use SendGrid for emails"},
            ],
        }

        agent = ConfidenceEvaluatorAgent()
        agent.evaluate("# Plan content", SAMPLE_TICKET_DATA, analysis_with_comments)

        assert len(prompts_received) > 0
        assert "SendGrid" in prompts_received[0]
        assert "PO User" in prompts_received[0]

    def test_run_delegates_to_evaluate(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that run() delegates to evaluate()."""
        async def mock_execute(prompt, session_id=None, system_prompt=None, cwd=None):
            return {
                "content": HIGH_CONFIDENCE_RESPONSE,
                "tool_uses": [],
                "session_id": "test-session",
            }

        mock_agent_sdk.execute_with_tools = mock_execute

        agent = ConfidenceEvaluatorAgent()
        result = agent.run(
            plan_content="# Plan",
            ticket_data=SAMPLE_TICKET_DATA,
            analysis=SAMPLE_ANALYSIS,
        )

        assert "confidence_score" in result


class TestExtractJsonFromResponse:
    """Test JSON extraction helper."""

    def test_extract_pure_json(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test extracting pure JSON response."""
        agent = ConfidenceEvaluatorAgent()
        result = agent._extract_json_from_response('{"score": 42}')
        assert result == {"score": 42}

    def test_extract_from_code_block(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test extracting JSON from markdown code block."""
        agent = ConfidenceEvaluatorAgent()
        response = 'Here is the result:\n```json\n{"score": 42}\n```'
        result = agent._extract_json_from_response(response)
        assert result == {"score": 42}

    def test_extract_from_surrounding_text(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test extracting JSON with surrounding text."""
        agent = ConfidenceEvaluatorAgent()
        response = 'The evaluation is: {"score": 42} end.'
        result = agent._extract_json_from_response(response)
        assert result == {"score": 42}

    def test_extract_returns_none_for_invalid(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that invalid responses return None."""
        agent = ConfidenceEvaluatorAgent()
        result = agent._extract_json_from_response("no json here")
        assert result is None
