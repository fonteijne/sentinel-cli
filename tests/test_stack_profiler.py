"""Unit tests for StackProfiler."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
import yaml

from src.stack_profiler import StackProfiler


@pytest.fixture
def profiler():
    """Create a StackProfiler instance."""
    return StackProfiler()


@pytest.fixture
def temp_repo():
    """Create a temporary directory simulating a project repo."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


def _write_file(path: Path, content: str) -> None:
    """Helper to write a file, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ──────────────────────────────────────────────
# Stack Detection Tests
# ──────────────────────────────────────────────


class TestDetectStack:
    """Tests for detect_stack()."""

    def test_detect_drupal9_from_composer(self, profiler, temp_repo):
        _write_file(temp_repo / "composer.json", json.dumps({
            "require": {"drupal/core-recommended": "^9.5"}
        }))
        assert profiler.detect_stack(temp_repo) == "drupal9"

    def test_detect_drupal10_from_composer(self, profiler, temp_repo):
        _write_file(temp_repo / "composer.json", json.dumps({
            "require": {"drupal/core": "^10.0"}
        }))
        assert profiler.detect_stack(temp_repo) == "drupal10"

    def test_detect_drupal11_from_composer(self, profiler, temp_repo):
        _write_file(temp_repo / "composer.json", json.dumps({
            "require": {"drupal/core-recommended": "^11.0"}
        }))
        assert profiler.detect_stack(temp_repo) == "drupal11"

    def test_detect_drupal_tilde_version(self, profiler, temp_repo):
        _write_file(temp_repo / "composer.json", json.dumps({
            "require": {"drupal/core": "~10.2"}
        }))
        assert profiler.detect_stack(temp_repo) == "drupal10"

    def test_detect_drupal_exact_version(self, profiler, temp_repo):
        _write_file(temp_repo / "composer.json", json.dumps({
            "require": {"drupal/core": "10.3.1"}
        }))
        assert profiler.detect_stack(temp_repo) == "drupal10"

    def test_detect_drupal_from_lando(self, profiler, temp_repo):
        _write_file(temp_repo / ".lando.yml", yaml.dump({"recipe": "drupal10"}))
        assert profiler.detect_stack(temp_repo) == "drupal10"

    def test_detect_drupal9_from_lando(self, profiler, temp_repo):
        _write_file(temp_repo / ".lando.yml", yaml.dump({"recipe": "drupal9"}))
        assert profiler.detect_stack(temp_repo) == "drupal9"

    def test_detect_drupal_from_core_file(self, profiler, temp_repo):
        _write_file(temp_repo / "web" / "core" / "lib" / "Drupal.php", "<?php")
        assert profiler.detect_stack(temp_repo) == "drupal10"  # defaults to 10

    def test_composer_takes_precedence_over_lando(self, profiler, temp_repo):
        """composer.json should be checked before .lando.yml."""
        _write_file(temp_repo / "composer.json", json.dumps({
            "require": {"drupal/core": "^10.0"}
        }))
        _write_file(temp_repo / ".lando.yml", yaml.dump({"recipe": "drupal9"}))
        assert profiler.detect_stack(temp_repo) == "drupal10"

    def test_no_stack_detected(self, profiler, temp_repo):
        assert profiler.detect_stack(temp_repo) is None

    def test_non_drupal_composer(self, profiler, temp_repo):
        _write_file(temp_repo / "composer.json", json.dumps({
            "require": {"laravel/framework": "^10.0"}
        }))
        assert profiler.detect_stack(temp_repo) is None

    def test_invalid_composer_json(self, profiler, temp_repo):
        _write_file(temp_repo / "composer.json", "not valid json{{{")
        assert profiler.detect_stack(temp_repo) is None

    def test_invalid_lando_yml(self, profiler, temp_repo):
        _write_file(temp_repo / ".lando.yml", ": invalid: yaml: [")
        assert profiler.detect_stack(temp_repo) is None


# ──────────────────────────────────────────────
# Drupal Profiling Tests
# ──────────────────────────────────────────────


def _create_drupal_repo(repo: Path) -> None:
    """Create a minimal Drupal project structure."""
    # composer.json
    _write_file(repo / "composer.json", json.dumps({
        "require": {
            "php": ">=8.1",
            "drupal/core-recommended": "^9.5",
            "drupal/admin_toolbar": "^3.0",
            "drupal/pathauto": "^1.11",
            "drush/drush": "^12",
        },
        "extra": {
            "patches": {
                "drupal/core": {"Fix something": "patches/core-fix.patch"}
            }
        },
    }))

    # .lando.yml
    _write_file(repo / ".lando.yml", yaml.dump({
        "recipe": "drupal9",
        "services": {
            "search": {"type": "solr:8"},
            "cache": {"type": "redis:7"},
        },
        "tooling": {
            "drush": {"service": "appserver"},
            "composer": {"service": "appserver"},
        },
    }))

    # Custom module
    _write_file(repo / "web" / "modules" / "custom" / "mymodule" / "mymodule.info.yml", yaml.dump({
        "name": "My Module",
        "type": "module",
        "package": "Custom",
        "core_version_requirement": "^9",
        "dependencies": ["drupal:node", "drupal:views"],
    }))

    # Services
    _write_file(repo / "web" / "modules" / "custom" / "mymodule" / "mymodule.services.yml", yaml.dump({
        "services": {
            "mymodule.data_processor": {
                "class": "Drupal\\mymodule\\Service\\DataProcessor",
                "arguments": ["@entity_type.manager"],
            },
        },
    }))

    # Routing
    _write_file(repo / "web" / "modules" / "custom" / "mymodule" / "mymodule.routing.yml", yaml.dump({
        "mymodule.dashboard": {
            "path": "/admin/mymodule/dashboard",
            "defaults": {"_controller": "\\Drupal\\mymodule\\Controller\\DashboardController::view"},
            "requirements": {"_permission": "access mymodule dashboard"},
        },
    }))

    # Hook implementation
    _write_file(repo / "web" / "modules" / "custom" / "mymodule" / "mymodule.module", """<?php

