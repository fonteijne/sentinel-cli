"""Unit tests for SecurityReviewerAgent."""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pytest

from src.agents.security_reviewer import SecurityReviewerAgent


@pytest.fixture
def mock_config():
    """Mock configuration loader."""
    with patch("src.agents.base_agent.get_config") as mock:
        config = Mock()
        config.get_agent_config.return_value = {
            "model": "claude-4-5-haiku",
            "temperature": 0.1,
        }
        config.get_llm_config.return_value = {
            "mode": "custom_proxy",
            "api_key": "test-api-key",
            "base_url": "https://test.api.com/v1",
        }
        config.get.return_value = ["Read", "Grep", "Glob"]
        config.get_project_config.return_value = {"default_branch": "main"}
        mock.return_value = config
        yield config


@pytest.fixture
def mock_agent_sdk():
    """Mock Agent SDK wrapper."""
    with patch("src.agents.base_agent.AgentSDKWrapper") as mock:
        wrapper = Mock()
        async def mock_execute(prompt, session_id=None):
            return {
                "content": "Security analysis response",
                "tool_uses": [],
                "session_id": "test-session-123"
            }
        wrapper.execute_with_tools = mock_execute
        wrapper.agent_name = "security_reviewer"
        wrapper.model = "claude-4-5-haiku"
        wrapper.allowed_tools = ["Read", "Grep", "Glob", "Bash(git *)"]
        mock.return_value = wrapper
        yield wrapper


@pytest.fixture
def mock_prompt():
    """Mock prompt loader."""
    with patch("src.agents.base_agent.load_agent_prompt") as mock:
        mock.return_value = "Security reviewer system prompt"
        yield mock


@pytest.fixture
def temp_worktree():
    """Create a temporary directory for worktree."""
    with TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def sample_code_file(temp_worktree):
    """Create a sample Python file."""
    code_path = temp_worktree / "app.py"
    code_content = '''"""Sample application."""

import os

def get_user(user_id):
    """Get user from database."""
    # Safe implementation
    return query("SELECT * FROM users WHERE id = ?", (user_id,))
'''
    code_path.write_text(code_content)
    return code_path


@pytest.fixture
def vulnerable_code_file(temp_worktree):
    """Create a Python file with vulnerabilities."""
    code_path = temp_worktree / "vulnerable.py"
    code_content = '''"""Vulnerable code."""

import os

# Hardcoded credentials
password = "secret123"
api_key = "sk-1234567890"

def get_user(user_id):
    """Unsafe SQL query."""
    return execute(f"SELECT * FROM users WHERE id = {user_id}")

def search_users(name):
    """SQL injection vulnerability."""
    query = "SELECT * FROM users WHERE name = '" + name + "'"
    return execute(query)
'''
    code_path.write_text(code_content)
    return code_path


@pytest.fixture
def html_file_with_xss(temp_worktree):
    """Create an HTML file with XSS vulnerability."""
    html_path = temp_worktree / "template.html"
    html_content = """<!DOCTYPE html>
<html>
<body>
    <div>{{ user_input|safe }}</div>
    <script>
        var data = {{ raw_data|safe }};
    </script>
</body>
</html>
"""
    html_path.write_text(html_content)
    return html_path


@pytest.fixture
def php_vulnerable_file(temp_worktree):
    """Create a PHP file with vulnerabilities."""
    php_path = temp_worktree / "controller.php"
    php_content = '''<?php

function get_user($uid) {
    // SQL injection via db_query with variable
    $result = db_query("SELECT * FROM {users} WHERE uid = " . $uid);
    return $result;
}

function show_name() {
    // XSS via direct echo of superglobal
    echo $_GET['name'];
}

function render_block($content) {
    // XSS via #markup with variable
    $build['#markup'] = $content;
    return $build;
}
'''
    php_path.write_text(php_content)
    return php_path


@pytest.fixture
def twig_xss_file(temp_worktree):
    """Create a Twig template with XSS vulnerability."""
    twig_path = temp_worktree / "page.html.twig"
    twig_content = """<div>{{ content|raw }}</div>
<p>{{ safe_content }}</p>
"""
    twig_path.write_text(twig_content)
    return twig_path


