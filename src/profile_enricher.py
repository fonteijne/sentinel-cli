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

        prompt = f"""Analyze this codebase and produce a comprehensive project profile.

## Deterministic Inventory

A machine scan of this {stack_type} project produced the following inventory:

```
{skeleton}
```

## Your Task

Using your tools (Read, Glob, Grep), explore this codebase and produce a complete
project-context.md document following the sections defined in your system prompt.

The inventory above tells you WHAT exists. Your job is to explain WHY it's structured
this way, HOW the pieces fit together, and WHAT conventions to follow.

**Key files to start with:**
- `composer.json` (dependencies and project config)
- `.lando.yml` (environment setup)
- `web/modules/custom/*/src/*.php` (main service classes)
- `web/modules/custom/*/*.module` (hook implementations)
- `web/modules/custom/*/*.services.yml` (dependency injection)
- `web/themes/custom/*/` (theme structure)

**Important:**
- Read actual code — don't guess from file names
- Be specific — cite file paths and class names
- Be concise — agents don't need essays, they need actionable context
- Include the deterministic inventory as a compact appendix at the end

Return the complete markdown document directly in your response.
Do NOT use the Write tool. Just output the markdown.
"""

        try:
            response = self.send_message(prompt, cwd=str(repo_path))
            logger.info(f"LLM enrichment complete ({len(response)} chars)")
            return response
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
