"""Sentinel CLI application."""

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Optional

import click
from importlib.metadata import version


from src.config_loader import get_config
from src.worktree_manager import WorktreeManager
from src.jira_factory import get_jira_client
from src.beads_manager import BeadsManager
from src.session_tracker import SessionTracker
from src.agents.plan_generator import PlanGeneratorAgent
from src.agents.python_developer import PythonDeveloperAgent
from src.agents.security_reviewer import SecurityReviewerAgent
from src.utils.adf_parser import parse_adf_to_text


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@click.group()
@click.version_option(version=version("sentinel"))
def cli() -> None:
    """Sentinel - Autonomous agent orchestration for Jira tickets.

    Sentinel automates the development workflow from Jira ticket to merge-ready code
    using specialized AI agents.
    """
    pass


@cli.command()
@click.argument("ticket_id")
@click.option(
    "--project",
    "-p",
    help="Project key (e.g., ACME). If not provided, extracted from ticket ID.",
)
@click.option(
    "--revise",
    is_flag=True,
    help="Revise existing plan based on MR feedback",
)
def plan(ticket_id: str, project: Optional[str] = None, revise: bool = False) -> None:
    """Generate implementation plan for a Jira ticket.

    Creates a git worktree, analyzes the ticket, generates a detailed plan,
    and creates a draft merge request.

    Use --revise to update an existing plan based on MR feedback.

    Args:
        ticket_id: Jira ticket ID (e.g., ACME-123)
        project: Project key (optional, extracted from ticket if not provided)
        revise: Revise existing plan based on MR feedback
    """
    try:
        # Extract project key from ticket ID if not provided
        if project is None:
            project = ticket_id.split("-")[0]

        # Initialize managers
        worktree_mgr = WorktreeManager()

        # Run revision workflow if --revise flag is set
        if revise:
            # For revision, worktree must already exist
            worktree_path = worktree_mgr.create_worktree(ticket_id, project)
            click.echo(f"🔄 Revising plan for: {ticket_id}")
            click.echo(f"🏗️  Project: {project}")

            click.echo("\n1️⃣  Fetching MR feedback...")
            plan_agent = PlanGeneratorAgent()
            result = plan_agent.run_revision(ticket_id=ticket_id, worktree_path=worktree_path)

            if result.get("feedback_count", 0) == 0:
                click.echo("   ℹ No unresolved discussions found")
                click.echo(f"\n✅ Nothing to revise for {ticket_id}")
                return

            click.echo(f"   ✓ Found {result['feedback_count']} unresolved discussion(s)")

            click.echo("\n2️⃣  Revising plan based on feedback...")
            revision_type = result.get("revision_type", "incremental")
            click.echo(f"   ✓ Revision type: {revision_type.replace('_', ' ').title()}")

            click.echo("\n3️⃣  Updating MR...")
            if result.get("plan_updated"):
                click.echo("   ✓ Revised plan committed and pushed")
            else:
                click.echo("   ℹ Plan unchanged")

            responses_posted = result.get("responses_posted", 0)
            click.echo(f"   ✓ Posted {responses_posted} response(s) to discussions")
            click.echo("   ✓ Added revision summary to MR")

            click.echo(f"\n✅ Plan revision complete for {ticket_id}")
            click.echo(f"   MR: {result['mr_url']}")
            click.echo("   Next: Review the updated plan and address any remaining feedback")

        else:
            # Normal plan generation workflow
            jira_client = get_jira_client()

            click.echo(f"📋 Planning ticket: {ticket_id}")
            click.echo(f"🏗️  Project: {project}")

            # Step 1: Fetch Jira ticket (before creating worktree to validate ticket exists)
            click.echo("\n1️⃣  Fetching Jira ticket...")
            ticket_data = jira_client.get_ticket(ticket_id)
            click.echo(f"   ✓ {ticket_data['summary']}")

            # Step 2: Create git worktree (only after ticket is validated)
            click.echo("\n2️⃣  Creating git worktree...")
            worktree_path = worktree_mgr.create_worktree(ticket_id, project)
            click.echo(f"   ✓ {worktree_path}")

            # Step 3: Generate plan
            click.echo("\n3️⃣  Generating implementation plan...")
            plan_agent = PlanGeneratorAgent()
            result = plan_agent.run(ticket_id=ticket_id, worktree_path=worktree_path)

            click.echo(f"   ✓ Plan saved: {result['plan_path']}")

            if result.get("plan_updated"):
                click.echo("   ✓ Plan committed and pushed")
            else:
                click.echo("   ℹ Plan unchanged - skipped commit")

            if result.get("mr_created"):
                click.echo(f"   ✓ Draft MR created: {result['mr_url']}")
            else:
                click.echo(f"   ℹ Using existing MR: {result['mr_url']}")

            click.echo(f"\n✅ Plan workflow complete for {ticket_id}")
            click.echo(f"   Next: Review draft MR, then run 'sentinel execute {ticket_id}'")

    except ValueError as e:
        if "not found" in str(e):
            click.echo(f"\n❌ {e}", err=True)
            click.echo("   Check that the ticket ID is correct and exists in Jira.")
            sys.exit(1)
        raise
    except Exception as e:
        logger.error(f"Plan command failed: {e}", exc_info=True)
        click.echo(f"\n❌ Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("ticket_id")
@click.option(
    "--project",
    "-p",
    help="Project key (e.g., ACME). If not provided, extracted from ticket ID.",
)
@click.option(
    "--max-iterations",
    "-i",
    default=5,
    help="Maximum number of security review iterations.",
)
@click.option(
    "--force",
    "-f",
    is_flag=True,
    help="Force-push to remote even if diverged (use with caution).",
)
@click.option(
    "--revise",
    is_flag=True,
    help="Revise existing implementation based on MR feedback",
)
def execute(ticket_id: str, project: Optional[str] = None, max_iterations: int = 5, force: bool = False, revise: bool = False) -> None:
    """Execute implementation plan for a Jira ticket.

    Reads the plan, implements features using TDD, and iterates with security review
    until code is approved or max iterations reached.

    Use --revise to update the implementation based on code review feedback.

    Args:
        ticket_id: Jira ticket ID (e.g., ACME-123)
        project: Project key (optional)
        max_iterations: Maximum security review iterations
        force: Force-push to remote if branch has diverged
        revise: Revise existing implementation based on MR feedback
    """
    try:
        # Extract project key from ticket ID if not provided
        if project is None:
            project = ticket_id.split("-")[0]

        # Initialize managers
        worktree_mgr = WorktreeManager()
        beads_mgr = BeadsManager()

        # Get worktree path
        worktree_path = worktree_mgr.get_worktree_path(ticket_id, project)
        if not worktree_path:
            click.echo(f"\n❌ Worktree not found for {ticket_id}", err=True)
            click.echo("   Run 'sentinel plan' first to create the worktree")
            sys.exit(1)

        # Run revision workflow if --revise flag is set
        if revise:
            click.echo(f"🔄 Revising implementation for: {ticket_id}")
            click.echo(f"🏗️  Project: {project}")

            click.echo("\n1️⃣  Fetching MR feedback...")
            developer = PythonDeveloperAgent()
            result = developer.run_revision(ticket_id=ticket_id, worktree_path=worktree_path)

            if result.get("feedback_count", 0) == 0:
                click.echo("   ℹ No unresolved discussions found")
                click.echo(f"\n✅ Nothing to revise for {ticket_id}")
                return

            click.echo(f"   ✓ Found {result['feedback_count']} unresolved discussion(s)")

            click.echo("\n2️⃣  Implementing fixes based on feedback...")
            click.echo(f"   ✓ {result.get('tasks_completed', 0)} task(s) completed")
            if result.get("tasks_failed", 0) > 0:
                click.echo(f"   ⚠ {result['tasks_failed']} task(s) failed")

            click.echo("\n3️⃣  Updating MR...")
            if result.get("changes_committed"):
                click.echo("   ✓ Revised implementation committed")
            else:
                click.echo("   ℹ No code changes made")

            responses_posted = result.get("responses_posted", 0)
            click.echo(f"   ✓ Posted {responses_posted} response(s) to discussions")

            test_results = result.get("test_results", {})
            if test_results.get("success"):
                click.echo("   ✓ All tests passing")
            else:
                click.echo("   ⚠️  Some tests failing - review needed")

            # Push changes to remote
            click.echo("\n4️⃣  Pushing changes to remote...")
            try:
                import subprocess

                # Get current branch name
                branch_result = subprocess.run(
                    ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                    cwd=worktree_path,
                    capture_output=True,
                    text=True,
                    check=True,
                )
                branch_name = branch_result.stdout.strip()

                # Build push command
                push_cmd = ["git", "push", "-u", "origin", branch_name]
                if force:
                    push_cmd.insert(2, "--force")
                    click.echo("   ⚠️  Force-pushing (may overwrite remote commits)")

                # Attempt push
                push_result = subprocess.run(
                    push_cmd,
                    cwd=worktree_path,
                    capture_output=True,
                    text=True,
                )

                if push_result.returncode == 0:
                    click.echo(f"   ✓ Pushed to origin/{branch_name}")
                else:
                    error_output = push_result.stderr
                    if "non-fast-forward" in error_output or "rejected" in error_output:
                        click.echo("   ⚠️  Push rejected: remote branch has diverged")
                        click.echo("   💡 Use --force flag to force-push and overwrite remote")
                        click.echo(f"      Example: sentinel execute {ticket_id} --revise --force")
                    else:
                        click.echo(f"   ⚠️  Push failed: {error_output}")

            except Exception as e:
                logger.warning(f"Failed to push changes: {e}")
                click.echo(f"   ⚠️  Push failed: {e}")
                click.echo("   💡 You may need to push manually from the worktree")

            click.echo(f"\n✅ Implementation revision complete for {ticket_id}")
            click.echo(f"   MR: {result['mr_url']}")
            click.echo("   Next: Review the updated implementation and address any remaining feedback")

            return

        # Normal execution workflow (not --revise)
        click.echo(f"⚙️  Executing ticket: {ticket_id}")
        click.echo(f"🏗️  Project: {project}")
        click.echo(f"🔄 Max iterations: {max_iterations}")

        # Initialize Python project structure in worktree
        click.echo("\n1️⃣  Initializing worktree...")
        project_name = ticket_id.lower().replace("-", "_")
        worktree_mgr.initialize_python_project(worktree_path, project_name)
        click.echo("   ✓ Python project structure initialized")

        # Initialize beads for coordination
        click.echo("\n2️⃣  Initializing task tracking...")
        beads_mgr.init_project(ticket_id, str(worktree_path))
        click.echo("   ✓ Beads initialized")

        # Find plan file
        plan_file = worktree_path / ".agents" / "plans" / f"{ticket_id}.md"
        if not plan_file.exists():
            click.echo(f"\n❌ Plan file not found: {plan_file}", err=True)
            click.echo("   Run 'sentinel plan' first to generate the plan")
            sys.exit(1)

        # Execute implementation with developer and security review loop
        click.echo("\n3️⃣  Executing implementation...")
        developer = PythonDeveloperAgent()
        security = SecurityReviewerAgent()

        for iteration in range(1, max_iterations + 1):
            click.echo(f"\n   Iteration {iteration}/{max_iterations}")

            # Developer implements features
            click.echo("   🔨 Developer: Implementing features...")
            dev_result = developer.run(plan_file=plan_file, worktree_path=worktree_path)
            click.echo(f"      ✓ {dev_result['tasks_completed']} tasks completed")
            if dev_result['tasks_failed'] > 0:
                click.echo(f"      ⚠ {dev_result['tasks_failed']} tasks failed")

            # Security reviews the implementation
            click.echo("   🔒 Security: Reviewing code...")
            sec_result = security.run(worktree_path=worktree_path, ticket_id=ticket_id)

            if sec_result["approved"]:
                click.echo("      ✅ Security review PASSED")
                break
            else:
                issues_count = len(sec_result.get("findings", []))
                click.echo(f"      ⚠️  Found {issues_count} security issues")

                # Create beads tasks for security findings (for next iteration)
                if iteration < max_iterations:
                    click.echo("      📝 Creating fix tasks for security findings...")
                    for finding in sec_result.get("findings", []):
                        try:
                            task_title = f"Fix {finding['severity'].upper()} - {finding['category']}: {finding['file']}:{finding['line']}"
                            task_description = f"{finding['description']}\n\nRecommendation: {finding['recommendation']}"

                            beads_mgr.create_task(
                                title=task_title[:100],  # Limit title length
                                task_type="bug",
                                priority=0 if finding['severity'] == 'critical' else 1,  # P0 for critical, P1 for high
                                description=task_description,
                                working_dir=str(worktree_path),
                            )
                        except Exception as e:
                            logger.warning(f"Could not create beads task for finding: {e}")

                    click.echo(f"      ✓ Created {issues_count} fix tasks")
                    click.echo("      ↻  Developer will address feedback...")
                else:
                    click.echo("\n❌ Max iterations reached without approval", err=True)
                    click.echo("   Manual review required. Check security findings.")
                    sys.exit(1)

        # Push changes to remote
        click.echo("\n4️⃣  Pushing changes to remote...")
        try:
            import subprocess

            # Get current branch name
            branch_result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                check=True,
            )
            branch_name = branch_result.stdout.strip()

            # Build push command
            push_cmd = ["git", "push", "-u", "origin", branch_name]
            if force:
                push_cmd.insert(2, "--force")
                click.echo("   ⚠️  Force-pushing (may overwrite remote commits)")

            # Attempt push
            push_result = subprocess.run(
                push_cmd,
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )

            if push_result.returncode == 0:
                click.echo(f"   ✓ Pushed to origin/{branch_name}")

                # Mark MR as ready for review (remove draft status)
                try:
                    from src.gitlab_client import GitLabClient

                    gitlab = GitLabClient()
                    config = get_config()
                    project_config = config.get_project_config(project)
                    git_url = project_config.get("git_url", "")

                    # Extract project path from git URL
                    if git_url.startswith("git@"):
                        project_path = git_url.split(":")[1].replace(".git", "")
                    elif git_url.startswith("https://"):
                        project_path = git_url.split("gitlab.com/")[1].replace(".git", "")
                    else:
                        project_path = f"{project.lower()}/backend"

                    # Find the MR for this branch
                    source_branch = f"feature/{ticket_id}"
                    mrs = gitlab.list_merge_requests(
                        project_id=project_path,
                        source_branch=source_branch,
                    )

                    if mrs:
                        mr_iid = mrs[0]["iid"]
                        gitlab.mark_as_ready(project_id=project_path, mr_iid=mr_iid)
                        click.echo("   ✓ MR marked as ready for review")

                except Exception as e:
                    logger.warning(f"Failed to mark MR as ready: {e}")
                    # Non-fatal - just log and continue

            else:
                error_output = push_result.stderr
                if "non-fast-forward" in error_output or "rejected" in error_output:
                    click.echo("   ⚠️  Push rejected: remote branch has diverged")
                    click.echo("   💡 Use --force flag to force-push and overwrite remote")
                    click.echo(f"      Example: sentinel execute {ticket_id} --force")
                else:
                    click.echo(f"   ⚠️  Push failed: {error_output}")

        except Exception as e:
            logger.warning(f"Failed to push changes: {e}")
            click.echo(f"   ⚠️  Push failed: {e}")
            click.echo("   💡 You may need to push manually from the worktree")

        click.echo(f"\n✅ Execute workflow complete for {ticket_id}")
        click.echo("   Code is ready for human review in the MR")

    except Exception as e:
        logger.error(f"Execute command failed: {e}", exc_info=True)
        click.echo(f"\n❌ Error: {e}", err=True)
        sys.exit(1)


