"""Tests for src.agents._structured_errors parsers and normalizer."""

from __future__ import annotations

from src.agents._structured_errors import (
    normalize_failure_signature,
    parse_composer_validate,
    parse_drush_config_validation,
    parse_mypy,
    parse_phpstan_json,
    parse_phpunit_junit,
    parse_pytest_short,
    parse_ruff_json,
)


# ---------------------------------------------------------------------------
# parse_pytest_short
# ---------------------------------------------------------------------------


class TestParsePytestShort:
    def test_empty_input_returns_empty(self) -> None:
        assert parse_pytest_short("") == []

    def test_happy_path_failed_and_error(self) -> None:
        sample = (
            "============================= short test summary info ==============================\n"
            "FAILED tests/test_foo.py::test_one - AssertionError: expected 1 got 2\n"
            "FAILED tests/test_bar.py::test_two - ValueError: bad input\n"
            "ERROR tests/test_baz.py::test_three - ImportError: missing module\n"
            "========================= 2 failed, 1 error in 0.05s ===============================\n"
        )
        out = parse_pytest_short(sample)
        assert len(out) == 3
        assert out[0]["file"] == "tests/test_foo.py"
        assert out[0]["line"] == 0
        assert out[0]["rule"] == "test_failed"
        assert "AssertionError" in out[0]["message"]
        assert out[2]["rule"] == "test_error"
        assert out[2]["file"] == "tests/test_baz.py"

    def test_malformed_input_returns_empty(self) -> None:
        # Random unrelated text — no FAILED/ERROR lines at all.
        assert parse_pytest_short("hello world\nno tests here\n") == []


# ---------------------------------------------------------------------------
# parse_phpunit_junit
# ---------------------------------------------------------------------------


class TestParsePhpunitJunit:
    def test_empty_input_returns_empty(self) -> None:
        assert parse_phpunit_junit("") == []

    def test_happy_path_failure_and_error(self) -> None:
        xml = """<?xml version="1.0" encoding="UTF-8"?>
<testsuites>
  <testsuite name="App\\Tests">
    <testcase name="testOne" classname="App\\Tests\\FooTest" file="/app/tests/FooTest.php" line="42">
      <failure type="PHPUnit\\Framework\\ExpectationFailedException">Failed asserting that 1 matches expected 2.</failure>
    </testcase>
    <testcase name="testTwo" classname="App\\Tests\\BarTest" file="/app/tests/BarTest.php" line="10">
      <error type="RuntimeException">Database is gone</error>
    </testcase>
    <testcase name="testPasses" classname="App\\Tests\\OkTest" file="/app/tests/OkTest.php" line="5"/>
  </testsuite>
</testsuites>
"""
        out = parse_phpunit_junit(xml)
        assert len(out) == 2
        assert out[0]["file"] == "/app/tests/FooTest.php"
        assert out[0]["line"] == 42
        assert out[0]["rule"] == "PHPUnit\\Framework\\ExpectationFailedException"
        assert "Failed asserting" in out[0]["message"]
        assert out[1]["rule"] == "RuntimeException"
        assert out[1]["line"] == 10

    def test_malformed_input_returns_empty(self) -> None:
        # Truncated/garbled XML.
        assert parse_phpunit_junit("<testsuites><not closed") == []


# ---------------------------------------------------------------------------
# parse_phpstan_json
# ---------------------------------------------------------------------------


class TestParsePhpstanJson:
    def test_empty_input_returns_empty(self) -> None:
        assert parse_phpstan_json("") == []

    def test_happy_path(self) -> None:
        sample = """{
          "totals": {"errors": 0, "file_errors": 2},
          "files": {
            "/app/web/modules/custom/foo/src/Foo.php": {
              "messages": [
                {"message": "Undefined variable: $x", "line": 17, "identifier": "variable.undefined", "level": "5"},
                {"message": "Method Foo::bar() has no return type", "line": 33, "level": "6"}
              ]
            },
            "/app/web/modules/custom/foo/src/Bar.php": {
              "messages": [
                {"message": "Class Baz not found", "line": 5}
              ]
            }
          }
        }"""
        out = parse_phpstan_json(sample)
        assert len(out) == 3
        assert out[0]["file"] == "/app/web/modules/custom/foo/src/Foo.php"
        assert out[0]["line"] == 17
        assert out[0]["rule"] == "variable.undefined"
        # Second message has no identifier — falls back to level.
        assert out[1]["rule"] == "level:6"
        # Third message has neither — falls back to bare "phpstan".
        assert out[2]["rule"] == "phpstan"

    def test_tolerates_warning_prefix(self) -> None:
        # PHPStan sometimes prints a warning before the JSON document.
        sample = (
            "Note: Using configuration file /app/phpstan.neon\n"
            '{"totals":{"errors":0,"file_errors":1},'
            '"files":{"/a.php":{"messages":[{"message":"oops","line":1}]}}}'
        )
        out = parse_phpstan_json(sample)
        assert len(out) == 1
        assert out[0]["message"] == "oops"

    def test_malformed_json_returns_empty(self) -> None:
        assert parse_phpstan_json("not json at all {{{") == []


