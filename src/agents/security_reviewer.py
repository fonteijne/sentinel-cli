"""Security Reviewer Agent - Scans for vulnerabilities with VETO power."""

import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agents.base_agent import ReviewAgent


logger = logging.getLogger(__name__)

# File extensions to scan per category
CODE_EXTENSIONS = {".py", ".php", ".module", ".theme", ".inc", ".install", ".profile"}
TEMPLATE_EXTENSIONS = {".html", ".twig", ".tpl.php"}
CONFIG_EXTENSIONS = {".yaml", ".yml", ".json", ".env", ".ini", ".conf"}
ALL_SCANNABLE = CODE_EXTENSIONS | TEMPLATE_EXTENSIONS | CONFIG_EXTENSIONS

# Directories to always skip
SKIP_DIRS = {".venv", "venv", "node_modules", "vendor", "__pycache__", ".git"}


class SecurityReviewerAgent(ReviewAgent):
    """Agent that reviews code for security vulnerabilities.

    Uses Claude Sonnet 4.5 for security analysis with VETO power.
    Can block progress if critical security issues are found.
    """

    def __init__(self) -> None:
        """Initialize security reviewer agent."""
        super().__init__(
            agent_name="security_reviewer",
            model="claude-4-5-haiku",
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

    def _get_changed_files(
        self, worktree_path: Path, default_branch: str = "main"
    ) -> Optional[List[Path]]:
        """Get list of files changed on the feature branch.

        Uses git merge-base to find the branch point and git diff to get
        only files that were added, copied, modified, or renamed.

        Args:
            worktree_path: Path to the git worktree
            default_branch: Default branch name to diff against

        Returns:
            List of changed file paths, or None if git operations fail
            (caller should fall back to scanning all files)
        """
        try:
            # Find the merge base between HEAD and the default branch
            # Try origin/{branch} first, then just {branch}
            merge_base = None
            for ref in [f"origin/{default_branch}", default_branch]:
                result = subprocess.run(
                    ["git", "merge-base", "HEAD", ref],
                    cwd=worktree_path,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    merge_base = result.stdout.strip()
                    break

            if not merge_base:
                logger.warning(
                    f"Could not find merge-base with {default_branch}, "
                    "falling back to full scan"
                )
                return None

            # Get changed files (Added, Copied, Modified, Renamed)
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=ACMR",
                 f"{merge_base}..HEAD"],
                cwd=worktree_path,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                logger.warning(f"git diff failed: {result.stderr}")
                return None

            files = []
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if not line:
                    continue
                file_path = worktree_path / line
                if file_path.exists():
                    files.append(file_path)

            logger.info(
                f"Git diff found {len(files)} changed files "
                f"(base: {merge_base[:8]}..HEAD)"
            )
            return files

        except FileNotFoundError:
            logger.warning("git not found, falling back to full scan")
            return None
        except Exception as e:
            logger.warning(f"Error getting changed files: {e}")
            return None

    def _get_all_scannable_files(self, worktree_path: Path) -> List[Path]:
        """Fallback: get all scannable files in the worktree.

        Args:
            worktree_path: Path to scan

        Returns:
            List of file paths with scannable extensions
        """
        files = []
        for f in worktree_path.rglob("*"):
            if any(skip in f.parts for skip in SKIP_DIRS):
                continue
            if f.suffix in ALL_SCANNABLE and f.is_file():
                files.append(f)
        return files

    def scan_code(
        self, worktree_path: Path, default_branch: str = "main"
    ) -> List[Dict[str, Any]]:
        """Scan changed code for security vulnerabilities.

        Only scans files changed on the feature branch relative to the
        default branch. Falls back to full scan if git operations fail.

        Args:
            worktree_path: Path to git worktree
            default_branch: Branch to diff against

        Returns:
            List of finding dictionaries
        """
        logger.info(f"Scanning code for vulnerabilities: {worktree_path}")

        # Scope to changed files only
        changed_files = self._get_changed_files(worktree_path, default_branch)
        if changed_files is not None:
            if not changed_files:
                logger.info("No changed files detected — skipping security scan")
                return []
            files = changed_files
        else:
            # Fallback: scan all scannable files
            files = self._get_all_scannable_files(worktree_path)

        logger.info(f"Scanning {len(files)} files for vulnerabilities")

        findings: List[Dict[str, Any]] = []

        # OWASP pattern scanning
        try:
            result = self.execute_command(
                "scan-owasp",
                {
                    "target_path": str(worktree_path),
                    "scan_types": self.owasp_checks,
                }
            )

            if result.get("success"):
                logger.info("Executing OWASP scan workflow")
                owasp_findings = self._execute_owasp_workflow(
                    result, worktree_path, files
                )
                findings.extend(owasp_findings)
                logger.info(f"OWASP workflow found {len(owasp_findings)} issues")
            else:
                logger.error(
                    f"OWASP scan command validation failed: {result.get('errors')}"
                )

        except Exception as e:
            logger.warning(f"OWASP scan command not available, using fallback: {e}")

        # Targeted scans on changed files
        secrets_findings = self._scan_for_secrets(worktree_path, files)
        logger.info(f"Secrets scan found {len(secrets_findings)} issues")
        findings.extend(secrets_findings)

        sqli_findings = self._scan_for_sql_injection(worktree_path, files)
        logger.info(f"SQL injection scan found {len(sqli_findings)} issues")
        findings.extend(sqli_findings)

        xss_findings = self._scan_for_xss(worktree_path, files)
        logger.info(f"XSS scan found {len(xss_findings)} issues")
        findings.extend(xss_findings)

        # Deduplicate findings by file:line:category
        findings = self._deduplicate_findings(findings)

        severity_counts = {}
        for f in findings:
            sev = f.get("severity", "unknown")
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        severity_summary = ", ".join(
            f"{count} {sev}" for sev, count in sorted(severity_counts.items())
        )

        logger.info(
            f"Security scan complete: {len(findings)} findings "
            f"(after deduplication) [{severity_summary}]"
        )

        return findings

    def _deduplicate_findings(
        self, findings: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Deduplicate security findings by file:line:category.

        Args:
            findings: List of security findings

        Returns:
            Deduplicated list of findings
        """
        seen = set()
        unique_findings = []

        for finding in findings:
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
            logger.info(
                f"Removed {len(findings) - len(unique_findings)} duplicate findings"
            )

        return unique_findings

    def _scan_for_secrets(
        self, worktree_path: Path, files: List[Path]
    ) -> List[Dict[str, Any]]:
        """Scan for hardcoded secrets in the given files.

        Args:
            worktree_path: Worktree root for relative path calculation
            files: List of files to scan

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

        # Secrets can be in any file type
        for file_path in files:
            if any(skip in file_path.parts for skip in SKIP_DIRS):
                continue

            try:
                content = file_path.read_text()
                for i, line in enumerate(content.split("\n"), 1):
                    for pattern in secret_patterns:
                        if pattern.lower() in line.lower() and "=" in line:
                            # Skip env var references (Python and PHP)
                            if any(safe in line for safe in [
                                "os.getenv", "environ", "getenv(",
                                "$_ENV", "$_SERVER", "env(",
                            ]):
                                continue
                            findings.append({
                                "severity": "high",
                                "category": "Sensitive Data Exposure",
                                "file": str(file_path.relative_to(worktree_path)),
                                "line": i,
                                "description": f"Potential hardcoded secret: {line.strip()[:50]}",
                                "recommendation": "Use environment variables for secrets",
                            })
            except Exception as e:
                logger.warning(f"Could not scan {file_path}: {e}")

        return findings

    def _scan_for_sql_injection(
        self, worktree_path: Path, files: List[Path]
    ) -> List[Dict[str, Any]]:
        """Scan for SQL injection vulnerabilities.

        Supports both Python and PHP/Drupal patterns.

        Args:
            worktree_path: Worktree root for relative path calculation
            files: List of files to scan

        Returns:
            List of findings
        """
        findings = []

        # Python SQL injection patterns
        py_dangerous = [
            ("execute(f\"", "f-string in SQL execute"),
            ('execute("SELECT', "String concatenation in SQL execute"),
            (".format(", "format() in SQL context"),
        ]

        # PHP/Drupal SQL injection patterns
        php_dangerous = [
            ("db_query(", "$", "Drupal db_query with variable interpolation"),
            ("->query(", ".$", "PDO query with string concatenation"),
            ("mysql_query(", None, "Deprecated mysql_query() usage"),
        ]

        for file_path in files:
            if any(skip in file_path.parts for skip in SKIP_DIRS):
                continue

            suffix = file_path.suffix
            if suffix not in CODE_EXTENSIONS:
                continue

            try:
                content = file_path.read_text()
                rel_path = str(file_path.relative_to(worktree_path))

                for i, line in enumerate(content.split("\n"), 1):
                    # Python patterns
                    if suffix == ".py":
                        for pattern, desc in py_dangerous:
                            if pattern in line and any(
                                kw in line
                                for kw in ["SELECT", "INSERT", "UPDATE", "DELETE"]
                            ):
                                findings.append({
                                    "severity": "critical",
                                    "category": "SQL Injection",
                                    "file": rel_path,
                                    "line": i,
                                    "description": f"Potential SQL injection ({desc})",
                                    "recommendation": "Use parameterized queries or ORM",
                                })

                    # PHP/Drupal patterns
                    if suffix in {".php", ".module", ".theme", ".inc",
                                  ".install", ".profile"}:
                        for pattern_info in php_dangerous:
                            func_pattern, var_marker, desc = pattern_info
                            if func_pattern in line:
                                if var_marker is None:
                                    # Always flag (e.g. mysql_query)
                                    findings.append({
                                        "severity": "critical",
                                        "category": "SQL Injection",
                                        "file": rel_path,
                                        "line": i,
                                        "description": f"Potential SQL injection ({desc})",
                                        "recommendation": "Use parameterized queries or Drupal database API with placeholders",
                                    })
                                elif var_marker in line:
                                    findings.append({
                                        "severity": "critical",
                                        "category": "SQL Injection",
                                        "file": rel_path,
                                        "line": i,
                                        "description": f"Potential SQL injection ({desc})",
                                        "recommendation": "Use parameterized queries or Drupal database API with placeholders",
                                    })

            except Exception as e:
                logger.warning(f"Could not scan {file_path}: {e}")

        return findings

    def _execute_owasp_workflow(
        self,
        owasp_command_result: Dict[str, Any],
        worktree_path: Path,
        files: List[Path],
    ) -> List[Dict[str, Any]]:
        """Execute OWASP scan workflow on the given files.

        Args:
            owasp_command_result: Result from execute_command("scan-owasp")
            worktree_path: Worktree root for relative paths
            files: List of files to scan

        Returns:
            List of security findings from OWASP patterns
        """
        import re
        import yaml

        findings = []

        try:
            commands_dir = Path(__file__).parent.parent.parent / "commands"
            owasp_yaml_path = commands_dir / "security_reviewer" / "scan-owasp.yaml"

            if not owasp_yaml_path.exists():
                logger.warning(f"OWASP YAML not found: {owasp_yaml_path}")
                return findings

            with open(owasp_yaml_path, "r") as f:
                owasp_def = yaml.safe_load(f)

            vulnerability_checks = owasp_def.get("vulnerability_checks", [])
            logger.info(f"Loaded {len(vulnerability_checks)} OWASP vulnerability checks")

            # Scan only the provided files with code/config extensions
            scannable = [f for f in files if f.suffix in (CODE_EXTENSIONS | CONFIG_EXTENSIONS)]

            for scan_file in scannable:
                if any(skip in scan_file.parts for skip in SKIP_DIRS):
                    continue

                try:
                    content = scan_file.read_text()
                    lines = content.split("\n")
                    rel_path = str(scan_file.relative_to(worktree_path))

                    for check in vulnerability_checks:
                        check_id = check.get("id", "unknown")
                        check_name = check.get("name", "Unknown Vulnerability")
                        patterns = check.get("patterns", [])

                        for pattern_def in patterns:
                            pattern = pattern_def.get("pattern", "")
                            severity = pattern_def.get(
                                "severity", check.get("severity", "MEDIUM")
                            )

                            for i, line in enumerate(lines, 1):
                                try:
                                    if re.search(pattern, line, re.IGNORECASE):
                                        findings.append({
                                            "severity": severity.lower(),
                                            "category": check_name,
                                            "file": rel_path,
                                            "line": i,
                                            "description": f"Potential {check_name}: {line.strip()[:60]}",
                                            "recommendation": pattern_def.get(
                                                "check",
                                                "Review for security best practices",
                                            ),
                                            "owasp_id": check_id,
                                        })
                                except re.error:
                                    logger.debug(f"Invalid regex pattern: {pattern}")
                                    continue

                except Exception as e:
                    logger.warning(f"Could not scan {scan_file}: {e}")

            logger.info(f"OWASP workflow scan complete: {len(findings)} findings")

        except Exception as e:
            logger.error(f"Error executing OWASP workflow: {e}")

        return findings

    def _scan_for_xss(
        self, worktree_path: Path, files: List[Path]
    ) -> List[Dict[str, Any]]:
        """Scan for XSS vulnerabilities.

        Supports HTML/Twig templates and PHP/Drupal code.

        Args:
            worktree_path: Worktree root for relative path calculation
            files: List of files to scan

        Returns:
            List of findings
        """
        findings = []

        template_files = [f for f in files if f.suffix in TEMPLATE_EXTENSIONS]
        php_files = [
            f for f in files
            if f.suffix in {".php", ".module", ".theme", ".inc",
                            ".install", ".profile"}
        ]

        # Check HTML/Twig templates for unescaped output
        for tmpl_file in template_files:
            try:
                content = tmpl_file.read_text()
                rel_path = str(tmpl_file.relative_to(worktree_path))
                for i, line in enumerate(content.split("\n"), 1):
                    # Django/Jinja |safe filter
                    if "{{" in line and "|safe" in line:
                        findings.append({
                            "severity": "high",
                            "category": "Cross-Site Scripting (XSS)",
                            "file": rel_path,
                            "line": i,
                            "description": "Potentially unsafe HTML output (|safe filter)",
                            "recommendation": "Ensure proper escaping or validate |safe usage",
                        })
                    # Twig |raw filter
                    if "|raw" in line:
                        findings.append({
                            "severity": "high",
                            "category": "Cross-Site Scripting (XSS)",
                            "file": rel_path,
                            "line": i,
                            "description": "Twig |raw filter disables auto-escaping",
                            "recommendation": "Use |escape or remove |raw unless output is trusted",
                        })
            except Exception as e:
                logger.warning(f"Could not scan {tmpl_file}: {e}")

        # Check PHP files for direct output of user input
        for php_file in php_files:
            try:
                content = php_file.read_text()
                rel_path = str(php_file.relative_to(worktree_path))
                for i, line in enumerate(content.split("\n"), 1):
                    # Direct echo/print of superglobals
                    if any(
                        f"{output} ${superglobal}" in line.replace(" ", "")
                        or f"{output} ${superglobal}" in line
                        for output in ["echo", "print"]
                        for superglobal in ["_GET", "_POST", "_REQUEST"]
                    ):
                        findings.append({
                            "severity": "critical",
                            "category": "Cross-Site Scripting (XSS)",
                            "file": rel_path,
                            "line": i,
                            "description": "Direct output of user input superglobal",
                            "recommendation": "Use htmlspecialchars() or Drupal's Xss::filter()",
                        })
                    # Drupal #markup with variable
                    if "'#markup'" in line and "$" in line:
                        findings.append({
                            "severity": "high",
                            "category": "Cross-Site Scripting (XSS)",
                            "file": rel_path,
                            "line": i,
                            "description": "Drupal #markup render element with variable",
                            "recommendation": "Use #plain_text or Xss::filter() for user input",
                        })
            except Exception as e:
                logger.warning(f"Could not scan {php_file}: {e}")

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
            feedback.append(
                f"🚨 CRITICAL: {len(critical)} critical security issues must be fixed:"
            )
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
            **kwargs: Additional parameters (ticket_id, etc.)

        Returns:
            Dictionary with:
                - approved: bool
                - findings: List of security findings
                - feedback: List of feedback strings
                - veto: bool (if critical issues found)
        """
        logger.info(f"Running security review for: {worktree_path}")

        # Extract project key and default branch from config
        ticket_id = kwargs.get("ticket_id")
        default_branch = "main"
        if ticket_id and "-" in ticket_id:
            project_key = ticket_id.split("-")[0]
            self.set_project(project_key)
            try:
                project_config = self.config.get_project_config(project_key)
                if project_config:
                    default_branch = project_config.get(
                        "default_branch", "main"
                    )
            except Exception:
                pass  # Use default

        # Scan code (scoped to changed files)
        findings = self.scan_code(worktree_path, default_branch=default_branch)

        # Provide feedback
        feedback = self.provide_feedback(findings)

        # Approve or veto
        approved = self.approve_or_veto(findings)

        return {
            "approved": approved,
            "findings": findings,
            "feedback": feedback,
            "veto": not approved,
            "critical_count": sum(
                1 for f in findings if f["severity"] == "critical"
            ),
            "high_count": sum(
                1 for f in findings if f["severity"] == "high"
            ),
        }
