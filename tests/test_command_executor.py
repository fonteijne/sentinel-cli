"""Unit tests for CommandExecutor."""

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from src.command_executor import CommandDefinition, CommandExecutor


@pytest.fixture
def temp_commands_dir():
    """Create a temporary directory for command files."""
    with TemporaryDirectory() as tmpdir:
        commands_dir = Path(tmpdir) / "commands"
        commands_dir.mkdir()

        # Create agent type directories
        python_dir = commands_dir / "python_developer"
        python_dir.mkdir()
        plan_dir = commands_dir / "plan_generator"
        plan_dir.mkdir()

        # Create sample command YAML files with proper structure
        implement_cmd = {
            "name": "implement-tdd",
            "description": "Implement feature using TDD",
            "version": "1.0",
            "parameters": {
                "feature_description": {
                    "type": "string",
                    "required": True,
                    "description": "Description of feature to implement"
                },
                "test_framework": {
                    "type": "string",
                    "required": False,
                    "description": "Testing framework to use"
                }
            },
            "workflow": [
                "Write failing test",
                "Implement feature",
                "Refactor"
            ],
            "configuration": {
                "language": "python",
                "style": "pep8"
            },
            "quality_gates": [
                "All tests pass",
                "Code coverage > 80%"
            ],
            "example_usage": "implement-tdd --feature_description='Add user login'"
        }

        plan_cmd = {
            "name": "create-plan",
            "description": "Generate implementation plan",
            "version": "1.0",
            "parameters": {
                "ticket_id": {
                    "type": "string",
                    "required": True,
                    "description": "JIRA ticket ID"
                }
            },
            "workflow": [
                "Analyze ticket",
                "Create plan",
                "Review plan"
            ],
            "configuration": {},
            "quality_gates": ["Plan is complete"],
            "example_usage": "create-plan --ticket_id=PROJ-123"
        }

        with open(python_dir / "implement-tdd.yaml", "w") as f:
            yaml.dump(implement_cmd, f)

        with open(plan_dir / "create-plan.yaml", "w") as f:
            yaml.dump(plan_cmd, f)

        yield commands_dir


class TestCommandDefinition:
    """Test suite for CommandDefinition class."""

    def test_init_from_dict(self):
        """Test initializing CommandDefinition from dictionary."""
        data = {
            "name": "test-command",
            "description": "Test command",
            "version": "1.0",
            "parameters": {
                "param1": {"type": "string", "required": True}
            },
            "workflow": ["step1", "step2"],
            "configuration": {"key": "value"},
            "quality_gates": ["gate1"],
            "example_usage": "test-command --param1=value"
        }

        cmd = CommandDefinition(data)
        assert cmd.name == "test-command"
        assert cmd.description == "Test command"
        assert cmd.version == "1.0"
        assert "param1" in cmd.parameters
        assert len(cmd.workflow) == 2
        assert cmd.configuration["key"] == "value"

    def test_validate_parameters_success(self):
        """Test parameter validation with valid parameters."""
        data = {
            "name": "test",
            "parameters": {
                "required_string": {"type": "string", "required": True},
                "optional_number": {"type": "number", "required": False}
            }
        }

        cmd = CommandDefinition(data)
        params = {"required_string": "value"}
        errors = cmd.validate_parameters(params)
        assert errors == []

    def test_validate_parameters_missing_required(self):
        """Test parameter validation with missing required parameter."""
        data = {
            "name": "test",
            "parameters": {
                "required_param": {"type": "string", "required": True}
            }
        }

        cmd = CommandDefinition(data)
        params = {}
        errors = cmd.validate_parameters(params)
        assert len(errors) == 1
        assert "required_param" in errors[0]
        assert "missing" in errors[0].lower()

    def test_validate_parameters_wrong_type(self):
        """Test parameter validation with wrong parameter type."""
        data = {
            "name": "test",
            "parameters": {
                "string_param": {"type": "string", "required": False},
                "number_param": {"type": "number", "required": False},
                "boolean_param": {"type": "boolean", "required": False}
            }
        }

        cmd = CommandDefinition(data)
        params = {
            "string_param": 123,  # Should be string
            "number_param": "not a number",  # Should be number
            "boolean_param": "true"  # Should be boolean
        }
        errors = cmd.validate_parameters(params)
        assert len(errors) == 3


