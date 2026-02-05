"""Security Reviewer Agent - Scans for vulnerabilities with VETO power."""

import logging
from pathlib import Path
from typing import Any, Dict, List

from src.agents.base_agent import ReviewAgent


logger = logging.getLogger(__name__)


class SecurityReviewerAgent(ReviewAgent):
    """Agent that reviews code for security vulnerabilities.

    Uses Claude Sonnet 4.5 for security analysis with VETO power.
    Can block progress if critical security issues are found.
    """

    def __init__(self) -> None:
        """Initialize security reviewer agent."""
        super().__init__(
            agent_name="security_reviewer",
            model="claude-4-5-sonnet",
            temperature=0.1,  # Low temperature for consistent security analysis
            veto_power=True,
        )

        # Load OWASP Top 10 patterns to check
        self.owasp_checks = [
            "SQL Injection",
            "Cross-Site Scripting (XSS)",
            "Broken Authentication",
            "Sensitive Data Exposure",
            "XML External Entities (XXE)",
            "Broken Access Control",
            "Security Misconfiguration",
            "Insecure Deserialization",
            "Using Components with Known Vulnerabilities",
            "Insufficient Logging & Monitoring",
        ]

    def scan_code(self, worktree_path: Path) -> List[Dict[str, Any]]:
        """Scan code for security vulnerabilities.

        Args:
            worktree_path: Path to git worktree

        Returns:
            List of finding dictionaries with:
                - severity: "critical", "high", "medium", "low"
                - category: OWASP category
                - file: File path
                - line: Line number (optional)
                - description: Finding description
                - recommendation: How to fix

        Note:
            Can use the custom "scan-owasp" command for structured scanning.
        """
        logger.info(f"Scanning code for vulnerabilities: {worktree_path}")

        findings = []

        # TODO: Use LLM to perform comprehensive security scan
        # This would:
        # 1. Read all Python files in the worktree
        # 2. Analyze each file for OWASP Top 10 vulnerabilities
        # 3. Check for hardcoded secrets/credentials
        # 4. Verify proper input validation
        # 5. Check for secure defaults

        # Use OWASP scan command for comprehensive scanning
        try:
            result = self.execute_command(
                "scan-owasp",
                {
                    "target_path": str(worktree_path),
                    "scan_types": self.owasp_checks,
                }
            )

            if result.get("success"):
                # Execute the OWASP scan workflow
                logger.info("Executing OWASP scan workflow")
                owasp_findings = self._execute_owasp_workflow(result, worktree_path)
                findings.extend(owasp_findings)
                logger.info(f"OWASP workflow found {len(owasp_findings)} issues")
            else:
                logger.error(f"OWASP scan command validation failed: {result.get('errors')}")

        except Exception as e:
            logger.warning(f"OWASP scan command not available, using fallback: {e}")

        # Simplified scan - check for common patterns
        findings.extend(self._scan_for_secrets(worktree_path))
        findings.extend(self._scan_for_sql_injection(worktree_path))
        findings.extend(self._scan_for_xss(worktree_path))

        # Deduplicate findings by file:line:category
        findings = self._deduplicate_findings(findings)

        logger.info(f"Security scan complete: {len(findings)} findings (after deduplication)")

        return findings

    def _deduplicate_findings(self, findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Deduplicate security findings by file:line:category.

        Args:
            findings: List of security findings

        Returns:
            Deduplicated list of findings
        """
        seen = set()
        unique_findings = []

        for finding in findings:
            # Create hash key from file, line, and category
            key = (
                finding.get("file", ""),
                finding.get("line", 0),
                finding.get("category", ""),
            )

            if key not in seen:
                seen.add(key)
                unique_findings.append(finding)
            else:
                logger.debug(f"Skipping duplicate finding: {key}")

        if len(findings) > len(unique_findings):
            logger.info(f"Removed {len(findings) - len(unique_findings)} duplicate findings")

        return unique_findings

    def _scan_for_secrets(self, worktree_path: Path) -> List[Dict[str, Any]]:
        """Scan for hardcoded secrets.

        Args:
            worktree_path: Path to scan

        Returns:
            List of findings
        """
        findings = []

        secret_patterns = [
            "password =",
            "api_key =",
            "secret =",
            "token =",
            "API_KEY",
            "SECRET_KEY",
        ]

        # Scan Python files
        for py_file in worktree_path.rglob("*.py"):
            if ".venv" in str(py_file) or "venv" in str(py_file):
                continue

            try:
                content = py_file.read_text()
                for i, line in enumerate(content.split("\n"), 1):
                    for pattern in secret_patterns:
                        if pattern.lower() in line.lower() and "=" in line:
                            # Check if it's not a config/env reference
                            if "os.getenv" not in line and "environ" not in line:
                                findings.append({
                                    "severity": "high",
                                    "category": "Sensitive Data Exposure",
                                    "file": str(py_file.relative_to(worktree_path)),
                                    "line": i,
                                    "description": f"Potential hardcoded secret: {line.strip()[:50]}",
                                    "recommendation": "Use environment variables for secrets",
                                })
            except Exception as e:
                logger.warning(f"Could not scan {py_file}: {e}")

        return findings

    def _scan_for_sql_injection(self, worktree_path: Path) -> List[Dict[str, Any]]:
        """Scan for SQL injection vulnerabilities.

        Args:
            worktree_path: Path to scan

        Returns:
            List of findings
        """
        findings = []

        dangerous_patterns = [
            "execute(f\"",  # f-string in SQL
            'execute("SELECT',  # String concatenation in SQL
            ".format(",  # Format in SQL
            "% (",  # String interpolation in SQL
        ]

        for py_file in worktree_path.rglob("*.py"):
            if ".venv" in str(py_file) or "venv" in str(py_file):
                continue

            try:
                content = py_file.read_text()
                for i, line in enumerate(content.split("\n"), 1):
                    for pattern in dangerous_patterns:
                        if pattern in line and ("SELECT" in line or "INSERT" in line or "UPDATE" in line):
                            findings.append({
                                "severity": "critical",
                                "category": "SQL Injection",
                                "file": str(py_file.relative_to(worktree_path)),
                                "line": i,
                                "description": "Potential SQL injection vulnerability",
                                "recommendation": "Use parameterized queries or ORM",
                            })
            except Exception as e:
                logger.warning(f"Could not scan {py_file}: {e}")

        return findings

    def _execute_owasp_workflow(
        self, owasp_command_result: Dict[str, Any], worktree_path: Path
    ) -> List[Dict[str, Any]]:
        """Execute OWASP scan workflow from command definition.

        Args:
            owasp_command_result: Result from execute_command("scan-owasp")
            worktree_path: Path to scan

        Returns:
            List of security findings from OWASP patterns
        """
        import re
        import yaml

        findings = []

        try:
            # Load the full OWASP command YAML to get vulnerability_checks
            commands_dir = Path(__file__).parent.parent.parent / ".agents" / "commands"
            owasp_yaml_path = commands_dir / "security_reviewer" / "scan_owasp.yaml"

            if not owasp_yaml_path.exists():
                logger.warning(f"OWASP YAML not found: {owasp_yaml_path}")
                return findings

            with open(owasp_yaml_path, "r") as f:
                owasp_def = yaml.safe_load(f)

            vulnerability_checks = owasp_def.get("vulnerability_checks", [])
            logger.info(f"Loaded {len(vulnerability_checks)} OWASP vulnerability checks")

            # Scan all Python files
            for py_file in worktree_path.rglob("*.py"):
                if ".venv" in str(py_file) or "venv" in str(py_file):
                    continue

                try:
                    content = py_file.read_text()
                    lines = content.split("\n")

                    # Check each vulnerability pattern
                    for check in vulnerability_checks:
                        check_id = check.get("id", "unknown")
                        check_name = check.get("name", "Unknown Vulnerability")
                        patterns = check.get("patterns", [])

                        for pattern_def in patterns:
                            pattern = pattern_def.get("pattern", "")
                            severity = pattern_def.get("severity", check.get("severity", "MEDIUM"))

                            # Scan each line for the pattern
                            for i, line in enumerate(lines, 1):
                                try:
                                    # Use regex to match pattern
                                    if re.search(pattern, line, re.IGNORECASE):
                                        # Found a match
                                        findings.append({
                                            "severity": severity.lower(),
                                            "category": check_name,
                                            "file": str(py_file.relative_to(worktree_path)),
                                            "line": i,
                                            "description": f"Potential {check_name}: {line.strip()[:60]}",
                                            "recommendation": pattern_def.get("check", "Review for security best practices"),
                                            "owasp_id": check_id,
                                        })
                                except re.error:
                                    # Invalid regex, skip
                                    logger.debug(f"Invalid regex pattern: {pattern}")
                                    continue

                except Exception as e:
                    logger.warning(f"Could not scan {py_file}: {e}")

            logger.info(f"OWASP workflow scan complete: {len(findings)} findings")

        except Exception as e:
            logger.error(f"Error executing OWASP workflow: {e}")

        return findings

    def _scan_for_xss(self, worktree_path: Path) -> List[Dict[str, Any]]:
        """Scan for XSS vulnerabilities.

        Args:
            worktree_path: Path to scan

        Returns:
            List of findings
        """
        findings = []

        # Check HTML templates for unescaped output
        for html_file in worktree_path.rglob("*.html"):
            try:
                content = html_file.read_text()
                for i, line in enumerate(content.split("\n"), 1):
                    # Look for {{ variable }} without |safe filter in Django/Jinja
                    if "{{" in line and "|safe" in line:
                        findings.append({
                            "severity": "high",
                            "category": "Cross-Site Scripting (XSS)",
                            "file": str(html_file.relative_to(worktree_path)),
                            "line": i,
                            "description": "Potentially unsafe HTML output",
                            "recommendation": "Ensure proper escaping or validate |safe usage",
                        })
            except Exception as e:
                logger.warning(f"Could not scan {html_file}: {e}")

        return findings

    def check_vulnerabilities(self, code_files: List[Path]) -> List[Dict[str, Any]]:
        """Check specific code files for vulnerabilities.

        Args:
            code_files: List of file paths to check

        Returns:
            List of findings
        """
        findings: List[Dict[str, Any]] = []

        for file_path in code_files:
            logger.info(f"Checking file: {file_path}")

            # TODO: Use LLM to analyze file for vulnerabilities
            # This would provide more intelligent analysis than pattern matching

        return findings

    def provide_feedback(self, findings: List[Dict[str, Any]]) -> List[str]:
        """Provide actionable feedback for security findings.

        Args:
            findings: List of security findings

        Returns:
            List of feedback strings
        """
        feedback = []

        # Group findings by severity
        critical = [f for f in findings if f["severity"] == "critical"]
        high = [f for f in findings if f["severity"] == "high"]
        medium = [f for f in findings if f["severity"] == "medium"]

        if critical:
            feedback.append(f"🚨 CRITICAL: {len(critical)} critical security issues must be fixed:")
            for finding in critical:
                feedback.append(
                    f"  - {finding['category']} in {finding['file']}:{finding.get('line', '?')} "
                    f"- {finding['description']}"
                )
                feedback.append(f"    Fix: {finding['recommendation']}")

        if high:
            feedback.append(f"\n⚠️  HIGH: {len(high)} high severity issues found:")
            for finding in high[:5]:  # Show first 5
                feedback.append(
                    f"  - {finding['category']} in {finding['file']}:{finding.get('line', '?')}"
                )

        if medium:
            feedback.append(f"\n📋 MEDIUM: {len(medium)} medium severity issues found")

        return feedback

    def approve_or_veto(self, findings: List[Dict[str, Any]]) -> bool:
        """Decide whether to approve or veto the code.

        Args:
            findings: List of security findings

        Returns:
            True if approved (no critical issues), False if vetoed
        """
        critical_count = sum(1 for f in findings if f["severity"] == "critical")

        if critical_count > 0:
            logger.warning(f"VETO: {critical_count} critical security issues found")
            return False

        # Could also veto based on high severity count
        high_count = sum(1 for f in findings if f["severity"] == "high")
        if high_count > 5:  # More than 5 high severity issues
            logger.warning(f"VETO: Too many high severity issues ({high_count})")
            return False

        logger.info("Security review APPROVED: No blocking issues found")
        return True

    def run(  # type: ignore[override]
        self,
        worktree_path: Path,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Run the complete security review workflow.

        Args:
            worktree_path: Path to git worktree
            **kwargs: Additional parameters

        Returns:
            Dictionary with:
                - approved: bool
                - findings: List of security findings
                - feedback: List of feedback strings
                - veto: bool (if critical issues found)
        """
        logger.info(f"Running security review for: {worktree_path}")

        # Extract project key from ticket_id if provided
        ticket_id = kwargs.get("ticket_id")
        if ticket_id and "-" in ticket_id:
            project_key = ticket_id.split("-")[0]
            self.set_project(project_key)

        # Scan code
        findings = self.scan_code(worktree_path)

        # Provide feedback
        feedback = self.provide_feedback(findings)

        # Approve or veto
        approved = self.approve_or_veto(findings)

        return {
            "approved": approved,
            "findings": findings,
            "feedback": feedback,
            "veto": not approved,
            "critical_count": sum(1 for f in findings if f["severity"] == "critical"),
            "high_count": sum(1 for f in findings if f["severity"] == "high"),
        }
