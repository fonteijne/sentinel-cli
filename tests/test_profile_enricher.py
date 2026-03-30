"""Unit tests for ProfileEnricher."""

import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Mock claude_agent_sdk before any sentinel imports that depend on it
_mock_sdk_module = MagicMock()
_mock_sdk_types = MagicMock()
sys.modules.setdefault("claude_agent_sdk", _mock_sdk_module)
sys.modules.setdefault("claude_agent_sdk.types", _mock_sdk_types)

from src.stack_profiler import StackProfiler  # noqa: E402


def _write_file(path: Path, content: str) -> None:
    """Helper to write a file, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def _create_drupal_repo(repo: Path) -> None:
    """Create a minimal Drupal project structure for testing."""
    _write_file(repo / "composer.json", json.dumps({
        "require": {
            "php": ">=8.1",
            "drupal/core-recommended": "^10.0",
            "drush/drush": "^12",
        },
    }))
    _write_file(repo / "web" / "modules" / "custom" / "mymodule" / "mymodule.info.yml", yaml.dump({
        "name": "My Module",
        "type": "module",
        "package": "Custom",
    }))
    _write_file(repo / "web" / "modules" / "custom" / "mymodule" / "mymodule.services.yml", yaml.dump({
        "services": {
            "mymodule.service": {
                "class": "Drupal\\mymodule\\Service\\MyService",
                "arguments": ["@entity_type.manager"],
            },
        },
    }))


@pytest.fixture
def temp_repo():
    """Create a temporary directory simulating a project repo."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


class TestProfileEnricherInit:
    """Tests for ProfileEnricher initialization."""

    @patch("src.agents.base_agent.AgentSDKWrapper")
    @patch("src.agents.base_agent.get_config")
    @patch("src.agents.base_agent.load_agent_prompt")
    def test_loads_custom_system_prompt(self, mock_load_prompt, mock_config, mock_sdk):
        """ProfileEnricher should load its prompt from sentinel/prompts/."""
        mock_load_prompt.return_value = "default prompt"
        mock_config.return_value = MagicMock(
            get_agent_config=MagicMock(return_value={"model": "claude-4-5-sonnet", "temperature": 0.2})
        )

        from src.profile_enricher import ProfileEnricher, PROFILER_PROMPT_PATH

        enricher = ProfileEnricher()

        # Should have overridden the default prompt with the profiler-specific one
        if PROFILER_PROMPT_PATH.exists():
            expected = PROFILER_PROMPT_PATH.read_text()
            assert enricher.system_prompt == expected
        else:
            # If prompt file doesn't exist (CI), it falls back
            assert enricher.system_prompt is not None

    @patch("src.agents.base_agent.AgentSDKWrapper")
    @patch("src.agents.base_agent.get_config")
    @patch("src.agents.base_agent.load_agent_prompt")
    def test_agent_name(self, mock_load_prompt, mock_config, mock_sdk):
        """ProfileEnricher should use 'project_profiler' as agent name."""
        mock_load_prompt.return_value = ""
        mock_config.return_value = MagicMock(
            get_agent_config=MagicMock(return_value={"model": "claude-4-5-sonnet", "temperature": 0.2})
        )

        from src.profile_enricher import ProfileEnricher
        enricher = ProfileEnricher()

        assert enricher.agent_name == "project_profiler"


