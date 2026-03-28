"""Docker Compose subprocess wrapper for Sentinel.

Manages Docker Compose operations (up, down, exec, ps, logs) and provides
health check polling for service readiness.
"""

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ComposeResult:
    """Result of a Docker Compose operation."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    returncode: int = 0


@dataclass
class ServiceStatus:
    """Status of a Docker Compose service."""

    name: str
    state: str  # running, exited, etc.
    health: str = ""  # healthy, unhealthy, starting, ""


class ComposeRunner:
    """Manages Docker Compose operations for Sentinel project environments."""

    def __init__(self, compose_file: Optional[Path] = None, project_name: Optional[str] = None) -> None:
        """Initialize compose runner.

        Args:
            compose_file: Path to docker-compose.sentinel.yml
            project_name: Docker Compose project name for isolation
        """
        self.compose_file = compose_file
        self.project_name = project_name
        self._docker_compose_cmd = self._find_compose_command()

    def _find_compose_command(self) -> list[str]:
        """Find the docker compose command (v2 plugin or standalone)."""
        # Try docker compose (v2 plugin) first
        try:
            result = subprocess.run(
                ["docker", "compose", "version"],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return ["docker", "compose"]
        except FileNotFoundError:
            pass

        # Fall back to docker-compose (standalone)
        if shutil.which("docker-compose"):
            return ["docker-compose"]

        raise RuntimeError(
            "Docker Compose not found. Install docker-compose-v2 or docker-compose."
        )

    def _build_cmd(self, *args: str) -> list[str]:
        """Build a docker compose command with project/file options."""
        cmd = list(self._docker_compose_cmd)
        if self.project_name:
            cmd.extend(["-p", self.project_name])
        if self.compose_file:
            cmd.extend(["-f", str(self.compose_file)])
        cmd.extend(args)
        return cmd

    def _run(self, *args: str, timeout: int = 120) -> ComposeResult:
        """Run a docker compose command.

        Args:
            *args: Command arguments
            timeout: Command timeout in seconds

        Returns:
            ComposeResult with output and status
        """
        cmd = self._build_cmd(*args)
        logger.debug(f"Running: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return ComposeResult(
                success=result.returncode == 0,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
            )
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out after {timeout}s: {' '.join(cmd)}")
            return ComposeResult(
                success=False,
                stderr=f"Command timed out after {timeout} seconds",
                returncode=-1,
            )
        except FileNotFoundError as e:
            logger.error(f"Command not found: {e}")
            return ComposeResult(
                success=False,
                stderr=str(e),
                returncode=-1,
            )

    def up(self, detach: bool = True, build: bool = False, timeout: int = 300) -> ComposeResult:
        """Start services.

        Args:
            detach: Run in background
            build: Build images before starting
            timeout: Command timeout in seconds

        Returns:
            ComposeResult
        """
        args = ["up"]
        if detach:
            args.append("-d")
        if build:
            args.append("--build")
        args.append("--remove-orphans")

        return self._run(*args, timeout=timeout)

    def down(self, volumes: bool = True, remove_orphans: bool = True, timeout: int = 120) -> ComposeResult:
        """Stop and remove services.

        Args:
            volumes: Also remove volumes
            remove_orphans: Remove orphan containers
            timeout: Command timeout in seconds

        Returns:
            ComposeResult
        """
        args = ["down"]
        if volumes:
            args.append("-v")
        if remove_orphans:
            args.append("--remove-orphans")

        return self._run(*args, timeout=timeout)

    def exec(
        self,
        service: str,
        command: str | list[str],
        user: Optional[str] = None,
        workdir: Optional[str] = None,
        timeout: int = 300,
    ) -> ComposeResult:
        """Execute a command in a running service container.

        Args:
            service: Service name
            command: Command to execute (string or list)
            user: Run as specific user
            workdir: Working directory inside container
            timeout: Command timeout in seconds

        Returns:
            ComposeResult with command output
        """
        args = ["exec", "-T"]  # -T disables pseudo-TTY
        if user:
            args.extend(["-u", user])
        if workdir:
            args.extend(["-w", workdir])
        args.append(service)

        if isinstance(command, str):
            args.extend(["sh", "-c", command])
        else:
            args.extend(command)

        return self._run(*args, timeout=timeout)

    def ps(self) -> list[ServiceStatus]:
        """Get status of all services.

        Returns:
            List of ServiceStatus for each service
        """
        result = self._run("ps", "--format", "json")
        if not result.success:
            logger.warning(f"Failed to get service status: {result.stderr}")
            return []

        import json

        services = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                services.append(ServiceStatus(
                    name=data.get("Service", data.get("Name", "unknown")),
                    state=data.get("State", "unknown"),
                    health=data.get("Health", ""),
                ))
            except json.JSONDecodeError:
                continue

        return services

    def logs(self, service: Optional[str] = None, tail: int = 50) -> ComposeResult:
        """Get service logs.

        Args:
            service: Specific service (or all if None)
            tail: Number of lines from end

        Returns:
            ComposeResult with log output
        """
        args = ["logs", "--no-color", f"--tail={tail}"]
        if service:
            args.append(service)

        return self._run(*args)

    def wait_for_healthy(self, timeout: int = 120, poll_interval: int = 5) -> bool:
        """Wait for all services with healthchecks to become healthy.

        Args:
            timeout: Maximum wait time in seconds
            poll_interval: Seconds between status checks

        Returns:
            True if all services are healthy, False on timeout
        """
        start_time = time.time()
        logger.info(f"Waiting for services to be healthy (timeout: {timeout}s)")

        while time.time() - start_time < timeout:
            services = self.ps()

            if not services:
                logger.debug("No services found yet, waiting...")
                time.sleep(poll_interval)
                continue

            all_running = True
            has_unhealthy = False

            for svc in services:
                if svc.state != "running":
                    if svc.state in ("exited", "dead"):
                        logger.error(f"Service '{svc.name}' has {svc.state}")
                        return False
                    all_running = False
                elif svc.health and svc.health not in ("healthy", ""):
                    all_running = False
                    if svc.health == "unhealthy":
                        has_unhealthy = True

            if has_unhealthy:
                logger.warning("Some services are unhealthy, continuing to wait...")

            if all_running and services:
                # Check if any services with healthchecks are still starting
                still_starting = any(
                    svc.health == "starting" for svc in services
                )
                if not still_starting:
                    logger.info("All services are healthy")
                    return True

            elapsed = int(time.time() - start_time)
            logger.debug(f"Services not ready yet ({elapsed}s elapsed)")
            time.sleep(poll_interval)

        logger.error(f"Timed out waiting for services after {timeout}s")
        return False

    def cleanup_orphans(self) -> ComposeResult:
        """Remove orphan containers from this project.

        Returns:
            ComposeResult
        """
        if not self.project_name:
            return ComposeResult(success=True)

        # Use docker to find and remove orphan containers
        try:
            result = subprocess.run(
                [
                    "docker", "ps", "-a",
                    "--filter", f"label=com.docker.compose.project={self.project_name}",
                    "--format", "{{.ID}}",
                ],
                capture_output=True,
                text=True,
            )
            container_ids = result.stdout.strip().split("\n")
            container_ids = [cid for cid in container_ids if cid]

            if container_ids:
                subprocess.run(
                    ["docker", "rm", "-f"] + container_ids,
                    capture_output=True,
                    text=True,
                )
                logger.info(f"Cleaned up {len(container_ids)} orphan containers")

            return ComposeResult(success=True)
        except Exception as e:
            logger.warning(f"Orphan cleanup failed: {e}")
            return ComposeResult(success=False, stderr=str(e))
