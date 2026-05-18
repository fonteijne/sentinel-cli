"""Unit tests for src/utils/perf.py."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from src.utils import perf
from src.utils.perf import is_enabled, reset_for_tests, timed


def test_disabled_by_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When SENTINEL_PERF is unset, no log file is created."""
    monkeypatch.delenv("SENTINEL_PERF", raising=False)
    reset_for_tests()
    perf._LOG_PATH = tmp_path / "perf.jsonl"  # type: ignore[attr-defined]
    assert is_enabled() is False
    with timed("foo"):
        pass
    assert not (tmp_path / "perf.jsonl").exists()
    reset_for_tests()


def test_timed_writes_jsonl_when_enabled(enable_perf: None, tmp_log_dir: Path) -> None:
    """An enabled span produces one JSONL record with the documented schema."""
    with timed("unit.example", meta={"k": "v"}):
        pass
    log = tmp_log_dir / "perf.jsonl"
    assert log.exists()
    records = [json.loads(line) for line in log.read_text().splitlines() if line]
    assert len(records) == 1
    rec = records[0]
    assert set(rec.keys()) == {"ts", "span", "elapsed_s", "thread", "pid", "meta"}
    assert rec["span"] == "unit.example"
    assert rec["meta"] == {"k": "v"}
    assert isinstance(rec["elapsed_s"], (int, float))
    assert rec["elapsed_s"] >= 0


def test_timed_supports_add_meta(enable_perf: None, tmp_log_dir: Path) -> None:
    """add_meta() additions are persisted to the record."""
    with timed("unit.add_meta") as span:
        span.add_meta("size", 42)
        span.add_meta("kind", "thing")
    records = [json.loads(line)
               for line in (tmp_log_dir / "perf.jsonl").read_text().splitlines() if line]
    assert records[-1]["meta"] == {"size": 42, "kind": "thing"}


def test_timed_records_exception_meta(enable_perf: None, tmp_log_dir: Path) -> None:
    """Exceptions are tagged into meta.error and re-raised."""
    with pytest.raises(ValueError):
        with timed("unit.boom"):
            raise ValueError("explode")
    records = [json.loads(line)
               for line in (tmp_log_dir / "perf.jsonl").read_text().splitlines() if line]
    assert records[-1]["meta"]["error"] == "ValueError"


def test_timed_handles_concurrent_threads(enable_perf: None, tmp_log_dir: Path) -> None:
    """4 threads × 100 spans → 400 valid JSONL lines, no truncation."""

    def worker() -> None:
        for _ in range(100):
            with timed("unit.thread"):
                pass

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = (tmp_log_dir / "perf.jsonl").read_text().splitlines()
    assert len(lines) == 400
    for line in lines:
        rec = json.loads(line)
        assert rec["span"] == "unit.thread"


def test_disabled_path_returns_noop_span(monkeypatch: pytest.MonkeyPatch,
                                          tmp_path: Path) -> None:
    """add_meta on a disabled span is a no-op (does not raise, does not record)."""
    monkeypatch.delenv("SENTINEL_PERF", raising=False)
    reset_for_tests()
    perf._LOG_PATH = tmp_path / "perf.jsonl"  # type: ignore[attr-defined]
    with timed("noop") as span:
        span.add_meta("ignored", True)
    assert not (tmp_path / "perf.jsonl").exists()
    reset_for_tests()
