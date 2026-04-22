"""Drupal Developer Agent - Implements Drupal/PHP features using TDD."""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents.base_developer import BaseDeveloperAgent


logger = logging.getLogger(__name__)


class DrupalDeveloperAgent(BaseDeveloperAgent):
    """Agent that implements Drupal features using Test-Driven Development.

    Uses Claude Sonnet 4.5 for code generation with PHPUnit-based TDD.
    Inherits shared orchestration from BaseDeveloperAgent.
    Loads Drupal-specific overlay for system prompt enrichment.
    """

    _VALID_EXTENSIONS: frozenset = frozenset({
        # PHP / Drupal
        ".php", ".module", ".inc", ".install", ".theme", ".profile",
        ".engine", ".test",
        # Config / services
        ".yml", ".yaml",
        # Templates
        ".twig", ".html.twig",
        # Frontend assets
        ".js", ".css", ".scss", ".less",
        # Drupal libraries / info
        ".info", ".libraries",
        # Schema / SQL
        ".sql",
    })

    def __init__(self) -> None:
        """Initialize Drupal developer agent."""
        super().__init__(
            agent_name="drupal_developer",
            model="claude-4-5-sonnet",
            temperature=0.2,
        )
        self._load_stack_overlay()
        self._inject_environment_context()

    def _inject_environment_context(self) -> None:
        """Replace {{ key }} placeholders in system prompt with config values."""
        env = self.config.get("agents.drupal_developer.environment", {})
        if not env or not isinstance(env, dict):
            return
        def replace_placeholder(match: re.Match) -> str:
            key = match.group(1).strip()
            return str(env.get(key, "Not specified"))
        self.system_prompt = re.sub(r"\{\{\s*(\w+)\s*\}\}", replace_placeholder, self.system_prompt)

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

    def validate_config(self, worktree_path: Path) -> Dict[str, Any]:
        """Validate Drupal config sync by running site install.

        Matches DHL's GitLab CI ``test:test-site`` stage:
          cd web && drush site:install minimal --config-dir=../config/sync

        This catches config dependency gaps (e.g. field configs referencing
        paragraph types that were never created) that PHPUnit cannot detect.

        Args:
            worktree_path: Path to git worktree

        Returns:
            Dictionary with success, output, return_code
        """
        if not self._env_manager or not self._env_ticket_id:
            logger.warning("No container environment — skipping config validation")
            return {
                "success": True,
                "output": "Skipped (no container environment)",
                "return_code": 0,
            }

        logger.info("Running Drupal config sync validation (drush site:install)")

        # Ensure composer deps + scaffold files are installed
        self._ensure_composer_deps()

        # Run drush site:install matching DHL's CI pipeline.
        # Uses sh -c for env var expansion; defaults match typical Lando setup.
        result = self._env_manager.exec(
            ticket_id=self._env_ticket_id,
            service="appserver",
            command=[
                "sh", "-c",
                "PHP_OPTIONS='-d memory_limit=-1' "
                "php -d memory_limit=-1 ../vendor/bin/drush --verbose site:install minimal "
                "--config-dir=../config/sync -y "
                '--db-url="${DB_DRIVER:-mysql}://${MYSQL_USER:-drupal}:'
                '${MYSQL_PASSWORD:-drupal}@${DB_HOST:-database}/'
                '${MYSQL_DATABASE:-drupal}" '
                "--account-name=root --account-pass=rootpass",
            ],
            workdir="/app/web",
        )

        output = result.stdout + result.stderr
        success = result.returncode == 0

        if success:
            logger.info("Config validation passed — config sync clean")
        else:
            logger.error("Config validation FAILED:\n%s", output)

        env_issue = "memory size of" in output.lower() or "allowed memory" in output.lower()
        if env_issue:
            logger.warning("Config validation failed due to PHP memory exhaustion — environment issue")

        return {
            "success": success,
            "output": output,
            "return_code": result.returncode,
            **({"environment_issue": True} if env_issue else {}),
        }

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