class TestCommandExecutor:
    """Test suite for CommandExecutor class."""

    def test_init_with_custom_path(self, temp_commands_dir):
        """Test initialization with custom commands directory."""
        executor = CommandExecutor(temp_commands_dir)
        assert executor.commands_dir == temp_commands_dir

    def test_init_missing_directory(self):
        """Test initialization with missing commands directory does not raise error."""
        missing_path = Path("/nonexistent/commands")
        # CommandExecutor doesn't raise on init, only on load_command()
        executor = CommandExecutor(missing_path)
        assert executor.commands_dir == missing_path

    def test_load_existing_command_with_agent_type(self, temp_commands_dir):
        """Test loading an existing command with agent type specified."""
        executor = CommandExecutor(temp_commands_dir)
        command = executor.load_command("implement-tdd", agent_type="python_developer")

        assert isinstance(command, CommandDefinition)
        assert command.name == "implement-tdd"
        assert command.description == "Implement feature using TDD"
        assert "feature_description" in command.parameters

    def test_load_existing_command_without_agent_type(self, temp_commands_dir):
        """Test loading a command by searching all agent directories."""
        executor = CommandExecutor(temp_commands_dir)
        command = executor.load_command("implement-tdd", agent_type=None)

        assert isinstance(command, CommandDefinition)
        assert command.name == "implement-tdd"

    def test_load_nonexistent_command(self, temp_commands_dir):
        """Test loading a nonexistent command."""
        executor = CommandExecutor(temp_commands_dir)

        with pytest.raises(FileNotFoundError):
            executor.load_command("nonexistent_command")

    def test_list_commands_all(self, temp_commands_dir):
        """Test listing all available commands."""
        executor = CommandExecutor(temp_commands_dir)
        commands = executor.list_commands()

        assert len(commands) == 2
        command_names = [cmd["name"] for cmd in commands]
        assert "implement-tdd" in command_names
        assert "create-plan" in command_names

        # Check structure
        for cmd in commands:
            assert "name" in cmd
            assert "agent_type" in cmd
            assert "description" in cmd

    def test_list_commands_by_agent_type(self, temp_commands_dir):
        """Test listing commands for specific agent type."""
        executor = CommandExecutor(temp_commands_dir)
        commands = executor.list_commands(agent_type="python_developer")

        assert len(commands) == 1
        assert commands[0]["name"] == "implement-tdd"
        assert commands[0]["agent_type"] == "python_developer"

    def test_execute_command_success(self, temp_commands_dir):
        """Test executing a command with valid parameters."""
        executor = CommandExecutor(temp_commands_dir)
        result = executor.execute(
            "implement-tdd",
            {"feature_description": "Add login feature"},
            agent_type="python_developer"
        )

        assert result["success"] is True
        assert isinstance(result["command"], CommandDefinition)
        assert result["command"].name == "implement-tdd"
        assert "workflow" in result
        assert "configuration" in result
        assert "quality_gates" in result

    def test_execute_command_missing_parameters(self, temp_commands_dir):
        """Test executing a command with missing required parameters."""
        executor = CommandExecutor(temp_commands_dir)
        result = executor.execute(
            "implement-tdd",
            {},  # Missing required feature_description
            agent_type="python_developer"
        )

        assert result["success"] is False
        assert "errors" in result
        assert len(result["errors"]) > 0
        assert "feature_description" in result["errors"][0]

    def test_execute_command_not_found(self, temp_commands_dir):
        """Test executing a nonexistent command."""
        executor = CommandExecutor(temp_commands_dir)
        result = executor.execute("nonexistent", {})

        assert result["success"] is False
        assert "errors" in result

    def test_command_caching(self, temp_commands_dir):
        """Test that commands are cached after loading."""
        executor = CommandExecutor(temp_commands_dir)

        # Load command
        cmd1 = executor.load_command("implement-tdd", agent_type="python_developer")
        assert len(executor._command_cache) == 1

        # Load same command again (should use cache)
        cmd2 = executor.load_command("implement-tdd", agent_type="python_developer")
        assert cmd1 is cmd2  # Same object reference

    def test_empty_commands_directory(self):
        """Test with an empty commands directory."""
        with TemporaryDirectory() as tmpdir:
            commands_dir = Path(tmpdir) / "commands"
            commands_dir.mkdir()

            executor = CommandExecutor(commands_dir)
            commands = executor.list_commands()

            assert commands == []
