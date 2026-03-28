"""Tests for Lando to Docker Compose translator."""

import tempfile
from pathlib import Path

import pytest
import yaml

from src.lando_translator import LandoTranslator, RECIPE_SERVICES


class TestLandoTranslatorInit:
    """Test translator initialization."""

    def test_from_dict(self):
        config = {"recipe": "drupal10", "config": {"php": "8.2"}}
        translator = LandoTranslator(config)
        assert translator.recipe == "drupal10"
        assert translator.config == {"php": "8.2"}

    def test_from_file(self, tmp_path):
        lando_file = tmp_path / ".lando.yml"
        lando_file.write_text(yaml.dump({"recipe": "drupal10", "config": {"php": "8.2"}}))
        translator = LandoTranslator.from_file(lando_file)
        assert translator.recipe == "drupal10"

    def test_from_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            LandoTranslator.from_file(tmp_path / "missing.yml")

    def test_from_file_invalid_yaml(self, tmp_path):
        lando_file = tmp_path / ".lando.yml"
        lando_file.write_text("not: a: valid: yaml: [")
        with pytest.raises(yaml.YAMLError):
            LandoTranslator.from_file(lando_file)

    def test_from_file_not_dict(self, tmp_path):
        lando_file = tmp_path / ".lando.yml"
        lando_file.write_text("- just\n- a\n- list")
        with pytest.raises(ValueError, match="expected dict"):
            LandoTranslator.from_file(lando_file)


class TestDrupalRecipeTranslation:
    """Test Drupal recipe translations."""

    def test_drupal10_basic(self):
        config = {"recipe": "drupal10"}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-001")

        # Should have appserver and database
        assert "appserver" in result["services"]
        assert "database" in result["services"]

        # Appserver should be PHP 8.2 Apache
        appserver = result["services"]["appserver"]
        assert "php:8.2-apache" in appserver["image"]
        assert appserver["working_dir"] == "/app/web"

        # Database should be MySQL 8.0
        database = result["services"]["database"]
        assert "mysql:8.0" in database["image"]
        assert database["environment"]["MYSQL_DATABASE"] == "drupal"

    def test_drupal11_recipe(self):
        config = {"recipe": "drupal11"}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-002")

        appserver = result["services"]["appserver"]
        assert "php:8.3" in appserver["image"]

    def test_php_version_override(self):
        config = {"recipe": "drupal10", "config": {"php": "8.3"}}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-003")

        appserver = result["services"]["appserver"]
        assert "php:8.3" in appserver["image"]

    def test_webroot_override(self):
        config = {"recipe": "drupal10", "config": {"webroot": "docroot"}}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-004")

        appserver = result["services"]["appserver"]
        assert appserver["working_dir"] == "/app/docroot"

    def test_via_nginx(self):
        config = {"recipe": "drupal10", "config": {"via": "nginx"}}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-005")

        appserver = result["services"]["appserver"]
        assert "nginx" in appserver["image"]

    def test_database_mariadb_override(self):
        config = {"recipe": "drupal10", "config": {"database": "mariadb"}}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-006")

        database = result["services"]["database"]
        assert "mariadb" in database["image"]