def _reset_ticket(
    worktree_mgr: WorktreeManager,
    ticket_id: str,
    project: Optional[str],
    skip_confirm: bool = False,
) -> None:
    """Reset a single ticket."""
    if project is None:
        project = ticket_id.split("-")[0]

    click.echo(f"🔄 Resetting ticket: {ticket_id}")

    # Confirmation
    click.echo("\nThis will remove:")
    click.echo(f"  • Worktree for {ticket_id}")
    click.echo(f"  • Local branch feature/{ticket_id}")
    click.echo("\n⚠️  Any uncommitted changes will be lost!")

    if not skip_confirm and not click.confirm("Continue?", default=False):
        click.echo("\n❌ Reset cancelled")
        return

    result = worktree_mgr.reset_ticket(ticket_id, project)

    click.echo("\n1️⃣  Removing worktree...")
    if result["worktree_removed"]:
        click.echo("   ✓ Worktree removed")
    else:
        click.echo("   ℹ️  No worktree found")

    click.echo("\n2️⃣  Deleting local branch...")
    if result["branch_deleted"]:
        click.echo(f"   ✓ Branch feature/{ticket_id} deleted")
    else:
        click.echo("   ℹ️  No local branch found")

    click.echo(f"\n✅ Reset complete for {ticket_id}")
    click.echo("   ℹ️  Remote branches on origin are not affected")


