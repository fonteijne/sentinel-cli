"""Translate .lando.yml to docker-compose.sentinel.yml.

Converts Lando project configuration into a standard Docker Compose file
that Sentinel can manage directly, bypassing Lando's DX wrapper layer.
"""

import logging
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# Lando service type → Docker image mapping
LANDO_IMAGE_MAP: dict[str, str] = {
    # PHP / web servers
    "php": "devwithlando/php:{version}-{via}-4",
    # Databases
    "mysql": "mysql:{version}",
    "mariadb": "mariadb:{version}",
    "postgres": "postgres:{version}",
    # Cache / search
    "redis": "redis:{version}-alpine",
    "memcached": "memcached:{version}-alpine",
    "solr": "solr:{version}",
    "elasticsearch": "elasticsearch:{version}",
    # Utilities
    "node": "node:{version}",
    "mailhog": "mailhog/mailhog:latest",
    "phpmyadmin": "phpmyadmin:latest",
}

# Default versions when not specified in .lando.yml
DEFAULT_VERSIONS: dict[str, str] = {
    "php": "8.2",
    "mysql": "8.0",
    "mariadb": "10.6",
    "postgres": "16",
    "redis": "7",
    "memcached": "1.6",
    "solr": "8",
    "elasticsearch": "8",
    "node": "20",
}

# Lando recipe → implied services
RECIPE_SERVICES: dict[str, dict[str, dict[str, Any]]] = {
    "drupal10": {
        "appserver": {"type": "php:8.2", "via": "apache", "webroot": "web"},
        "database": {"type": "mysql:8.0", "creds": {"user": "drupal", "password": "drupal", "database": "drupal"}},
    },
    "drupal11": {
        "appserver": {"type": "php:8.3", "via": "apache", "webroot": "web"},
        "database": {"type": "mysql:8.0", "creds": {"user": "drupal", "password": "drupal", "database": "drupal"}},
    },
    "drupal9": {
        "appserver": {"type": "php:8.1", "via": "apache", "webroot": "web"},
        "database": {"type": "mysql:5.7", "creds": {"user": "drupal", "password": "drupal", "database": "drupal"}},
    },
    "lamp": {
        "appserver": {"type": "php:8.2", "via": "apache", "webroot": "."},
        "database": {"type": "mysql:8.0", "creds": {"user": "lamp", "password": "lamp", "database": "lamp"}},
    },
    "lemp": {
        "appserver": {"type": "php:8.2", "via": "nginx", "webroot": "."},
        "database": {"type": "mysql:8.0", "creds": {"user": "lemp", "password": "lemp", "database": "lemp"}},
    },
    "laravel": {
        "appserver": {"type": "php:8.2", "via": "apache", "webroot": "public"},
        "database": {"type": "mysql:8.0", "creds": {"user": "laravel", "password": "laravel", "database": "laravel"}},
        "cache": {"type": "redis"},
    },
    "wordpress": {
        "appserver": {"type": "php:8.2", "via": "apache", "webroot": "."},
        "database": {"type": "mysql:8.0", "creds": {"user": "wordpress", "password": "wordpress", "database": "wordpress"}},
    },
}


