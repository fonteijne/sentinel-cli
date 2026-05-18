"""Fixtures for perf-instrumentation tests."""

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from src.utils import perf


@pytest.fixture
def enable_perf(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enable SENTINEL_PERF and reset cached state."""
    monkeypatch.setenv("SENTINEL_PERF", "1")
    perf.reset_for_tests()


@pytest.fixture
def tmp_log_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Redirect perf-log writes to a temp dir.

    Pins the cached log path so ``perf_log_path()`` does not retry the
    ``/app/logs`` / ``logs`` resolution. Pairs with ``enable_perf`` for
    test isolation.
    """
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    perf._LOG_PATH = log_dir / "perf.jsonl"  # type: ignore[attr-defined]
    yield log_dir
    perf.reset_for_tests()