def _reset_all(
    worktree_mgr: WorktreeManager,
    project: Optional[str],
    skip_confirm: bool = False,
) -> None:
    """Reset all Sentinel state."""
    config = get_config()
    session_tracker = SessionTracker()

    # Determine which projects to reset
    if project:
        projects = [project]
    else:
        projects = list(config.get("projects", {}).keys())

    # Collect what will be cleaned
    existing_repos = []
    total_worktrees = 0

    for proj in projects:
        bare_dir = worktree_mgr.workspace_root / proj.lower()
        if bare_dir.exists():
            existing_repos.append(proj)
            worktrees = worktree_mgr.list_worktrees(proj)
            total_worktrees += len(worktrees)

    # Get sessions for the specified project(s) only
    # If a specific project is given, only get sessions for that project
    # Otherwise, get all sessions (for full reset)
    if project:
        tracked_sessions = session_tracker.get_tracked_sessions(project=project)
    else:
        tracked_sessions = session_tracker.get_tracked_sessions()

    if not existing_repos and not tracked_sessions:
        click.echo("ℹ️  Nothing to reset")
        return

    # Show what will be cleaned
    if project:
        click.echo(f"🔄 Reset Sentinel State for {project}\n")
    else:
        click.echo("🔄 Reset ALL Sentinel State\n")
    click.echo("This will remove:")

    if existing_repos:
        click.echo(f"  • {total_worktrees} worktree(s)")
        click.echo(f"  • {len(existing_repos)} bare repository(ies): {', '.join(existing_repos)}")
        click.echo("  • All local branches in those repositories")

    if tracked_sessions:
        if project:
            click.echo(f"  • {len(tracked_sessions)} Agent SDK session(s) for {project}")
        else:
            click.echo(f"  • {len(tracked_sessions)} Agent SDK session(s)")

    click.echo("\n⚠️  WARNING: This cannot be undone!")
    click.echo("⚠️  Repositories must be re-cloned after reset!")

    if not skip_confirm and not click.confirm("Are you sure?", default=False):
        click.echo("\n❌ Reset cancelled")
        return

    step = 1

    # Reset each project
    for proj in existing_repos:
        click.echo(f"\n{step}️⃣  Resetting {proj}...")
        result = worktree_mgr.reset_all(proj)
        click.echo(f"   ✓ Removed {result['worktrees_removed']} worktree(s)")
        click.echo("   ✓ Removed bare repository")
        step += 1

    # Clear sessions (only for the specified project(s))
    if tracked_sessions:
        click.echo(f"\n{step}️⃣  Clearing Agent SDK sessions...")
        claude_config_dir = os.path.expanduser(
            os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")
        )
        session_env_dir = Path(claude_config_dir) / "session-env"

        for session_id in tracked_sessions:
            session_path = session_env_dir / session_id
            if session_path.exists():
                shutil.rmtree(session_path)
            session_tracker.untrack_session(session_id)
        click.echo(f"   ✓ Cleared {len(tracked_sessions)} session(s)")

    click.echo("\n✅ Reset complete - Sentinel is ready for a fresh start")
    click.echo("   ℹ️  Remote branches on origin are not affected")