class TestExplicitServices:
    """Test explicit service definitions."""

    def test_additional_redis_service(self):
        config = {
            "recipe": "drupal10",
            "services": {
                "cache": {"type": "redis"},
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-010")

        assert "cache" in result["services"]
        cache = result["services"]["cache"]
        assert "redis" in cache["image"]
        assert "alpine" in cache["image"]

    def test_additional_node_service(self):
        config = {
            "recipe": "drupal10",
            "services": {
                "node": {"type": "node:18"},
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-011")

        assert "node" in result["services"]
        node_svc = result["services"]["node"]
        assert "node:18" in node_svc["image"]

    def test_additional_mailhog_service(self):
        config = {
            "recipe": "drupal10",
            "services": {
                "mail": {"type": "mailhog"},
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-012")

        assert "mail" in result["services"]
        assert "mailhog" in result["services"]["mail"]["image"]

    def test_custom_database_creds(self):
        config = {
            "recipe": "drupal10",
            "services": {
                "database": {
                    "creds": {
                        "user": "custom_user",
                        "password": "custom_pass",
                        "database": "custom_db",
                    },
                },
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-013")

        db = result["services"]["database"]
        assert db["environment"]["MYSQL_USER"] == "custom_user"
        assert db["environment"]["MYSQL_PASSWORD"] == "custom_pass"
        assert db["environment"]["MYSQL_DATABASE"] == "custom_db"

    def test_service_without_type_skipped(self):
        config = {
            "services": {
                "mystery": {},
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-014")

        assert "mystery" not in result["services"]

    def test_unknown_service_type_skipped(self):
        config = {
            "services": {
                "exotic": {"type": "unknown_thing:1.0"},
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-015")

        assert "exotic" not in result["services"]


class TestNetworkAndVolumes:
    """Test network and volume configuration."""

    def test_ticket_specific_network(self):
        config = {"recipe": "drupal10"}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-020")

        assert "sentinel-STNL-020" in result["networks"]
        # All services should use this network
        for svc in result["services"].values():
            assert "sentinel-STNL-020" in svc["networks"]

    def test_named_volume_for_code(self):
        config = {"recipe": "drupal10"}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-021")

        assert "sentinel-projects" in result["volumes"]
        assert result["volumes"]["sentinel-projects"] == {"external": True}

        # Appserver should mount the named volume
        appserver = result["services"]["appserver"]
        assert any("sentinel-projects:/app" in v for v in appserver["volumes"])

    def test_custom_volume_name(self):
        config = {"recipe": "drupal10"}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-022", volume_name="custom-vol")

        assert "custom-vol" in result["volumes"]

    def test_db_data_volume(self):
        config = {"recipe": "drupal10"}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-023")

        database = result["services"]["database"]
        assert any("db-data" in v for v in database["volumes"])
        assert "db-data" in result["volumes"]


class TestBuildSteps:
    """Test build and run command translation."""

    def test_build_as_root_commands(self):
        config = {
            "recipe": "drupal10",
            "services": {
                "appserver": {
                    "build_as_root": [
                        "apt-get update",
                        "apt-get install -y vim",
                    ],
                },
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-030")

        appserver = result["services"]["appserver"]
        assert "labels" in appserver
        commands = appserver["labels"]["sentinel.post_start_commands"]
        assert "apt-get update" in commands
        assert "apt-get install -y vim" in commands

    def test_build_commands(self):
        config = {
            "recipe": "drupal10",
            "services": {
                "appserver": {
                    "build": ["composer install"],
                },
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-031")

        appserver = result["services"]["appserver"]
        assert "composer install" in appserver["labels"]["sentinel.post_start_commands"]


class TestOverrides:
    """Test Lando overrides translation."""

    def test_environment_overrides(self):
        config = {
            "recipe": "drupal10",
            "services": {
                "appserver": {
                    "overrides": {
                        "environment": {
                            "CUSTOM_VAR": "value",
                        },
                    },
                },
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-040")

        appserver = result["services"]["appserver"]
        assert appserver["environment"]["CUSTOM_VAR"] == "value"
        # Original env vars should still be present
        assert appserver["environment"]["LANDO_INFO"] == ""

    def test_volume_overrides(self):
        config = {
            "recipe": "drupal10",
            "services": {
                "appserver": {
                    "overrides": {
                        "volumes": ["/host/path:/container/path"],
                    },
                },
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-041")

        appserver = result["services"]["appserver"]
        assert "/host/path:/container/path" in appserver["volumes"]


class TestHealthchecks:
    """Test healthcheck generation."""

    def test_mysql_healthcheck(self):
        config = {"recipe": "drupal10"}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-050")

        database = result["services"]["database"]
        assert "healthcheck" in database
        assert "mysqladmin" in database["healthcheck"]["test"][1]

    def test_postgres_healthcheck(self):
        config = {
            "recipe": "drupal10",
            "config": {"database": "postgres"},
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-051")

        database = result["services"]["database"]
        assert "healthcheck" in database
        assert "pg_isready" in database["healthcheck"]["test"][1]


class TestTooling:
    """Test tooling command extraction."""

    def test_extract_tooling_dict(self):
        config = {
            "recipe": "drupal10",
            "tooling": {
                "drush": {"service": "appserver", "cmd": "drush"},
                "phpunit": {"service": "appserver", "cmd": "vendor/bin/phpunit"},
            },
        }
        translator = LandoTranslator(config)
        commands = translator.get_tooling_commands()

        assert "drush" in commands
        assert "phpunit" in commands
        assert "appserver" in commands["drush"]

    def test_extract_tooling_string(self):
        config = {
            "recipe": "drupal10",
            "tooling": {
                "test": "vendor/bin/phpunit",
            },
        }
        translator = LandoTranslator(config)
        commands = translator.get_tooling_commands()

        assert "test" in commands


class TestYamlOutput:
    """Test YAML output generation."""

    def test_translate_to_yaml_is_valid(self):
        config = {"recipe": "drupal10"}
        translator = LandoTranslator(config)
        yaml_str = translator.translate_to_yaml("STNL-060")

        # Should be valid YAML
        parsed = yaml.safe_load(yaml_str)
        assert "services" in parsed
        assert "appserver" in parsed["services"]

    def test_yaml_round_trip(self):
        config = {
            "recipe": "drupal10",
            "services": {
                "cache": {"type": "redis"},
            },
        }
        translator = LandoTranslator(config)
        yaml_str = translator.translate_to_yaml("STNL-061")

        # Parse back and verify structure
        parsed = yaml.safe_load(yaml_str)
        assert "cache" in parsed["services"]
        assert "database" in parsed["services"]
        assert "appserver" in parsed["services"]


class TestOtherRecipes:
    """Test non-Drupal recipes."""

    def test_laravel_recipe(self):
        config = {"recipe": "laravel"}
        translator = LandoTranslator(config)
        result = translator.translate("LAR-001")

        assert "appserver" in result["services"]
        assert "database" in result["services"]
        assert "cache" in result["services"]

        appserver = result["services"]["appserver"]
        assert appserver["working_dir"] == "/app/public"

    def test_wordpress_recipe(self):
        config = {"recipe": "wordpress"}
        translator = LandoTranslator(config)
        result = translator.translate("WP-001")

        db = result["services"]["database"]
        assert db["environment"]["MYSQL_DATABASE"] == "wordpress"

    def test_unknown_recipe_with_config(self):
        config = {
            "recipe": "custom-thing",
            "config": {
                "php": "8.1",
                "webroot": "public_html",
                "database": "mariadb:10.5",
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("CUSTOM-001")

        appserver = result["services"]["appserver"]
        assert "php:8.1" in appserver["image"]
        assert appserver["working_dir"] == "/app/public_html"

        database = result["services"]["database"]
        assert "mariadb:10.5" in database["image"]


class TestViaVersionStripping:
    """Test that via version suffix is stripped from image name."""

    def test_apache_version_stripped(self):
        config = {"recipe": "drupal9", "config": {"via": "apache:2.4"}}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-080")

        appserver = result["services"]["appserver"]
        assert "apache:2.4" not in appserver["image"]
        assert "apache-" in appserver["image"]

    def test_nginx_version_stripped(self):
        config = {"recipe": "drupal10", "config": {"via": "nginx:1.25"}}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-081")

        appserver = result["services"]["appserver"]
        assert "nginx:1.25" not in appserver["image"]
        assert "nginx-" in appserver["image"]

    def test_plain_via_unchanged(self):
        config = {"recipe": "drupal10", "config": {"via": "apache"}}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-082")

        appserver = result["services"]["appserver"]
        assert "apache-" in appserver["image"]


class TestComposeTypePassthrough:
    """Test Lando's type: compose passthrough."""

    def test_compose_service_passes_through(self):
        config = {
            "services": {
                "adminer": {
                    "type": "compose",
                    "services": {
                        "image": "dehy/adminer",
                        "command": "/bin/s6-svscan /etc/services.d",
                    },
                },
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-083")

        assert "adminer" in result["services"]
        adminer = result["services"]["adminer"]
        assert adminer["image"] == "dehy/adminer"
        assert adminer["command"] == "/bin/s6-svscan /etc/services.d"

    def test_compose_service_without_services_key_skipped(self):
        config = {
            "services": {
                "broken": {"type": "compose"},
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-084")

        assert "broken" not in result["services"]


class TestDhlExpressFullConfig:
    """Integration test with the actual DHL Express .lando.yml structure."""

    def test_dhl_express_translation(self):
        config = {
            "name": "dhl-express",
            "recipe": "drupal9",
            "config": {
                "webroot": "web",
                "php": "8.1",
                "via": "apache:2.4",
                "database": "mariadb",
                "xdebug": True,
            },
            "services": {
                "appserver": {
                    "overrides": {
                        "environment": {
                            "ONESHOE_ENV": "development",
                            "DRUSH_OPTIONS_URI": "https://dhl-express.lndo.site",
                        },
                    },
                },
                "adminer": {
                    "type": "compose",
                    "services": {
                        "image": "dehy/adminer",
                        "command": "/bin/s6-svscan /etc/services.d",
                    },
                },
                "mailhog": {"type": "mailhog"},
                "node": {
                    "type": "node:18",
                    "overrides": {
                        "environment": {"ONESHOE_ENV": "development"},
                    },
                },
                "search": {"type": "solr:8"},
                "redis": {"type": "redis"},
            },
            "tooling": {
                "drush": {"service": "appserver", "cmd": "drush --root=/app/web"},
                "composer": {"service": "appserver", "cmd": "php /usr/local/bin/composer"},
                "build-theme": {"service": "node", "cmd": "bash scripts/build-theme.sh"},
            },
        }
        translator = LandoTranslator(config)
        result = translator.translate("STNL-100")

        # All 7 services should be present
        assert len(result["services"]) == 7
        assert set(result["services"].keys()) == {
            "appserver", "database", "adminer", "mailhog", "node", "search", "redis",
        }

        # Appserver: PHP 8.1, apache (no :2.4 leak), custom env vars
        app = result["services"]["appserver"]
        assert "php:8.1-apache-" in app["image"]
        assert app["environment"]["ONESHOE_ENV"] == "development"
        assert app["working_dir"] == "/app/web"

        # Database: MariaDB (from config override)
        db = result["services"]["database"]
        assert "mariadb" in db["image"]

        # Adminer: compose passthrough
        adminer = result["services"]["adminer"]
        assert adminer["image"] == "dehy/adminer"

        # Node: version 18, custom env
        node = result["services"]["node"]
        assert "node:18" in node["image"]
        assert node["environment"]["ONESHOE_ENV"] == "development"

        # Tooling
        tooling = translator.get_tooling_commands()
        assert "drush" in tooling
        assert "composer" in tooling
        assert "build-theme" in tooling


class TestLandoInfoEnvVar:
    """Test that LANDO_INFO is set to prevent Lando-aware code from breaking."""

    def test_appserver_has_lando_info(self):
        config = {"recipe": "drupal10"}
        translator = LandoTranslator(config)
        result = translator.translate("STNL-070")

        appserver = result["services"]["appserver"]
        assert "LANDO_INFO" in appserver["environment"]
        assert appserver["environment"]["LANDO_INFO"] == ""