class LandoTranslator:
    """Translates .lando.yml configuration to Docker Compose format."""

    def __init__(self, lando_config: dict[str, Any]) -> None:
        """Initialize with parsed .lando.yml content.

        Args:
            lando_config: Parsed YAML dictionary from .lando.yml
        """
        self.lando = lando_config
        self.recipe = lando_config.get("recipe", "")
        self.config = lando_config.get("config", {})
        self.services_config = lando_config.get("services", {})
        self.tooling = lando_config.get("tooling", {})

    @classmethod
    def from_file(cls, lando_path: Path) -> "LandoTranslator":
        """Create translator from a .lando.yml file.

        Args:
            lando_path: Path to .lando.yml

        Returns:
            LandoTranslator instance

        Raises:
            FileNotFoundError: If file doesn't exist
            yaml.YAMLError: If YAML is invalid
        """
        with open(lando_path) as f:
            config = yaml.safe_load(f)
        if not isinstance(config, dict):
            raise ValueError(f"Invalid .lando.yml: expected dict, got {type(config).__name__}")
        return cls(config)

    def translate(self, ticket_id: str, volume_name: str = "sentinel-projects") -> dict[str, Any]:
        """Translate Lando config to Docker Compose format.

        Args:
            ticket_id: Ticket ID for unique naming
            volume_name: Named volume shared with Sentinel container

        Returns:
            Docker Compose configuration as dictionary
        """
        # Start from recipe defaults, then overlay explicit services
        services = self._resolve_services()

        compose: dict[str, Any] = {
            "services": {},
            "volumes": {},
            "networks": {
                f"sentinel-{ticket_id}": {
                    "name": f"sentinel-{ticket_id}",
                },
            },
        }

        for name, svc_config in services.items():
            compose_service = self._translate_service(name, svc_config, ticket_id, volume_name)
            if compose_service:
                compose["services"][name] = compose_service

        # Add named volumes used by services
        if any(
            "db-data" in str(svc.get("volumes", []))
            for svc in compose["services"].values()
        ):
            compose["volumes"]["db-data"] = None

        # Add external volume reference for sentinel-projects
        compose["volumes"][volume_name] = {"external": True}

        return compose

    def translate_to_yaml(self, ticket_id: str, volume_name: str = "sentinel-projects") -> str:
        """Translate and return as YAML string.

        Args:
            ticket_id: Ticket ID for unique naming
            volume_name: Named volume shared with Sentinel container

        Returns:
            Docker Compose YAML string
        """
        compose = self.translate(ticket_id, volume_name)
        return yaml.dump(compose, default_flow_style=False, sort_keys=False)

    def get_tooling_commands(self) -> dict[str, str]:
        """Extract available tooling commands from Lando config.

        Returns:
            Dict mapping command name to description/service
        """
        commands = {}
        for cmd_name, cmd_config in self.tooling.items():
            if isinstance(cmd_config, dict):
                service = cmd_config.get("service", "appserver")
                cmd = cmd_config.get("cmd", cmd_name)
                commands[cmd_name] = f"Runs '{cmd}' in {service}"
            elif isinstance(cmd_config, str):
                commands[cmd_name] = f"Runs '{cmd_config}' in appserver"
        return commands

    def _resolve_services(self) -> dict[str, dict[str, Any]]:
        """Resolve final service configs from recipe + explicit services."""
        # Start with recipe defaults
        recipe_key = self.recipe.replace("drupal", "drupal").lower()
        base_services = {}

        if recipe_key in RECIPE_SERVICES:
            # Deep copy recipe defaults
            for name, svc in RECIPE_SERVICES[recipe_key].items():
                base_services[name] = dict(svc)
        elif self.recipe:
            # Unknown recipe — try generic mapping from recipe config
            base_services = self._infer_from_recipe_config()

        # Apply recipe-level config overrides
        if self.config:
            if "appserver" in base_services or not base_services:
                appserver = base_services.setdefault("appserver", {})
                if "php" in self.config:
                    svc_type = appserver.get("type", "php")
                    base_type = svc_type.split(":")[0]
                    appserver["type"] = f"{base_type}:{self.config['php']}"
                if "via" in self.config:
                    appserver["via"] = self.config["via"]
                if "webroot" in self.config:
                    appserver["webroot"] = self.config["webroot"]
                if "database" in self.config:
                    db_svc = base_services.setdefault("database", {})
                    db_type_str = self.config["database"]
                    if ":" not in db_type_str:
                        db_type_str = f"{db_type_str}:{DEFAULT_VERSIONS.get(db_type_str, 'latest')}"
                    db_svc["type"] = db_type_str
                if "xdebug" in self.config:
                    appserver["xdebug"] = self.config["xdebug"]

        # Overlay explicit service definitions
        for name, svc_config in self.services_config.items():
            if name in base_services:
                base_services[name].update(svc_config)
            else:
                base_services[name] = dict(svc_config) if isinstance(svc_config, dict) else {}

        return base_services

    def _infer_from_recipe_config(self) -> dict[str, dict[str, Any]]:
        """Infer services from recipe config when recipe isn't in RECIPE_SERVICES."""
        services: dict[str, dict[str, Any]] = {}

        php_version = self.config.get("php", DEFAULT_VERSIONS["php"])
        via = self.config.get("via", "apache")
        webroot = self.config.get("webroot", ".")
        database = self.config.get("database", f"mysql:{DEFAULT_VERSIONS['mysql']}")

        services["appserver"] = {
            "type": f"php:{php_version}",
            "via": via,
            "webroot": webroot,
        }

        if ":" not in database:
            database = f"{database}:{DEFAULT_VERSIONS.get(database, 'latest')}"
        services["database"] = {
            "type": database,
            "creds": {"user": "app", "password": "app", "database": "app"},
        }

        return services

    def _translate_service(
        self,
        name: str,
        svc_config: dict[str, Any],
        ticket_id: str,
        volume_name: str,
    ) -> Optional[dict[str, Any]]:
        """Translate a single Lando service to Docker Compose service.

        Args:
            name: Service name
            svc_config: Lando service configuration
            ticket_id: Ticket ID
            volume_name: Named volume for code sharing

        Returns:
            Docker Compose service dict, or None to skip
        """
        svc_type_raw = svc_config.get("type", "")
        if not svc_type_raw:
            logger.warning(f"Service '{name}' has no type, skipping")
            return None

        # Parse type:version
        parts = svc_type_raw.split(":", 1)
        svc_type = parts[0].lower()
        version = parts[1] if len(parts) > 1 else DEFAULT_VERSIONS.get(svc_type, "latest")

        # Handle Lando's 'compose' type — raw Docker Compose service passthrough
        if svc_type == "compose":
            return self._translate_compose_service(name, svc_config, ticket_id)

        # Get the image template
        image_template = LANDO_IMAGE_MAP.get(svc_type)
        if not image_template:
            logger.warning(f"Unknown Lando service type '{svc_type}' for service '{name}', skipping")
            return None

        # Build compose service
        compose_svc: dict[str, Any] = {
            "networks": [f"sentinel-{ticket_id}"],
        }

        # Type-specific translation
        if svc_type == "php":
            compose_svc.update(self._translate_php_service(name, svc_config, version, volume_name, ticket_id))
        elif svc_type in ("mysql", "mariadb", "postgres"):
            compose_svc.update(self._translate_database_service(name, svc_config, svc_type, version))
        elif svc_type == "redis":
            compose_svc["image"] = image_template.format(version=version)
        elif svc_type == "memcached":
            compose_svc["image"] = image_template.format(version=version)
        elif svc_type == "solr":
            compose_svc["image"] = image_template.format(version=version)
        elif svc_type == "node":
            compose_svc.update(self._translate_node_service(name, svc_config, version, volume_name, ticket_id))
        elif svc_type == "mailhog":
            compose_svc["image"] = image_template.format(version=version)
        elif svc_type == "phpmyadmin":
            compose_svc["image"] = image_template.format(version=version)
        else:
            compose_svc["image"] = image_template.format(version=version)

        # Apply overrides from Lando's 'overrides' key
        overrides = svc_config.get("overrides", {})
        if overrides:
            self._apply_overrides(compose_svc, overrides)

        return compose_svc

    def _translate_php_service(
        self,
        name: str,
        svc_config: dict[str, Any],
        version: str,
        volume_name: str,
        ticket_id: str,
    ) -> dict[str, Any]:
        """Translate a PHP/appserver service."""
        via_raw = svc_config.get("via", "apache")
        # Strip version from via (e.g., "apache:2.4" → "apache")
        via = via_raw.split(":")[0] if ":" in str(via_raw) else str(via_raw)
        webroot = svc_config.get("webroot", ".")

        image = LANDO_IMAGE_MAP["php"].format(version=version, via=via)

        result: dict[str, Any] = {
            "image": image,
            "volumes": [f"{volume_name}:/app"],
            "working_dir": f"/app/{webroot}" if webroot != "." else "/app",
            "environment": {
                "LANDO_INFO": "",  # Prevent Lando-aware code from breaking
                "LANDO_WEBROOT": f"/app/{webroot}" if webroot != "." else "/app",
            },
        }

        # Database connection environment (if database service exists)
        result["environment"].update({
            "DB_HOST": "database",
            "DB_PORT": "3306",
        })

        # Redis connection (if likely present)
        result["environment"]["REDIS_HOST"] = "cache"

        # Build steps
        build_commands = []
        if svc_config.get("build_as_root"):
            cmds = svc_config["build_as_root"]
            if isinstance(cmds, list):
                build_commands.extend(cmds)
            else:
                build_commands.append(str(cmds))

        if svc_config.get("build"):
            cmds = svc_config["build"]
            if isinstance(cmds, list):
                build_commands.extend(cmds)
            else:
                build_commands.append(str(cmds))

        if svc_config.get("run_as_root"):
            cmds = svc_config["run_as_root"]
            if isinstance(cmds, list):
                build_commands.extend(cmds)
            else:
                build_commands.append(str(cmds))

        if build_commands:
            # Store as labels for EnvironmentManager to execute post-start
            result["labels"] = {
                "sentinel.post_start_commands": ";".join(build_commands),
            }

        return result

    def _translate_database_service(
        self,
        name: str,
        svc_config: dict[str, Any],
        svc_type: str,
        version: str,
    ) -> dict[str, Any]:
        """Translate a database service."""
        image = LANDO_IMAGE_MAP[svc_type].format(version=version)
        creds = svc_config.get("creds", {})

        result: dict[str, Any] = {
            "image": image,
            "volumes": ["db-data:/var/lib/mysql"] if svc_type != "postgres" else ["db-data:/var/lib/postgresql/data"],
        }

        if svc_type in ("mysql", "mariadb"):
            result["environment"] = {
                "MYSQL_DATABASE": creds.get("database", "app"),
                "MYSQL_USER": creds.get("user", "app"),
                "MYSQL_PASSWORD": creds.get("password", "app"),
                "MYSQL_ROOT_PASSWORD": "root",
            }
            result["healthcheck"] = {
                "test": ["CMD", "mysqladmin", "ping", "-h", "localhost", "-u", "root", "-proot"],
                "interval": "5s",
                "timeout": "5s",
                "retries": 30,
            }
        elif svc_type == "postgres":
            result["environment"] = {
                "POSTGRES_DB": creds.get("database", "app"),
                "POSTGRES_USER": creds.get("user", "app"),
                "POSTGRES_PASSWORD": creds.get("password", "app"),
            }
            result["healthcheck"] = {
                "test": ["CMD-SHELL", f"pg_isready -U {creds.get('user', 'app')}"],
                "interval": "5s",
                "timeout": "5s",
                "retries": 30,
            }

        return result

    def _translate_node_service(
        self,
        name: str,
        svc_config: dict[str, Any],
        version: str,
        volume_name: str,
        ticket_id: str,
    ) -> dict[str, Any]:
        """Translate a Node.js service."""
        image = LANDO_IMAGE_MAP["node"].format(version=version)
        return {
            "image": image,
            "volumes": [f"{volume_name}:/app"],
            "working_dir": "/app",
            "command": "tail -f /dev/null",  # Keep alive for exec
        }

    def _translate_compose_service(
        self,
        name: str,
        svc_config: dict[str, Any],
        ticket_id: str,
    ) -> Optional[dict[str, Any]]:
        """Translate a Lando 'compose' type service (raw Docker Compose passthrough).

        Lando's 'compose' type allows embedding raw Docker Compose service config
        under the 'services' key.
        """
        raw_services = svc_config.get("services", {})
        if not raw_services:
            logger.warning(f"Compose service '{name}' has no 'services' config, skipping")
            return None

        # The raw config is already a Docker Compose service definition
        compose_svc = dict(raw_services)
        compose_svc["networks"] = [f"sentinel-{ticket_id}"]

        return compose_svc

    def _apply_overrides(self, compose_svc: dict[str, Any], overrides: dict[str, Any]) -> None:
        """Apply Lando overrides to the compose service.

        Lando's 'overrides' key maps directly to Docker Compose service keys.
        """
        # Handle nested 'services' key (Lando v3 format)
        if "services" in overrides:
            overrides = overrides["services"]

        for key, value in overrides.items():
            if key == "environment" and isinstance(value, dict):
                compose_svc.setdefault("environment", {}).update(value)
            elif key == "volumes" and isinstance(value, list):
                compose_svc.setdefault("volumes", []).extend(value)
            elif key == "ports" and isinstance(value, list):
                compose_svc.setdefault("ports", []).extend(value)
            else:
                compose_svc[key] = value
