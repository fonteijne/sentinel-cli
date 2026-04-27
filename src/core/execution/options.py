"""Canonical execution-options model.

The Sentinel CLI grew flags ad hoc and Command Center later added a small
``ExecutionOptions`` model that silently dropped any flag it did not know
about. This module is the single source of truth that bridges the two:

* The CLI parses Click options into a :class:`WorkflowOptions` instance.
* The HTTP API parses the request body into a :class:`WorkflowOptions`
  instance via :class:`PlanOptions` / :class:`ExecuteOptions` /
  :class:`DebriefOptions` (which all forbid extra fields).
* The shared workflow runner (see :mod:`src.core.execution.workflows`) only
  ever consumes :class:`WorkflowOptions` — never a raw dict — so the CLI and
  service paths can never diverge silently.

Design rules (load-bearing):

* ``model_config = ConfigDict(extra="forbid")`` on every public model.
  Free-form dicts flow into agent prompts and Bash tool calls — extras are a
  prompt-injection vector. Unsupported options must fail validation, never
  be dropped silently.
* The model is **versioned** (``OPTIONS_SCHEMA_VERSION``). Persisted runs
  store the version they were created against, so a newer worker can refuse
  to resume an option set whose semantics it cannot guarantee.
* Each kind has its own subclass — keeps the surface area honest. ``--no-env``
  has no meaning for ``plan``, so ``PlanOptions`` does not carry it.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Bumped every time a meaningful option is added/renamed/removed. Persisted on
# the execution row so future workers can detect option-set drift.
OPTIONS_SCHEMA_VERSION = 1

# Re-used by the API layer — mirrors src.service.routes.commands._TICKET_ID_PATTERN.
_TICKET_ID_PATTERN = r"^[A-Z][A-Z0-9_]+-\d+$"


def _empty_string_to_none(value: Any) -> Any:
    """Coerce ``""`` → ``None`` before pattern validation runs.

    Swagger UI fills optional string fields with empty strings; treating
    those as absent keeps the API behaviour consistent across clients.
    """
    if value == "":
        return None
    return value


class _BaseOptions(BaseModel):
    """Common fields shared across plan/execute/debrief.

    ``prompt`` is the operator's free-form instruction injected into the
    agent's session. It is intentionally unconstrained — agents already
    sanitize their own prompts; here we only enforce a length cap so a
    misuse can't grow the events table or bust SQLite ``page_size`` limits.
    """

    model_config = ConfigDict(extra="forbid")

    prompt: Optional[str] = Field(default=None, max_length=8000)


class PlanOptions(_BaseOptions):
    """Options understood by the ``plan`` workflow.

    Mirrors the ``sentinel plan`` Click flags. Does NOT include ``--revise``
    on purpose: ``plan`` auto-detects state since the deprecation noted on
    the CLI command, so the only thing it ever did remotely was confuse
    operators. Emit a clear validation error if it shows up in a request.
    """

    force: bool = False


class ExecuteOptions(_BaseOptions):
    """Options understood by the ``execute`` workflow.

    Every flag here MUST round-trip through:
        CLI (`sentinel execute`) → CLI (`--remote` POST body) → API → worker.

    If you add a flag, also wire it into:
        * :func:`src.cli._remote_execute` — payload mapping;
        * :class:`src.service.routes.commands.StartExecutionBody` —
          request shape (which now reuses ``ExecuteOptions``);
        * the option-mapping tests in ``tests/core/test_options.py``.
    """

    revise: bool = False
    force: bool = False
    no_env: bool = False
    max_iterations: int = Field(default=5, ge=1, le=50)
    # ``max_turns`` historically meant "agent SDK turn budget" and is only
    # plumbed into the agent via its own tracker. Keep it optional so the
    # default agent budget is used when absent.
    max_turns: Optional[int] = Field(default=None, ge=1, le=200)


class DebriefOptions(_BaseOptions):
    """Options understood by the ``debrief`` workflow.

    ``follow_up_ticket`` was on the original v2 schema with no behaviour
    behind it. We keep it here, but it is currently surfaced as
    *unsupported* by the workflow (rejected at runtime) until the debrief
    follow-up wiring lands. That is preferable to the previous silent-drop
    behaviour: an operator now learns immediately that the option isn't
    being honoured.
    """

    follow_up_ticket: Optional[str] = Field(
        default=None, pattern=_TICKET_ID_PATTERN
    )

    _follow_up_empty_to_none = field_validator("follow_up_ticket", mode="before")(
        _empty_string_to_none
    )


# Public union type alias — the workflow runner accepts any of these.
WorkflowOptions = PlanOptions | ExecuteOptions | DebriefOptions


# --------------------------------------------------------------------------- #
# Helpers used by API/CLI when persisting on the execution row
# --------------------------------------------------------------------------- #


def to_metadata_options(options: WorkflowOptions) -> Dict[str, Any]:
    """Serialize options for storage in ``executions.metadata_json.options``.

    The serialized form keeps the schema version alongside the values so
    future readers can refuse rows they don't understand.
    """
    return {
        "schema_version": OPTIONS_SCHEMA_VERSION,
        "values": options.model_dump(mode="json"),
    }


def from_metadata_options(
    kind: str, raw: Optional[Dict[str, Any]]
) -> WorkflowOptions:
    """Re-hydrate options from ``metadata_json.options``.

    Falls back to defaults when the row predates the versioned schema (the
    pre-existing scaffold rows did not record a version). Raises ``ValueError``
    when the persisted version is newer than this worker supports.
    """
    cls = {
        "plan": PlanOptions,
        "execute": ExecuteOptions,
        "debrief": DebriefOptions,
    }.get(kind)
    if cls is None:
        raise ValueError(f"unknown execution kind: {kind!r}")

    if not raw:
        return cls()

    if "schema_version" in raw and "values" in raw:
        version = int(raw.get("schema_version") or 0)
        if version > OPTIONS_SCHEMA_VERSION:
            raise ValueError(
                f"options schema_version={version} > supported "
                f"{OPTIONS_SCHEMA_VERSION}; refusing to run"
            )
        values = raw.get("values") or {}
    else:
        # Legacy / un-versioned scaffold rows. Pass the raw dict directly so
        # ``extra="forbid"`` can complain about anything we can't handle —
        # an explicit error here is much better than silently dropping it.
        values = raw

    return cls.model_validate(values)
