"""Tests for ``register_prompt_cache_invalidator``.

The invalidator wires :class:`PostmortemRecorded` to :meth:`PromptLoader.clear_cache`.
Two cases:
    * The cache IS cleared on ``PostmortemRecorded``.
    * The cache is NOT cleared on any other event type (no over-eager clearing).
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterator

import pytest

from src.core.events import (
    DeveloperCappedOut,
    EventBus,
    PostmortemRecorded,
)
from src.core.learning.cache_invalidator import register_prompt_cache_invalidator
from src.prompt_loader import PromptLoader


@pytest.fixture
def prompts_dir() -> Iterator[Path]:
    """Minimal prompt tree: plan_generator + shared/base_instructions."""
    with TemporaryDirectory() as tmpdir:
        d = Path(tmpdir) / "prompts"
        d.mkdir()
        (d / "plan_generator.md").write_text("# Plan Generator\nbody")
        shared = d / "shared"
        shared.mkdir()
        (shared / "base_instructions.md").write_text("# Base\n")
        yield d


@pytest.fixture
def loader(prompts_dir: Path) -> PromptLoader:
    return PromptLoader(prompts_dir)


def test_clears_cache_on_postmortem_recorded(
    event_bus: EventBus, loader: PromptLoader
) -> None:
    register_prompt_cache_invalidator(event_bus, loader)

    # Prime the cache.
    loader.load("plan_generator")
    assert len(loader._cache) == 1

    event_bus.publish(
        PostmortemRecorded(
            execution_id="test-exec-1",  # parent row from sqlite_mem_conn fixture
            ts="",
            postmortem_id=42,
            failure_signature="phpunit::failed_assertion::foo",
        )
    )

    assert len(loader._cache) == 0


def test_ignores_other_events(
    event_bus: EventBus, loader: PromptLoader
) -> None:
    register_prompt_cache_invalidator(event_bus, loader)

    loader.load("plan_generator")
    assert len(loader._cache) == 1

    # A different event type — must NOT trigger the invalidator.
    event_bus.publish(
        DeveloperCappedOut(
            execution_id="test-exec-1",
            ts="",
            agent="drupal_developer",
            attempts=3,
            last_structured_errors=[],
        )
    )

    assert len(loader._cache) == 1


def test_handles_multiple_subscribers(
    event_bus: EventBus, loader: PromptLoader
) -> None:
    """A second subscriber on PostmortemRecorded co-exists with the invalidator."""
    register_prompt_cache_invalidator(event_bus, loader)

    seen: list[int] = []

    def _other_subscriber(event: PostmortemRecorded) -> None:  # type: ignore[type-arg]
        seen.append(event.postmortem_id)

    event_bus.subscribe(PostmortemRecorded, _other_subscriber)

    loader.load("plan_generator")
    event_bus.publish(
        PostmortemRecorded(
            execution_id="test-exec-1",
            ts="",
            postmortem_id=7,
            failure_signature="x",
        )
    )

    assert seen == [7]
    assert len(loader._cache) == 0