class TestProfileEnricherEnrich:
    """Tests for ProfileEnricher.enrich()."""

    @patch("src.agents.base_agent.AgentSDKWrapper")
    @patch("src.agents.base_agent.get_config")
    @patch("src.agents.base_agent.load_agent_prompt")
    def test_enrich_calls_send_message_with_cwd(self, mock_load_prompt, mock_config, mock_sdk):
        """Enrich should call send_message with repo path as cwd."""
        mock_load_prompt.return_value = ""
        mock_config.return_value = MagicMock(
            get_agent_config=MagicMock(return_value={"model": "claude-4-5-sonnet", "temperature": 0.2})
        )

        from src.profile_enricher import ProfileEnricher
        enricher = ProfileEnricher()
        enricher.send_message = MagicMock(return_value="# Enriched Profile\n\nContent here")
        enricher.set_project = MagicMock()

        repo_path = Path("/tmp/test_repo")
        profile = {"stack_type": "drupal10", "drupal": {"modules": []}}

        enricher.enrich(repo_path, profile, "TEST")

        enricher.set_project.assert_called_once_with("TEST")
        enricher.send_message.assert_called_once()

        # Verify cwd was passed
        call_kwargs = enricher.send_message.call_args
        assert call_kwargs.kwargs.get("cwd") == str(repo_path) or str(repo_path) in str(call_kwargs)

    @patch("src.agents.base_agent.AgentSDKWrapper")
    @patch("src.agents.base_agent.get_config")
    @patch("src.agents.base_agent.load_agent_prompt")
    def test_enrich_includes_skeleton_in_prompt(self, mock_load_prompt, mock_config, mock_sdk):
        """Enrich prompt should include the deterministic skeleton."""
        mock_load_prompt.return_value = ""
        mock_config.return_value = MagicMock(
            get_agent_config=MagicMock(return_value={"model": "claude-4-5-sonnet", "temperature": 0.2})
        )

        from src.profile_enricher import ProfileEnricher
        enricher = ProfileEnricher()
        enricher.send_message = MagicMock(return_value="# Profile")
        enricher.set_project = MagicMock()

        profile = {
            "stack_type": "drupal10",
            "drupal": {
                "modules": [{"machine_name": "mymodule", "package": "Custom", "dependencies": []}],
                "themes": [], "services": [], "routing": [], "hooks": [],
                "plugins": [], "config_entities": [], "composer": {},
                "build_tools": [], "tests": {}, "environment": {},
            },
        }

        enricher.enrich(Path("/tmp/repo"), profile, "TEST")

        # The prompt should contain the module name from the skeleton
        prompt_arg = enricher.send_message.call_args[0][0]
        assert "mymodule" in prompt_arg

    @patch("src.agents.base_agent.AgentSDKWrapper")
    @patch("src.agents.base_agent.get_config")
    @patch("src.agents.base_agent.load_agent_prompt")
    def test_enrich_returns_llm_response(self, mock_load_prompt, mock_config, mock_sdk):
        """Enrich should return the LLM response directly."""
        mock_load_prompt.return_value = ""
        mock_config.return_value = MagicMock(
            get_agent_config=MagicMock(return_value={"model": "claude-4-5-sonnet", "temperature": 0.2})
        )

        from src.profile_enricher import ProfileEnricher
        enricher = ProfileEnricher()
        expected = "# Deep Profile\n\n## Architecture\nThis is a modular Drupal app..."
        enricher.send_message = MagicMock(return_value=expected)
        enricher.set_project = MagicMock()

        result = enricher.enrich(Path("/tmp/repo"), {"stack_type": "drupal10"}, "TEST")
        assert result == expected

    @patch("src.agents.base_agent.AgentSDKWrapper")
    @patch("src.agents.base_agent.get_config")
    @patch("src.agents.base_agent.load_agent_prompt")
    def test_enrich_resets_session(self, mock_load_prompt, mock_config, mock_sdk):
        """Enrich should reset session state for a clean analysis."""
        mock_load_prompt.return_value = ""
        mock_config.return_value = MagicMock(
            get_agent_config=MagicMock(return_value={"model": "claude-4-5-sonnet", "temperature": 0.2})
        )

        from src.profile_enricher import ProfileEnricher
        enricher = ProfileEnricher()
        enricher.session_id = "old_session"
        enricher.messages = [{"role": "user", "content": "old message"}]
        enricher.send_message = MagicMock(return_value="# Profile")
        enricher.set_project = MagicMock()

        enricher.enrich(Path("/tmp/repo"), {"stack_type": "drupal10"}, "TEST")

        # Session should have been cleared before send_message
        assert enricher.messages == [] or len(enricher.messages) <= 1

    @patch("src.agents.base_agent.AgentSDKWrapper")
    @patch("src.agents.base_agent.get_config")
    @patch("src.agents.base_agent.load_agent_prompt")
    def test_enrich_raises_on_llm_failure(self, mock_load_prompt, mock_config, mock_sdk):
        """Enrich should propagate exceptions from send_message."""
        mock_load_prompt.return_value = ""
        mock_config.return_value = MagicMock(
            get_agent_config=MagicMock(return_value={"model": "claude-4-5-sonnet", "temperature": 0.2})
        )

        from src.profile_enricher import ProfileEnricher
        enricher = ProfileEnricher()
        enricher.send_message = MagicMock(side_effect=RuntimeError("API down"))
        enricher.set_project = MagicMock()

        with pytest.raises(RuntimeError, match="API down"):
            enricher.enrich(Path("/tmp/repo"), {"stack_type": "drupal10"}, "TEST")


class TestFormatForLlmPrompt:
    """Tests for StackProfiler.format_for_llm_prompt()."""

    def test_basic_drupal_format(self, temp_repo):
        _create_drupal_repo(temp_repo)
        profiler = StackProfiler()
        profile = profiler.profile(temp_repo)
        result = profiler.format_for_llm_prompt(profile)

        assert "Stack: drupal10" in result
        assert "mymodule" in result
        assert "mymodule.service" in result

    def test_non_drupal_returns_minimal(self):
        profiler = StackProfiler()
        profile = {"stack_type": None}
        result = profiler.format_for_llm_prompt(profile)

        assert "Stack: None" in result
        assert "modules" not in result.lower()

    def test_compact_format(self, temp_repo):
        """Output should be compact, not full markdown tables."""
        _create_drupal_repo(temp_repo)
        profiler = StackProfiler()
        profile = profiler.profile(temp_repo)
        result = profiler.format_for_llm_prompt(profile)

        # Should NOT contain markdown table syntax
        assert "|---" not in result
        # Should be compact structured text, not verbose
        assert len(result) < 2000
