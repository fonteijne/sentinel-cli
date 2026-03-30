"""Deterministic stack detection and project profiling for Sentinel.

Analyzes a project repository to detect the technology stack and generate
a structured project context file that specializes agent planning prompts.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


def generate_profile_markdown(
    repo_path: Path,
    project_key: str,
    use_llm: bool = True,
) -> tuple[str, str | None]:
    """Generate project profile markdown for a repository.

    Shared logic used by both the CLI command and auto-profiling in plan_generator.
    Runs the deterministic profiler first, then optionally enriches with LLM.

    Args:
        repo_path: Path to the project repository (worktree)
        project_key: Project key for agent session tracking
        use_llm: Whether to attempt LLM enrichment (falls back to deterministic)

    Returns:
        Tuple of (markdown_content, stack_type)
    """
    profiler = StackProfiler()
    profile = profiler.profile(repo_path)
    stack_type = profile.get("stack_type")

    if not use_llm or not stack_type:
        # Deterministic-only: wrap the compact format in a header
        skeleton = profiler.format_for_llm_prompt(profile)
        markdown = f"# Project Context\n\n{skeleton}\n"
        return markdown, stack_type

    try:
        from src.profile_enricher import ProfileEnricher
        enricher = ProfileEnricher()
        markdown = enricher.enrich(repo_path, profile, project_key)
        # Sanity check: LLM errors may return short garbage instead of raising
        if len(markdown) < 100:
            logger.warning(
                f"LLM response too short ({len(markdown)} chars), falling back to deterministic"
            )
            raise ValueError("LLM response too short")
        return markdown, stack_type
    except Exception as e:
        logger.warning(f"LLM enrichment failed, using deterministic profile: {e}")
        skeleton = profiler.format_for_llm_prompt(profile)
        markdown = f"# Project Context\n\n{skeleton}\n"
        return markdown, stack_type


# Drupal core package names in composer.json
DRUPAL_CORE_PACKAGES = ["drupal/core", "drupal/core-recommended"]

# Version constraint → major version mapping
DRUPAL_VERSION_MAP = {
    "^9": "drupal9",
    "~9": "drupal9",
    "9.": "drupal9",
    "^10": "drupal10",
    "~10": "drupal10",
    "10.": "drupal10",
    "^11": "drupal11",
    "~11": "drupal11",
    "11.": "drupal11",
}


class StackProfiler:
    """Deterministic codebase stack analysis.

    Detects technology stack and gathers project-specific context
    through filesystem checks, YAML parsing, and JSON parsing.
    No LLM calls — purely deterministic.
    """

    def detect_stack(self, repo_path: Path) -> str | None:
        """Detect the project's technology stack.

        Detection order (most specific first):
        1. composer.json → drupal/core version
        2. .lando.yml → recipe field
        3. web/core/lib/Drupal.php existence (fallback)

        Args:
            repo_path: Path to the project repository root

        Returns:
            Stack identifier (e.g., 'drupal9', 'drupal10', 'drupal11') or None
        """
        # 1. Check composer.json for Drupal core
        stack = self._detect_from_composer(repo_path)
        if stack:
            return stack

        # 2. Check .lando.yml recipe
        stack = self._detect_from_lando(repo_path)
        if stack:
            return stack

        # 3. Fallback: check for Drupal core file
        if (repo_path / "web" / "core" / "lib" / "Drupal.php").exists():
            logger.info("Detected Drupal from web/core/lib/Drupal.php (version unknown)")
            return "drupal10"  # Default to 10 if we can't determine version

        return None

    def profile(self, repo_path: Path) -> dict[str, Any]:
        """Run full stack analysis on a repository.

        Args:
            repo_path: Path to the project repository root

        Returns:
            Structured profile data including stack_type and stack-specific details
        """
        stack_type = self.detect_stack(repo_path)

        profile: dict[str, Any] = {
            "stack_type": stack_type,
            "profiled_at": datetime.now(timezone.utc).isoformat(),
            "repo_path": str(repo_path),
        }

        if stack_type and stack_type.startswith("drupal"):
            profile["drupal"] = self._profile_drupal(repo_path, stack_type)

        return profile

    def format_for_llm_prompt(self, profile: dict[str, Any]) -> str:
        """Format deterministic profile as compact context for LLM enrichment.

        Produces a structured but concise summary suitable for inclusion in an
        LLM prompt. Focuses on names and relationships, not full tables.

        Args:
            profile: Profile data from profile()

        Returns:
            Compact text summary for LLM consumption
        """
        stack_type = profile.get("stack_type", "unknown")
        lines = [f"Stack: {stack_type}"]

        if not (stack_type and stack_type.startswith("drupal")):
            return "\n".join(lines)

        drupal = profile.get("drupal", {})

        # Modules with dependencies
        modules = drupal.get("modules", [])
        if modules:
            lines.append("\nCustom modules:")
            for mod in modules:
                deps = ", ".join(mod.get("dependencies", [])) or "none"
                lines.append(f"  - {mod['machine_name']} (package: {mod.get('package', '?')}, deps: {deps})")

        # Themes
        themes = drupal.get("themes", [])
        if themes:
            lines.append("\nCustom themes:")
            for theme in themes:
                lines.append(f"  - {theme['machine_name']} (base: {theme.get('base_theme') or 'none'})")

        # Services
        services = drupal.get("services", [])
        if services:
            lines.append("\nServices:")
            for svc in services:
                lines.append(f"  - {svc['name']} -> {svc['class']} ({svc['module']})")

        # Routes
        routes = drupal.get("routing", [])
        if routes:
            lines.append("\nRoutes:")
            for route in routes:
                lines.append(f"  - {route['path']} ({route['name']}, {route['module']})")

        # Hooks
        hooks = drupal.get("hooks", [])
        if hooks:
            lines.append("\nHooks:")
            for hook in hooks:
                lines.append(f"  - {hook['function']}() ({hook['module']})")

        # Plugins
        plugins = drupal.get("plugins", [])
        if plugins:
            lines.append("\nPlugins:")
            for plugin in plugins:
                lines.append(f"  - {plugin['name']} ({plugin['type']}, {plugin['module']})")

        # Composer summary
        composer = drupal.get("composer", {})
        if composer:
            lines.append(f"\nPHP: {composer.get('php_version', '?')}")
            lines.append(f"Drush: {'yes' if composer.get('has_drush') else 'no'}")
            lines.append(f"Patches: {'yes' if composer.get('has_patches') else 'no'}")
            contrib = composer.get("contrib_modules", [])
            if contrib:
                lines.append(f"Contrib ({len(contrib)}): {', '.join(contrib[:30])}")

        # Environment
        env = drupal.get("environment", {})
        if env.get("services"):
            lines.append("\nEnvironment services:")
            for svc in env["services"]:
                lines.append(f"  - {svc['name']}: {svc['type']}")
            if env.get("tooling"):
                lines.append(f"Tooling: {', '.join(env['tooling'])}")

        # Tests
        tests = drupal.get("tests", {})
        if tests:
            lines.append(f"\nPHPUnit: {'yes' if tests.get('has_phpunit') else 'no'}")
            if tests.get("test_types"):
                lines.append(f"Test types: {', '.join(tests['test_types'])}")

        return "\n".join(lines)

    # ──────────────────────────────────────────────
    # Stack detection helpers
    # ──────────────────────────────────────────────

    def _detect_from_composer(self, repo_path: Path) -> str | None:
        """Detect Drupal version from composer.json."""
        composer_path = repo_path / "composer.json"
        if not composer_path.exists():
            return None

        try:
            data = json.loads(composer_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Failed to parse composer.json: {e}")
            return None

        require = data.get("require", {})
        for package in DRUPAL_CORE_PACKAGES:
            version = require.get(package)
            if version:
                return self._parse_drupal_version(version)

        return None

    def _detect_from_lando(self, repo_path: Path) -> str | None:
        """Detect stack from .lando.yml recipe field."""
        for lando_file in [".lando.yml", ".lando.yaml"]:
            lando_path = repo_path / lando_file
            if not lando_path.exists():
                continue

            try:
                data = yaml.safe_load(lando_path.read_text())
            except (yaml.YAMLError, OSError) as e:
                logger.warning(f"Failed to parse {lando_file}: {e}")
                continue

            if not isinstance(data, dict):
                continue

            recipe = data.get("recipe", "")
            if isinstance(recipe, str) and recipe.startswith("drupal"):
                logger.info(f"Detected {recipe} from {lando_file} recipe")
                return recipe

        return None

    def _parse_drupal_version(self, version_constraint: str) -> str | None:
        """Parse a composer version constraint to a Drupal major version."""
        for prefix, stack in DRUPAL_VERSION_MAP.items():
            if version_constraint.startswith(prefix):
                logger.info(f"Detected {stack} from composer constraint '{version_constraint}'")
                return stack

        logger.warning(f"Could not parse Drupal version from '{version_constraint}'")
        return None

    # ──────────────────────────────────────────────
    # Drupal-specific profiling
    # ──────────────────────────────────────────────

    def _profile_drupal(self, repo_path: Path, stack_type: str) -> dict[str, Any]:
        """Run Drupal-specific analysis.

        Args:
            repo_path: Path to the project repository root
            stack_type: Detected stack type (e.g., 'drupal9')

        Returns:
            Dictionary with Drupal-specific profile data
        """
        result: dict[str, Any] = {
            "version": stack_type.replace("drupal", ""),
        }

        result["modules"] = self._find_custom_modules(repo_path)
        result["themes"] = self._find_custom_themes(repo_path)
        result["services"] = self._find_services(repo_path)
        result["routing"] = self._find_routing(repo_path)
        result["hooks"] = self._find_hooks(repo_path)
        result["plugins"] = self._find_plugins(repo_path)
        result["config_entities"] = self._find_config_entities(repo_path)
        result["composer"] = self._analyze_composer(repo_path)
        result["build_tools"] = self._find_build_tools(repo_path)
        result["tests"] = self._find_tests(repo_path)
        result["environment"] = self._analyze_environment(repo_path)

        return result

    def _find_custom_modules(self, repo_path: Path) -> list[dict[str, Any]]:
        """Find all custom Drupal modules."""
        modules = []
        modules_dir = repo_path / "web" / "modules" / "custom"
        if not modules_dir.exists():
            return modules

        for info_file in sorted(modules_dir.glob("*/*.info.yml")):
            try:
                data = yaml.safe_load(info_file.read_text())
                if not isinstance(data, dict):
                    continue
                modules.append({
                    "name": data.get("name", info_file.parent.name),
                    "machine_name": info_file.parent.name,
                    "package": data.get("package", "Custom"),
                    "dependencies": data.get("dependencies", []),
                })
            except (yaml.YAMLError, OSError) as e:
                logger.warning(f"Failed to parse {info_file}: {e}")

        return modules

    def _find_custom_themes(self, repo_path: Path) -> list[dict[str, Any]]:
        """Find all custom Drupal themes."""
        themes = []
        themes_dir = repo_path / "web" / "themes" / "custom"
        if not themes_dir.exists():
            return themes

        for info_file in sorted(themes_dir.glob("*/*.info.yml")):
            try:
                data = yaml.safe_load(info_file.read_text())
                if not isinstance(data, dict):
                    continue
                themes.append({
                    "name": data.get("name", info_file.parent.name),
                    "machine_name": info_file.parent.name,
                    "base_theme": data.get("base theme", None),
                })
            except (yaml.YAMLError, OSError) as e:
                logger.warning(f"Failed to parse {info_file}: {e}")

        return themes

    def _find_services(self, repo_path: Path) -> list[dict[str, Any]]:
        """Find custom service definitions."""
        services = []
        modules_dir = repo_path / "web" / "modules" / "custom"
        if not modules_dir.exists():
            return services

        for svc_file in sorted(modules_dir.glob("*/*.services.yml")):
            try:
                data = yaml.safe_load(svc_file.read_text())
                if not isinstance(data, dict):
                    continue
                svc_defs = data.get("services", {})
                if isinstance(svc_defs, dict):
                    for svc_name, svc_def in svc_defs.items():
                        if isinstance(svc_def, dict):
                            services.append({
                                "name": svc_name,
                                "class": svc_def.get("class", ""),
                                "module": svc_file.parent.name,
                            })
            except (yaml.YAMLError, OSError) as e:
                logger.warning(f"Failed to parse {svc_file}: {e}")

        return services

    def _find_routing(self, repo_path: Path) -> list[dict[str, Any]]:
        """Find custom route definitions."""
        routes = []
        modules_dir = repo_path / "web" / "modules" / "custom"
        if not modules_dir.exists():
            return routes

        for route_file in sorted(modules_dir.glob("*/*.routing.yml")):
            try:
                data = yaml.safe_load(route_file.read_text())
                if not isinstance(data, dict):
                    continue
                for route_name, route_def in data.items():
                    if isinstance(route_def, dict):
                        routes.append({
                            "name": route_name,
                            "path": route_def.get("path", ""),
                            "module": route_file.parent.name,
                        })
            except (yaml.YAMLError, OSError) as e:
                logger.warning(f"Failed to parse {route_file}: {e}")

        return routes

    def _find_hooks(self, repo_path: Path) -> list[dict[str, str]]:
        """Find hook implementations in .module files."""
        hooks = []
        modules_dir = repo_path / "web" / "modules" / "custom"
        if not modules_dir.exists():
            return hooks

        # Procedural hooks: function modulename_hookname(
        hook_pattern = re.compile(r"^function\s+(\w+)\s*\(", re.MULTILINE)

        for module_file in sorted(modules_dir.glob("*/*.module")):
            try:
                content = module_file.read_text()
                module_name = module_file.stem
                for match in hook_pattern.finditer(content):
                    func_name = match.group(1)
                    # Only include functions that look like hooks (start with module name)
                    if func_name.startswith(f"{module_name}_"):
                        hook_suffix = func_name[len(module_name) + 1:]
                        hooks.append({
                            "function": func_name,
                            "hook": hook_suffix,
                            "module": module_name,
                        })
            except OSError as e:
                logger.warning(f"Failed to read {module_file}: {e}")

        return hooks

    def _find_plugins(self, repo_path: Path) -> list[dict[str, str]]:
        """Find Drupal plugins (Block, Field, Views, etc.)."""
        plugins = []
        modules_dir = repo_path / "web" / "modules" / "custom"
        if not modules_dir.exists():
            return plugins

        for plugin_file in sorted(modules_dir.glob("*/src/Plugin/**/*.php")):
            # Determine plugin type from directory structure
            # e.g., src/Plugin/Block/MyBlock.php → type=Block
            try:
                parts = plugin_file.parts
                plugin_idx = parts.index("Plugin")
                plugin_type = parts[plugin_idx + 1] if plugin_idx + 1 < len(parts) - 1 else "Unknown"
            except ValueError:
                plugin_type = "Unknown"

            plugins.append({
                "name": plugin_file.stem,
                "type": plugin_type,
                "module": plugin_file.relative_to(modules_dir).parts[0],
                "path": str(plugin_file.relative_to(repo_path)),
            })

        return plugins

    def _find_config_entities(self, repo_path: Path) -> list[str]:
        """Find config entity install YAML files."""
        entities = []
        modules_dir = repo_path / "web" / "modules" / "custom"
        if not modules_dir.exists():
            return entities

        for config_file in sorted(modules_dir.glob("*/config/install/*.yml")):
            entities.append(str(config_file.relative_to(repo_path)))

        return entities

    def _analyze_composer(self, repo_path: Path) -> dict[str, Any]:
        """Analyze composer.json for PHP version, contrib modules, patches."""
        composer_path = repo_path / "composer.json"
        if not composer_path.exists():
            return {}

        try:
            data = json.loads(composer_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

        require = data.get("require", {})
        result: dict[str, Any] = {}

        # PHP version
        php_version = require.get("php")
        if php_version:
            result["php_version"] = php_version

        # Contrib modules (drupal/* packages excluding core)
        contrib = [
            pkg for pkg in require
            if pkg.startswith("drupal/") and pkg not in DRUPAL_CORE_PACKAGES
            and pkg != "drupal/core-composer-scaffold"
            and pkg != "drupal/core-project-message"
            and pkg != "drupal/core-dev"
        ]
        result["contrib_modules"] = sorted(contrib)

        # Drush
        require_dev = data.get("require-dev", {})
        all_deps = {**require, **require_dev}
        result["has_drush"] = "drush/drush" in all_deps

        # Patches
        patches = data.get("extra", {}).get("patches", {})
        result["has_patches"] = bool(patches)
        result["patched_packages"] = sorted(patches.keys()) if patches else []

        return result

    def _find_build_tools(self, repo_path: Path) -> list[str]:
        """Detect frontend build tools."""
        tools = []

        if (repo_path / "package.json").exists():
            tools.append("npm/yarn")
        if (repo_path / "Gruntfile.js").exists() or (repo_path / "Gruntfile.coffee").exists():
            tools.append("grunt")
        if (repo_path / "gulpfile.js").exists() or (repo_path / "gulpfile.ts").exists():
            tools.append("gulp")
        if (repo_path / "webpack.config.js").exists():
            tools.append("webpack")
        if (repo_path / "vite.config.js").exists() or (repo_path / "vite.config.ts").exists():
            tools.append("vite")
        if (repo_path / "Makefile").exists():
            tools.append("make")

        # Also check in custom themes
        themes_dir = repo_path / "web" / "themes" / "custom"
        if themes_dir.exists():
            for theme_dir in themes_dir.iterdir():
                if theme_dir.is_dir() and (theme_dir / "package.json").exists():
                    tools.append(f"npm/yarn ({theme_dir.name} theme)")

        return tools

    def _find_tests(self, repo_path: Path) -> dict[str, Any]:
        """Detect test infrastructure."""
        result: dict[str, Any] = {
            "has_phpunit": False,
            "test_types": [],
            "test_count": 0,
        }

        # PHPUnit config
        for phpunit_file in ["phpunit.xml", "phpunit.xml.dist"]:
            if (repo_path / phpunit_file).exists():
                result["has_phpunit"] = True
                break

        # Count test files by type
        modules_dir = repo_path / "web" / "modules" / "custom"
        if modules_dir.exists():
            unit_tests = list(modules_dir.glob("*/tests/src/Unit/**/*Test.php"))
            kernel_tests = list(modules_dir.glob("*/tests/src/Kernel/**/*Test.php"))
            functional_tests = list(modules_dir.glob("*/tests/src/Functional/**/*Test.php"))
            js_tests = list(modules_dir.glob("*/tests/src/FunctionalJavascript/**/*Test.php"))

            if unit_tests:
                result["test_types"].append(f"Unit ({len(unit_tests)})")
            if kernel_tests:
                result["test_types"].append(f"Kernel ({len(kernel_tests)})")
            if functional_tests:
                result["test_types"].append(f"Functional ({len(functional_tests)})")
            if js_tests:
                result["test_types"].append(f"FunctionalJavascript ({len(js_tests)})")

            result["test_count"] = len(unit_tests) + len(kernel_tests) + len(functional_tests) + len(js_tests)

        return result

    def _analyze_environment(self, repo_path: Path) -> dict[str, Any]:
        """Analyze environment services from .lando.yml."""
        result: dict[str, Any] = {"services": []}

        for lando_file in [".lando.yml", ".lando.yaml"]:
            lando_path = repo_path / lando_file
            if not lando_path.exists():
                continue

            try:
                data = yaml.safe_load(lando_path.read_text())
            except (yaml.YAMLError, OSError):
                continue

            if not isinstance(data, dict):
                continue

            services = data.get("services", {})
            if isinstance(services, dict):
                for name, svc_def in services.items():
                    if isinstance(svc_def, dict):
                        svc_type = svc_def.get("type", "")
                        result["services"].append({
                            "name": name,
                            "type": str(svc_type),
                        })

            # Tooling
            tooling = data.get("tooling", {})
            if isinstance(tooling, dict):
                result["tooling"] = sorted(tooling.keys())

            break

        return result

