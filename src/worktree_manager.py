"""Git worktree manager for isolated feature development."""

import subprocess
from pathlib import Path
from typing import Optional

from src.attachment_manager import AttachmentManager
from src.config_loader import get_config

# Branch prefix for all Sentinel-created branches
BRANCH_PREFIX = "sentinel/feature"


def get_branch_name(ticket_id: str) -> str:
    """Get the standard branch name for a ticket.

    Args:
        ticket_id: Ticket ID (e.g., "ACME-123")

    Returns:
        Branch name (e.g., "sentinel/feature/ACME-123")
    """
    return f"{BRANCH_PREFIX}/{ticket_id}"


class WorktreeManager:
    """Manages git worktrees for isolated ticket development."""

    def __init__(self) -> None:
        """Initialize worktree manager."""
        self.config = get_config()
        self.workspace_root = self.config.workspace_root

    def _find_existing_clone(self, git_url: str, exclude_key: str) -> Optional[Path]:
        """Find an existing bare clone for the same git URL under a different project.

        Multiple Jira projects can share the same git repo. When one project
        already has a bare clone, other projects can symlink to it instead
        of cloning again.

        Args:
            git_url: Git URL to match
            exclude_key: Project key to skip (the one we're creating for)

        Returns:
            Path to existing bare clone, or None
        """
        all_projects = self.config.get_all_projects()
        for key, cfg in all_projects.items():
            if key.upper() == exclude_key.upper():
                continue
            if cfg.get("git_url") == git_url:
                candidate = self.workspace_root / key.lower()
                if candidate.exists() and (candidate / "config").exists():
                    return candidate
        return None

    def ensure_bare_clone(self, project_key: str) -> Path:
        """Ensure a bare clone exists for the project.

        If another project shares the same git_url and already has a bare
        clone, creates a symlink instead of cloning again.

        Args:
            project_key: Project key (e.g., "ACME")

        Returns:
            Path to the bare clone directory

        Raises:
            ValueError: If project config not found
            subprocess.CalledProcessError: If git operations fail
        """
        project_config = self.config.get_project_config(project_key)
        if not project_config:
            raise ValueError(f"No configuration found for project: {project_key}")

        git_url = project_config.get("git_url")
        if not git_url:
            raise ValueError(f"No git_url configured for project: {project_key}")

        # Ensure workspace root exists
        self.workspace_root.mkdir(parents=True, exist_ok=True)

        # Bare clone directory
        bare_clone_dir = self.workspace_root / project_key.lower()

        # If no bare clone exists, check if another project shares the same
        # git_url and already has one — symlink to it instead of re-cloning.
        if not bare_clone_dir.exists():
            existing_clone = self._find_existing_clone(git_url, project_key)
            if existing_clone:
                bare_clone_dir.symlink_to(existing_clone)
                import logging
                logging.getLogger(__name__).info(
                    f"Reusing bare clone {existing_clone} for project {project_key} "
                    f"(same git_url)"
                )

        # Check if bare clone already exists
        if (bare_clone_dir / "config").exists():
            # Verify it's a bare repo
            try:
                result = subprocess.run(
                    ["git", "config", "--get", "core.bare"],
                    cwd=bare_clone_dir,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                if result.stdout.strip() == "true":
                    # Fetch latest changes
                    subprocess.run(
                        ["git", "fetch", "--all"],
                        cwd=bare_clone_dir,
                        check=True,
                    )
                    return bare_clone_dir
            except subprocess.CalledProcessError:
                # Not a bare repo or corrupted, remove and recreate
                import shutil
                shutil.rmtree(bare_clone_dir)

        # Create new bare clone
        subprocess.run(
            ["git", "clone", "--bare", git_url, str(bare_clone_dir)],
            cwd=self.workspace_root,
            check=True,
        )

        return bare_clone_dir

    def create_worktree(self, ticket_id: str, project_key: str) -> Path:
        """Create a git worktree for a ticket.

        Args:
            ticket_id: Ticket ID (e.g., "ACME-123")
            project_key: Project key (e.g., "ACME")

        Returns:
            Path to the worktree directory

        Raises:
            ValueError: If configuration is invalid
            subprocess.CalledProcessError: If git operations fail
        """
        # Ensure bare clone exists
        bare_clone_dir = self.ensure_bare_clone(project_key)

        # Get default branch
        project_config = self.config.get_project_config(project_key)
        default_branch = project_config.get("default_branch", "main")

        # Worktree directory
        worktree_dir = bare_clone_dir / ticket_id

        # Check if worktree already exists
        if worktree_dir.exists():
            # Verify it's a valid worktree
            try:
                subprocess.run(
                    ["git", "status"],
                    cwd=worktree_dir,
                    capture_output=True,
                    check=True,
                )
                return worktree_dir
            except subprocess.CalledProcessError:
                # Invalid worktree, remove and recreate
                import shutil
                shutil.rmtree(worktree_dir)

        # Create worktree from default branch
        subprocess.run(
            ["git", "worktree", "add", str(worktree_dir), default_branch],
            cwd=bare_clone_dir,
            check=True,
        )

        # Create or checkout feature branch
        branch_name = get_branch_name(ticket_id)

        # Check if branch already exists
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=worktree_dir,
            capture_output=True,
        )

        if result.returncode == 0:
            # Branch exists locally, just checkout to it
            subprocess.run(
                ["git", "checkout", branch_name],
                cwd=worktree_dir,
                check=True,
            )
        else:
            # Check if branch exists on remote (e.g. after a reset)
            remote_ref = f"origin/{branch_name}"
            remote_result = subprocess.run(
                ["git", "rev-parse", "--verify", remote_ref],
                cwd=worktree_dir,
                capture_output=True,
            )

            if remote_result.returncode == 0:
                # Remote branch exists — create local branch tracking it
                subprocess.run(
                    ["git", "checkout", "-b", branch_name, remote_ref],
                    cwd=worktree_dir,
                    check=True,
                )
            else:
                # No local or remote branch — create fresh
                subprocess.run(
                    ["git", "checkout", "-b", branch_name],
                    cwd=worktree_dir,
                    check=True,
                )

        return worktree_dir

    def initialize_python_project(self, worktree_path: Path, project_name: str) -> None:
        """Initialize a Python project structure in the worktree.

        Creates pyproject.toml and basic directory structure so that generated
        code can be tested in isolation with its own dependencies.

        Args:
            worktree_path: Path to the worktree directory
            project_name: Name of the project (e.g., "todo-app")

        Raises:
            FileExistsError: If pyproject.toml already exists
        """
        import logging
        logger = logging.getLogger(__name__)

        # Check if already initialized
        pyproject_file = worktree_path / "pyproject.toml"
        if pyproject_file.exists():
            logger.info(f"Python project already initialized in {worktree_path}")
            return

        # Create basic project structure
        (worktree_path / "src").mkdir(exist_ok=True)
        (worktree_path / "tests").mkdir(exist_ok=True)

        # Create __init__.py files
        (worktree_path / "src" / "__init__.py").touch()
        (worktree_path / "tests" / "__init__.py").touch()

        # Create pyproject.toml
        pyproject_content = f'''[tool.poetry]
name = "{project_name}"
version = "0.1.0"
description = "Generated implementation for {project_name}"
authors = ["Sentinel AI <sentinel@example.com>"]
readme = "README.md"
packages = [{{include = "src"}}]

[tool.poetry.dependencies]
python = "^3.11"

[tool.poetry.group.dev.dependencies]
pytest = "^7.4.0"
pytest-cov = "^4.1.0"
mypy = "^1.7.0"
ruff = "^0.1.0"

[build-system]
requires = ["poetry-core"]
build-backend = "poetry.core.masonry.api"

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --tb=short"

[tool.mypy]
python_version = "3.11"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = false
strict_optional = true

[tool.ruff]
line-length = 100
target-version = "py311"
select = ["E", "F", "I", "N", "W"]
ignore = []
'''
        pyproject_file.write_text(pyproject_content)

        # Create README.md
        readme_file = worktree_path / "README.md"
        readme_content = f'''# {project_name}

Generated implementation by Sentinel AI.

## Development

Install dependencies:
```bash
poetry install
```

Run tests:
```bash
poetry run pytest
```

Run type checking:
```bash
poetry run mypy src/
```

Run linting:
```bash
poetry run ruff check src/
```
'''
        readme_file.write_text(readme_content)

        logger.info(f"Initialized Python project structure in {worktree_path}")

    def cleanup_worktree(self, ticket_id: str, project_key: str) -> None:
        """Remove a git worktree.

        Args:
            ticket_id: Ticket ID
            project_key: Project key

        Raises:
            subprocess.CalledProcessError: If git operations fail
        """
        bare_clone_dir = self.workspace_root / project_key.lower()
        worktree_dir = bare_clone_dir / ticket_id

        if not worktree_dir.exists():
            return

        # Clean up attachments before removing worktree
        AttachmentManager.cleanup(ticket_id, worktree_dir)

        # Remove worktree
        subprocess.run(
            ["git", "worktree", "remove", str(worktree_dir), "--force"],
            cwd=bare_clone_dir,
            check=False,  # Don't fail if worktree doesn't exist
        )

    def delete_local_branch(self, ticket_id: str, project_key: str) -> bool:
        """Delete a local feature branch from the bare repository.

        Args:
            ticket_id: Ticket ID (e.g., ACME-123)
            project_key: Project key (e.g., ACME)

        Returns:
            True if branch was deleted, False if it didn't exist
        """
        bare_clone_dir = self.workspace_root / project_key.lower()
        branch_name = get_branch_name(ticket_id)

        if not bare_clone_dir.exists():
            return False

        # Check if branch exists
        result = subprocess.run(
            ["git", "rev-parse", "--verify", branch_name],
            cwd=bare_clone_dir,
            capture_output=True,
        )

        if result.returncode != 0:
            return False  # Branch doesn't exist

        # Delete the branch
        subprocess.run(
            ["git", "branch", "-D", branch_name],
            cwd=bare_clone_dir,
            check=True,
        )
        return True

    def reset_ticket(self, ticket_id: str, project_key: str) -> dict[str, bool]:
        """Reset a ticket by removing its worktree and local branch.

        Args:
            ticket_id: Ticket ID (e.g., ACME-123)
            project_key: Project key (e.g., ACME)

        Returns:
            Dict with 'worktree_removed' and 'branch_deleted' booleans
        """
        result = {
            "worktree_removed": False,
            "branch_deleted": False,
        }

        # First remove worktree (must be done before deleting branch)
        bare_clone_dir = self.workspace_root / project_key.lower()
        worktree_dir = bare_clone_dir / ticket_id

        if worktree_dir.exists():
            self.cleanup_worktree(ticket_id, project_key)
            result["worktree_removed"] = True

        # Then delete the local branch
        result["branch_deleted"] = self.delete_local_branch(ticket_id, project_key)

        return result

    def reset_all(self, project_key: str) -> dict[str, int]:
        """Reset all worktrees and remove the bare repository for a project.

        Args:
            project_key: Project key (e.g., ACME)

        Returns:
            Dict with counts: 'worktrees_removed', 'repo_removed' (0 or 1)
        """
        import shutil

        result = {
            "worktrees_removed": 0,
            "repo_removed": 0,
        }

        bare_clone_dir = self.workspace_root / project_key.lower()

        if not bare_clone_dir.exists():
            return result

        # First cleanup all worktrees
        result["worktrees_removed"] = self.cleanup_all_worktrees(project_key)

        # Then remove the entire bare repository
        shutil.rmtree(bare_clone_dir)
        result["repo_removed"] = 1

        return result

    def get_worktree_path(self, ticket_id: str, project_key: str) -> Optional[Path]:
        """Get the path to a worktree if it exists.

        Args:
            ticket_id: Ticket ID
            project_key: Project key

        Returns:
            Path to worktree or None if it doesn't exist
        """
        bare_clone_dir = self.workspace_root / project_key.lower()
        worktree_dir = bare_clone_dir / ticket_id

        if worktree_dir.exists():
            # Verify it's a valid git directory
            try:
                subprocess.run(
                    ["git", "status"],
                    cwd=worktree_dir,
                    capture_output=True,
                    check=True,
                )
                return worktree_dir
            except subprocess.CalledProcessError:
                return None

        return None

    def list_worktrees(self, project_key: str) -> list[str]:
        """List all worktrees for a project.

        Args:
            project_key: Project key

        Returns:
            List of ticket IDs with active worktrees
        """
        bare_clone_dir = self.workspace_root / project_key.lower()

        if not bare_clone_dir.exists():
            return []

        try:
            result = subprocess.run(
                ["git", "worktree", "list", "--porcelain"],
                cwd=bare_clone_dir,
                capture_output=True,
                text=True,
                check=True,
            )

            # Parse worktree list output
            worktrees = []
            for line in result.stdout.split("\n"):
                if line.startswith("worktree"):
                    path = Path(line.split(" ", 1)[1])
                    # Extract ticket ID from path
                    ticket_id = path.name
                    if ticket_id != project_key.lower():  # Skip bare repo itself
                        worktrees.append(ticket_id)

            return worktrees

        except subprocess.CalledProcessError:
            return []

    def cleanup_all_worktrees(self, project_key: str) -> int:
        """Remove all worktrees for a project.

        Args:
            project_key: Project key

        Returns:
            Number of worktrees removed

        Raises:
            subprocess.CalledProcessError: If git operations fail
        """
        bare_clone_dir = self.workspace_root / project_key.lower()

        if not bare_clone_dir.exists():
            return 0

        # Get list of all worktrees
        worktrees = self.list_worktrees(project_key)
        removed_count = 0

        # Remove each worktree
        for ticket_id in worktrees:
            try:
                self.cleanup_worktree(ticket_id, project_key)
                removed_count += 1
            except subprocess.CalledProcessError:
                # Log error but continue with other worktrees
                import logging
                logger = logging.getLogger(__name__)
                logger.warning(f"Failed to remove worktree for {ticket_id}")

        return removed_count
