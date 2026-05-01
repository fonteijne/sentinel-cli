"""TUI action registry — local in-process runs + remote service calls.

Two kinds of actions live here:

* **local** (``validate``, ``status``, ``drain``) — run the existing Click
  command callbacks in-process. Fast, read-only, don't need the service.
  ``runner`` is a synchronous ``Callable[..., bool]`` invoked inside
  :func:`src.tui.widgets.run_output.capture_stdout_to_log` so fd-level
  stdout/stderr lands in the Output panel.

* **remote** (``plan``, ``execute``, ``debrief``) — dispatch to the
  Command Center service via :mod:`src.tui.service_client`. The home
  screen POSTs ``/executions``, tails the WebSocket stream, and renders
  frames into the Output panel. Quitting the TUI leaves the worker
  running on the service side.

``ActionDef.kind`` is the discriminator. Remote actions carry a
``remote_kind`` matching the service's ``ExecutionKind`` enum
(``plan`` / ``execute`` / ``debrief``) and have ``runner=None`` — the
home screen calls ``service_client.start(...)`` directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional


@dataclass(frozen=True)
class ActionDef:
    """Metadata about a TUI action.

    Attributes:
        key: Stable identifier — also used as ``#action-{key}`` in the
            ListView and for registry lookups.
        label: Human-readable label shown in the action list.
        needs_ticket: Whether this action requires a ticket id
            (prompt the user if so).
        needs_project: Whether a project must be picked first.
        kind: ``"local"`` runs in-process; ``"remote"`` dispatches to
            the Command Center service.
        runner: Sync callable for local actions. ``None`` for remote
            actions (the home screen calls the service client instead).
        remote_kind: For remote actions, the ``ExecutionKind`` to send
            to ``POST /executions``. ``None`` for local actions.
    """

    key: str
    label: str
    needs_ticket: bool
    needs_project: bool
    kind: Literal["local", "remote"] = "local"
    runner: Optional[Callable[..., bool]] = None
    remote_kind: Optional[str] = None


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
# Local action runners — uniform signature so the dispatcher can pass both
# args regardless of what each action actually consumes.
# --------------------------------------------------------------------------- #


def run_validate(ticket_id: Optional[str], project: Optional[str]) -> bool:
    return _run_cli_callback("validate")


def run_status(ticket_id: Optional[str], project: Optional[str]) -> bool:
    return _run_cli_callback("status", project=project)


def run_drain(ticket_id: Optional[str], project: Optional[str]) -> bool:
    return _run_cli_callback("push-pending", quiet=False)


# --------------------------------------------------------------------------- #
# Action registry — drives the home screen's action list.
#
# Order matters: tests/tui/test_app_smoke.py::test_app_mounts_and_shows_actions
# asserts the ids on the ListView match ``{f"action-{a.key}" for a in ACTIONS}``
# (via set equality, so order is only load-bearing for UX). Keep plan /
# execute / debrief at the top where the operator expects them.
# --------------------------------------------------------------------------- #

ACTIONS: tuple[ActionDef, ...] = (
    ActionDef(
        key="plan",
        label="Plan a ticket",
        needs_ticket=True,
        needs_project=True,
        kind="remote",
        remote_kind="plan",
    ),
    ActionDef(
        key="execute",
        label="Execute a plan",
        needs_ticket=True,
        needs_project=True,
        kind="remote",
        remote_kind="execute",
    ),
    ActionDef(
        key="debrief",
        label="Debrief a ticket",
        needs_ticket=True,
        needs_project=True,
        kind="remote",
        remote_kind="debrief",
    ),
    ActionDef(
        key="status",
        label="Status (worktrees + deferred pushes)",
        needs_ticket=False,
        needs_project=True,
        kind="local",
        runner=run_status,
    ),
    ActionDef(
        key="drain",
        label="Drain deferred pushes",
        needs_ticket=False,
        needs_project=False,
        kind="local",
        runner=run_drain,
    ),
    ActionDef(
        key="validate",
        label="Validate credentials",
        needs_ticket=False,
        needs_project=False,
        kind="local",
        runner=run_validate,
    ),
)
