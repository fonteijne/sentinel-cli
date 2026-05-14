"""Python Developer Agent - Implements Python features using TDD."""

import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents._structured_errors import (
    StructuredError,
    parse_mypy,
    parse_pytest_short,
    parse_ruff_json,
)
from src.agents.base_developer import BaseDeveloperAgent


logger = logging.getLogger(__name__)


# Cap host-side static checks at 3 minutes — mirrors run_tests' subprocess
# timeout (300s for tests). Static checks should be much faster; the wider
# limit protects against pathological cases.
_STATIC_CHECK_TIMEOUT_S = 180


class PythonDeveloperAgent(BaseDeveloperAgent):
    """Agent that implements Python features using Test-Driven Development.

    Uses Claude Sonnet 4.5 for code generation with TDD approach.
    Inherits shared orchestration from BaseDeveloperAgent.
    """

    _VALID_EXTENSIONS: frozenset = frozenset({
        # Python
        ".py", ".pyi", ".pyx", ".pxd",
        # Config
        ".cfg", ".toml", ".ini", ".yml", ".yaml",
        # Data / schemas
        ".json", ".sql",
        # Templates
        ".html", ".jinja", ".jinja2", ".j2",
    })

    def __init__(self) -> None:
        """Initialize Python developer agent."""
        super().__init__(
            agent_name="python_developer",
            model="claude-4-5-sonnet",
            temperature=0.2,
        )

    def _build_tdd_prompt(
        self, task: str, context: Dict[str, Any], worktree_path: Path
    ) -> str:
        """Build Python-specific TDD prompt.

        Args:
            task: Task description
            context: Implementation context
            worktree_path: Path to git worktree

        Returns:
            Full TDD prompt for Python implementation
        """
        return f"""Execute Test-Driven Development (TDD) for the following task:

TASK: {task}

CONTEXT: {context}

WORKTREE PATH: {worktree_path}

TDD WORKFLOW (RED-GREEN-REFACTOR):

**Phase 1: RED - Write Failing Test**
1. Analyze the feature requirements
2. Create appropriate test file in tests/ directory
3. Write comprehensive test cases:
   - Happy path scenarios
   - Edge cases
   - Error conditions
4. Run pytest to confirm test FAILS
5. If test passes unexpectedly, revise to actually test the feature

**Phase 2: GREEN - Minimal Implementation**
1. Analyze the failing test output
2. Implement the MINIMAL code needed to pass tests
3. Write clean, well-typed Python code
4. Run pytest to confirm tests PASS
5. If tests fail, debug and iterate until passing

**Phase 3: REFACTOR - Improve Code Quality**
1. Review implementation for improvements
2. Apply refactoring (DRY principle, clear naming, proper structure)
3. Run pytest after EACH change to ensure tests still pass
4. Continue until code quality is acceptable

**Quality Gates:**
- All tests must pass
- Code must have type hints
- Follow PEP 8 style guidelines
- Cyclomatic complexity <= 10
- Function length <= 50 lines

**Deliverables:**
1. Test file(s) created
2. Implementation file(s) created/modified
3. All tests passing
4. Clean, well-structured code

Execute this TDD cycle now. Use Read/Write/Edit tools for files and Bash for running pytest.
"""

    def _get_test_command(self, paths: Optional[List[str]] = None) -> List[str]:
        """Return pytest command for Python projects.

        ``paths`` is accepted to match the base abstract signature but
        ignored — pytest-style scoping for changed Python files lands
        in a follow-up plan; this PR focuses on the Drupal verifier.
        Behavior is unchanged from before the changed-files-scope work.
        """
        return ["pytest", "-v", "--tb=short"]

    def _parse_test_output(
        self, raw: str, return_code: int
    ) -> List[StructuredError]:
        """Parse pytest --tb=short text output into structured errors."""
        return parse_pytest_short(raw)

    def run_static_checks(self, worktree_path: Path) -> Dict[str, Any]:
        """Run ruff + mypy on the host (Python projects don't use the appserver).

        On ``FileNotFoundError`` (tooling missing) the check is skipped with
        ``passed=True`` — mirrors the Drupal "no container" behavior so we
        don't fail a ticket because the env is incomplete.

        Returns:
            Dictionary matching ``run_tests`` shape (passed, test_results,
            structured_errors, return_code).
        """
        ruff_passed, ruff_out, ruff_errors, ruff_rc, ruff_skipped = self._run_ruff(
            worktree_path
        )
        mypy_passed, mypy_out, mypy_errors, mypy_rc, mypy_skipped = self._run_mypy(
            worktree_path
        )

        # If both tools are missing on this host, treat the whole step as a
        # skip — same semantics as a missing container for Drupal.
        if ruff_skipped and mypy_skipped:
            return {
                "passed": True,
                "test_results": "Skipped (ruff and mypy not available)",
                "structured_errors": [],
                "return_code": 0,
            }

        passed = ruff_passed and mypy_passed
        combined_output = ruff_out + mypy_out

        return {
            "passed": passed,
            "test_results": combined_output,
            "structured_errors": ruff_errors + mypy_errors,
            "return_code": 0 if passed else max(ruff_rc, mypy_rc),
        }

    def _run_ruff(
        self, worktree_path: Path
    ) -> tuple[bool, str, List[StructuredError], int, bool]:
        """Run ``ruff check --output-format=json``.

        Returns ``(passed, raw_output, errors, returncode, skipped)``.
        ``skipped=True`` only when the binary itself is missing.
        """
        try:
            result = subprocess.run(
                ["ruff", "check", "--output-format=json", "."],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=_STATIC_CHECK_TIMEOUT_S,
            )
        except FileNotFoundError:
            logger.info("ruff not installed — skipping ruff check")
            return True, "ruff: not installed (skipped)\n", [], 0, True
        except subprocess.TimeoutExpired:
            logger.error("ruff timed out")
            return False, "ruff: timed out\n", [], -1, False
        except Exception as e:
            logger.error("ruff invocation failed: %s", e)
            return False, f"ruff: {e}\n", [], -1, False

        errors = parse_ruff_json(result.stdout)
        passed = result.returncode == 0
        out = (result.stdout or "") + (result.stderr or "")
        return passed, out, errors, result.returncode, False

    def _run_mypy(
        self, worktree_path: Path
    ) -> tuple[bool, str, List[StructuredError], int, bool]:
        """Run ``mypy .``.

        Returns ``(passed, raw_output, errors, returncode, skipped)``.
        ``skipped=True`` only when the binary itself is missing.
        """
        try:
            result = subprocess.run(
                ["mypy", "."],
                cwd=worktree_path,
                capture_output=True,
                text=True,
                timeout=_STATIC_CHECK_TIMEOUT_S,
            )
        except FileNotFoundError:
            logger.info("mypy not installed — skipping mypy check")
            return True, "mypy: not installed (skipped)\n", [], 0, True
        except subprocess.TimeoutExpired:
            logger.error("mypy timed out")
            return False, "mypy: timed out\n", [], -1, False
        except Exception as e:
            logger.error("mypy invocation failed: %s", e)
            return False, f"mypy: {e}\n", [], -1, False

        errors = parse_mypy((result.stdout or "") + (result.stderr or ""))
        passed = result.returncode == 0
        out = (result.stdout or "") + (result.stderr or "")
        return passed, out, errors, result.returncode, False

    def _get_test_stub(self) -> str:
        """Return Python test stub.

        Returns:
            Minimal pytest test file content
        """
        return '''"""Tests for implementation."""

import pytest


def test_basic_functionality():
    """Test basic functionality."""
    # TODO: Implement actual tests
    pass


def test_edge_cases():
    """Test edge cases."""
    # TODO: Implement actual tests
    pass


def test_error_handling():
    """Test error handling."""
    # TODO: Implement actual tests
    pass
'''
