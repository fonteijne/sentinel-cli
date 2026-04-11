"""Prompt loader for Sentinel agents."""

from pathlib import Path
from typing import Dict


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

        self._cache: Dict[str, str] = {}

    def load(self, agent_name: str, use_cache: bool = True) -> str:
        """Load system prompt for an agent.

        Args:
            agent_name: Name of the agent (e.g., "plan_generator")
            use_cache: Whether to use cached prompt if available

        Returns:
            System prompt text

        Raises:
            FileNotFoundError: If prompt file doesn't exist
        """
        # Check cache first
        if use_cache and agent_name in self._cache:
            return self._cache[agent_name]

        # Load prompt file
        prompt_file = self.prompts_dir / f"{agent_name}.md"
        if not prompt_file.exists():
            raise FileNotFoundError(
                f"Prompt file not found: {prompt_file}\n"
                f"Expected location: {self.prompts_dir}/{agent_name}.md"
            )

        with open(prompt_file, "r") as f:
            prompt_content = f.read()

        # Load shared base instructions if they exist
        base_instructions = self._load_base_instructions()
        if base_instructions:
            prompt_content = f"{base_instructions}\n\n{prompt_content}"

        # Cache the loaded prompt
        self._cache[agent_name] = prompt_content

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

    def reload(self, agent_name: str) -> str:
        """Reload prompt for an agent, bypassing cache.

        Args:
            agent_name: Name of the agent

        Returns:
            System prompt text
        """
        # Remove from cache if present
        if agent_name in self._cache:
            del self._cache[agent_name]

        return self.load(agent_name, use_cache=False)


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


def load_agent_prompt(agent_name: str) -> str:
    """Convenience function to load an agent prompt.

    Args:
        agent_name: Name of the agent

    Returns:
        System prompt text
    """
    return get_prompt_loader().load(agent_name)
