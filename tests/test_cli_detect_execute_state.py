"""Tests for ``src.cli._detect_execute_state``.

The detector mirrors :meth:`PlanGeneratorAgent._detect_plan_state` — it
inspects the ticket's source-branch MR for unresolved discussions and
returns either ``{"state": "has_feedback", ...}`` or ``{"state": "fresh"}``.

These tests stub ``get_config`` and ``GitLabClient`` directly; we never
touch a real config file or hit GitLab.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

import pytest

from src import cli as cli_module
from src.cli import _detect_execute_state


def _stub_config(git_url: str) -> Any:
    """Return a stub `get_config()` whose ``get_project_config`` yields ``git_url``."""
    project_config = {"git_url": git_url}
    cfg = Mock()
    cfg.get_project_config.return_value = project_config
    return cfg


def _patch_gitlab(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mrs: list[dict[str, Any]] | None = None,
    discussions: list[dict[str, Any]] | None = None,
) -> Mock:
    """Replace ``src.gitlab_client.GitLabClient`` with a Mock instance.

    ``_detect_execute_state`` imports ``GitLabClient`` lazily inside the
    function, so we patch the class at its source module — that's what the
    local import resolves to.
    """
    instance = Mock()
    instance.list_merge_requests.return_value = mrs or []
    instance.get_merge_request_discussions.return_value = discussions or []

    cls = Mock()
    cls.return_value = instance
    cls.extract_project_path = Mock(return_value="group/project")

    monkeypatch.setattr("src.gitlab_client.GitLabClient", cls)
    return instance


def test_detect_returns_fresh_when_no_git_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No ``git_url`` configured → no MR lookup possible → ``fresh``."""
    monkeypatch.setattr(cli_module, "get_config", lambda: _stub_config(""))

    state = _detect_execute_state("ACME-1", "ACME")

    assert state == {"state": "fresh"}


def test_detect_returns_fresh_when_no_mrs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``git_url`` present but no MR for the source branch → ``fresh``."""
    monkeypatch.setattr(
        cli_module, "get_config", lambda: _stub_config("git@gitlab/x.git")
    )
    _patch_gitlab(monkeypatch, mrs=[])

    state = _detect_execute_state("ACME-1", "ACME")

    assert state == {"state": "fresh"}


def test_detect_returns_fresh_when_only_resolved_discussions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MR exists but ``unresolved_only=True`` returns ``[]`` → ``fresh``.

    This is the deliberate behavior choice documented in the plan: if all
    MR discussions are resolved, fresh execute is the right path. Mirrors
    today's behavior when the user picked plain "Execute" from the menu.
    """
    monkeypatch.setattr(
        cli_module, "get_config", lambda: _stub_config("git@gitlab/x.git")
    )
    _patch_gitlab(
        monkeypatch,
        mrs=[{"iid": 7, "web_url": "https://gitlab/mr/7"}],
        discussions=[],
    )

    state = _detect_execute_state("ACME-1", "ACME")

    assert state == {"state": "fresh"}


def test_detect_returns_has_feedback_with_unresolved_discussions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MR with at least one unresolved discussion → ``has_feedback``."""
    monkeypatch.setattr(
        cli_module, "get_config", lambda: _stub_config("git@gitlab/x.git")
    )
    instance = _patch_gitlab(
        monkeypatch,
        mrs=[{"iid": 42, "web_url": "https://gitlab/mr/42"}],
        discussions=[{"id": "d1", "notes": []}],
    )

    state = _detect_execute_state("ACME-1", "ACME")

    assert state == {
        "state": "has_feedback",
        "mr_iid": 42,
        "mr_url": "https://gitlab/mr/42",
        "project_path": "group/project",
    }
    # Confirms we asked specifically for unresolved discussions.
    instance.get_merge_request_discussions.assert_called_once()
    _, kwargs = instance.get_merge_request_discussions.call_args
    assert kwargs.get("unresolved_only") is True


def test_detect_propagates_gitlab_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """GitLab errors must propagate — no silent demote-to-fresh.

    A silent fallback would re-create the same footgun the menu collapse
    is meant to fix (overwriting in-flight revision work).
    """
    monkeypatch.setattr(
        cli_module, "get_config", lambda: _stub_config("git@gitlab/x.git")
    )
    instance = _patch_gitlab(monkeypatch)
    instance.list_merge_requests.side_effect = RuntimeError("gitlab down")

    with pytest.raises(RuntimeError, match="gitlab down"):
        _detect_execute_state("ACME-1", "ACME")
