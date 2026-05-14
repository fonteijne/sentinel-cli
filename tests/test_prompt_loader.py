"""Unit tests for PromptLoader."""

import logging
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# Phase 2A — pitfalls injection
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db_with_postmortem():
    """In-memory SQLite with migrations + a parent execution + one drupal postmortem.

    Local to this file (mirrors ``tests/conftest.py:sqlite_mem_conn``) because
    the loader tests don't need the broader event-bus fixtures and we want to
    keep the import surface minimal.
    """
    from src.core.persistence import apply_migrations, connect, insert_postmortem

    conn = connect(":memory:")
    apply_migrations(conn)
    conn.execute(
        """
        INSERT INTO executions (id, ticket_id, kind, status, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            "exec-1",
            "TEST-1",
            "execute",
            "running",
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    insert_postmortem(
        conn,
        execution_id="exec-1",
        stack_type="drupal",
        agent="drupal_developer",
        failure_signature="phpunit::failed_assertion::sentinel_demo",
        context_excerpt="Demo failure for prompt-loader tests.",
        fix_summary="Fix the assertion.",
        provenance="auto",
        confidence=88,
    )
    try:
        yield conn
    finally:
        conn.close()


class TestPromptLoaderPhase2A:
    """Phase 2A: pitfalls injection contract on ``PromptLoader.load``."""

    def test_load_with_stack_type_no_conn_no_op(
        self, temp_prompts_dir, monkeypatch
    ):
        """stack_type without a conn must be a no-op regardless of flag state."""
        monkeypatch.setenv("POSTMORTEM_INJECTION", "1")
        loader = PromptLoader(temp_prompts_dir)

        with_stack = loader.load("plan_generator", stack_type="drupal")
        # Use a fresh loader so the cache doesn't return the previous value
        # under a different cache key.
        loader_baseline = PromptLoader(temp_prompts_dir)
        baseline = loader_baseline.load("plan_generator")

        assert with_stack == baseline
        assert "Known pitfalls" not in with_stack

    def test_load_with_flag_off_no_op(
        self, temp_prompts_dir, monkeypatch, tmp_db_with_postmortem
    ):
        """Flag explicitly disabled — no pitfalls section appended."""
        monkeypatch.setenv("POSTMORTEM_INJECTION", "0")
        loader = PromptLoader(temp_prompts_dir)

        prompt = loader.load(
            "plan_generator", stack_type="drupal", conn=tmp_db_with_postmortem
        )

        assert "Known pitfalls" not in prompt
        assert "phpunit::failed_assertion::sentinel_demo" not in prompt

    def test_load_with_flag_unset_no_op(
        self, temp_prompts_dir, monkeypatch, tmp_db_with_postmortem
    ):
        """Flag unset — default-off behaviour, no injection."""
        monkeypatch.delenv("POSTMORTEM_INJECTION", raising=False)
        loader = PromptLoader(temp_prompts_dir)

        prompt = loader.load(
            "plan_generator", stack_type="drupal", conn=tmp_db_with_postmortem
        )

        assert "Known pitfalls" not in prompt
        assert "phpunit::failed_assertion::sentinel_demo" not in prompt

    def test_load_with_flag_on_appends_pitfalls(
        self, temp_prompts_dir, monkeypatch, tmp_db_with_postmortem
    ):
        """Flag on + stack + conn — pitfalls section is appended."""
        monkeypatch.setenv("POSTMORTEM_INJECTION", "1")
        loader = PromptLoader(temp_prompts_dir)

        prompt = loader.load(
            "plan_generator", stack_type="drupal", conn=tmp_db_with_postmortem
        )

        assert "## Known pitfalls" in prompt
        assert "phpunit::failed_assertion::sentinel_demo" in prompt

    def test_cache_key_separates_stacks(
        self, temp_prompts_dir, monkeypatch, tmp_db_with_postmortem
    ):
        """Same agent loaded for different stacks → distinct cache entries."""
        monkeypatch.setenv("POSTMORTEM_INJECTION", "1")
        loader = PromptLoader(temp_prompts_dir)

        drupal_prompt = loader.load(
            "plan_generator", stack_type="drupal", conn=tmp_db_with_postmortem
        )
        python_prompt = loader.load(
            "plan_generator", stack_type="python", conn=tmp_db_with_postmortem
        )

        assert ("plan_generator", "drupal") in loader._cache
        assert ("plan_generator", "python") in loader._cache
        # Drupal sees the postmortem; python does not (different stack).
        assert "phpunit::failed_assertion::sentinel_demo" in drupal_prompt
        assert "phpunit::failed_assertion::sentinel_demo" not in python_prompt

        # Re-loading drupal returns the cached entry (same identity).
        again = loader.load(
            "plan_generator", stack_type="drupal", conn=tmp_db_with_postmortem
        )
        assert again is drupal_prompt

    def test_cache_invalidation_after_clear_cache(
        self, temp_prompts_dir, monkeypatch, tmp_db_with_postmortem
    ):
        """Inserting a new postmortem only takes effect after clear_cache()."""
        from src.core.persistence import insert_postmortem

        monkeypatch.setenv("POSTMORTEM_INJECTION", "1")
        loader = PromptLoader(temp_prompts_dir)

        first = loader.load(
            "plan_generator", stack_type="drupal", conn=tmp_db_with_postmortem
        )
        assert "phpunit::failed_assertion::sentinel_demo" in first

        insert_postmortem(
            tmp_db_with_postmortem,
            execution_id="exec-1",
            stack_type="drupal",
            agent="drupal_developer",
            failure_signature="composer::missing_dependency::sentinel_demo2",
            context_excerpt="Second demo failure.",
            fix_summary="Re-run composer install.",
            provenance="auto",
            confidence=90,
        )

        # Cached — second signature not yet visible.
        cached = loader.load(
            "plan_generator", stack_type="drupal", conn=tmp_db_with_postmortem
        )
        assert "composer::missing_dependency::sentinel_demo2" not in cached

        loader.clear_cache()
        refreshed = loader.load(
            "plan_generator", stack_type="drupal", conn=tmp_db_with_postmortem
        )
        assert "phpunit::failed_assertion::sentinel_demo" in refreshed
        assert "composer::missing_dependency::sentinel_demo2" in refreshed

    def test_existing_callers_still_get_string_keyed_behaviour(
        self, temp_prompts_dir
    ):
        """No-kwargs load caches under ('agent_name', '') — tuple-key contract."""
        loader = PromptLoader(temp_prompts_dir)
        loader.load("plan_generator")

        assert ("plan_generator", "") in loader._cache
        assert len(loader._cache) == 1

    def test_pitfalls_section_falls_back_on_db_error(
        self, temp_prompts_dir, monkeypatch, tmp_db_with_postmortem, caplog
    ):
        """A closed connection must not raise — base prompt + a warning log."""
        monkeypatch.setenv("POSTMORTEM_INJECTION", "1")
        loader = PromptLoader(temp_prompts_dir)
        tmp_db_with_postmortem.close()

        with caplog.at_level(logging.WARNING, logger="src.prompt_loader"):
            prompt = loader.load(
                "plan_generator",
                stack_type="drupal",
                conn=tmp_db_with_postmortem,
            )

        assert "Plan Generator" in prompt
        assert "Known pitfalls" not in prompt
        assert any(
            "Pitfalls injection failed" in rec.message for rec in caplog.records
        )
