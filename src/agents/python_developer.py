"""Python Developer Agent - Implements Python features using TDD."""

import logging
from pathlib import Path
from typing import Any, Dict, List

from src.agents.base_developer import BaseDeveloperAgent


logger = logging.getLogger(__name__)


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

    def _get_test_command(self) -> List[str]:
        """Return pytest command for Python projects.

        Returns:
            pytest command list
        """
        return ["pytest", "-v", "--tb=short"]

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
