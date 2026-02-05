"""Unit tests for PromptLoader."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from src.prompt_loader import PromptLoader


@pytest.fixture
def temp_prompts_dir():
    """Create a temporary directory for prompt files."""
    with TemporaryDirectory() as tmpdir:
        prompts_dir = Path(tmpdir) / "prompts"
        prompts_dir.mkdir()

        # Create sample prompt files
        (prompts_dir / "plan_generator.md").write_text(
            "# Plan Generator\n\nYou are a plan generator agent."
        )
        (prompts_dir / "python_developer.md").write_text(
            "# Python Developer\n\nYou are a Python developer agent."
        )

        # Create shared directory with base instructions
        shared_dir = prompts_dir / "shared"
        shared_dir.mkdir()
        (shared_dir / "base_instructions.md").write_text(
            "# Base Instructions\n\nThese are shared instructions for all agents."
        )

        yield prompts_dir


class TestPromptLoader:
    """Test suite for PromptLoader class."""

    def test_init_with_custom_path(self, temp_prompts_dir):
        """Test initialization with custom prompts directory."""
        loader = PromptLoader(temp_prompts_dir)
        assert loader.prompts_dir == temp_prompts_dir

    def test_init_missing_directory(self):
        """Test initialization with missing prompts directory does not raise error."""
        missing_path = Path("/nonexistent/prompts")
        # PromptLoader doesn't raise on init, only on load()
        loader = PromptLoader(missing_path)
        assert loader.prompts_dir == missing_path

    def test_load_existing_prompt(self, temp_prompts_dir):
        """Test loading an existing prompt."""
        loader = PromptLoader(temp_prompts_dir)
        prompt = loader.load("plan_generator")

        assert "Plan Generator" in prompt
        assert "plan generator agent" in prompt

    def test_load_with_base_instructions(self, temp_prompts_dir):
        """Test loading a prompt includes base instructions."""
        loader = PromptLoader(temp_prompts_dir)
        prompt = loader.load("plan_generator")

        # Should include base instructions
        assert "Base Instructions" in prompt
        assert "shared instructions" in prompt
        # Should also include agent-specific content
        assert "Plan Generator" in prompt

    def test_load_nonexistent_prompt(self, temp_prompts_dir):
        """Test loading a nonexistent prompt."""
        loader = PromptLoader(temp_prompts_dir)

        with pytest.raises(FileNotFoundError):
            loader.load("nonexistent_agent")

    def test_load_multiple_prompts(self, temp_prompts_dir):
        """Test loading multiple different prompts."""
        loader = PromptLoader(temp_prompts_dir)

        plan_prompt = loader.load("plan_generator")
        dev_prompt = loader.load("python_developer")

        assert "plan generator" in plan_prompt.lower()
        assert "python developer" in dev_prompt.lower()
        assert plan_prompt != dev_prompt

    def test_cache_functionality(self, temp_prompts_dir):
        """Test that caching works properly."""
        loader = PromptLoader(temp_prompts_dir)

        # Load once
        prompt1 = loader.load("plan_generator")

        # Modify the file
        (temp_prompts_dir / "plan_generator.md").write_text("Modified content")

        # Load again with cache (should get cached version)
        prompt2 = loader.load("plan_generator", use_cache=True)
        assert prompt1 == prompt2

        # Load without cache (should get new content)
        prompt3 = loader.load("plan_generator", use_cache=False)
        assert "Modified content" in prompt3

    def test_reload_method(self, temp_prompts_dir):
        """Test reload method bypasses cache."""
        loader = PromptLoader(temp_prompts_dir)

        # Load once
        prompt1 = loader.load("plan_generator")

        # Modify the file
        (temp_prompts_dir / "plan_generator.md").write_text("Reloaded content")

        # Reload should bypass cache
        prompt2 = loader.reload("plan_generator")
        assert "Reloaded content" in prompt2
        assert prompt1 != prompt2

    def test_clear_cache(self, temp_prompts_dir):
        """Test clearing the cache."""
        loader = PromptLoader(temp_prompts_dir)

        # Load and cache
        loader.load("plan_generator")
        assert len(loader._cache) == 1

        # Clear cache
        loader.clear_cache()
        assert len(loader._cache) == 0

    def test_empty_prompts_directory(self):
        """Test with an empty prompts directory."""
        with TemporaryDirectory() as tmpdir:
            prompts_dir = Path(tmpdir) / "prompts"
            prompts_dir.mkdir()

            loader = PromptLoader(prompts_dir)

            # Should raise when trying to load from empty directory
            with pytest.raises(FileNotFoundError):
                loader.load("any_agent")