# ---------------------------------------------------------------------------
# parse_composer_validate
# ---------------------------------------------------------------------------


class TestParseComposerValidate:
    def test_empty_input_returns_empty(self) -> None:
        assert parse_composer_validate("") == []

    def test_valid_returns_empty(self) -> None:
        assert parse_composer_validate("./composer.json is valid\n") == []

    def test_invalid_produces_one_entry(self) -> None:
        bad = (
            "./composer.json is valid for simple usage with composer but has\n"
            "strict errors that make it unable to be published as a package:\n"
            "ERROR: name : The package name is required\n"
        )
        out = parse_composer_validate(bad)
        assert len(out) == 1
        assert out[0]["file"] == "composer.json"
        assert out[0]["line"] == 0
        assert out[0]["rule"] == "composer_validate"
        assert "ERROR" in out[0]["message"]

    def test_warning_produces_one_entry(self) -> None:
        sample = "./composer.json is valid, but with a few warnings\nWARNING: license missing"
        out = parse_composer_validate(sample)
        assert len(out) == 1
        assert out[0]["rule"] == "composer_validate"

    def test_truncates_long_message(self) -> None:
        big = "ERROR " + ("x" * 5000)
        out = parse_composer_validate(big)
        assert len(out) == 1
        assert len(out[0]["message"]) == 1000


# ---------------------------------------------------------------------------
# parse_mypy
# ---------------------------------------------------------------------------


class TestParseMypy:
    def test_empty_input_returns_empty(self) -> None:
        assert parse_mypy("") == []

    def test_happy_path_with_and_without_rule(self) -> None:
        sample = (
            "src/foo.py:10: error: Incompatible return value type [return-value]\n"
            "src/bar.py:42:5: error: Name \"baz\" is not defined  [name-defined]\n"
            "src/qux.py:7: error: Something went wrong\n"
            "src/qux.py:8: note: this is just a note\n"
            "Found 3 errors in 3 files\n"
        )
        out = parse_mypy(sample)
        assert len(out) == 3
        assert out[0]["file"] == "src/foo.py"
        assert out[0]["line"] == 10
        assert out[0]["rule"] == "return-value"
        assert out[1]["rule"] == "name-defined"
        assert out[1]["line"] == 42
        # No rule bracket -> default
        assert out[2]["rule"] == "mypy_error"

    def test_malformed_input_returns_empty(self) -> None:
        assert parse_mypy("not a mypy line at all\nstill not\n") == []


# ---------------------------------------------------------------------------
# parse_ruff_json
# ---------------------------------------------------------------------------


class TestParseRuffJson:
    def test_empty_input_returns_empty(self) -> None:
        assert parse_ruff_json("") == []

    def test_empty_list_returns_empty(self) -> None:
        assert parse_ruff_json("[]") == []

    def test_happy_path(self) -> None:
        sample = """[
          {"filename": "src/foo.py", "code": "F401", "message": "imported but unused",
           "location": {"row": 3, "column": 1}},
          {"filename": "src/bar.py", "code": "E501", "message": "line too long",
           "location": {"row": 88, "column": 1}}
        ]"""
        out = parse_ruff_json(sample)
        assert len(out) == 2
        assert out[0]["file"] == "src/foo.py"
        assert out[0]["line"] == 3
        assert out[0]["rule"] == "F401"
        assert out[1]["line"] == 88

    def test_malformed_returns_empty(self) -> None:
        assert parse_ruff_json("not json") == []


# ---------------------------------------------------------------------------
# normalize_failure_signature
# ---------------------------------------------------------------------------