@cli.command()
@click.argument("ticket_id", required=False)
@click.option(
    "--all",
    "-a",
    "reset_all_flag",
    is_flag=True,
    help="Reset everything: all worktrees, branches, repositories, and sessions.",
)
@click.option(
    "--project",
    "-p",
    help="Project key (e.g., ACME). Required with --all, optional with ticket_id.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompts.",
)
def reset(
    ticket_id: Optional[str] = None,
    reset_all_flag: bool = False,
    project: Optional[str] = None,
    yes: bool = False,
) -> None:
    """Reset a ticket or all Sentinel state to start fresh.

    For a single ticket:
      sentinel reset ACME-123

    For everything:
      sentinel reset --all
      sentinel reset --all --project ACME  (single project only)

    This removes worktrees AND local branches, ensuring a clean slate.
    Remote branches on origin are not affected.
    """
    try:
        if not ticket_id and not reset_all_flag:
            click.echo("❌ Error: Provide a ticket ID or use --all", err=True)
            click.echo("\nUsage:")
            click.echo("  sentinel reset ACME-123      # Reset single ticket")
            click.echo("  sentinel reset --all         # Reset everything")
            sys.exit(1)

        if ticket_id and reset_all_flag:
            click.echo("❌ Error: Cannot use ticket ID with --all", err=True)
            sys.exit(1)

        worktree_mgr = WorktreeManager()

        if reset_all_flag:
            _reset_all(worktree_mgr, project, skip_confirm=yes)
        else:
            assert ticket_id is not None  # Already validated above
            _reset_ticket(worktree_mgr, ticket_id, project, skip_confirm=yes)

    except Exception as e:
        logger.error(f"Reset command failed: {e}", exc_info=True)
        click.echo(f"\n❌ Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    "--project",
    "-p",
    help="Project key to list worktrees for.",
)
def status(project: Optional[str] = None) -> None:
    """Show status of active worktrees and tasks.

    Args:
        project: Project key to filter by (optional)
    """
    try:
        click.echo("📊 Sentinel Status\n")

        config = get_config()

        if project:
            projects = [project]
        else:
            # List all configured projects
            projects_config = config.get("projects", {})
            projects = list(projects_config.keys())

        worktree_mgr = WorktreeManager()
        beads_mgr = BeadsManager()

        for proj in projects:
            click.echo(f"\n🏗️  Project: {proj}")

            # List worktrees
            worktrees = worktree_mgr.list_worktrees(proj)
            if worktrees:
                click.echo(f"   Active worktrees: {len(worktrees)}")
                for wt in worktrees[:5]:  # Show first 5
                    click.echo(f"     • {wt}")
                if len(worktrees) > 5:
                    click.echo(f"     ... and {len(worktrees) - 5} more")
            else:
                click.echo("   No active worktrees")

        # Show beads stats
        try:
            stats = beads_mgr.get_stats()
            click.echo("\n📋 Task Tracking:")
            click.echo(f"   Open: {stats.get('open', 0)}")
            click.echo(f"   Ready: {stats.get('ready', 0)}")
            click.echo(f"   Closed: {stats.get('closed', 0)}")
        except Exception:
            click.echo("\n📋 Task Tracking: Not initialized")

    except Exception as e:
        logger.error(f"Status command failed: {e}", exc_info=True)
        click.echo(f"\n❌ Error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("ticket_id")
def info(ticket_id: str) -> None:
    """Display information about a Jira ticket.

    Fetches and displays the summary and description of a Jira ticket
    to validate connectivity and ticket details.

    Args:
        ticket_id: Jira ticket ID (e.g., ACME-123)
    """
    try:
        click.echo(f"🔍 Fetching ticket: {ticket_id}\n")

        # Initialize Jira client
        jira_client = get_jira_client()

        # Fetch ticket
        ticket_data = jira_client.get_ticket(ticket_id)

        # Display ticket information
        click.echo("=" * 80)
        click.echo(f"📋 {ticket_id}: {ticket_data['summary']}")
        click.echo("=" * 80)
        click.echo(f"\n🏷️  Status: {ticket_data.get('status', 'Unknown')}")
        click.echo(f"👤 Assignee: {ticket_data.get('assignee', 'Unassigned')}")
        click.echo(f"🔖 Type: {ticket_data.get('type', 'Unknown')}")

        if ticket_data.get('priority'):
            click.echo(f"⚡ Priority: {ticket_data['priority']}")

        click.echo("\n📝 Description:")
        click.echo("-" * 80)
        raw_description = ticket_data.get('description', 'No description provided')
        # Parse ADF format if it's a dict, otherwise display as-is
        if isinstance(raw_description, dict):
            description = parse_adf_to_text(raw_description)
        else:
            description = raw_description
        click.echo(description)
        click.echo("-" * 80)

        # Display additional useful fields if available
        if ticket_data.get('labels'):
            click.echo(f"\n🏷️  Labels: {', '.join(ticket_data['labels'])}")

        if ticket_data.get('components'):
            click.echo(f"🧩 Components: {', '.join(ticket_data['components'])}")

        click.echo(f"\n✅ Ticket {ticket_id} retrieved successfully")

    except ValueError as e:
        if "not found" in str(e):
            click.echo(f"\n❌ {e}", err=True)
            click.echo("   Check that the ticket ID is correct and exists in Jira.")
        else:
            click.echo(f"❌ Configuration error: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.error(f"Info command failed: {e}", exc_info=True)
        click.echo(f"❌ Error fetching ticket: {e}", err=True)
        sys.exit(1)


@cli.command()
def validate() -> None:
    """Validate API credentials and connectivity.

    Tests connections to:
    - Jira API
    - GitLab API
    - LLM provider (custom proxy, direct API, or Claude Code subscription)
    """
    try:
        click.echo(f"🔐 Validating API Credentials (Sentinel v{version('sentinel')})\n")

        all_valid = True
        # Track which services failed for targeted fix instructions
        jira_failed = False
        gitlab_failed = False
        llm_failed = False
        llm_mode = "unknown"  # Track LLM mode for fix instructions
        beads_failed = False

        # Test Jira
        click.echo("1️⃣  Testing Jira API...")
        try:
            jira_client = get_jira_client()
            # Test by fetching current user - use correct API version
            from src.jira_server_client import JiraServerClient
            api_version = "2" if isinstance(jira_client, JiraServerClient) else "3"
            response = jira_client.session.get(f"{jira_client.base_url}/rest/api/{api_version}/myself")
            response.raise_for_status()
            user_data = response.json()
            click.echo(f"   ✅ Jira connected: {user_data.get('displayName', 'Unknown')}")
            click.echo(f"      URL: {jira_client.base_url}")
            click.echo(f"      Email: {user_data.get('emailAddress', 'N/A')}")
        except ValueError as e:
            click.echo(f"   ❌ Jira configuration error: {e}")
            all_valid = False
            jira_failed = True
        except Exception as e:
            click.echo(f"   ❌ Jira connection failed: {e}")
            all_valid = False
            jira_failed = True

        # Test GitLab
        click.echo("\n2️⃣  Testing GitLab API...")
        try:
            from src.gitlab_client import GitLabClient
            gitlab_client = GitLabClient()
            # Test by fetching current user
            response = gitlab_client.session.get(f"{gitlab_client.base_url}/api/v4/user")
            response.raise_for_status()
            user_data = response.json()
            click.echo(f"   ✅ GitLab connected: {user_data.get('name', 'Unknown')}")
            click.echo(f"      URL: {gitlab_client.base_url}")
            click.echo(f"      Username: @{user_data.get('username', 'N/A')}")
        except ValueError as e:
            click.echo(f"   ❌ GitLab configuration error: {e}")
            all_valid = False
            gitlab_failed = True
        except Exception as e:
            click.echo(f"   ❌ GitLab connection failed: {e}")
            all_valid = False
            gitlab_failed = True

        # Test LLM configuration
        click.echo("\n3️⃣  Testing LLM Configuration...")
        try:
            from src.config_loader import get_config
            cfg = get_config()
            llm_config = cfg.get_llm_config()
            llm_mode = llm_config["mode"]

            if llm_mode == "custom_proxy":
                click.echo("   Mode: Custom Proxy")
                click.echo(f"      API Key: {'*' * 8}{llm_config['api_key'][-4:] if llm_config['api_key'] and len(llm_config['api_key']) > 4 else 'Not set'}")
                click.echo(f"      Base URL: {llm_config['base_url']}")
                click.echo("   ✅ Custom proxy configured")
            elif llm_mode == "direct_api":
                click.echo("   Mode: Direct Anthropic API")
                click.echo(f"      API Key: {'*' * 8}{llm_config['api_key'][-4:] if llm_config['api_key'] and len(llm_config['api_key']) > 4 else 'Not set'}")
                click.echo("   ✅ Direct API configured")
            else:  # subscription
                click.echo("   Mode: Claude Code Subscription")
                # Check if Claude CLI is authenticated
                import subprocess

                # Disable auto-updates to prevent orphaned npm processes
                # See: https://github.com/anthropics/claude-code/issues/114
                claude_env = os.environ.copy()
                claude_env["DISABLE_AUTOUPDATER"] = "1"

                try:
                    result = subprocess.run(
                        ["claude", "--version"],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        env=claude_env
                    )
                    if result.returncode == 0:
                        click.echo(f"      Claude CLI: {result.stdout.strip()}")
                        # Try to check auth status
                        auth_result = subprocess.run(
                            ["claude", "auth", "status"],
                            capture_output=True,
                            text=True,
                            timeout=10,
                            env=claude_env
                        )
                        if auth_result.returncode == 0:
                            click.echo("   ✅ Claude Code authenticated")
                        else:
                            click.echo("   ⚠️  Claude Code not authenticated")
                            click.echo("      Run: sentinel auth login")
                            all_valid = False
                            llm_failed = True
                    else:
                        click.echo("   ❌ Claude CLI not working")
                        all_valid = False
                        llm_failed = True
                except FileNotFoundError:
                    click.echo("   ❌ Claude CLI not found")
                    click.echo("      Install: npm install -g @anthropic-ai/claude-code")
                    all_valid = False
                    llm_failed = True
                except subprocess.TimeoutExpired:
                    # auth status can be slow - check credentials file as fallback
                    creds_file = Path.home() / ".claude" / ".credentials.json"
                    if creds_file.exists():
                        try:
                            import json
                            creds = json.loads(creds_file.read_text())
                            if creds.get("claudeAiOauth", {}).get("accessToken"):
                                click.echo("   ✅ Claude Code authenticated (credentials found)")
                            else:
                                click.echo("   ⚠️  Claude Code credentials incomplete")
                                click.echo("      Run: sentinel auth login")
                                all_valid = False
                                llm_failed = True
                        except Exception:
                            click.echo("   ⚠️  Claude CLI timeout - may need authentication")
                            all_valid = False
                            llm_failed = True
                    else:
                        click.echo("   ⚠️  Claude CLI timeout - may need authentication")
                        all_valid = False
                        llm_failed = True
        except ValueError as e:
            click.echo(f"   ❌ LLM configuration error: {e}")
            all_valid = False
            llm_failed = True
        except Exception as e:
            click.echo(f"   ❌ LLM error: {e}")
            all_valid = False
            llm_failed = True

        # Test Beads
        click.echo("\n4️⃣  Testing Beads CLI...")
        import subprocess as sp
        try:
            beads_mgr = BeadsManager()
            # BeadsManager.__init__ verifies bd --version works
            click.echo("   ✅ Beads CLI installed")
            try:
                stats = beads_mgr.get_stats()
                click.echo(f"      Total issues: {stats.get('total', 0)}")
            except sp.CalledProcessError:
                # No database initialized yet - that's OK for validation
                click.echo("      ℹ️  No beads database (run 'bd init' in a project)")
        except RuntimeError as e:
            click.echo(f"   ❌ {e}")
            all_valid = False
            beads_failed = True
        except Exception as e:
            click.echo(f"   ❌ Beads CLI error: {e}")
            all_valid = False
            beads_failed = True

        # Summary
        click.echo("\n" + "=" * 50)
        if all_valid:
            click.echo("✅ All credentials validated successfully!")
        else:
            click.echo("⚠️  Some credentials need attention")
            click.echo("\nTo fix:")

            # Provide targeted fix instructions based on what failed
            step = 1

            # LLM-specific instructions
            if llm_failed:
                if llm_mode == "subscription":
                    click.echo(f"{step}. Run 'sentinel auth login' to authenticate with Claude Code")
                    step += 1
                else:
                    click.echo(f"{step}. Add ANTHROPIC_API_KEY to sentinel/config/.env")
                    if llm_mode == "custom_proxy":
                        click.echo(f"   (and ANTHROPIC_BASE_URL for custom proxy)")
                    step += 1

            # Jira/GitLab instructions
            if jira_failed or gitlab_failed:
                if jira_failed and gitlab_failed:
                    click.echo(f"{step}. Add Jira and GitLab credentials to sentinel/config/.env")
                elif jira_failed:
                    click.echo(f"{step}. Add Jira credentials (JIRA_API_TOKEN, JIRA_EMAIL, JIRA_BASE_URL) to sentinel/config/.env")
                else:
                    click.echo(f"{step}. Add GitLab credentials (GITLAB_API_TOKEN) to sentinel/config/.env")
                step += 1

            # Beads instructions
            if beads_failed:
                click.echo(f"{step}. Install beads CLI: npm install -g @beads/bd")
                step += 1

            # Generic hint if .env doesn't exist
            if jira_failed or gitlab_failed or (llm_failed and llm_mode != "subscription"):
                env_path = Path(__file__).parent.parent / "config" / ".env"
                if not env_path.exists():
                    click.echo(f"\n   Tip: Copy sentinel/config/.env.example to sentinel/config/.env first")

            click.echo(f"\n{step}. Run 'sentinel validate' again")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Validation failed: {e}", exc_info=True)
        click.echo(f"\n❌ Validation error: {e}", err=True)
        sys.exit(1)


@cli.group()
def auth() -> None:
    """Manage LLM authentication.

    Commands:
    - configure: Interactive setup for all LLM modes (recommended for new users)
    - login: Authenticate with Claude Code subscription
    - logout: Clear Claude Code credentials
    - status: Check current authentication status
    """
    pass


@auth.command("login")
def auth_login() -> None:
    """Authenticate with Claude Code subscription.

    Opens interactive login flow for Claude Code.
    Required for subscription mode (when no ANTHROPIC_API_KEY is set).
    """
    import subprocess
    import json

    click.echo("🔐 Claude Code Authentication\n")

    # Check current LLM mode
    cfg = get_config()
    llm_config = cfg.get_llm_config()

    if llm_config["mode"] != "subscription":
        click.echo(f"ℹ️  Current mode: {llm_config['mode']}")
        click.echo("   Authentication only needed for subscription mode.")
        click.echo("   To use subscription mode, remove ANTHROPIC_API_KEY from .env")
        return

    # Check if already authenticated
    creds_file = Path.home() / ".claude" / ".credentials.json"
    if creds_file.exists():
        try:
            creds = json.loads(creds_file.read_text())
            if creds.get("claudeAiOauth", {}).get("accessToken"):
                click.echo("✅ Already authenticated with Claude Code")
                click.echo("   Run 'sentinel auth logout' first to re-authenticate")
                return
        except Exception:
            pass  # Credentials file exists but is invalid, proceed with login

    click.echo("Starting Claude Code...\n")
    click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    click.echo("  1. Type /login and press Enter")
    click.echo("  2. Complete the authentication in your browser")
    click.echo("  3. Type /exit to return here")
    click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

    try:
        # Disable auto-updates during login to prevent orphaned npm processes
        # See: https://github.com/anthropics/claude-code/issues/114
        env = os.environ.copy()
        env["DISABLE_AUTOUPDATER"] = "1"

        # Run claude interactively - it auto-triggers auth flow if not authenticated
        # The /login command works inside interactive mode if manual re-auth needed
        result = subprocess.run(
            ["claude"],
            env=env,
            check=False
        )

        # Check if credentials now exist
        creds_file = Path.home() / ".claude" / ".credentials.json"
        if creds_file.exists():
            try:
                creds = json.loads(creds_file.read_text())
                if creds.get("claudeAiOauth", {}).get("accessToken"):
                    click.echo("\n✅ Authentication successful!")
                    return
            except Exception:
                pass
        click.echo("\n⚠️  Authentication may not have completed")
        click.echo("   Run 'sentinel validate' to check status")
    except FileNotFoundError:
        click.echo("❌ Claude CLI not found")
        click.echo("\nInstall with: npm install -g @anthropic-ai/claude-code")
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Authentication error: {e}")
        sys.exit(1)


@auth.command("logout")
def auth_logout() -> None:
    """Log out from Claude Code subscription.

    Clears cached Claude Code credentials by removing the credentials file.
    """
    click.echo("🔐 Claude Code Logout\n")

    creds_file = Path.home() / ".claude" / ".credentials.json"

    if not creds_file.exists():
        click.echo("ℹ️  Already logged out (no credentials found)")
        return

    try:
        # Remove the credentials file to log out
        creds_file.unlink()
        click.echo("✅ Logged out successfully")
        click.echo("   Credentials file removed")
    except PermissionError:
        click.echo("❌ Permission denied - cannot remove credentials file")
        click.echo(f"   Try: rm {creds_file}")
        sys.exit(1)
    except Exception as e:
        click.echo(f"❌ Logout error: {e}")
        sys.exit(1)


@auth.command("status")
def auth_status() -> None:
    """Check Claude Code authentication status.

    Shows current LLM mode and authentication state.
    """
    import subprocess

    click.echo("🔐 LLM Authentication Status\n")

    cfg = get_config()
    llm_config = cfg.get_llm_config()
    mode = llm_config["mode"]

    click.echo(f"Mode: {mode.replace('_', ' ').title()}")

    if mode == "custom_proxy":
        click.echo(f"API Key: {'*' * 8}{llm_config['api_key'][-4:] if llm_config['api_key'] and len(llm_config['api_key']) > 4 else 'Not set'}")
        click.echo(f"Base URL: {llm_config['base_url']}")
        click.echo("\n✅ Using custom proxy authentication")
    elif mode == "direct_api":
        click.echo(f"API Key: {'*' * 8}{llm_config['api_key'][-4:] if llm_config['api_key'] and len(llm_config['api_key']) > 4 else 'Not set'}")
        click.echo("\n✅ Using direct Anthropic API")
    else:  # subscription
        try:
            # Disable auto-updates to prevent orphaned npm processes
            env = os.environ.copy()
            env["DISABLE_AUTOUPDATER"] = "1"

            result = subprocess.run(
                ["claude", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
                env=env
            )
            if result.returncode == 0:
                click.echo(f"\n{result.stdout.strip()}")
                click.echo("\n✅ Claude Code authenticated")
            else:
                click.echo("\n⚠️  Not authenticated")
                click.echo("Run: sentinel auth login")
        except FileNotFoundError:
            click.echo("\n❌ Claude CLI not found")
            click.echo("Install: npm install -g @anthropic-ai/claude-code")
        except subprocess.TimeoutExpired:
            click.echo("\n⚠️  Status check timed out")


@auth.command("configure")
def auth_configure() -> None:
    """Interactive LLM provider configuration.

    Set up authentication for one of three modes:
    - Claude Code subscription (Pro/Max plan)
    - Direct Anthropic API (API key)
    - Custom proxy (API key + base URL, e.g., LLM Provider)
    """
    import subprocess
    import json

    click.echo("🔐 LLM Provider Configuration\n")
    click.echo("Choose your LLM provider:\n")
    click.echo("  1. Claude Code Subscription (Pro/Max plan)")
    click.echo("     - Uses your Claude.ai account")
    click.echo("     - No API key needed\n")
    click.echo("  2. Direct Anthropic API")
    click.echo("     - Uses ANTHROPIC_API_KEY")
    click.echo("     - Billed via Anthropic Console\n")
    click.echo("  3. Custom Proxy (e.g., LLM Provider)")
    click.echo("     - Uses API key + custom base URL")
    click.echo("     - For enterprise/proxy setups\n")

    choice = click.prompt("Enter choice", type=click.IntRange(1, 3), default=1)

    if choice == 1:
        # Claude Code subscription
        click.echo("\n📋 Claude Code Subscription Setup\n")

        # Check for existing API key in .env that would override subscription mode
        env_path = Path(__file__).parent.parent / "config" / ".env"
        if env_path.exists():
            env_content = env_path.read_text()
            if "ANTHROPIC_API_KEY=" in env_content and not env_content.split("ANTHROPIC_API_KEY=")[1].startswith("\n"):
                click.echo("⚠️  ANTHROPIC_API_KEY is set in .env")
                click.echo("   This will override subscription mode.")
                if click.confirm("Remove ANTHROPIC_API_KEY from .env?", default=True):
                    # Remove the API key line
                    lines = env_content.split("\n")
                    new_lines = [l for l in lines if not l.startswith("ANTHROPIC_API_KEY=")]
                    # Also remove ANTHROPIC_BASE_URL if present
                    new_lines = [l for l in new_lines if not l.startswith("ANTHROPIC_BASE_URL=")]
                    env_path.write_text("\n".join(new_lines))
                    click.echo("   ✓ Removed API key from .env")
                else:
                    click.echo("\n❌ Cannot use subscription mode with API key set")
                    return

        # Check if already authenticated
        creds_file = Path.home() / ".claude" / ".credentials.json"
        if creds_file.exists():
            try:
                creds = json.loads(creds_file.read_text())
                if creds.get("claudeAiOauth", {}).get("accessToken"):
                    click.echo("✅ Already authenticated with Claude Code")
                    return
            except Exception:
                pass

        click.echo("Starting Claude Code for authentication...\n")
        click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        click.echo("  1. Type /login and press Enter")
        click.echo("  2. Complete the authentication in your browser")
        click.echo("  3. Type /exit to return here")
        click.echo("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n")

        env = os.environ.copy()
        env["DISABLE_AUTOUPDATER"] = "1"

        try:
            subprocess.run(["claude"], env=env, check=False)

            if creds_file.exists():
                try:
                    creds = json.loads(creds_file.read_text())
                    if creds.get("claudeAiOauth", {}).get("accessToken"):
                        click.echo("\n✅ Claude Code subscription configured!")
                        return
                except Exception:
                    pass
            click.echo("\n⚠️  Authentication may not have completed")
            click.echo("   Run 'sentinel validate' to check status")
        except FileNotFoundError:
            click.echo("❌ Claude CLI not found")
            click.echo("\nInstall with: npm install -g @anthropic-ai/claude-code")
            sys.exit(1)

    elif choice == 2:
        # Direct Anthropic API
        click.echo("\n📋 Direct Anthropic API Setup\n")
        click.echo("Get your API key from: https://console.anthropic.com/\n")

        api_key = click.prompt("Enter your ANTHROPIC_API_KEY", hide_input=True)

        if not api_key or len(api_key) < 10:
            click.echo("❌ Invalid API key")
            sys.exit(1)

        # Save to .env.local file
        env_path = Path(__file__).parent.parent / "config" / ".env"
        _update_env_file(env_path, {
            "ANTHROPIC_API_KEY": api_key,
            "ANTHROPIC_BASE_URL": "",  # Clear any existing base URL
        })

        click.echo("\n✅ Direct API configured!")
        click.echo(f"   API Key: {'*' * 8}{api_key[-4:]}")
        click.echo(f"   Saved to: {env_path.parent / '.env.local'}")

    else:
        # Custom proxy
        click.echo("\n📋 Custom Proxy Setup\n")
        click.echo("Configure for proxies like LLM Provider, AWS Bedrock gateway, etc.\n")

        api_key = click.prompt("Enter your API key", hide_input=True)
        if not api_key or len(api_key) < 10:
            click.echo("❌ Invalid API key")
            sys.exit(1)

        base_url = click.prompt("Enter the base URL (e.g., https://proxy.example.com/v1)")
        if not base_url or not base_url.startswith("http"):
            click.echo("❌ Invalid base URL")
            sys.exit(1)

        # Save to .env.local file
        env_path = Path(__file__).parent.parent / "config" / ".env"
        _update_env_file(env_path, {
            "ANTHROPIC_API_KEY": api_key,
            "ANTHROPIC_BASE_URL": base_url,
        })

        click.echo("\n✅ Custom proxy configured!")
        click.echo(f"   API Key: {'*' * 8}{api_key[-4:]}")
        click.echo(f"   Base URL: {base_url}")
        click.echo(f"   Saved to: {env_path.parent / '.env.local'}")

    click.echo("\nRun 'sentinel validate' to verify configuration.")


def _update_env_file(env_path: Path, updates: dict) -> None:
    """Update or create .env.local file with the given key-value pairs.

    Writes to .env.local instead of .env to support read-only .env mounts
    in containerized environments. The .env.local file takes precedence
    over .env when loaded by config_loader.
    """
    # Always write to .env.local for local overrides
    local_env_path = env_path.parent / ".env.local"

    # Read existing .env.local content if it exists
    if local_env_path.exists():
        lines = local_env_path.read_text().split("\n")
    else:
        # Start with a header comment
        lines = ["# Local environment overrides (takes precedence over .env)"]

    # Update or add each key
    for key, value in updates.items():
        key_found = False
        for i, line in enumerate(lines):
            if line.startswith(f"{key}=") or line.startswith(f"# {key}="):
                if value:
                    lines[i] = f"{key}={value}"
                else:
                    # Remove the line if value is empty
                    lines[i] = f"# {key}="
                key_found = True
                break

        if not key_found and value:
            lines.append(f"{key}={value}")

    # Write to .env.local
    local_env_path.write_text("\n".join(lines))


@cli.group()
def projects() -> None:
    """Manage Sentinel projects.

    Commands for listing, adding, and removing projects from Sentinel configuration.
    """
    pass


@projects.command("list")
def projects_list() -> None:
    """List all configured projects."""
    try:
        config = get_config()
        worktree_mgr = WorktreeManager()
        all_projects = config.get_all_projects()

        if not all_projects:
            click.echo("No projects configured.")
            click.echo("\nUse 'sentinel projects add' to add a project.")
            return

        click.echo("📋 Configured Projects\n")
        for key, project in all_projects.items():
            repo_cloned = (worktree_mgr.workspace_root / key.lower()).exists()
            click.echo(f"  {key}")
            click.echo(f"    Git URL:     {project.get('git_url', 'N/A')}")
            click.echo(f"    Branch:      {project.get('default_branch', 'main')}")
            click.echo(f"    Repo cloned: {'Yes' if repo_cloned else 'No'}")
            if repo_cloned:
                worktrees = worktree_mgr.list_worktrees(key)
                if worktrees:
                    click.echo(f"    Worktrees:   {len(worktrees)} ({', '.join(worktrees)})")
                else:
                    click.echo(f"    Worktrees:   0")
            click.echo()

    except Exception as e:
        logger.error(f"Projects list failed: {e}", exc_info=True)
        click.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


@projects.command("add")
def projects_add() -> None:
    """Add a new project to Sentinel."""
    try:
        click.echo("➕ Add New Project\n")

        # Prompt for project details
        project_key = click.prompt("JIRA project key").strip().upper()
        git_url = click.prompt("Git origin URL (use HTTPS, not SSH)").strip()
        default_branch = click.prompt("Default branch", default="main").strip()

        # Validate inputs
        if not project_key:
            click.echo("❌ Project key cannot be empty", err=True)
            sys.exit(1)

        if not git_url:
            click.echo("❌ Git URL cannot be empty", err=True)
            sys.exit(1)

        # Add project to config
        config = get_config()
        config.add_project(project_key, git_url, default_branch)

        click.echo(f"\n✅ Project {project_key} added successfully")
        click.echo(f"   Git URL: {git_url}")
        click.echo(f"   Branch:  {default_branch}")

    except ValueError as e:
        click.echo(f"\n❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.error(f"Projects add failed: {e}", exc_info=True)
        click.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


@projects.command("edit")
@click.argument("project_key")
def projects_edit(project_key: str) -> None:
    """Edit an existing project's configuration.

    Args:
        project_key: JIRA project key to edit
    """
    try:
        config = get_config()

        # Check if project exists
        existing = config.get_project_config(project_key)
        if not existing:
            click.echo(f"❌ Project '{project_key}' not found", err=True)
            sys.exit(1)

        click.echo(f"✏️  Edit Project: {project_key.upper()}\n")
        click.echo("Press Enter to keep current value.\n")

        # Prompt with current values as defaults
        git_url = click.prompt(
            "Git origin URL (use HTTPS, not SSH)",
            default=existing.get("git_url", ""),
        ).strip()

        default_branch = click.prompt(
            "Default branch",
            default=existing.get("default_branch", "main"),
        ).strip()

        # Check if anything changed
        if (
            git_url == existing.get("git_url", "")
            and default_branch == existing.get("default_branch", "main")
        ):
            click.echo("\nℹ️  No changes made")
            return

        # Update project
        config.update_project(project_key, git_url, default_branch)

        click.echo(f"\n✅ Project {project_key.upper()} updated")
        click.echo(f"   Git URL: {git_url}")
        click.echo(f"   Branch:  {default_branch}")

    except ValueError as e:
        click.echo(f"\n❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.error(f"Projects edit failed: {e}", exc_info=True)
        click.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


@projects.command("remove")
@click.argument("project_key")
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Skip confirmation prompt.",
)
def projects_remove(project_key: str, yes: bool) -> None:
    """Remove a project from Sentinel configuration.

    Args:
        project_key: JIRA project key to remove
    """
    try:
        config = get_config()
        worktree_mgr = WorktreeManager()

        # Check if project exists
        existing = config.get_project_config(project_key)
        if not existing:
            click.echo(f"❌ Project '{project_key}' not found", err=True)
            sys.exit(1)

        # Check for existing worktrees
        worktrees = worktree_mgr.list_worktrees(project_key)
        bare_repo_exists = (worktree_mgr.workspace_root / project_key.lower()).exists()

        # Show what will be removed
        click.echo(f"🗑️  Remove Project: {project_key.upper()}\n")
        click.echo(f"   Git URL: {existing.get('git_url', 'N/A')}")
        click.echo(f"   Branch:  {existing.get('default_branch', 'main')}")

        click.echo("\n⚠️  This will remove:")
        click.echo("   • Project from Sentinel configuration")
        if bare_repo_exists:
            click.echo(f"   • Bare repository ({worktree_mgr.workspace_root / project_key.lower()})")
        if worktrees:
            click.echo(f"   • {len(worktrees)} worktree(s): {', '.join(worktrees[:5])}")
            if len(worktrees) > 5:
                click.echo(f"     ... and {len(worktrees) - 5} more")
        click.echo("\n   Remote repositories are NOT affected.")

        if not yes and not click.confirm("\nRemove this project?", default=False):
            click.echo("\n❌ Cancelled")
            return

        # Remove worktrees and bare repo first
        if bare_repo_exists:
            click.echo("\n1️⃣  Removing worktrees and bare repository...")
            result = worktree_mgr.reset_all(project_key)
            click.echo(f"   ✓ Removed {result['worktrees_removed']} worktree(s)")
            if result["repo_removed"]:
                click.echo("   ✓ Removed bare repository")

        # Remove from config
        click.echo("\n2️⃣  Removing from configuration...")
        config.remove_project(project_key)
        click.echo("   ✓ Configuration updated")

        click.echo(f"\n✅ Project {project_key.upper()} completely removed")

    except ValueError as e:
        click.echo(f"\n❌ {e}", err=True)
        sys.exit(1)
    except Exception as e:
        logger.error(f"Projects remove failed: {e}", exc_info=True)
        click.echo(f"❌ Error: {e}", err=True)
        sys.exit(1)


if __name__ == "__main__":
    cli()
