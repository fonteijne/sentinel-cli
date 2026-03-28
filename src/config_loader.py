"""Configuration loader for Sentinel agents."""

import copy
import os
from pathlib import Path
from typing import Any, Dict

import yaml
from dotenv import load_dotenv


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge two dictionaries, with override taking precedence.

    Args:
        base: Base dictionary
        override: Dictionary with values to override

    Returns:
        Merged dictionary
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class ConfigLoader:
    """Loads and manages Sentinel configuration from YAML and environment.

    Supports a config.local.yaml file that extends/overrides config.yaml.
    The local config file is intended for machine-specific settings that
    should not be committed to version control.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """Initialize the config loader.

        Args:
            config_path: Path to config.yaml. Defaults to config/config.yaml
        """
        if config_path is None:
            # Default to config/config.yaml relative to project root
            self.config_path = Path(__file__).parent.parent / "config" / "config.yaml"
        else:
            self.config_path = Path(config_path)

        # Derive local config path from main config path
        self.local_config_path = self.config_path.parent / "config.local.yaml"

        # Load .env file from config directory if it exists
        # Then load .env.local which takes precedence (for local overrides)
        env_path = self.config_path.parent / ".env"
        env_local_path = self.config_path.parent / ".env.local"
        if env_path.exists():
            load_dotenv(env_path)
        if env_local_path.exists():
            load_dotenv(env_local_path, override=True)

        self._config: Dict[str, Any] = {}
        self._local_config: Dict[str, Any] = {}
        self._load_config()

    def _load_config(self) -> None:
        """Load configuration from YAML files.

        Loads config.yaml as base, then merges config.local.yaml on top
        if it exists. The local config takes precedence for any overlapping keys.
        """
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")

        # Load base config
        with open(self.config_path, "r") as f:
            base_config = yaml.safe_load(f) or {}

        # Load local config if it exists
        if self.local_config_path.exists():
            with open(self.local_config_path, "r") as f:
                self._local_config = yaml.safe_load(f) or {}
        else:
            self._local_config = {}

        # Merge configs (local overrides base)
        self._config = _deep_merge(base_config, self._local_config)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value by key path.

        Args:
            key: Dot-separated key path (e.g., "jira.base_url")
            default: Default value if key not found

        Returns:
            Configuration value or default
        """
        keys = key.split(".")
        value = self._config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def get_env(self, env_key: str, default: str | None = None) -> str | None:
        """Get environment variable value.

        Args:
            env_key: Environment variable name
            default: Default value if not found

        Returns:
            Environment variable value or default
        """
        return os.environ.get(env_key, default)

    def get_jira_config(self) -> Dict[str, Any]:
        """Get Jira configuration from environment variables.

        Returns:
            Dictionary with Jira configuration
        """
        return {
            "base_url": self.get_env("JIRA_BASE_URL", ""),
            "api_token": self.get_env("JIRA_API_TOKEN"),
            "email": self.get_env("JIRA_EMAIL"),
        }

    def get_gitlab_config(self) -> Dict[str, Any]:
        """Get GitLab configuration from environment variables.

        Returns:
            Dictionary with GitLab configuration
        """
        return {
            "base_url": self.get_env("GITLAB_BASE_URL", "https://gitlab.com"),
            "api_token": self.get_env("GITLAB_API_TOKEN"),
        }

    def get_llm_config(self) -> Dict[str, Any]:
        """Get LLM configuration with auto-detected mode.

        Mode detection:
        - API_KEY + BASE_URL: Custom proxy (user-specified endpoint)
        - API_KEY only: Direct Anthropic API
        - Neither: Claude Code subscription

        Returns:
            Dictionary with mode and credentials
        """
        api_key = self.get_env("ANTHROPIC_API_KEY")
        base_url = self.get_env("ANTHROPIC_BASE_URL")

        if api_key and base_url:
            return {
                "mode": "custom_proxy",
                "api_key": api_key,
                "base_url": base_url,
            }
        elif api_key:
            return {
                "mode": "direct_api",
                "api_key": api_key,
                "base_url": None,
            }
        else:
            return {
                "mode": "subscription",
                "api_key": None,
                "base_url": None,
            }

    def get_llm_provider_config(self) -> Dict[str, Any]:
        """Deprecated: Use get_llm_config() instead.

        Returns:
            Dictionary with LLM Provider configuration (for backward compatibility)
        """
        config = self.get_llm_config()
        return {
            "api_key": config.get("api_key"),
            "base_url": config.get("base_url") or "https://api.llm-provider.example.com/v1",
        }

    def get_agent_sdk_config(self) -> Dict[str, Any]:
        """Get Agent SDK configuration with resolved environment variables.

        Returns:
            Dictionary with Agent SDK configuration
        """
        llm_config = self.get_llm_config()
        return {
            **llm_config,
            "default_tools": self.get("agent_sdk.default_tools", ["Read", "Grep", "Glob"]),
            "enable_auto_edits": self.get("agent_sdk.auto_edits", True),
        }

    def get_agent_config(self, agent_name: str) -> Dict[str, Any]:
        """Get configuration for a specific agent.

        Args:
            agent_name: Name of the agent (e.g., "plan_generator")

        Returns:
            Dictionary with agent configuration
        """
        result = self.get(f"agents.{agent_name}", {})
        return dict(result) if result else {}

    def get_project_config(self, project_key: str) -> Dict[str, Any]:
        """Get configuration for a specific project.

        Args:
            project_key: Project key (e.g., "ACME" or "acme", case-insensitive)

        Returns:
            Dictionary with project configuration
        """
        # Try exact match first
        result = self.get(f"projects.{project_key}", {})
        if result:
            return dict(result)

        # Try case-insensitive match
        projects = self.get("projects", {})
        if isinstance(projects, dict):
            for key, value in projects.items():
                if key.upper() == project_key.upper():
                    return dict(value) if value else {}

        return {}

    def get_environment_config(self) -> Dict[str, Any]:
        """Get environment (container orchestration) configuration with defaults.

        Returns:
            Dictionary with environment config
        """
        defaults = {
            "runtime": "dood",
            "health_timeout": 120,
            "auto_detect": True,
            "auto_cleanup": True,
            "volume_name": "sentinel-projects",
        }
        env_config = self.get("environment", {})
        if isinstance(env_config, dict):
            defaults.update(env_config)
        return defaults

    def get_all_projects(self) -> Dict[str, Any]:
        """Get all configured projects.

        Returns:
            Dictionary of all projects keyed by project key
        """
        projects = self.get("projects", {})
        return dict(projects) if projects else {}

    def add_project(
        self, project_key: str, git_url: str, default_branch: str = "main"
    ) -> None:
        """Add a new project to configuration.

        Args:
            project_key: JIRA project key (will be uppercased)
            git_url: Git origin URL
            default_branch: Default branch name

        Raises:
            ValueError: If project already exists
        """
        project_key = project_key.upper()

        # Check if project already exists (case-insensitive)
        existing = self.get_project_config(project_key)
        if existing:
            raise ValueError(f"Project '{project_key}' already exists")

        # Ensure projects dict exists
        if "projects" not in self._config:
            self._config["projects"] = {}

        # Add the new project
        self._config["projects"][project_key] = {
            "git_url": git_url,
            "default_branch": default_branch,
            "jira_project_key": project_key,
        }

        self._save_config()

    def remove_project(self, project_key: str) -> None:
        """Remove a project from configuration.

        Args:
            project_key: JIRA project key (case-insensitive)

        Raises:
            ValueError: If project does not exist
        """
        project_key_upper = project_key.upper()

        # Find the actual key (case-insensitive)
        projects = self.get("projects", {})
        actual_key = None
        if isinstance(projects, dict):
            for key in projects.keys():
                if key.upper() == project_key_upper:
                    actual_key = key
                    break

        if actual_key is None:
            raise ValueError(f"Project '{project_key}' not found")

        del self._config["projects"][actual_key]
        self._save_config()

    def update_project(
        self, project_key: str, git_url: str, default_branch: str
    ) -> None:
        """Update an existing project's configuration.

        Args:
            project_key: JIRA project key (case-insensitive)
            git_url: Git origin URL
            default_branch: Default branch name

        Raises:
            ValueError: If project does not exist
        """
        project_key_upper = project_key.upper()

        # Find the actual key (case-insensitive)
        projects = self.get("projects", {})
        actual_key = None
        if isinstance(projects, dict):
            for key in projects.keys():
                if key.upper() == project_key_upper:
                    actual_key = key
                    break

        if actual_key is None:
            raise ValueError(f"Project '{project_key}' not found")

        # Update the project
        self._config["projects"][actual_key] = {
            "git_url": git_url,
            "default_branch": default_branch,
            "jira_project_key": actual_key,
        }

        self._save_config()

    def _save_config(self) -> None:
        """Write project configuration to config.local.yaml.

        Project additions/updates/removals are saved to the local config file
        to avoid committing machine-specific project configurations to git.
        """
        # Ensure projects section exists in local config
        if "projects" not in self._local_config:
            self._local_config["projects"] = {}

        # Sync projects from merged config to local config
        self._local_config["projects"] = self._config.get("projects", {})

        with open(self.local_config_path, "w") as f:
            yaml.dump(self._local_config, f, default_flow_style=False, sort_keys=False)

    @property
    def workspace_root(self) -> Path:
        """Get workspace root directory path.

        Returns:
            Path to workspace root
        """
        root_str = self.get("workspace.root_dir", "~/sentinel-workspaces")
        return Path(root_str).expanduser()

    @property
    def plans_dir(self) -> Path:
        """Get plans directory path.

        Returns:
            Path to plans directory
        """
        return Path(self.get("workspace.plans_dir", ".agents/plans"))

    @property
    def memory_dir(self) -> Path:
        """Get memory directory path.

        Returns:
            Path to memory directory
        """
        return Path(self.get("workspace.memory_dir", ".agents/memory"))


# Global config instance
_config: ConfigLoader | None = None


def get_config() -> ConfigLoader:
    """Get the global configuration instance.

    Returns:
        ConfigLoader instance
    """
    global _config
    if _config is None:
        _config = ConfigLoader()
    return _config
