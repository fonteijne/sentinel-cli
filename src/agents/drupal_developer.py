"""Drupal Developer Agent - Implements Drupal/PHP features using TDD."""

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents._structured_errors import (
    StructuredError,
    parse_composer_validate,
    parse_phpstan_json,
    parse_phpunit_junit,
)
from src.agents.base_developer import BaseDeveloperAgent


logger = logging.getLogger(__name__)


# Path the PHPUnit run writes its JUnit XML to. Inside the container the file
# lives under /tmp/ — best-effort to read it back; if unreachable we fall back
# to ``[]`` and rely on static-check signal for the loop to converge.
_PHPUNIT_JUNIT_PATH = "/tmp/phpunit-junit.xml"


# Matches cweagans/composer-patches' "Cannot apply patch <desc> (<path>)!" line.
# We capture the path inside the parens — that's the file we re-run with
# ``patch --dry-run`` to surface the rejected hunk.
_PATCH_FAIL_RE = re.compile(r"Cannot apply patch[^()\n]*\(([^)\n]+)\)")


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

        # Ensure composer deps + scaffold files are installed.
        # If composer install fails permanently (3 attempts), abort BEFORE
        # running drush — otherwise the broken vendor/ tree produces
        # misleading downstream errors (e.g. "module X requires module Y"
        # when the real cause is a failed patch or blocked plugin).
        composer_result = self._ensure_composer_deps()
        if composer_result is not None and composer_result.returncode != 0:
            composer_output = (
                (composer_result.stdout or "") + (composer_result.stderr or "")
            )
            logger.error(
                "composer install failed before config validation could run — "
                "aborting. Most common causes: a failing cweagans/composer-patches "
                "patch, or a Composer plugin blocked by allow-plugins config."
            )
            patch_diagnostics = self._diagnose_failed_patches(composer_output)
            return {
                "success": False,
                "output": (
                    "composer install failed after 3 attempts; cannot validate "
                    "config sync. The downstream drush site:install was skipped "
                    "because a half-installed vendor/ tree produces misleading "
                    "'missing module' errors that obscure the real cause.\n\n"
                    "--- composer install output (last attempt) ---\n"
                    + composer_output
                    + patch_diagnostics
                ),
                "return_code": composer_result.returncode,
            }

        # Run drush site:install matching DHL's CI pipeline.
        # Uses sh -c for env var expansion; defaults match typical Lando setup.
        #
        # The wipe is three-pronged because two earlier single-prong
        # attempts each missed a class of stale state:
        #
        #   1. DB at SQL level (DROP DATABASE / CREATE DATABASE) — drops
        #      every table atomically. Replaces ``drush sql:drop -y || true``
        #      which used to fail silently on a half-written settings.php
        #      and let site:install hit its own atomic DROP TABLE that
        #      breaks on any missing table.
        #   2. settings.php — left over from a previous iteration's install,
        #      it pins the connection drush uses for the *bootstrap probe*
        #      (which decides "is this site already installed?"). If that
        #      probe runs against a connection different from the one we
        #      just wiped (e.g. project's .lando.yml uses a non-default DB
        #      name), the probe sees the old install and throws
        #      ``AlreadyInstalledException``. Removing it forces drush to
        #      regenerate from ``--db-url`` so probe and wipe target the
        #      same DB.
        #   3. sites/default/files/php — Drupal's compiled-container cache.
        #      Survives DB wipes; can hold class definitions that reference
        #      schema versions matching the prior install, contributing to
        #      false "already installed" detections.
        #
        # The DB service is on host ``database`` with root password ``root``
        # (set in lando_translator's mysql/mariadb block).
        db_url = (
            '"${DB_DRIVER:-mysql}://${MYSQL_USER:-drupal}:'
            '${MYSQL_PASSWORD:-drupal}@${DB_HOST:-database}/'
            '${MYSQL_DATABASE:-drupal}"'
        )
        result = self._env_manager.exec(
            ticket_id=self._env_ticket_id,
            service="appserver",
            command=[
                "sh", "-c",
                # SQL-level wipe. && (not ;) so a connection failure here
                # short-circuits the whole step — site:install would fail
                # less informatively against an unreachable DB. 2>&1 on the
                # mysql call so any connection / privilege error reaches
                # the caller's stderr (the parser otherwise sees the
                # downstream AlreadyInstalled error and misattributes).
                'DB_NAME="${MYSQL_DATABASE:-drupal}"; '
                'DB_HOST_VAL="${DB_HOST:-database}"; '
                'mysql -h "$DB_HOST_VAL" -uroot -proot 2>&1 -e '
                '"DROP DATABASE IF EXISTS \\`$DB_NAME\\`; '
                'CREATE DATABASE \\`$DB_NAME\\`;" && '
                # Force regeneration from --db-url. Without this, drush's
                # bootstrap probe reads the old settings.php and may target
                # a DB other than the one we just cleared.
                'rm -f sites/default/settings.php sites/default/services.yml && '
                # Drupal's compiled-container cache can survive DB wipes
                # and contribute to "already installed" detections; nuke it.
                'rm -rf sites/default/files/php && '
                "PHP_OPTIONS='-d memory_limit=-1' "
                "php -d memory_limit=-1 ../vendor/bin/drush --verbose site:install minimal "
                f"--config-dir=../config/sync -y --db-url={db_url} "
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

    def _diagnose_failed_patches(self, composer_output: str) -> str:
        """Re-run failed patches with ``patch --dry-run`` to surface rejected hunks.

        cweagans/composer-patches reports only ``Cannot apply patch X (Y)!`` and
        swallows the GNU ``patch`` output that names the file/hunk/line that
        actually rejected. We re-run each failed patch ourselves so the abort
        message includes the specific rejection.

        Target dir guess uses the convention ``patches/contrib/<module>/...``
        → ``web/modules/contrib/<module>``. When that doesn't match the
        project layout we fall back to a hint instead of a real diagnosis,
        rather than guessing wrong and confusing the reader.

        Returns a formatted block to append to the abort message, or an
        empty string when nothing further can be determined (no patch line
        matched, or no environment to exec into).
        """
        if not self._env_manager or not self._env_ticket_id:
            return ""

        # dict.fromkeys preserves first-seen order while deduping repeated paths
        # across composer's multiple retry blocks.
        patch_paths = list(dict.fromkeys(_PATCH_FAIL_RE.findall(composer_output)))
        if not patch_paths:
            return ""

        sections: List[str] = []
        for raw_path in patch_paths:
            norm = raw_path.lstrip("./").strip()
            parts = norm.split("/")
            target_dir: Optional[str] = None
            if len(parts) >= 3 and parts[0] == "patches" and parts[1] == "contrib":
                target_dir = f"web/modules/contrib/{parts[2]}"

            if target_dir is None:
                sections.append(
                    f"--- {raw_path} ---\n"
                    "Could not auto-detect target install dir from patch path "
                    "(expected layout: patches/contrib/<module>/...).\n"
                    "Run manually inside appserver: composer install -vvv"
                )
                continue

            dry = self._env_manager.exec(
                ticket_id=self._env_ticket_id,
                service="appserver",
                command=[
                    "sh",
                    "-c",
                    # head -120 caps output so a wildly drifted patch doesn't
                    # bloat the abort message; the first few rejected hunks
                    # are always the most useful signal.
                    f"patch -p1 --dry-run -i /app/{norm} -d /app/{target_dir} 2>&1 "
                    "| head -120",
                ],
                workdir="/app",
            )
            sections.append(
                f"--- {raw_path} (target: {target_dir}) ---\n"
                + ((dry.stdout or "") + (dry.stderr or "")).strip()
            )

        if not sections:
            return ""
        return (
            "\n\n--- patch dry-run diagnostics ---\n\n"
            + "\n\n".join(sections)
            + "\n"
        )

    def _get_test_command(self, paths: Optional[List[str]] = None) -> List[str]:
        """Return PHPUnit command for Drupal projects.

        When ``paths`` is non-empty, runs phpunit against just those
        files/dirs — the changed-files scope, derived from the pre-task
        SHA in ``BaseDeveloperAgent._derive_changed_test_paths``. This
        keeps the verifier from blaming the current task for *prior*
        tasks' broken tests.

        When ``paths`` is ``None`` or empty, falls back to
        ``web/modules/custom`` so implementation-only tasks (no test
        files touched) still get a verifier signal. The broad scope
        avoids ``--testsuite=unit`` for two reasons:

          1. The developer agent only modifies code under
             ``web/modules/custom`` — that's where its tests land, and
             that's what the verifier should grade.
          2. Running the full ``unit`` testsuite sweeps in contrib tests
             whose autoload depends on modules the project doesn't
             require (e.g. honeypot's tests reference ``drupal/rules``
             classes; if rules isn't installed, PHPUnit dies before
             running anything). That kills every task's verifier with
             an error that has nothing to do with the developer's work.
             Scoping to custom sidesteps the entire pollution problem.

        Includes ``--log-junit`` so a structured error report is produced
        alongside the human-readable output. The verifier loop reads this
        file in ``_parse_test_output``.
        """
        cmd = ["vendor/bin/phpunit"]
        cmd += list(paths) if paths else ["web/modules/custom"]
        cmd += ["--no-coverage", f"--log-junit={_PHPUNIT_JUNIT_PATH}"]
        return cmd

    def _parse_test_output(
        self, raw: str, return_code: int
    ) -> List[StructuredError]:
        """Parse PHPUnit output into structured errors.

        We prefer the JUnit XML written by ``--log-junit``. When tests run on
        the host the file is local and reachable; when tests run inside the
        appserver container the host has no shared /tmp, so we return ``[]``
        and let the static-check verifier (PHPStan + composer validate)
        carry the structured signal for the refine prompt. This is an
        acknowledged Phase 1 trade — Loop A still terminates correctly
        because the cap is hard.
        """
        try:
            xml_path = Path(_PHPUNIT_JUNIT_PATH)
            if xml_path.exists():
                return parse_phpunit_junit(xml_path.read_text())
            logger.debug(
                "PHPUnit JUnit XML not accessible at %s — returning [] for parser",
                xml_path,
            )
        except OSError as e:
            logger.debug("Could not read PHPUnit JUnit XML: %s", e)
        return []

    def run_static_checks(self, worktree_path: Path) -> Dict[str, Any]:
        """Run PHPStan + composer validate inside the appserver container.

        Mirrors ``validate_config``: when no container is attached we skip
        gracefully (passed=True, structured_errors=[]). This treats a missing
        environment as an env issue, not a code failure — Loop A only fails
        when a verifier produces real errors.

        Returns:
            Dictionary matching ``run_tests`` shape (passed, test_results,
            structured_errors, return_code).
        """
        if not self._env_manager or not self._env_ticket_id:
            logger.warning("No container environment — skipping static checks")
            return {
                "passed": True,
                "test_results": "Skipped (no container environment)",
                "structured_errors": [],
                "return_code": 0,
            }

        # Ensure composer deps + scaffold files are installed (PHPStan and
        # composer validate both need vendor/).
        self._ensure_composer_deps()

        try:
            phpstan = self._env_manager.exec(
                ticket_id=self._env_ticket_id,
                service="appserver",
                command=[
                    "vendor/bin/phpstan",
                    "analyse",
                    "--error-format=json",
                    "--no-progress",
                    "web/modules/custom",
                ],
                workdir="/app",
            )
            composer = self._env_manager.exec(
                ticket_id=self._env_ticket_id,
                service="appserver",
                command=[
                    "composer",
                    "validate",
                    "--strict",
                    "--no-check-all",
                ],
                workdir="/app",
            )
        except Exception as e:
            logger.error("Static check execution failed: %s", e)
            return {
                "passed": False,
                "test_results": str(e),
                "structured_errors": [],
                "return_code": -1,
            }

        phpstan_errors = parse_phpstan_json(phpstan.stdout)
        composer_errors = parse_composer_validate(
            (composer.stdout or "") + (composer.stderr or "")
        )

        passed = (phpstan.returncode == 0) and (composer.returncode == 0)
        combined_output = (
            (phpstan.stdout or "")
            + (phpstan.stderr or "")
            + (composer.stdout or "")
            + (composer.stderr or "")
        )

        return {
            "passed": passed,
            "test_results": combined_output,
            "structured_errors": phpstan_errors + composer_errors,
            "return_code": 0 if passed else 1,
        }

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