class TestNormalizeFailureSignature:
    def test_empty_returns_sentinel(self) -> None:
        assert normalize_failure_signature([]) == "empty_failure"

    def test_strips_absolute_paths(self) -> None:
        err: dict = {
            "file": "/app/foo.py",
            "line": 0,
            "rule": "test_failed",
            "message": "AssertionError in /var/www/sentinel/tests/test_foo.py for value",
        }
        sig = normalize_failure_signature([err])  # type: ignore[list-item]
        assert "/var/www" not in sig
        assert "/app" not in sig
        # The bare filename should remain (it sits at the end of the path so it survives).
        assert "test_foo.py" in sig or "value" in sig

    def test_strips_line_numbers(self) -> None:
        err: dict = {
            "file": "f.py",
            "line": 0,
            "rule": "mypy_error",
            "message": "issue at line 42 and again on line 100",
        }
        sig = normalize_failure_signature([err])  # type: ignore[list-item]
        assert "42" not in sig
        assert "100" not in sig
        assert "line" in sig

    def test_strips_colon_line_numbers(self) -> None:
        err: dict = {
            "file": "f.py",
            "line": 0,
            "rule": "mypy_error",
            "message": "src/foo.py:123 incompatible types",
        }
        sig = normalize_failure_signature([err])  # type: ignore[list-item]
        assert ":123" not in sig

    def test_deterministic(self) -> None:
        err: dict = {
            "file": "f.py",
            "line": 7,
            "rule": "test_failed",
            "message": "AssertionError: expected 1, got 2",
        }
        a = normalize_failure_signature([err])  # type: ignore[list-item]
        b = normalize_failure_signature([err])  # type: ignore[list-item]
        assert a == b

    def test_truncates_to_200(self) -> None:
        err: dict = {
            "file": "f.py",
            "line": 0,
            "rule": "test_failed",
            "message": "x" * 5000,
        }
        sig = normalize_failure_signature([err])  # type: ignore[list-item]
        assert len(sig) <= 200

    def test_lowercases(self) -> None:
        err: dict = {
            "file": "f.py",
            "line": 0,
            "rule": "TEST_FAILED",
            "message": "AssertionError: BIG SHOUTING",
        }
        sig = normalize_failure_signature([err])  # type: ignore[list-item]
        assert sig == sig.lower()


# ---------------------------------------------------------------------------
# parse_drush_config_validation
# ---------------------------------------------------------------------------


# Real-world Drupal install-page snippets observed in the DHL pipeline logs.
# Both shapes appear inside a `<li class="messages__item">` block in the HTML
# dump that drush prints when ``site:install --config-dir=...`` fails
# validation.
_DRUSH_MISSING = (
    '<li class="messages__item">Unable to install the '
    '<em class="placeholder">responsive_preview</em> module since it does '
    'not exist.</li>'
)
_DRUSH_REQUIRES = (
    '<li class="messages__item">Unable to install the '
    '<em class="placeholder">Drupal Symfony Mailer</em> module since it '
    'requires the <em class="placeholder">Mailer Transport</em> module.</li>'
)


