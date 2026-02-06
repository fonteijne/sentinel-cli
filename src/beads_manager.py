"""Beads CLI wrapper for task coordination."""

import subprocess
from typing import Any, Dict, List, Optional


class BeadsManager:
    """Manages beads tasks for agent coordination."""

    def __init__(self) -> None:
        """Initialize beads manager."""
        # Verify beads is installed
        try:
            subprocess.run(
                ["bd", "--version"],
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError("beads CLI (bd) not found. Install with: npm install -g @beads/bd")

    def init_project(self, ticket_id: str, working_dir: Optional[str] = None) -> None:
        """Initialize beads project for a ticket (if not already initialized).

        Args:
            ticket_id: Ticket ID (e.g., "ACME-123")
            working_dir: Working directory (optional)

        Note:
            If beads is already initialized in the directory, this is a no-op.
            For git worktrees, beads must be initialized in the bare repository.
        """
        from pathlib import Path

        # Check if beads is already initialized
        check_cmd = ["bd", "stats"]
        result = subprocess.run(
            check_cmd,
            cwd=working_dir,
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            # Already initialized
            return

        # Determine the correct directory to initialize beads
        # If this is a git worktree, we need to initialize in the bare repo
        init_dir = working_dir

        if working_dir:
            work_path = Path(working_dir)

            # Check if this is a worktree by looking for .git file (not directory)
            git_path = work_path / ".git"
            if git_path.is_file():
                # This is a worktree, find the bare repo
                # The parent directory of the worktree is the bare repo
                bare_repo = work_path.parent
                init_dir = str(bare_repo)

        # Initialize new beads project in the correct directory
        subprocess.run(
            ["bd", "init"],
            cwd=init_dir,
            capture_output=True,
            check=True,
        )

    def create_task(
        self,
        title: str,
        task_type: str = "task",
        priority: int = 2,
        description: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> str:
        """Create a new beads task.

        Args:
            title: Task title
            task_type: Task type - "task", "bug", or "feature"
            priority: Priority 0-4 (0=critical, 2=medium, 4=backlog)
            description: Task description (optional)
            working_dir: Working directory (optional)

        Returns:
            Task ID (e.g., "sentinel-abc")

        Raises:
            subprocess.CalledProcessError: If bd command fails
        """
        cmd = [
            "bd", "create",
            f"--title={title}",
            f"--type={task_type}",
            f"--priority={priority}",
        ]

        if description:
            cmd.append(f"--description={description}")

        result = subprocess.run(
            cmd,
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        # Extract task ID from output
        # Output format: "✓ Created issue: sentinel-abc: Title"
        output = result.stdout.strip()
        if "Created issue:" in output:
            task_id = output.split("Created issue:")[1].split(":")[0].strip()
            return task_id

        raise RuntimeError(f"Failed to extract task ID from: {output}")

    def update_task(
        self,
        task_id: str,
        status: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        notes: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> None:
        """Update a beads task.

        Args:
            task_id: Task ID
            status: New status - "open", "in_progress", "blocked", "closed"
            title: New title (optional)
            description: New description (optional)
            notes: Additional notes to append (optional)
            working_dir: Working directory (optional)

        Raises:
            subprocess.CalledProcessError: If bd command fails
        """
        cmd = ["bd", "update", task_id]

        if status:
            cmd.append(f"--status={status}")
        if title:
            cmd.append(f"--title={title}")
        if description:
            cmd.append(f"--description={description}")
        if notes:
            cmd.append(f"--notes={notes}")

        subprocess.run(
            cmd,
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=True,
        )

    def close_task(
        self,
        task_id: str,
        reason: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> None:
        """Close a beads task.

        Args:
            task_id: Task ID
            reason: Reason for closing (optional)
            working_dir: Working directory (optional)

        Raises:
            subprocess.CalledProcessError: If bd command fails
        """
        cmd = ["bd", "close", task_id]

        if reason:
            cmd.append(f"--reason={reason}")

        subprocess.run(
            cmd,
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=True,
        )

    def get_task(self, task_id: str, working_dir: Optional[str] = None) -> Dict[str, Any]:
        """Get details of a beads task.

        Args:
            task_id: Task ID
            working_dir: Working directory (optional)

        Returns:
            Dictionary with task details

        Raises:
            subprocess.CalledProcessError: If bd command fails
        """
        result = subprocess.run(
            ["bd", "show", task_id],
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        # Parse the output (simple text-based parsing)
        # This is a simplified version - actual implementation may need more robust parsing
        output = result.stdout
        lines = output.strip().split("\n")

        # Extract basic info from first line
        # Format: "○ task-id · Title   [● P1 · STATUS]"
        first_line = lines[0] if lines else ""

        task_data = {
            "id": task_id,
            "title": "",
            "status": "unknown",
            "priority": 0,
            "raw_output": output,
        }

        # Simple parsing - this could be enhanced
        if "·" in first_line:
            parts = first_line.split("·")
            if len(parts) >= 2:
                task_data["title"] = parts[1].strip().split("[")[0].strip()

        if "[" in first_line and "]" in first_line:
            status_part = first_line.split("[")[1].split("]")[0]
            if "OPEN" in status_part:
                task_data["status"] = "open"
            elif "IN_PROGRESS" in status_part or "IN PROGRESS" in status_part:
                task_data["status"] = "in_progress"
            elif "CLOSED" in status_part:
                task_data["status"] = "closed"

        return task_data

    def list_tasks(
        self,
        status: Optional[str] = None,
        working_dir: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List beads tasks.

        Args:
            status: Filter by status (optional)
            working_dir: Working directory (optional)

        Returns:
            List of task dictionaries

        Raises:
            subprocess.CalledProcessError: If bd command fails
        """
        cmd = ["bd", "list"]

        if status:
            cmd.append(f"--status={status}")

        result = subprocess.run(
            cmd,
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        # Parse output into list of tasks
        # Format: "○ task-id [● P1] [type] - Title"
        tasks = []
        for line in result.stdout.strip().split("\n"):
            if line.strip() and not line.startswith("📋"):
                # Extract task ID
                if "sentinel-" in line:
                    task_id = line.split("sentinel-")[1].split()[0]
                    task_id = f"sentinel-{task_id}"

                    tasks.append({
                        "id": task_id,
                        "raw": line,
                    })

        return tasks

    def get_ready_tasks(self, working_dir: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get tasks that are ready to work (no blockers).

        Args:
            working_dir: Working directory (optional)

        Returns:
            List of ready task dictionaries

        Raises:
            subprocess.CalledProcessError: If bd command fails
        """
        result = subprocess.run(
            ["bd", "ready"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        # Parse ready tasks from output
        tasks = []
        for line in result.stdout.strip().split("\n"):
            if line.strip() and "sentinel-" in line:
                # Extract task ID
                task_id_part = line.split("sentinel-")[1].split(":")[0].split()[0]
                task_id = f"sentinel-{task_id_part}"

                tasks.append({
                    "id": task_id,
                    "raw": line,
                })

        return tasks

    def sync(self, working_dir: Optional[str] = None) -> None:
        """Sync beads with git remote.

        Args:
            working_dir: Working directory (optional)

        Raises:
            subprocess.CalledProcessError: If bd command fails
        """
        subprocess.run(
            ["bd", "sync"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=True,
        )

    def get_stats(self, working_dir: Optional[str] = None) -> Dict[str, Any]:
        """Get beads project statistics.

        Args:
            working_dir: Working directory (optional)

        Returns:
            Dictionary with project stats

        Raises:
            subprocess.CalledProcessError: If bd command fails
        """
        result = subprocess.run(
            ["bd", "stats"],
            cwd=working_dir,
            capture_output=True,
            text=True,
            check=True,
        )

        # Simple stats parsing
        output = result.stdout
        stats = {
            "raw_output": output,
        }

        # Parse key stats from output
        for line in output.split("\n"):
            if "Total Issues:" in line:
                stats["total"] = int(line.split(":")[1].strip())  # type: ignore[assignment]
            elif "Open:" in line:
                stats["open"] = int(line.split(":")[1].strip())  # type: ignore[assignment]
            elif "Closed:" in line:
                stats["closed"] = int(line.split(":")[1].strip())  # type: ignore[assignment]
            elif "Ready to Work:" in line:
                stats["ready"] = int(line.split(":")[1].strip())  # type: ignore[assignment]

        return stats
