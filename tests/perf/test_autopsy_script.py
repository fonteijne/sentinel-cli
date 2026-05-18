"""Snapshot test for the autopsy script's report rendering."""

from __future__ import annotations

import io
from contextlib import redirect_stdout
from pathlib import Path

from tests.perf import autopsy

FIXTURES = Path(__file__).parent / "fixtures"
DIAG = FIXTURES / "sample_diagnostics.jsonl"
EXPECTED = FIXTURES / "sample_autopsy.expected.txt"


def test_autopsy_snapshot() -> None:
    """Feed the fixture through the autopsy CLI and compare stdout to the frozen .expected.txt.

    On mismatch, regenerate with::

        python tests/perf/autopsy.py \\
            --diagnostics tests/perf/fixtures/sample_diagnostics.jsonl \\
            --perf /dev/null > tests/perf/fixtures/sample_autopsy.expected.txt
    """
    buf = io.StringIO()
    with redirect_stdout(buf):
        autopsy.main([
            "--diagnostics", str(DIAG),
            "--perf", "/dev/null",
        ])
    actual = buf.getvalue()
    expected = EXPECTED.read_text()
    assert actual == expected, (
        "Autopsy output drifted from snapshot. To regenerate:\n"
        "  python tests/perf/autopsy.py "
        f"--diagnostics {DIAG} --perf /dev/null > {EXPECTED}\n"
        "Then re-run the test."
    )
