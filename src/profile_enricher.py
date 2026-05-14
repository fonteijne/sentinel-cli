"""LLM-powered project profile enrichment.

Takes a deterministic codebase skeleton from StackProfiler and enriches it
with architectural insights, domain understanding, and conventions by using
an LLM agent with codebase exploration tools.
"""

import logging
from pathlib import Path
from typing import Any, Dict

from src.agents.base_agent import PlanningAgent
from src.stack_profiler import StackProfiler

logger = logging.getLogger(__name__)

# Path to the profiler system prompt (ships with Sentinel, not in .agents/)
PROFILER_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "project_profiler.md"


class ProfileEnricher(PlanningAgent):
    """Enriches deterministic project profiles with LLM analysis.

    Uses the agent SDK to explore a codebase and produce an insight-dense
    project profile that helps planning and implementation agents understand
    the codebase architecture, conventions, and patterns.
    """

    def __init__(self) -> None:
        """Initialize the profile enricher agent."""
        super().__init__(
            agent_name="project_profiler",
            model="claude-4-5-sonnet",
            temperature=0.2,
        )
        # Override system prompt — profiler prompt lives in sentinel/prompts/,
        # not in .agents/prompts/ (it ships with Sentinel, not per-workspace)
        if PROFILER_PROMPT_PATH.exists():
            self.system_prompt = PROFILER_PROMPT_PATH.read_text()
            logger.info("Loaded project profiler system prompt")
        else:
            logger.warning(f"Profiler prompt not found at {PROFILER_PROMPT_PATH}")

    def enrich(
        self,
        repo_path: Path,
        deterministic_profile: Dict[str, Any],
        project_key: str,
    ) -> str:
        """Enrich a deterministic profile with LLM-powered analysis.

        Args:
            repo_path: Path to the project repository (worktree)
            deterministic_profile: Output from StackProfiler.profile()
            project_key: Project key for agent session tracking

        Returns:
            Complete project-context.md markdown content
        """
        logger.info(f"Starting LLM enrichment for {project_key}")

        # Set project for LLM provider configuration
        self.set_project(project_key)

        # Reset session to start fresh
        self.session_id = None
        self.messages.clear()

        # Format deterministic skeleton as compact input
        profiler = StackProfiler()
        skeleton = profiler.format_for_llm_prompt(deterministic_profile)
        stack_type = deterministic_profile.get("stack_type", "unknown")

        prompt = f"""Write a project-context.md for this {stack_type} codebase.

## Authoritative Inventory

A deterministic scan already produced the structural facts below. **Treat this as
authoritative.** Do not re-derive any of it with tools.

```
{skeleton}
```

## What to produce

Write the sections defined in your system prompt. The inventory above tells you
WHAT exists; your job is the *why* and *how* — architectural roles, integration
patterns, conventions, gotchas. That is the part a machine scan cannot produce.

## Tool-use budget — STRICT

You have a hard cap of about 8 file reads. Use them on the highest-leverage
files only:

- 2-3 `.module` files of central modules (whichever look foundational based on
  the inventory's dependency graph)
- 1-2 main service classes from those modules (`src/*.php`)
- 1 representative `.services.yml` to confirm DI conventions
- 1 controller, plugin, or form to confirm UI conventions

That is enough. If you find yourself reaching for a 9th read, stop and write.
Do not glob to enumerate things the inventory already lists.

## Output rules

- Return the markdown document directly in your response. Do NOT use Write.
- Do NOT include a "Codebase Inventory" appendix — Sentinel will append the
  deterministic skeleton itself.
- Cite file paths and class names. Avoid prose without referents.
- ≤ 600 lines total.
"""

        try:
            # max_turns=12 caps the tool-use loop. The deterministic skeleton
            # already covers structural enumeration, so the LLM should be
            # writing prose, not exploring. The previous unbounded loop saw
            # 26 tool calls and 186s for output that should take ~30s.
            response = self.send_message(prompt, cwd=str(repo_path), max_turns=12)
            logger.info(f"LLM enrichment complete ({len(response)} chars)")

            # Append the deterministic skeleton as an appendix so consumers
            # still get the structural reference without spending LLM tokens
            # on it. Trims a section the model used to re-emit verbatim.
            appendix = (
                "\n\n---\n\n## Codebase Inventory (Appendix)\n\n"
                "*Generated deterministically by StackProfiler — do not edit by hand.*\n\n"
                f"```\n{skeleton}\n```\n"
            )
            return response + appendix
        except Exception as e:
            logger.error(f"LLM enrichment failed: {e}")
            raise

    def run(self, **kwargs: Any) -> str:
        """Run the profile enrichment.

        Required kwargs: repo_path, deterministic_profile, project_key
        """
        return self.enrich(
            repo_path=kwargs["repo_path"],
            deterministic_profile=kwargs["deterministic_profile"],
            project_key=kwargs["project_key"],
        )
