"""Adapters that turn verifier stdout/stderr/XML/JSON into structured errors.

Each parser returns ``list[StructuredError]``. They never return ``None`` and they
never raise — malformed input yields ``[]`` with a warning log. The agent's refine
loop consumes the structured shape directly so the model sees deduplicated,
machine-readable errors rather than raw terminal output.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from typing import TypedDict

logger = logging.getLogger(__name__)


class StructuredError(TypedDict):
    file: str
    line: int
    rule: str
    message: str


# ---------------------------------------------------------------------------
# pytest --tb=short
# ---------------------------------------------------------------------------

_PYTEST_LINE_RE = re.compile(r"^(FAILED|ERROR)\s+(.+?)\s+-\s+(.*)$", re.MULTILINE)


def parse_pytest_short(stdout: str) -> list[StructuredError]:
    """Parse ``pytest --tb=short`` text output.

    Each ``FAILED tests/path::test_x - reason`` line becomes a single entry.
    The summary lines do not carry a line number; ``line`` is left at 0.
    """
    try:
        if not stdout:
            return []
        out: list[StructuredError] = []
        for match in _PYTEST_LINE_RE.finditer(stdout):
            kind, file_part, reason = match.group(1), match.group(2), match.group(3)
            # Drop ``::test_x`` suffix from the file portion if present.
            file_path = file_part.split("::", 1)[0]
            rule = "test_failed" if kind == "FAILED" else "test_error"
            out.append(
                StructuredError(
                    file=file_path,
                    line=0,
                    rule=rule,
                    message=reason.strip(),
                )
            )
        return out
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("parse_pytest_short failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# PHPUnit JUnit XML
# ---------------------------------------------------------------------------


def parse_phpunit_junit(xml: str) -> list[StructuredError]:
    """Parse PHPUnit JUnit XML.

    Walks all ``<testcase>`` nodes that contain a ``<failure>`` or ``<error>``
    child. ``file`` falls back to the testcase ``classname`` if no ``file`` attr
    is present; ``line`` falls back to 0.
    """
    try:
        if not xml or not xml.strip():
            return []
        root = ET.fromstring(xml)
        out: list[StructuredError] = []
        for testcase in root.iter("testcase"):
            for child in testcase:
                if child.tag not in ("failure", "error"):
                    continue
                file_attr = testcase.get("file") or testcase.get("classname") or ""
                line_attr = testcase.get("line") or "0"
                try:
                    line = int(line_attr)
                except (ValueError, TypeError):
                    line = 0
                rule = child.get("type")
                if not rule:
                    rule = (
                        "phpunit_failure"
                        if child.tag == "failure"
                        else "phpunit_error"
                    )
                message_text = (child.text or "").strip()
                out.append(
                    StructuredError(
                        file=file_attr,
                        line=line,
                        rule=rule,
                        message=message_text,
                    )
                )
        return out
    except ET.ParseError as e:
        logger.warning("parse_phpunit_junit failed (ParseError): %s", e)
        return []
    except Exception as e:
        logger.warning("parse_phpunit_junit failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# PHPStan --error-format=json
# ---------------------------------------------------------------------------


def parse_phpstan_json(json_str: str) -> list[StructuredError]:
    """Parse PHPStan JSON output.

    PHPStan occasionally prints warnings on stderr before the JSON document.
    If the input is the concatenated stream we slice from the first ``{`` and
    try again. On failure we return ``[]`` and log a warning.
    """
    try:
        if not json_str or not json_str.strip():
            return []
        text = json_str
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            brace = text.find("{")
            if brace == -1:
                logger.warning("parse_phpstan_json: no JSON object found in input")
                return []
            try:
                data = json.loads(text[brace:])
            except json.JSONDecodeError as e:
                logger.warning("parse_phpstan_json: JSONDecodeError after trim: %s", e)
                return []

        files = data.get("files") or {}
        if not isinstance(files, dict):
            return []

        out: list[StructuredError] = []
        for path, payload in files.items():
            if not isinstance(payload, dict):
                continue
            messages = payload.get("messages") or []
            if not isinstance(messages, list):
                continue
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                identifier = msg.get("identifier")
                level = msg.get("level")
                if identifier:
                    rule = str(identifier)
                elif level is not None:
                    rule = f"level:{level}"
                else:
                    rule = "phpstan"
                line_raw = msg.get("line", 0)
                try:
                    line = int(line_raw) if line_raw is not None else 0
                except (TypeError, ValueError):
                    line = 0
                out.append(
                    StructuredError(
                        file=str(path),
                        line=line,
                        rule=rule,
                        message=str(msg.get("message", "")),
                    )
                )
        return out
    except Exception as e:
        logger.warning("parse_phpstan_json failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# composer validate
# ---------------------------------------------------------------------------


def parse_composer_validate(output: str) -> list[StructuredError]:
    """Binary ok/not-ok parse for ``composer validate``.

    If the output indicates the manifest is valid (and contains no error or
    warning markers), return ``[]``. Otherwise emit a single structured entry
    with the (truncated) output as the message.
    """
    try:
        if not output:
            return []
        text = output
        upper = text.upper()
        is_valid = "IS VALID" in upper and "ERROR" not in upper and "WARNING" not in upper
        if is_valid:
            return []
        return [
            StructuredError(
                file="composer.json",
                line=0,
                rule="composer_validate",
                message=text[:1000],
            )
        ]
    except Exception as e:
        logger.warning("parse_composer_validate failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# mypy
# ---------------------------------------------------------------------------

# Matches ``file:line: error: message [rule]`` and ``file:line:col: error: message [rule]``.
_MYPY_LINE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?:\s*\d+:)?\s*error:\s*"
    r"(?P<message>.+?)(?:\s*\[(?P<rule>[^\]]+)\])?\s*$"
)


def parse_mypy(stdout: str) -> list[StructuredError]:
    """Parse mypy ``error:`` lines into structured entries."""
    try:
        if not stdout:
            return []
        out: list[StructuredError] = []
        for line in stdout.splitlines():
            m = _MYPY_LINE_RE.match(line)
            if not m:
                continue
            try:
                line_no = int(m.group("line"))
            except (TypeError, ValueError):
                line_no = 0
            rule = m.group("rule") or "mypy_error"
            out.append(
                StructuredError(
                    file=m.group("file"),
                    line=line_no,
                    rule=rule,
                    message=m.group("message").strip(),
                )
            )
        return out
    except Exception as e:
        logger.warning("parse_mypy failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# ruff --output-format=json
# ---------------------------------------------------------------------------


def parse_ruff_json(json_str: str) -> list[StructuredError]:
    """Parse ruff JSON output (a list of diagnostic objects)."""
    try:
        if not json_str or not json_str.strip():
            return []
        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning("parse_ruff_json: JSONDecodeError: %s", e)
            return []
        if not isinstance(data, list):
            return []
        out: list[StructuredError] = []
        for entry in data:
            if not isinstance(entry, dict):
                continue
            location = entry.get("location") or {}
            row = 0
            if isinstance(location, dict):
                row_raw = location.get("row", 0)
                try:
                    row = int(row_raw) if row_raw is not None else 0
                except (TypeError, ValueError):
                    row = 0
            out.append(
                StructuredError(
                    file=str(entry.get("filename", "")),
                    line=row,
                    rule=str(entry.get("code") or "ruff"),
                    message=str(entry.get("message", "")),
                )
            )
        return out
    except Exception as e:
        logger.warning("parse_ruff_json failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# drush site:install --config-dir output (Drupal config sync validation)
# ---------------------------------------------------------------------------

# Drush dumps the Drupal install-task HTML page on stderr/stdout when
# config validation fails. The two patterns we see in practice are:
#   "Unable to install the <em>X</em> module since it does not exist."
#   "Unable to install the <em>X</em> module since it requires the
#    <em>Y</em> module."
# Both messages also appear unwrapped in plain-text drush output on some
# versions, so the regex tolerates the <em> tags being absent.
_DRUSH_MODULE_DOES_NOT_EXIST = re.compile(
    r"Unable to install the\s+(?:<em[^>]*>)?([\w\- ]+?)(?:</em>)?\s+module "
    r"since it does not exist",
    re.IGNORECASE,
)
_DRUSH_MODULE_REQUIRES = re.compile(
    r"Unable to install the\s+(?:<em[^>]*>)?([\w\- ]+?)(?:</em>)?\s+module "
    r"since it requires the\s+(?:<em[^>]*>)?([\w\- ]+?)(?:</em>)?\s+module",
    re.IGNORECASE,
)

# AlreadyInstalledException: drush refuses to reinstall over an existing
# Drupal install. In Sentinel this almost always means the per-ticket DB
# wasn't cleaned between runs — a partial DROP TABLE earlier left rows
# behind and install_verify_completed_task() now finds them.
_DRUSH_ALREADY_INSTALLED = re.compile(
    r"AlreadyInstalledException", re.IGNORECASE,
)

# Generic catcher for any other Drupal exception thrown by drush. We extract
# the class name so the operator at least gets a useful failure_signature
# even when no specific parser matches.
_DRUSH_GENERIC_EXCEPTION = re.compile(
    r"\[(Drupal\\[\w\\]+Exception)\]", re.IGNORECASE,
)


def _drush_module_slug(name: str) -> str:
    """Best-effort conversion of a human module name to its machine name."""
    return name.strip().lower().replace(" ", "_")


def parse_drush_config_validation(output: str) -> list[StructuredError]:
    """Parse failure messages from ``drush site:install --config-dir=...``.

    Returns a list of structured errors describing the *actionable* config
    problems Drupal reported (missing modules, unmet module dependencies).
    The hint is embedded in ``message`` because ``StructuredError`` has no
    dedicated hint field.

    On unrecognised output the parser returns ``[]`` — callers should treat
    that as "drush failed but we don't know why" and fall back to showing
    a truncated raw output.
    """
    try:
        if not output:
            return []
        out: list[StructuredError] = []
        seen: set[tuple[str, str]] = set()

        # "requires the X module" must be checked before "does not exist"
        # because the requires-pattern is a strict superset of the does-not-exist
        # pattern's prefix.
        for m in _DRUSH_MODULE_REQUIRES.finditer(output):
            module = m.group(1).strip()
            dep = m.group(2).strip()
            # The lazy `[\w\- ]+?` capture in _DRUSH_MODULE_REQUIRES /
            # _DRUSH_MODULE_DOES_NOT_EXIST legally matches whitespace-only spans
            # because the character class includes ` `. Tightening the regex risks
            # regressions on real drush prose (e.g. multi-word names like
            # "Drupal Symfony Mailer"), so we drop empty captures at the boundary
            # instead. See issue M2 in feat-sentinel-learning-system-review.md.
            if not module or not dep:
                continue
            key = ("requires", f"{module}->{dep}")
            if key in seen:
                continue
            seen.add(key)
            dep_slug = _drush_module_slug(dep)
            out.append(
                StructuredError(
                    file="config/sync/core.extension.yml",
                    line=0,
                    rule="drush.config.unmet_dependency",
                    message=(
                        f"module '{module}' requires '{dep}' which is not enabled. "
                        f"Hint: add '{dep_slug}: 0' under module: in "
                        f"config/sync/core.extension.yml, or composer require "
                        f"the package providing '{dep}'."
                    ),
                )
            )

        for m in _DRUSH_MODULE_DOES_NOT_EXIST.finditer(output):
            module = m.group(1).strip()
            # Same boundary guard as above — drop whitespace-only captures.
            if not module:
                continue
            # Skip if the same module already matched the stronger
            # "requires" pattern at the same offset.
            key = ("missing", module)
            if key in seen:
                continue
            seen.add(key)
            slug = _drush_module_slug(module)
            out.append(
                StructuredError(
                    file="config/sync/core.extension.yml",
                    line=0,
                    rule="drush.config.missing_module",
                    message=(
                        f"module '{module}' is referenced in config/sync but is "
                        f"not installed. Hint: composer require drupal/{slug} "
                        f"(and commit composer.json + composer.lock), or remove "
                        f"'{slug}' from config/sync/core.extension.yml."
                    ),
                )
            )

        # AlreadyInstalledException is a single-shot signal — emit one entry
        # regardless of how many times the substring appears.
        if _DRUSH_ALREADY_INSTALLED.search(output) and ("already_installed", "") not in seen:
            seen.add(("already_installed", ""))
            out.append(
                StructuredError(
                    file="",
                    line=0,
                    rule="drush.bootstrap.already_installed",
                    message=(
                        "Drupal is already installed in this database. "
                        "Hint: the per-ticket DB volume wasn't cleaned between "
                        "runs (a previous DROP TABLE failed atomically on a "
                        "missing table). Recreate the appserver DB volume, or "
                        "ensure 'drush sql:drop -y' runs successfully before "
                        "'drush site:install'."
                    ),
                )
            )

        # Generic Drupal\...Exception fallback — only emit if nothing more
        # specific matched, so we don't double-report the cases above.
        if not out:
            for m in _DRUSH_GENERIC_EXCEPTION.finditer(output):
                cls = m.group(1)
                key = ("generic", cls)
                if key in seen:
                    continue
                seen.add(key)
                out.append(
                    StructuredError(
                        file="",
                        line=0,
                        rule=f"drush.exception.{cls.split(chr(92))[-1]}",
                        message=(
                            f"drush terminated with {cls}. See the drush "
                            f"output for the exception body and stack trace."
                        ),
                    )
                )

        return out
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("parse_drush_config_validation failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# failure-signature normalization (for postmortem dedup keys)
# ---------------------------------------------------------------------------


def normalize_failure_signature(errors: list[StructuredError]) -> str:
    """Build a deterministic dedup key from the first (highest-rank) error.

    The signature is intentionally small and stable so that the same root cause
    across runs hashes to the same key. Absolute paths and line numbers are
    stripped because they vary across runs without changing the underlying
    failure.
    """
    if not errors:
        return "empty_failure"
    first = errors[0]
    s = f"{first.get('rule', '')}:{first.get('message', '')}"
    s = s.lower()
    # Strip absolute paths like /var/www/foo/bar/ but leave bare filenames.
    s = re.sub(r"/[^\s:]+/", "", s)
    # Strip line-number tokens.
    s = re.sub(r"line\s+\d+", "line", s)
    s = re.sub(r":\d+", "", s)
    # Collapse whitespace.
    s = re.sub(r"\s+", " ", s).strip()
    return s[:200]
