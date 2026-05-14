"""Golden-file tests for structured-error parsers.

Plan reference: phase-1-close-the-leash.plan.md §Tasks-13.

Each fixture under ``tests/fixtures/static_check_output/`` is the kind of
output the verifier shells out to, captured (or hand-crafted to mirror the
shape of) a real run with secrets and customer paths scrubbed. These tests
guard the parser contract against ANY real verifier output, not just the
inline strings used in ``test_structured_error_adapters.py``.

If a parser regresses on a real-world shape, this file fails first — and the
fixture file documents exactly which shape was lost.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from src.agents._structured_errors import (
    StructuredError,
    parse_composer_validate,
    parse_mypy,
    parse_phpstan_json,
    parse_phpunit_junit,
    parse_pytest_short,
    parse_ruff_json,
)

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "static_check_output"


@pytest.mark.parametrize(
    "fixture_name,parser,expected_min_count",
    [
        ("phpstan_pass.json", parse_phpstan_json, 0),
        ("phpstan_fail.json", parse_phpstan_json, 2),
        ("phpunit_junit_pass.xml", parse_phpunit_junit, 0),
        ("phpunit_junit_fail.xml", parse_phpunit_junit, 2),
        ("pytest_short_pass.txt", parse_pytest_short, 0),
        ("pytest_short_fail.txt", parse_pytest_short, 2),
        ("composer_validate_ok.txt", parse_composer_validate, 0),
        ("composer_validate_fail.txt", parse_composer_validate, 1),
        ("mypy_pass.txt", parse_mypy, 0),
        ("mypy_fail.txt", parse_mypy, 3),
        ("ruff_pass.json", parse_ruff_json, 0),
        ("ruff_fail.json", parse_ruff_json, 2),
    ],
)
def test_parser_against_golden_fixture(
    fixture_name: str,
    parser: Callable[[str], list[StructuredError]],
    expected_min_count: int,
) -> None:
    path = _FIXTURES_DIR / fixture_name
    assert path.exists(), f"fixture missing: {path}"

    output = parser(path.read_text())

    assert len(output) >= expected_min_count, (
        f"{fixture_name} expected ≥{expected_min_count} structured errors, "
        f"got {len(output)}"
    )

    # Every entry must be well-formed: file present, line is int, rule and
    # message non-empty. ``line`` may legitimately be 0 (e.g. pytest --tb=short
    # summary lines and composer_validate), so we only require the type.
    for err in output:
        assert err["file"], (
            f"{fixture_name}: entry has empty file: {err!r}"
        )
        assert isinstance(err["line"], int), (
            f"{fixture_name}: entry line is not int: {err!r}"
        )
        assert err["rule"], (
            f"{fixture_name}: entry has empty rule: {err!r}"
        )
        assert err["message"], (
            f"{fixture_name}: entry has empty message: {err!r}"
        )


def test_phpstan_fail_recovers_identifier_and_level_variants() -> None:
    """The PHPStan fail fixture mixes ``identifier`` and ``level`` messages.

    The parser must use ``identifier`` when present and fall back to
    ``level:N`` (or ``phpstan`` if neither exists). This single test
    exercises all three branches at once.
    """
    output = parse_phpstan_json(
        (_FIXTURES_DIR / "phpstan_fail.json").read_text()
    )
    rules = {e["rule"] for e in output}
    assert "method.notFound" in rules, "identifier branch lost"
    assert "argument.type" in rules, "second identifier lost"
    assert "level:5" in rules, "level fallback lost"
    assert "phpstan" in rules, "default fallback lost"


def test_phpunit_junit_fail_distinguishes_failure_from_error() -> None:
    """JUnit XML fixture has both ``<failure>`` and ``<error>`` children.

    The parser uses the ``type`` attribute; both kinds of node appear in the
    output. We assert at least one of each made it through.
    """
    output = parse_phpunit_junit(
        (_FIXTURES_DIR / "phpunit_junit_fail.xml").read_text()
    )
    rules = [e["rule"] for e in output]
    # Failure (assertion) and error (TypeError) both surface their `type` attr.
    assert any("ExpectationFailedException" in r for r in rules), (
        "failure-type rule missing"
    )
    assert "TypeError" in rules, "error-type rule missing"


def test_pytest_short_fail_distinguishes_failed_from_error() -> None:
    """Pytest fixture has both ``FAILED`` and ``ERROR`` summary lines."""
    output = parse_pytest_short(
        (_FIXTURES_DIR / "pytest_short_fail.txt").read_text()
    )
    rules = [e["rule"] for e in output]
    assert "test_failed" in rules, "FAILED line missing"
    assert "test_error" in rules, "ERROR line missing"


def test_mypy_fail_handles_rule_optional_and_col_variant() -> None:
    """Mypy fixture covers ``file:line: error: msg [rule]``,
    ``file:line:col: error: msg [rule]``, and ``file:line: error: msg``
    (no rule). Default ``mypy_error`` must apply to the no-rule line.
    """
    output = parse_mypy(
        (_FIXTURES_DIR / "mypy_fail.txt").read_text()
    )
    rules = [e["rule"] for e in output]
    assert "return-value" in rules
    assert "arg-type" in rules  # the line:col variant
    assert "mypy_error" in rules  # the no-rule fallback


def test_composer_validate_fail_truncates_long_messages() -> None:
    """``parse_composer_validate`` truncates message at 1000 chars.

    The fail fixture is well under 1000 chars, but we assert the upper bound
    so a future fixture growth doesn't silently break the contract.
    """
    output = parse_composer_validate(
        (_FIXTURES_DIR / "composer_validate_fail.txt").read_text()
    )
    assert len(output) == 1
    assert len(output[0]["message"]) <= 1000
    assert output[0]["file"] == "composer.json"
    assert output[0]["rule"] == "composer_validate"