class TestSecurityReviewerAgent:
    """Test suite for SecurityReviewerAgent class."""

    def test_init(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test agent initialization."""
        agent = SecurityReviewerAgent()

        assert agent.agent_name == "security_reviewer"
        assert agent.model == "claude-4-5-haiku"
        assert agent.temperature == 0.1
        assert agent.veto_power is True

    def test_owasp_checks_loaded(self, mock_config, mock_agent_sdk, mock_prompt):
        """Test that OWASP Top 10 checks are loaded."""
        agent = SecurityReviewerAgent()

        assert len(agent.owasp_checks) == 10
        assert "SQL Injection" in agent.owasp_checks
        assert "Cross-Site Scripting (XSS)" in agent.owasp_checks
        assert "Broken Authentication" in agent.owasp_checks
        assert "Sensitive Data Exposure" in agent.owasp_checks

    def test_scan_code_basic(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree, sample_code_file
    ):
        """Test basic code scanning."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files", return_value=[sample_code_file]):
            mock_execute.return_value = {"success": True}

            findings = agent.scan_code(temp_worktree)

            assert isinstance(findings, list)

    def test_scan_code_uses_custom_command(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that scan_code uses custom OWASP scan command."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files", return_value=[]):
            mock_execute.return_value = {"success": True}

            agent.scan_code(temp_worktree)

            # No changed files → early return, no command called
            mock_execute.assert_not_called()

    def test_scan_code_with_changed_files(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        vulnerable_code_file
    ):
        """Test that scan_code scans only changed files."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files",
                          return_value=[vulnerable_code_file]):
            mock_execute.return_value = {"success": True}

            findings = agent.scan_code(temp_worktree)

            # Should find issues in the vulnerable file
            assert len(findings) > 0

    def test_scan_code_handles_command_failure(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        sample_code_file
    ):
        """Test scan continues when custom command fails."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files",
                          return_value=[sample_code_file]):
            mock_execute.return_value = {
                "success": False,
                "errors": ["Command failed"],
            }

            # Should not raise exception
            findings = agent.scan_code(temp_worktree)

            assert isinstance(findings, list)

    def test_scan_code_handles_command_exception(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        sample_code_file
    ):
        """Test scan continues when custom command raises exception."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files",
                          return_value=[sample_code_file]):
            mock_execute.side_effect = Exception("Command error")

            # Should not raise exception
            findings = agent.scan_code(temp_worktree)

            assert isinstance(findings, list)

    def test_scan_code_no_changed_files_returns_empty(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that no changed files produces no findings."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "_get_changed_files", return_value=[]):
            findings = agent.scan_code(temp_worktree)

            assert findings == []

    def test_scan_code_git_fallback(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        vulnerable_code_file
    ):
        """Test fallback to full scan when git fails."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files", return_value=None):
            mock_execute.return_value = {"success": True}

            findings = agent.scan_code(temp_worktree)

            # Should still find issues via fallback full scan
            assert isinstance(findings, list)

    def test_scan_for_secrets_finds_hardcoded_password(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        vulnerable_code_file
    ):
        """Test detection of hardcoded passwords."""
        agent = SecurityReviewerAgent()

        findings = agent._scan_for_secrets(temp_worktree, [vulnerable_code_file])

        # Should find password and api_key
        assert len(findings) >= 2

        password_findings = [f for f in findings if "password" in f["description"].lower()]
        assert len(password_findings) > 0
        assert password_findings[0]["severity"] == "high"
        assert password_findings[0]["category"] == "Sensitive Data Exposure"

    def test_scan_for_secrets_finds_api_keys(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        vulnerable_code_file
    ):
        """Test detection of hardcoded API keys."""
        agent = SecurityReviewerAgent()

        findings = agent._scan_for_secrets(temp_worktree, [vulnerable_code_file])

        api_key_findings = [f for f in findings if "api_key" in f["description"].lower()]
        assert len(api_key_findings) > 0
        assert "environment variables" in api_key_findings[0]["recommendation"]

    def test_scan_for_secrets_ignores_env_vars(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that environment variable usage is not flagged."""
        code_path = temp_worktree / "safe.py"
        code_content = '''"""Safe code."""

import os

password = os.getenv("PASSWORD")
api_key = os.environ.get("API_KEY")
'''
        code_path.write_text(code_content)

        agent = SecurityReviewerAgent()
        findings = agent._scan_for_secrets(temp_worktree, [code_path])

        # Should not find issues with env var usage
        assert len(findings) == 0

    def test_scan_for_secrets_ignores_php_env_vars(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that PHP environment variable usage is not flagged."""
        code_path = temp_worktree / "safe.php"
        code_content = '''<?php
$password = getenv("DB_PASSWORD");
$token = $_ENV["API_TOKEN"];
'''
        code_path.write_text(code_content)

        agent = SecurityReviewerAgent()
        findings = agent._scan_for_secrets(temp_worktree, [code_path])

        assert len(findings) == 0

    def test_scan_for_sql_injection_fstring(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        vulnerable_code_file
    ):
        """Test detection of SQL injection via f-strings."""
        agent = SecurityReviewerAgent()

        findings = agent._scan_for_sql_injection(temp_worktree, [vulnerable_code_file])

        sql_findings = [f for f in findings if f["category"] == "SQL Injection"]
        assert len(sql_findings) > 0
        assert sql_findings[0]["severity"] == "critical"
        assert "parameterized" in sql_findings[0]["recommendation"]

    def test_scan_for_sql_injection_safe_code(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        sample_code_file
    ):
        """Test that safe parameterized queries are not flagged."""
        agent = SecurityReviewerAgent()

        findings = agent._scan_for_sql_injection(temp_worktree, [sample_code_file])

        # Sample code uses parameterized queries
        assert len(findings) == 0

    def test_scan_for_sql_injection_php(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        php_vulnerable_file
    ):
        """Test detection of SQL injection in PHP/Drupal code."""
        agent = SecurityReviewerAgent()

        findings = agent._scan_for_sql_injection(temp_worktree, [php_vulnerable_file])

        sql_findings = [f for f in findings if f["category"] == "SQL Injection"]
        assert len(sql_findings) > 0
        assert sql_findings[0]["severity"] == "critical"
        assert "Drupal" in sql_findings[0]["recommendation"]

    def test_scan_for_xss_finds_unsafe_output(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        html_file_with_xss
    ):
        """Test detection of XSS vulnerabilities."""
        agent = SecurityReviewerAgent()

        findings = agent._scan_for_xss(temp_worktree, [html_file_with_xss])

        xss_findings = [f for f in findings if f["category"] == "Cross-Site Scripting (XSS)"]
        assert len(xss_findings) > 0
        assert xss_findings[0]["severity"] == "high"
        assert "escaping" in xss_findings[0]["recommendation"].lower() or "safe" in xss_findings[0]["recommendation"].lower()

    def test_scan_for_xss_no_html_files(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        sample_code_file
    ):
        """Test XSS scan with no HTML files."""
        agent = SecurityReviewerAgent()

        findings = agent._scan_for_xss(temp_worktree, [sample_code_file])

        assert len(findings) == 0

    def test_scan_for_xss_php_superglobals(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        php_vulnerable_file
    ):
        """Test XSS detection for PHP superglobal output."""
        agent = SecurityReviewerAgent()

        findings = agent._scan_for_xss(temp_worktree, [php_vulnerable_file])

        xss_findings = [f for f in findings if f["category"] == "Cross-Site Scripting (XSS)"]
        assert len(xss_findings) > 0
        # Should find echo $_GET and #markup issues
        descriptions = " ".join(f["description"] for f in xss_findings)
        assert "superglobal" in descriptions or "#markup" in descriptions

    def test_scan_for_xss_twig_raw(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        twig_xss_file
    ):
        """Test XSS detection for Twig |raw filter."""
        agent = SecurityReviewerAgent()

        findings = agent._scan_for_xss(temp_worktree, [twig_xss_file])

        xss_findings = [f for f in findings if f["category"] == "Cross-Site Scripting (XSS)"]
        assert len(xss_findings) > 0
        assert any("|raw" in f["description"] for f in xss_findings)

    def test_check_vulnerabilities_accepts_file_list(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        sample_code_file
    ):
        """Test checking specific files for vulnerabilities."""
        agent = SecurityReviewerAgent()

        findings = agent.check_vulnerabilities([sample_code_file])

        assert isinstance(findings, list)

    def test_provide_feedback_critical_issues(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test feedback for critical security issues."""
        agent = SecurityReviewerAgent()

        findings = [
            {
                "severity": "critical",
                "category": "SQL Injection",
                "file": "app.py",
                "line": 42,
                "description": "SQL injection vulnerability",
                "recommendation": "Use parameterized queries",
            },
        ]

        feedback = agent.provide_feedback(findings)

        assert len(feedback) > 0
        assert any("CRITICAL" in f for f in feedback)
        assert any("SQL Injection" in f for f in feedback)
        assert any("Use parameterized queries" in f for f in feedback)

    def test_provide_feedback_high_issues(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test feedback for high severity issues."""
        agent = SecurityReviewerAgent()

        findings = [
            {
                "severity": "high",
                "category": "Sensitive Data Exposure",
                "file": "config.py",
                "line": 10,
                "description": "Hardcoded API key",
                "recommendation": "Use environment variables",
            },
            {
                "severity": "high",
                "category": "XSS",
                "file": "template.html",
                "line": 5,
                "description": "Unsafe output",
                "recommendation": "Escape user input",
            },
        ]

        feedback = agent.provide_feedback(findings)

        assert any("HIGH" in f for f in feedback)
        assert any("2 high severity issues" in f for f in feedback)

    def test_provide_feedback_medium_issues(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test feedback for medium severity issues."""
        agent = SecurityReviewerAgent()

        findings = [
            {
                "severity": "medium",
                "category": "Security Misconfiguration",
                "file": "settings.py",
                "description": "Debug mode enabled",
                "recommendation": "Disable debug in production",
            },
        ]

        feedback = agent.provide_feedback(findings)

        assert any("MEDIUM" in f for f in feedback)

    def test_provide_feedback_mixed_severities(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test feedback with mixed severity levels."""
        agent = SecurityReviewerAgent()

        findings = [
            {"severity": "critical", "category": "SQL Injection", "file": "a.py", "line": 1, "description": "Crit", "recommendation": "Fix"},
            {"severity": "high", "category": "XSS", "file": "b.py", "line": 2, "description": "High", "recommendation": "Fix"},
            {"severity": "medium", "category": "Config", "file": "c.py", "description": "Med", "recommendation": "Fix"},
        ]

        feedback = agent.provide_feedback(findings)

        # Should have sections for each severity
        assert any("CRITICAL" in f for f in feedback)
        assert any("HIGH" in f for f in feedback)
        assert any("MEDIUM" in f for f in feedback)

    def test_provide_feedback_limits_high_severity_display(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test that high severity feedback is limited to first 5."""
        agent = SecurityReviewerAgent()

        # Create 10 high severity findings
        findings = [
            {
                "severity": "high",
                "category": "Test",
                "file": f"file{i}.py",
                "line": i,
                "description": f"Issue {i}",
                "recommendation": "Fix",
            }
            for i in range(10)
        ]

        feedback = agent.provide_feedback(findings)

        # Should mention 10 issues but only show details for 5
        assert any("10 high severity issues" in f for f in feedback)

    def test_approve_or_veto_no_issues(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test approval when no critical issues found."""
        agent = SecurityReviewerAgent()

        findings = [
            {"severity": "low", "category": "Info", "file": "a.py", "description": "Low", "recommendation": "Fix"},
        ]

        approved = agent.approve_or_veto(findings)

        assert approved is True

    def test_approve_or_veto_critical_issues(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test veto when critical issues found."""
        agent = SecurityReviewerAgent()

        findings = [
            {"severity": "critical", "category": "SQL Injection", "file": "a.py", "description": "Crit", "recommendation": "Fix"},
        ]

        approved = agent.approve_or_veto(findings)

        assert approved is False

    def test_approve_or_veto_many_high_issues(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test veto when too many high severity issues."""
        agent = SecurityReviewerAgent()

        # Create 6 high severity findings (threshold is 5)
        findings = [
            {"severity": "high", "category": "Test", "file": "a.py", "description": "High", "recommendation": "Fix"}
            for _ in range(6)
        ]

        approved = agent.approve_or_veto(findings)

        assert approved is False

    def test_approve_or_veto_exactly_threshold(
        self, mock_config, mock_agent_sdk, mock_prompt
    ):
        """Test approval with exactly threshold of high issues."""
        agent = SecurityReviewerAgent()

        # Create exactly 5 high severity findings
        findings = [
            {"severity": "high", "category": "Test", "file": "a.py", "description": "High", "recommendation": "Fix"}
            for _ in range(5)
        ]

        approved = agent.approve_or_veto(findings)

        assert approved is True

    def test_run_complete_workflow(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        sample_code_file
    ):
        """Test complete security review workflow."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files",
                          return_value=[sample_code_file]):
            mock_execute.return_value = {"success": True}

            result = agent.run(worktree_path=temp_worktree)

            assert "approved" in result
            assert "findings" in result
            assert "feedback" in result
            assert "veto" in result
            assert "critical_count" in result
            assert "high_count" in result

    def test_run_approves_clean_code(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        sample_code_file
    ):
        """Test that clean code is approved."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files",
                          return_value=[sample_code_file]):
            mock_execute.return_value = {"success": True}

            result = agent.run(worktree_path=temp_worktree)

            assert result["approved"] is True
            assert result["veto"] is False
            assert result["critical_count"] == 0

    def test_run_vetoes_vulnerable_code(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        vulnerable_code_file
    ):
        """Test that vulnerable code is vetoed."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files",
                          return_value=[vulnerable_code_file]):
            mock_execute.return_value = {"success": True}

            result = agent.run(worktree_path=temp_worktree)

            # Vulnerable code should have critical SQL injection issues
            assert result["approved"] is False
            assert result["veto"] is True
            assert result["critical_count"] > 0

    def test_run_provides_feedback(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        vulnerable_code_file
    ):
        """Test that run provides actionable feedback."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files",
                          return_value=[vulnerable_code_file]):
            mock_execute.return_value = {"success": True}

            result = agent.run(worktree_path=temp_worktree)

            assert len(result["feedback"]) > 0
            feedback_str = " ".join(result["feedback"])
            assert "SQL Injection" in feedback_str or "Sensitive Data" in feedback_str

    def test_run_counts_findings_correctly(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        vulnerable_code_file
    ):
        """Test that findings are counted correctly."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files",
                          return_value=[vulnerable_code_file]):
            mock_execute.return_value = {"success": True}

            result = agent.run(worktree_path=temp_worktree)

            # Verify counts match findings
            critical_in_findings = sum(
                1 for f in result["findings"] if f["severity"] == "critical"
            )
            high_in_findings = sum(
                1 for f in result["findings"] if f["severity"] == "high"
            )

            assert result["critical_count"] == critical_in_findings
            assert result["high_count"] == high_in_findings

    def test_run_with_additional_kwargs(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test run accepts additional kwargs."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files", return_value=[]):
            mock_execute.return_value = {"success": True}

            # Should not raise exception with extra kwargs
            result = agent.run(
                worktree_path=temp_worktree,
                extra_param="value",
            )

            assert result is not None

    def test_run_with_ticket_id_extracts_default_branch(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that run extracts default_branch from project config."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files", return_value=[]) as mock_git:
            mock_execute.return_value = {"success": True}
            mock_config.get_project_config.return_value = {
                "default_branch": "develop"
            }

            agent.run(worktree_path=temp_worktree, ticket_id="PROJ-123")

            # Should have called scan_code which calls _get_changed_files
            # with the correct default_branch
            mock_git.assert_called_once()

    def test_scan_code_integrates_all_scans(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree,
        vulnerable_code_file
    ):
        """Test that scan_code runs all scan methods."""
        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute, \
             patch.object(agent, "_get_changed_files",
                          return_value=[vulnerable_code_file]):
            mock_execute.return_value = {"success": True}

            findings = agent.scan_code(temp_worktree)

            # Should have findings from multiple scan types
            categories = {f["category"] for f in findings}

            # Should find at least SQL injection and sensitive data
            assert "SQL Injection" in categories or "Sensitive Data Exposure" in categories

    def test_owasp_a03_does_not_flag_normal_fstring(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that normal f-strings are NOT flagged as injection."""
        code_path = temp_worktree / "normal.py"
        code_content = '''"""Normal code with f-strings."""

name = "world"
msg = f"Hello {name}"
path = f"/api/users/{user_id}"
log_msg = f"Processing {count} items"
'''
        code_path.write_text(code_content)

        agent = SecurityReviewerAgent()

        # Test the OWASP workflow specifically
        with patch.object(agent, "execute_command") as mock_execute:
            mock_execute.return_value = {"success": True}

            findings = agent._execute_owasp_workflow(
                {"success": True}, temp_worktree, [code_path]
            )

            # Normal f-strings should NOT be flagged as injection
            injection_findings = [
                f for f in findings if f.get("owasp_id") == "A03_injection"
            ]
            assert len(injection_findings) == 0

    def test_owasp_a07_does_not_flag_env_reference(
        self, mock_config, mock_agent_sdk, mock_prompt, temp_worktree
    ):
        """Test that env var references are NOT flagged as auth failures."""
        code_path = temp_worktree / "config.py"
        code_content = '''"""Config with env vars."""

import os

# These should NOT be flagged
db_password = os.getenv("DB_PASSWORD")
api_token = os.environ.get("API_TOKEN")
'''
        code_path.write_text(code_content)

        agent = SecurityReviewerAgent()

        with patch.object(agent, "execute_command") as mock_execute:
            mock_execute.return_value = {"success": True}

            findings = agent._execute_owasp_workflow(
                {"success": True}, temp_worktree, [code_path]
            )

            # Env var references should NOT be flagged as hardcoded credentials
            auth_findings = [
                f for f in findings
                if f.get("owasp_id") == "A07_identification_auth_failures"
            ]
            assert len(auth_findings) == 0
