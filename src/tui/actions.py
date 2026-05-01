"""Thin wrappers that invoke existing Click command callbacks from the TUI.

Every action here calls into ``src.cli`` — no workflow logic lives in this
module. The wrappers:

* forward kwargs to the Click command's ``.callback`` (the plain function
  wrapped by ``@cli.command``),
* catch ``SystemExit`` so a CLI-style ``sys.exit(1)`` on failure doesn't
  kill the Textual app,
* return a boolean success flag so the caller can colour the result.

Stdout capture happens upstream in the caller (see ``src.tui.app``): the
caller replaces ``sys.stdout`` with a pipe that forwards lines into a
``RichLog`` widget before invoking an action.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass(frozen=True)
class ActionDef:
    """Metadata about a TUI action."""

    key: str
    label: str
    needs_ticket: bool
    needs_project: bool
    runner: Callable[..., bool]


def _run_cli_callback(command_name: str, **kwargs: object) -> bool:
    """Invoke a Click command's underlying callback.

    Returns ``True`` on success (the callback returned without raising and
    without ``sys.exit(non-zero)``); ``False`` on any failure. All output
    (stdout / stderr / ``click.echo``) is expected to be captured by the
    caller — this helper does not touch I/O itself.
    """
    from src import cli as cli_module

    cmd = cli_module.cli.commands.get(command_name)
    if cmd is None or cmd.callback is None:
        print(f"[tui] internal error: command '{command_name}' not found")
        return False

    try:
        cmd.callback(**kwargs)
        return True
    except SystemExit as exc:
        # Several CLI commands call sys.exit(1) on failure. In TUI-land that
        # would kill the whole app — treat it as a non-fatal action failure.
        code = exc.code if isinstance(exc.code, int) else 1
        return code == 0
    except Exception as exc:  # noqa: BLE001 — surface any exception to the log
        print(f"[tui] action '{command_name}' raised: {type(exc).__name__}: {exc}")
        return False


# --------------------------------------------------------------------------- #
# Action runners
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Action runners — uniform signature so the dispatcher can pass both args
# regardless of what each action actually consumes.
# --------------------------------------------------------------------------- #


def run_validate(ticket_id: Optional[str], project: Optional[str]) -> bool:
    return _run_cli_callback("validate")


def run_status(ticket_id: Optional[str], project: Optional[str]) -> bool:
    return _run_cli_callback("status", project=project)


def run_drain(ticket_id: Optional[str], project: Optional[str]) -> bool:
    return _run_cli_callback("push-pending", quiet=False)


def run_plan(ticket_id: Optional[str], project: Optional[str]) -> bool:
    if not ticket_id:
        print("[tui] plan requires a ticket id")
        return False
    return _run_cli_callback(
        "plan",
        ticket_id=ticket_id,
        project=project,
        revise=False,
        force=False,
        prompt=None,
    )


def run_execute(ticket_id: Optional[str], project: Optional[str]) -> bool:
    if not ticket_id:
        print("[tui] execute requires a ticket id")
        return False
    return _run_cli_callback(
        "execute",
        ticket_id=ticket_id,
        project=project,
        max_iterations=5,
        force=False,
        revise=False,
        no_env=False,
        prompt=None,
        remote=False,
        follow=False,
        idempotency_key=None,
    )


def run_debrief(ticket_id: Optional[str], project: Optional[str]) -> bool:
    if not ticket_id:
        print("[tui] debrief requires a ticket id")
        return False
    return _run_cli_callback(
        "debrief",
        ticket_id=ticket_id,
        project=project,
        prompt=None,
    )


# --------------------------------------------------------------------------- #
# Action registry — drives the home screen's action list
# --------------------------------------------------------------------------- #

ACTIONS: tuple[ActionDef, ...] = (
    ActionDef("plan", "Plan a ticket", needs_ticket=True, needs_project=True, runner=run_plan),
    ActionDef("execute", "Execute a plan", needs_ticket=True, needs_project=True, runner=run_execute),
    ActionDef("debrief", "Debrief a ticket", needs_ticket=True, needs_project=True, runner=run_debrief),
    ActionDef("status", "Status (worktrees + deferred pushes)", needs_ticket=False, needs_project=True, runner=run_status),
    ActionDef("drain", "Drain deferred pushes", needs_ticket=False, needs_project=False, runner=run_drain),
    ActionDef("validate", "Validate credentials", needs_ticket=False, needs_project=False, runner=run_validate),
)
