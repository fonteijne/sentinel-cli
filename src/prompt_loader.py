"""Prompt loader for Sentinel agents."""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


def _postmortem_injection_enabled() -> bool:
    """Phase 2A feature flag — set POSTMORTEM_INJECTION=1 to enable.

    Read at call time (no caching) so flipping the env var takes effect on
    the next ``load()`` call without process restart. Same contract as
    Phase 1's DEV_VERIFIER_LOOP.
    """
    return os.getenv("POSTMORTEM_INJECTION", "0") == "1"


class PromptLoader:
    """Loads agent system prompts from markdown files."""

    def __init__(self, prompts_dir: Path | None = None) -> None:
        """Initialize the prompt loader.

        Args:
            prompts_dir: Path to prompts directory. Defaults to .agents/prompts/
        """
        if prompts_dir is None:
            # Default to prompts/ relative to sentinel project root
            project_root = Path(__file__).parent.parent
            self.prompts_dir = project_root / "prompts"
        else:
            self.prompts_dir = Path(prompts_dir)

        # Cache key is (agent_name, stack_type or "") so the same agent rendered
        # for different stacks doesn't collide. Phase 2A: pitfalls vary by stack.
        self._cache: Dict[tuple[str, str], str] = {}

    def load(
        self,
        agent_name: str,
        use_cache: bool = True,
        *,
        stack_type: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> str:
        """Load system prompt for an agent.

        Args:
            agent_name: Name of the agent (e.g., "plan_generator")
            use_cache: Whether to use cached prompt if available
            stack_type: Optional stack scope for pitfalls injection (Phase 2A).
            conn: Optional SQLite connection used to query active postmortems.
                Caller owns the connection's lifecycle.

        Returns:
            System prompt text
        """
        cache_key = (agent_name, stack_type or "")

        if use_cache and cache_key in self._cache:
            return self._cache[cache_key]

        prompt_file = self.prompts_dir / f"{agent_name}.md"
        if not prompt_file.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {prompt_file}\n"
                f"Expected location: {self.prompts_dir}/{agent_name}.md"
            )

        with open(prompt_file, "r") as f:
            prompt_content = f.read()

        base_instructions = self._load_base_instructions()
        if base_instructions:
            prompt_content = f"{base_instructions}\n\n{prompt_content}"

        # Phase 2A pitfalls injection — only when caller supplied a stack +
        # connection AND the kill-switch flag is on. Lazy imports keep the
        # no-op path free of persistence/learning import overhead.
        if stack_type and conn is not None and _postmortem_injection_enabled():
            try:
                from src.core.persistence import query_active_postmortems
                from src.core.learning import render_pitfalls_section

                rows = query_active_postmortems(
                    conn, stack_type, min_confidence=70, limit=15
                )
                section, dropped = render_pitfalls_section(rows)
                if section:
                    prompt_content = f"{prompt_content}\n\n{section}"
                    # Audit trail: which postmortems actually reached this
                    # prompt. Lets operators verify the learning loop is
                    # alive ("did my injected postmortem get used?") and
                    # later reconstruct, from logs alone, which rules were
                    # in scope for any given run. Only emitted when at
                    # least one postmortem was rendered — empty queries
                    # stay silent to avoid log noise.
                    dropped_set = set(dropped)
                    used_ids = [r["id"] for r in rows if r["id"] not in dropped_set]
                    logger.info(
                        "PostmortemsInjected: agent=%s stack=%s used=%s dropped=%s",
                        agent_name, stack_type, used_ids, dropped,
                    )
                if dropped:
                    logger.warning(
                        "PromptBudgetExceeded: dropped %d postmortem(s) (ids=%s) "
                        "rendering pitfalls for stack=%s agent=%s",
                        len(dropped), dropped, stack_type, agent_name,
                    )
            except Exception:
                # Pitfalls are best-effort: a broken DB or migration must not
                # break prompt loading. Fall through to the unmodified prompt.
                logger.warning(
                    "Pitfalls injection failed for %s/%s; serving base prompt",
                    agent_name, stack_type, exc_info=True,
                )

        self._cache[cache_key] = prompt_content

        return prompt_content

    def _load_base_instructions(self) -> str:
        """Load shared base instructions if they exist.

        Returns:
            Base instructions content or empty string
        """
        base_file = self.prompts_dir / "shared" / "base_instructions.md"
        if not base_file.exists():
            return ""

        with open(base_file, "r") as f:
            return f.read()

    def clear_cache(self) -> None:
        """Clear the prompt cache."""
        self._cache.clear()

    def reload(
        self,
        agent_name: str,
        *,
        stack_type: str | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> str:
        """Reload prompt for an agent, bypassing cache.

        Args:
            agent_name: Name of the agent
            stack_type: Optional stack scope (Phase 2A pitfalls).
            conn: Optional SQLite connection (Phase 2A pitfalls).

        Returns:
            System prompt text
        """
        cache_key = (agent_name, stack_type or "")
        if cache_key in self._cache:
            del self._cache[cache_key]

        return self.load(
            agent_name, use_cache=False, stack_type=stack_type, conn=conn
        )


# Global prompt loader instance
_prompt_loader: PromptLoader | None = None


def get_prompt_loader() -> PromptLoader:
    """Get the global prompt loader instance.

    Returns:
        PromptLoader instance
    """
    global _prompt_loader
    if _prompt_loader is None:
        _prompt_loader = PromptLoader()
    return _prompt_loader


def load_agent_prompt(
    agent_name: str,
    *,
    stack_type: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> str:
    """Convenience function to load an agent prompt.

    Args:
        agent_name: Name of the agent
        stack_type: Optional stack scope for pitfalls injection (Phase 2A).
        conn: Optional SQLite connection (Phase 2A pitfalls).

    Returns:
        System prompt text
    """
    return get_prompt_loader().load(agent_name, stack_type=stack_type, conn=conn)
