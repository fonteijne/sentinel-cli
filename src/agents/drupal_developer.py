"""Drupal Developer Agent - Implements Drupal/PHP features using TDD."""

import logging
from pathlib import Path
from typing import Any, Dict, List

from src.agents.base_developer import BaseDeveloperAgent


logger = logging.getLogger(__name__)


class DrupalDeveloperAgent(BaseDeveloperAgent):
    """Agent that implements Drupal features using Test-Driven Development.

    Uses Claude Sonnet 4.5 for code generation with PHPUnit-based TDD.
    Inherits shared orchestration from BaseDeveloperAgent.
    Loads Drupal-specific overlay for system prompt enrichment.
    """

    def __init__(self) -> None:
        """Initialize Drupal developer agent."""
        super().__init__(
            agent_name="drupal_developer",
            model="claude-4-5-sonnet",
            temperature=0.2,
        )
        self._load_stack_overlay()

    def _load_stack_overlay(self) -> None:
        """Append Drupal developer overlay to system prompt."""
        overlays_dir = Path(__file__).parent.parent.parent / "prompts" / "overlays"
        overlay_path = overlays_dir / "drupal_developer.md"
        if overlay_path.exists():
            try:
                content = overlay_path.read_text()
                self.system_prompt += "\n\n" + content
                logger.info(f"Loaded Drupal developer overlay ({len(content)} chars)")
            except OSError as e:
                logger.warning(f"Failed to read Drupal developer overlay: {e}")

    def _build_tdd_prompt(
        self, task: str, context: Dict[str, Any], worktree_path: Path
    ) -> str:
        """Build Drupal-specific TDD prompt.

        Args:
            task: Task description
            context: Implementation context
            worktree_path: Path to git worktree

        Returns:
            Full TDD prompt for Drupal/PHP implementation
        """
        return f"""Execute Test-Driven Development (TDD) for the following Drupal task:

TASK: {task}

CONTEXT: {context}

WORKTREE PATH: {worktree_path}

TDD WORKFLOW (RED-GREEN-REFACTOR) FOR DRUPAL:

**Phase 1: RED - Write Failing Test**
1. Analyze the feature requirements
2. Identify the correct test type:
   - Unit test (tests/src/Unit/) for pure logic with mocked dependencies
   - Kernel test (tests/src/Kernel/) for service container and database
   - Functional test (tests/src/Functional/) for full browser behavior
3. Create PHPUnit test class:
   - Extend the appropriate base class (UnitTestCase, KernelTestBase, BrowserTestBase)
   - Add @covers and @group annotations
   - Write test methods covering happy path, edge cases, error conditions
4. Run: `vendor/bin/phpunit --filter={{TestClassName}}` to confirm test FAILS
5. If test passes unexpectedly, revise to actually test the feature

**Phase 2: GREEN - Minimal Implementation**
1. Analyze the failing test output
2. Implement in the correct module location:
   - Hooks → `{{module}}.module`
   - Controllers → `src/Controller/`
   - Forms → `src/Form/`
   - Services → `src/Service/` (register in `{{module}}.services.yml`)
   - Plugins → `src/Plugin/` (Block, Field, Views, etc.)
   - Event subscribers → `src/EventSubscriber/`
3. Follow Drupal coding standards
4. Use dependency injection (constructor injection via create()), NEVER \\Drupal::service()
5. Run: `vendor/bin/phpunit --filter={{TestClassName}}` to confirm tests PASS
6. Run: `drush cr` if you changed routes, services, plugins, or hooks
7. If tests fail, debug and iterate until passing

**Phase 3: REFACTOR - Improve Code Quality**
1. Review implementation for improvements
2. Apply refactoring:
   - Extract services for reusable logic
   - Use render arrays with proper cache metadata (#cache tags, contexts, max-age)
   - Ensure proper access checking on routes
3. Run `drush cr` after any structural changes
4. Run `vendor/bin/phpunit --filter={{TestClassName}}` after EACH change
5. Continue until code quality is acceptable

**Quality Gates:**
- All tests must pass
- Follow Drupal coding standards (phpcs --standard=Drupal,DrupalPractice)
- Use dependency injection, not static service calls
- Render arrays with cache metadata on all output
- No direct modification of core or contrib modules
- Config changes exported (drush cex if applicable)
- Proper docblock annotations (@param, @return, @throws)

**Deliverables:**
1. PHPUnit test file(s) created
2. Implementation file(s) created/modified (.module, .php, .yml)
3. All tests passing
4. Service/routing/permission YAML files if needed
5. Config schema if config entities were added

Execute this TDD cycle now. Use Read/Write/Edit tools for files and Bash for running phpunit/drush.
"""

    def _get_test_command(self) -> List[str]:
        """Return PHPUnit command for Drupal projects.

        Returns:
            PHPUnit command list
        """
        return ["vendor/bin/phpunit", "--testsuite=unit", "--no-coverage"]

    def _get_test_stub(self) -> str:
        """Return Drupal PHPUnit test stub.

        Returns:
            Minimal PHPUnit unit test file content
        """
        return '''<?php

namespace Drupal\\Tests\\custom_module\\Unit;

use Drupal\\Tests\\UnitTestCase;

/**
 * Tests for basic functionality.
 *
 * @group custom_module
 */
class BasicTest extends UnitTestCase {

  /**
   * Tests basic functionality.
   */
  public function testBasicFunctionality(): void {
    // TODO: Implement actual tests.
    $this->assertTrue(TRUE);
  }

  /**
   * Tests edge cases.
   */
  public function testEdgeCases(): void {
    // TODO: Implement actual tests.
    $this->assertTrue(TRUE);
  }

  /**
   * Tests error handling.
   */
  public function testErrorHandling(): void {
    // TODO: Implement actual tests.
    $this->assertTrue(TRUE);
  }

}
'''
