"""Unit tests for ConfigLoader."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from src.config_loader import ConfigLoader


@pytest.fixture
def temp_config_dir():
    """Create a temporary directory for config files."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_config(temp_config_dir):
    """Create a sample config.yaml file."""
    config_data = {
        "version": "1.0",
        "agents": {
            "plan_generator": {
                "model": "claude-4-5-opus",
                "temperature": 0.3,
            }
        },
        "workspace": {
            "root_dir": "~/sentinel-workspaces",
            "plans_dir": ".agents/plans",
            "memory_dir": ".agents/memory",
        },
    }

    config_path = temp_config_dir / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config_data, f)

    return config_path


@pytest.fixture
def sample_env(temp_config_dir, monkeypatch):
    """Create a sample .env file and clear existing env vars."""
    # Clear any existing environment variables that might interfere
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("GITLAB_API_TOKEN", raising=False)
    monkeypatch.delenv("GITLAB_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)

    env_path = temp_config_dir / ".env"
    with open(env_path, "w") as f:
        f.write("JIRA_API_TOKEN=test_jira_token\n")
        f.write("JIRA_EMAIL=test@example.com\n")
        f.write("JIRA_BASE_URL=https://test.atlassian.net\n")
        f.write("GITLAB_API_TOKEN=test_gitlab_token\n")
        f.write("GITLAB_BASE_URL=https://gitlab.com\n")
        f.write("ANTHROPIC_API_KEY=test_anthropic_key\n")
        f.write("ANTHROPIC_BASE_URL=https://api.llm-provider.example.com/v1\n")

    return env_path


class TestConfigLoader:
    """Test suite for ConfigLoader class."""

    def test_init_with_custom_path(self, sample_config):
        """Test initialization with custom config path."""
        loader = ConfigLoader(sample_config)
        assert loader.config_path == sample_config

    def test_init_missing_file(self, temp_config_dir):
        """Test initialization with missing config file."""
        missing_path = temp_config_dir / "nonexistent.yaml"
        with pytest.raises(FileNotFoundError):
            ConfigLoader(missing_path)

    def test_get_simple_key(self, sample_config):
        """Test getting a simple configuration value."""
        loader = ConfigLoader(sample_config)
        assert loader.get("version") == "1.0"

    def test_get_nested_key(self, sample_config):
        """Test getting a nested configuration value."""
        loader = ConfigLoader(sample_config)
        assert loader.get("agents.plan_generator.model") == "claude-4-5-opus"
        assert loader.get("workspace.root_dir") == "~/sentinel-workspaces"

    def test_get_missing_key_default(self, sample_config):
        """Test getting a missing key returns default."""
        loader = ConfigLoader(sample_config)
        assert loader.get("nonexistent.key", "default") == "default"
        assert loader.get("nonexistent.key") is None

    def test_get_env(self, sample_config, sample_env):
        """Test getting environment variable."""
        # ConfigLoader auto-loads .env from config_path.parent
        loader = ConfigLoader(sample_config)
        assert loader.get_env("JIRA_API_TOKEN") == "test_jira_token"
        assert loader.get_env("NONEXISTENT", "default") == "default"

    def test_get_jira_config(self, sample_config, sample_env):
        """Test getting Jira configuration."""
        loader = ConfigLoader(sample_config)
        jira_config = loader.get_jira_config()

        assert jira_config["base_url"] == "https://test.atlassian.net"
        assert jira_config["api_token"] == "test_jira_token"
        assert jira_config["email"] == "test@example.com"

    def test_get_gitlab_config(self, sample_config, sample_env):
        """Test getting GitLab configuration."""
        loader = ConfigLoader(sample_config)
        gitlab_config = loader.get_gitlab_config()

        assert gitlab_config["base_url"] == "https://gitlab.com"
        assert gitlab_config["api_token"] == "test_gitlab_token"

    def test_get_llm_provider_config(self, sample_config, sample_env):
        """Test getting LLM Provider configuration (deprecated, uses get_llm_config)."""
        loader = ConfigLoader(sample_config)
        llm_provider_config = loader.get_llm_provider_config()

        # Now uses ANTHROPIC_API_KEY via get_llm_config
        assert llm_provider_config["api_key"] == "test_anthropic_key"
        assert llm_provider_config["base_url"] == "https://api.llm-provider.example.com/v1"

    def test_get_agent_config(self, sample_config):
        """Test getting agent configuration."""
        loader = ConfigLoader(sample_config)
        agent_config = loader.get_agent_config("plan_generator")

        assert agent_config["model"] == "claude-4-5-opus"
        assert agent_config["temperature"] == 0.3

    def test_get_agent_config_missing(self, sample_config):
        """Test getting missing agent configuration."""
        loader = ConfigLoader(sample_config)
        agent_config = loader.get_agent_config("nonexistent_agent")

        assert agent_config == {}

    def test_workspace_root(self, sample_config):
        """Test workspace root property."""
        loader = ConfigLoader(sample_config)
        root = loader.workspace_root

        assert isinstance(root, Path)
        assert "sentinel-workspaces" in str(root)

    def test_plans_dir(self, sample_config):
        """Test plans directory property."""
        loader = ConfigLoader(sample_config)
        plans = loader.plans_dir

        assert isinstance(plans, Path)
        assert str(plans) == ".agents/plans"

    def test_memory_dir(self, sample_config):
        """Test memory directory property."""
        loader = ConfigLoader(sample_config)
        memory = loader.memory_dir

        assert isinstance(memory, Path)
        assert str(memory) == ".agents/memory"

    def test_jira_config_from_env(self, sample_config, monkeypatch):
        """Test that Jira configuration is read from environment variables."""
        monkeypatch.setenv("JIRA_BASE_URL", "https://env.atlassian.net")
        monkeypatch.setenv("JIRA_API_TOKEN", "env_token")
        monkeypatch.setenv("JIRA_EMAIL", "env@example.com")

        loader = ConfigLoader(sample_config)
        jira_config = loader.get_jira_config()

        assert jira_config["base_url"] == "https://env.atlassian.net"
        assert jira_config["api_token"] == "env_token"
        assert jira_config["email"] == "env@example.com"


class TestLocalConfig:
    """Test suite for config.local.yaml functionality."""

    def test_local_config_overrides_base(self, temp_config_dir):
        """Test that config.local.yaml overrides values from config.yaml."""
        # Create base config
        base_config = {
            "version": "1.0",
            "agents": {
                "plan_generator": {
                    "model": "claude-4-5-sonnet",
                    "temperature": 0.3,
                }
            },
            "workspace": {
                "root_dir": "~/base-workspaces",
            },
        }
        config_path = temp_config_dir / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(base_config, f)

        # Create local config with overrides
        local_config = {
            "agents": {
                "plan_generator": {
                    "model": "claude-opus-4-5",
                }
            },
            "workspace": {
                "root_dir": "~/local-workspaces",
            },
        }
        local_config_path = temp_config_dir / "config.local.yaml"
        with open(local_config_path, "w") as f:
            yaml.dump(local_config, f)

        loader = ConfigLoader(config_path)

        # Local values should override base
        assert loader.get("workspace.root_dir") == "~/local-workspaces"
        assert loader.get("agents.plan_generator.model") == "claude-opus-4-5"
        # Non-overridden values should remain
        assert loader.get("agents.plan_generator.temperature") == 0.3
        assert loader.get("version") == "1.0"

    def test_local_config_adds_new_keys(self, temp_config_dir):
        """Test that config.local.yaml can add new keys."""
        base_config = {
            "version": "1.0",
            "agents": {},
        }
        config_path = temp_config_dir / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(base_config, f)

        local_config = {
            "projects": {
                "LOCAL_PROJECT": {
                    "git_url": "https://github.com/local/project.git",
                    "default_branch": "main",
                }
            },
        }
        local_config_path = temp_config_dir / "config.local.yaml"
        with open(local_config_path, "w") as f:
            yaml.dump(local_config, f)

        loader = ConfigLoader(config_path)

        # New keys from local config should be present
        project = loader.get_project_config("LOCAL_PROJECT")
        assert project["git_url"] == "https://github.com/local/project.git"

    def test_no_local_config_uses_base_only(self, sample_config):
        """Test that missing config.local.yaml works fine."""
        loader = ConfigLoader(sample_config)

        # Should use base config values
        assert loader.get("version") == "1.0"
        assert loader.get("workspace.root_dir") == "~/sentinel-workspaces"

    def test_save_config_writes_to_local(self, temp_config_dir):
        """Test that project changes are saved to config.local.yaml."""
        base_config = {
            "version": "1.0",
            "projects": {},
        }
        config_path = temp_config_dir / "config.yaml"
        with open(config_path, "w") as f:
            yaml.dump(base_config, f)

        loader = ConfigLoader(config_path)
        loader.add_project("NEWPROJ", "https://github.com/test/repo.git", "main")

        # Verify project was added
        project = loader.get_project_config("NEWPROJ")
        assert project["git_url"] == "https://github.com/test/repo.git"

        # Verify it was saved to local config
        local_config_path = temp_config_dir / "config.local.yaml"
        assert local_config_path.exists()

        with open(local_config_path, "r") as f:
            saved_local = yaml.safe_load(f)
        assert "NEWPROJ" in saved_local["projects"]

        # Verify base config was NOT modified
        with open(config_path, "r") as f:
            saved_base = yaml.safe_load(f)
        assert "NEWPROJ" not in saved_base.get("projects", {})

    def test_local_config_path_property(self, sample_config):
        """Test that local_config_path is correctly derived."""
        loader = ConfigLoader(sample_config)
        expected_local = sample_config.parent / "config.local.yaml"
        assert loader.local_config_path == expected_local
