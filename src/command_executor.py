"""Command executor for custom agent commands."""

from pathlib import Path
from typing import Any, Dict, List

import yaml


class CommandDefinition:
    """Represents a custom agent command definition."""

    def __init__(self, data: Dict[str, Any]) -> None:
        """Initialize command definition from YAML data.

        Args:
            data: Command definition dictionary
        """
        self.name = data.get("name", "")
        self.description = data.get("description", "")
        self.version = data.get("version", "1.0")
        self.parameters = data.get("parameters", {})
        self.workflow = data.get("workflow", [])
        self.configuration = data.get("configuration", {})
        self.quality_gates = data.get("quality_gates", [])
        self.example_usage = data.get("example_usage", "")

    def validate_parameters(self, params: Dict[str, Any]) -> List[str]:
        """Validate provided parameters against definition.

        Args:
            params: Parameters to validate

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        for param_name, param_def in self.parameters.items():
            is_required = param_def.get("required", False)
            param_type = param_def.get("type", "string")

            if is_required and param_name not in params:
                errors.append(f"Required parameter '{param_name}' is missing")

            if param_name in params:
                value = params[param_name]
                # Basic type checking
                if param_type == "string" and not isinstance(value, str):
                    errors.append(f"Parameter '{param_name}' must be a string")
                elif param_type == "number" and not isinstance(value, (int, float)):
                    errors.append(f"Parameter '{param_name}' must be a number")
                elif param_type == "boolean" and not isinstance(value, bool):
                    errors.append(f"Parameter '{param_name}' must be a boolean")

        return errors


class CommandExecutor:
    """Executes custom agent commands defined in YAML."""

    def __init__(self, commands_dir: Path | None = None) -> None:
        """Initialize the command executor.

        Args:
            commands_dir: Path to commands directory. Defaults to .agents/commands/
        """
        if commands_dir is None:
            # Default to commands/ relative to sentinel package root
            sentinel_root = Path(__file__).parent.parent
            self.commands_dir = sentinel_root / "commands"
        else:
            self.commands_dir = Path(commands_dir)

        self._command_cache: Dict[str, CommandDefinition] = {}

    def load_command(self, command_name: str, agent_type: str | None = None) -> CommandDefinition:
        """Load a command definition.

        Args:
            command_name: Name of the command (e.g., "implement-tdd")
            agent_type: Agent type directory (e.g., "python_developer"). If None, searches all.

        Returns:
            CommandDefinition instance

        Raises:
            FileNotFoundError: If command file doesn't exist
        """
        cache_key = f"{agent_type or 'any'}:{command_name}"
        if cache_key in self._command_cache:
            return self._command_cache[cache_key]

        # Try to find the command file
        command_file = None

        if agent_type:
            # Look in specific agent directory
            candidate = self.commands_dir / agent_type / f"{command_name}.yaml"
            if candidate.exists():
                command_file = candidate
        else:
            # Search all agent directories
            for agent_dir in self.commands_dir.iterdir():
                if agent_dir.is_dir():
                    candidate = agent_dir / f"{command_name}.yaml"
                    if candidate.exists():
                        command_file = candidate
                        break

        if command_file is None:
            raise FileNotFoundError(
                f"Command '{command_name}' not found in {self.commands_dir}\n"
                f"Agent type: {agent_type or 'any'}"
            )

        # Load and parse YAML
        with open(command_file, "r") as f:
            command_data = yaml.safe_load(f)

        command_def = CommandDefinition(command_data)
        self._command_cache[cache_key] = command_def

        return command_def

    def execute(
        self, command_name: str, parameters: Dict[str, Any], agent_type: str | None = None
    ) -> Dict[str, Any]:
        """Execute a command with given parameters.

        Args:
            command_name: Name of the command
            parameters: Command parameters
            agent_type: Agent type directory (optional)

        Returns:
            Execution result dictionary with:
                - success: bool
                - command: CommandDefinition
                - parameters: Dict[str, Any]
                - errors: List[str] (if validation failed)

        Note:
            This returns the command definition and validated parameters.
            The actual execution logic should be implemented by the agent
            using the workflow steps from the definition.
        """
        # Load command definition
        try:
            command_def = self.load_command(command_name, agent_type)
        except FileNotFoundError as e:
            return {
                "success": False,
                "errors": [str(e)],
            }

        # Validate parameters
        validation_errors = command_def.validate_parameters(parameters)
        if validation_errors:
            return {
                "success": False,
                "command": command_def,
                "parameters": parameters,
                "errors": validation_errors,
            }

        # Return successful validation with command definition
        return {
            "success": True,
            "command": command_def,
            "parameters": parameters,
            "workflow": command_def.workflow,
            "configuration": command_def.configuration,
            "quality_gates": command_def.quality_gates,
        }

    def list_commands(self, agent_type: str | None = None) -> List[Dict[str, str]]:
        """List available commands.

        Args:
            agent_type: Filter by agent type (optional)

        Returns:
            List of command info dictionaries with name, agent_type, description
        """
        commands = []

        if agent_type:
            # List commands for specific agent type
            agent_dir = self.commands_dir / agent_type
            if agent_dir.exists() and agent_dir.is_dir():
                for yaml_file in agent_dir.glob("*.yaml"):
                    try:
                        with open(yaml_file, "r") as f:
                            data = yaml.safe_load(f)
                        commands.append({
                            "name": data.get("name", yaml_file.stem),
                            "agent_type": agent_type,
                            "description": data.get("description", ""),
                        })
                    except Exception:
                        # Skip invalid files
                        pass
        else:
            # List all commands
            for agent_dir in self.commands_dir.iterdir():
                if agent_dir.is_dir():
                    for yaml_file in agent_dir.glob("*.yaml"):
                        try:
                            with open(yaml_file, "r") as f:
                                data = yaml.safe_load(f)
                            commands.append({
                                "name": data.get("name", yaml_file.stem),
                                "agent_type": agent_dir.name,
                                "description": data.get("description", ""),
                            })
                        except Exception:
                            # Skip invalid files
                            pass

        return commands


# Global command executor instance
_executor: CommandExecutor | None = None


def get_command_executor() -> CommandExecutor:
    """Get the global command executor instance.

    Returns:
        CommandExecutor instance
    """
    global _executor
    if _executor is None:
        _executor = CommandExecutor()
    return _executor


def execute_command(
    command_name: str, parameters: Dict[str, Any], agent_type: str | None = None
) -> Dict[str, Any]:
    """Convenience function to execute a command.

    Args:
        command_name: Name of the command
        parameters: Command parameters
        agent_type: Agent type (optional)

    Returns:
        Execution result dictionary
    """
    return get_command_executor().execute(command_name, parameters, agent_type)