class TestParseDrushConfigValidation:
    def test_empty_input_returns_empty(self) -> None:
        assert parse_drush_config_validation("") == []

    def test_unrelated_html_returns_empty(self) -> None:
        # The drush dump is huge HTML; only the install-error <li> entries
        # are actionable. Anything else should produce no signal.
        assert parse_drush_config_validation(
            "<html><body><h1>Drupal</h1><p>welcome</p></body></html>"
        ) == []

    def test_missing_module_emits_composer_hint(self) -> None:
        out = parse_drush_config_validation(_DRUSH_MISSING)
        assert len(out) == 1
        err = out[0]
        assert err["rule"] == "drush.config.missing_module"
        assert err["file"] == "config/sync/core.extension.yml"
        assert "responsive_preview" in err["message"]
        # The hint must mention both fix paths so the human can choose.
        assert "composer require drupal/responsive_preview" in err["message"]
        assert "core.extension.yml" in err["message"]

    def test_requires_module_emits_dependency_hint(self) -> None:
        out = parse_drush_config_validation(_DRUSH_REQUIRES)
        assert len(out) == 1
        err = out[0]
        assert err["rule"] == "drush.config.unmet_dependency"
        assert "Drupal Symfony Mailer" in err["message"]
        assert "Mailer Transport" in err["message"]
        # The slugified machine name must appear in the YAML hint so a human
        # can paste it directly into core.extension.yml.
        assert "mailer_transport" in err["message"]

    def test_combined_output_emits_both(self) -> None:
        # A single drush run can list multiple problems in one HTML page.
        out = parse_drush_config_validation(_DRUSH_MISSING + "\n" + _DRUSH_REQUIRES)
        rules = sorted(e["rule"] for e in out)
        assert rules == [
            "drush.config.missing_module",
            "drush.config.unmet_dependency",
        ]

    def test_dedup_on_repeated_message(self) -> None:
        # Drush sometimes echoes the same line twice (alert region + list).
        # Repeated occurrences of the same module should collapse to one entry.
        out = parse_drush_config_validation(_DRUSH_MISSING + _DRUSH_MISSING)
        assert len(out) == 1

    def test_plaintext_variant_without_em_tags(self) -> None:
        # Some drush versions / verbosity levels emit the message without HTML
        # wrapping. The parser must handle the unwrapped form too.
        plain = (
            "Unable to install the responsive_preview module since it "
            "does not exist."
        )
        out = parse_drush_config_validation(plain)
        assert len(out) == 1
        assert out[0]["rule"] == "drush.config.missing_module"
        assert "responsive_preview" in out[0]["message"]

    def test_empty_module_name_is_silently_dropped(self) -> None:
        # The lazy `[\w\- ]+?` regex legally matches whitespace-only spans
        # because the character class includes a literal space. Real-world
        # drush prose with odd spacing must NOT produce polluting bullets
        # like "module '' is referenced..." — the parser drops them at the
        # boundary instead. See issue M2 in
        # feat-sentinel-learning-system-review.md.

        # 1. HTML-wrapped variant with empty <em>.
        malformed_html = (
            '<li class="messages__item">Unable to install the '
            '<em class="placeholder">  </em> module since it does '
            'not exist.</li>'
        )
        assert parse_drush_config_validation(malformed_html) == []

        # 2. Plaintext variant with double-spacing (empty module slot).
        malformed_plain = (
            "Unable to install the  module since it does not exist."
        )
        assert parse_drush_config_validation(malformed_plain) == []

        # 3. Requires-variant: either side empty must drop the entry.
        malformed_requires = (
            "Unable to install the  module since it requires the  module."
        )
        assert parse_drush_config_validation(malformed_requires) == []

        # 4. Mixed: a malformed line alongside a valid one must yield only
        # the valid bullet (we do not regress on the surrounding loop).
        mixed = malformed_plain + "\n" + _DRUSH_MISSING
        out = parse_drush_config_validation(mixed)
        assert len(out) == 1
        assert out[0]["rule"] == "drush.config.missing_module"
        assert "responsive_preview" in out[0]["message"]

    def test_already_installed_exception(self) -> None:
        # Real-world output from a dirty-DB Sentinel run.
        sample = (
            "In install.core.inc line 1156:\n"
            "  [Drupal\\Core\\Installer\\Exception\\AlreadyInstalledException]\n"
            "  <ul><li>Om opnieuw te beginnen, ...</li></ul>\n"
        )
        out = parse_drush_config_validation(sample)
        assert len(out) == 1
        err = out[0]
        assert err["rule"] == "drush.bootstrap.already_installed"
        # The hint must point the operator at the DB-cleanup root cause,
        # not at config — this is an env issue, not a config issue.
        assert "DB" in err["message"] or "sql:drop" in err["message"]

    def test_already_installed_does_not_double_emit(self) -> None:
        # The exception class name appears multiple times in a real drush
        # stack trace; emit one entry only.
        sample = (
            "[Drupal\\Core\\Installer\\Exception\\AlreadyInstalledException]\n"
            "Exception trace: ... AlreadyInstalledException ... AlreadyInstalledException\n"
        )
        out = parse_drush_config_validation(sample)
        assert len(out) == 1

    def test_generic_drupal_exception_fallback(self) -> None:
        # If drush throws a Drupal exception we don't have a specific parser
        # for, the generic fallback should at least surface the class name.
        sample = (
            "[Drupal\\Core\\Database\\DatabaseNotFoundException]\n"
            "  Database not found\n"
        )
        out = parse_drush_config_validation(sample)
        assert len(out) == 1
        assert out[0]["rule"].startswith("drush.exception.")
        assert "DatabaseNotFoundException" in out[0]["rule"]

    def test_specific_match_suppresses_generic_fallback(self) -> None:
        # When AlreadyInstalledException is matched specifically, the generic
        # ``[Drupal\...Exception]`` fallback must NOT also fire — otherwise
        # we'd report the same problem twice with different rules.
        sample = (
            "In install.core.inc line 1156:\n"
            "[Drupal\\Core\\Installer\\Exception\\AlreadyInstalledException]\n"
            "Some text\n"
        )
        out = parse_drush_config_validation(sample)
        rules = [e["rule"] for e in out]
        assert rules == ["drush.bootstrap.already_installed"]