/**
 * Implements hook_form_alter().
 */
function mymodule_form_alter(&$form, $form_state, $form_id) {
  // Custom form alteration.
}

/**
 * Implements hook_preprocess_node().
 */
function mymodule_preprocess_node(&$variables) {
  // Custom preprocessing.
}
""")

    # Plugin
    _write_file(
        repo / "web" / "modules" / "custom" / "mymodule" / "src" / "Plugin" / "Block" / "DashboardBlock.php",
        "<?php\nnamespace Drupal\\mymodule\\Plugin\\Block;\n",
    )

    # Config
    _write_file(
        repo / "web" / "modules" / "custom" / "mymodule" / "config" / "install" / "mymodule.settings.yml",
        "enabled: true\n",
    )

    # Theme
    _write_file(repo / "web" / "themes" / "custom" / "mytheme" / "mytheme.info.yml", yaml.dump({
        "name": "My Theme",
        "type": "theme",
        "base theme": "claro",
    }))

    # Theme package.json
    _write_file(repo / "web" / "themes" / "custom" / "mytheme" / "package.json", "{}")

    # PHPUnit
    _write_file(repo / "phpunit.xml.dist", "<phpunit/>")

    # Test file
    _write_file(
        repo / "web" / "modules" / "custom" / "mymodule" / "tests" / "src" / "Unit" / "DataProcessorTest.php",
        "<?php\n",
    )

    # Makefile
    _write_file(repo / "Makefile", "build:\n\tcomposer install\n")


class TestProfileDrupal:
    """Tests for Drupal-specific profiling."""

    def test_full_profile(self, profiler, temp_repo):
        _create_drupal_repo(temp_repo)
        profile = profiler.profile(temp_repo)

        assert profile["stack_type"] == "drupal9"
        assert "drupal" in profile

        drupal = profile["drupal"]
        assert drupal["version"] == "9"

    def test_modules_detected(self, profiler, temp_repo):
        _create_drupal_repo(temp_repo)
        profile = profiler.profile(temp_repo)
        modules = profile["drupal"]["modules"]

        assert len(modules) == 1
        assert modules[0]["machine_name"] == "mymodule"
        assert modules[0]["package"] == "Custom"
        assert "drupal:node" in modules[0]["dependencies"]

    def test_themes_detected(self, profiler, temp_repo):
        _create_drupal_repo(temp_repo)
        profile = profiler.profile(temp_repo)
        themes = profile["drupal"]["themes"]

        assert len(themes) == 1
        assert themes[0]["machine_name"] == "mytheme"
        assert themes[0]["base_theme"] == "claro"

    def test_services_detected(self, profiler, temp_repo):
        _create_drupal_repo(temp_repo)
        profile = profiler.profile(temp_repo)
        services = profile["drupal"]["services"]

        assert len(services) == 1
        assert services[0]["name"] == "mymodule.data_processor"
        assert "DataProcessor" in services[0]["class"]

    def test_routing_detected(self, profiler, temp_repo):
        _create_drupal_repo(temp_repo)
        profile = profiler.profile(temp_repo)
        routes = profile["drupal"]["routing"]

        assert len(routes) == 1
        assert routes[0]["name"] == "mymodule.dashboard"
        assert "/admin/mymodule/dashboard" in routes[0]["path"]

    def test_hooks_detected(self, profiler, temp_repo):
        _create_drupal_repo(temp_repo)
        profile = profiler.profile(temp_repo)
        hooks = profile["drupal"]["hooks"]

        assert len(hooks) == 2
        hook_names = [h["hook"] for h in hooks]
        assert "form_alter" in hook_names
        assert "preprocess_node" in hook_names

    def test_plugins_detected(self, profiler, temp_repo):
        _create_drupal_repo(temp_repo)
        profile = profiler.profile(temp_repo)
        plugins = profile["drupal"]["plugins"]

        assert len(plugins) == 1
        assert plugins[0]["name"] == "DashboardBlock"
        assert plugins[0]["type"] == "Block"

    def test_composer_analyzed(self, profiler, temp_repo):
        _create_drupal_repo(temp_repo)
        profile = profiler.profile(temp_repo)
        composer = profile["drupal"]["composer"]

        assert composer["php_version"] == ">=8.1"
        assert composer["has_drush"] is True
        assert composer["has_patches"] is True
        assert "drupal/admin_toolbar" in composer["contrib_modules"]
        assert "drupal/pathauto" in composer["contrib_modules"]

    def test_build_tools_detected(self, profiler, temp_repo):
        _create_drupal_repo(temp_repo)
        profile = profiler.profile(temp_repo)
        build_tools = profile["drupal"]["build_tools"]

        assert "make" in build_tools
        assert any("mytheme" in t for t in build_tools)

    def test_tests_detected(self, profiler, temp_repo):
        _create_drupal_repo(temp_repo)
        profile = profiler.profile(temp_repo)
        tests = profile["drupal"]["tests"]

        assert tests["has_phpunit"] is True
        assert tests["test_count"] == 1
        assert any("Unit" in t for t in tests["test_types"])

    def test_environment_detected(self, profiler, temp_repo):
        _create_drupal_repo(temp_repo)
        profile = profiler.profile(temp_repo)
        env = profile["drupal"]["environment"]

        service_names = [s["name"] for s in env["services"]]
        assert "search" in service_names
        assert "cache" in service_names
        assert "drush" in env.get("tooling", [])

    def test_config_entities_detected(self, profiler, temp_repo):
        _create_drupal_repo(temp_repo)
        profile = profiler.profile(temp_repo)

        assert len(profile["drupal"]["config_entities"]) == 1

    def test_empty_drupal_repo(self, profiler, temp_repo):
        """Profile should handle minimal Drupal repos gracefully."""
        _write_file(temp_repo / "composer.json", json.dumps({
            "require": {"drupal/core": "^10.0"}
        }))
        profile = profiler.profile(temp_repo)

        assert profile["stack_type"] == "drupal10"
        assert profile["drupal"]["modules"] == []
        assert profile["drupal"]["themes"] == []


