"""
Security Agent for Amaze on Work.

Acts as a security gatekeeper to ensure no security vulnerabilities are introduced
or left unaddressed in the fix. Runs bandit, safety, and LLM-based analysis.
"""

import re
import json
import logging
import tempfile
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from src.agents.base import BaseAgent, AgentResponse
from src.agents.state import PipelineState, AgentType

logger = logging.getLogger(__name__)


@dataclass
class SecurityIssue:
    severity: str
    category: str
    title: str
    description: str
    file_path: str
    line_number: Optional[int]
    code_snippet: Optional[str]
    recommendation: str
    cwe_id: Optional[str] = None
    owasp_category: Optional[str] = None
    soc2_control: Optional[str] = None
    cvss_score: Optional[float] = None
    source: str = "custom"
    # Enhanced location fields
    function_name: Optional[str] = None
    class_name: Optional[str] = None
    end_line: Optional[int] = None


OWASP_TOP_10 = {
    "A01": "Broken Access Control",
    "A02": "Cryptographic Failures",
    "A03": "Injection",
    "A04": "Insecure Design",
    "A05": "Security Misconfiguration",
    "A06": "Vulnerable Components",
    "A07": "Auth Failures",
    "A08": "Data Integrity Failures",
    "A09": "Logging Failures",
    "A10": "SSRF",
}

SOC2_CONTROLS = {
    "CC6.1": "Logical Access Controls",
    "CC6.6": "Boundary Protection",
    "CC6.7": "Input Validation",
    "CC7.2": "System Monitoring",
    "CC8.1": "Change Management",
    "PI1.4": "Data Protection",
}

BANDIT_CWE_MAP = {
    "B101": "CWE-703", "B102": "CWE-78", "B103": "CWE-732", "B104": "CWE-284",
    "B105": "CWE-798", "B106": "CWE-798", "B107": "CWE-798", "B108": "CWE-377",
    "B110": "CWE-78",  "B112": "CWE-78",  "B301": "CWE-502", "B302": "CWE-78",
    "B303": "CWE-327", "B304": "CWE-327", "B305": "CWE-327", "B306": "CWE-295",
    "B307": "CWE-94",  "B308": "CWE-611", "B310": "CWE-20",  "B311": "CWE-330",
    "B312": "CWE-20",  "B324": "CWE-327", "B501": "CWE-295", "B502": "CWE-295",
    "B503": "CWE-295", "B504": "CWE-295", "B505": "CWE-327", "B506": "CWE-20",
    "B601": "CWE-78",  "B602": "CWE-78",  "B603": "CWE-78",  "B604": "CWE-78",
    "B605": "CWE-78",  "B606": "CWE-78",  "B607": "CWE-78",  "B608": "CWE-89",
    "B609": "CWE-78",  "B701": "CWE-94",  "B702": "CWE-79",  "B703": "CWE-79",
}

BANDIT_OWASP_MAP = {
    "B101": "A04", "B102": "A03", "B103": "A01", "B104": "A01",
    "B105": "A02", "B106": "A02", "B107": "A02", "B108": "A01",
    "B301": "A08", "B302": "A03", "B303": "A02", "B304": "A02",
    "B305": "A02", "B306": "A02", "B307": "A03", "B308": "A03",
    "B324": "A02", "B501": "A02", "B502": "A02", "B503": "A02",
    "B601": "A03", "B602": "A03", "B603": "A03", "B604": "A03",
    "B605": "A03", "B606": "A03", "B607": "A03", "B608": "A03",
    "B701": "A03", "B702": "A03", "B703": "A03",
}


class SecurityAgent(BaseAgent):
    """
    Security gatekeeper ensuring no vulnerabilities are introduced by the fix.

    Runs a multi-layered security review:
    1. Bandit static analysis (if installed)
    2. Safety dependency check (if installed)
    3. Custom pattern scanning
    4. LLM-based deep security analysis (STRIDE / OWASP methodology)
    """
    agent_type = AgentType.SECURITY

    # Patterns indicating value comes from config/environment (NOT hardcoded)
    SAFE_CONFIG_PATTERNS = [
        r'config\.\w+', r'settings\.\w+', r'cfg\.\w+', r'conf\.\w+',
        r'env\.\w+', r'ENV\.\w+', r'os\.getenv\s*\(',
        r'os\.environ\s*\[', r'os\.environ\.get\s*\(', r'getenv\s*\(',
        r'environ\s*\[', r'\.env\b', r'load_dotenv', r'dotenv',
    ]

    SAFE_CODE_PATTERNS = {
        "sql_injection": [
            r"execute\s*\([^,]+,\s*\(", r"execute\s*\([^,]+,\s*\[",
            r"=\s*\?", r"=\s*%s", r"=\s*:\w+", r"executemany",
        ],
        "weak_randomness": [
            r"secrets\.", r"secrets\s*import", r"SystemRandom",
            r"os\.urandom", r"token_bytes", r"token_hex", r"token_urlsafe",
        ],
        "xml_external_entity": [
            r"defusedxml", r"forbid_dtd\s*=\s*True",
            r"resolve_entities\s*=\s*False",
        ],
        "insecure_deserialization": [
            r"json\.loads?", r"yaml\.safe_load", r"SafeLoader", r"safe_load",
        ],
        "code_injection": [r"ast\.literal_eval", r"literal_eval"],
        "weak_hash": [
            r"sha256", r"sha384", r"sha512", r"blake2",
            r"bcrypt", r"argon2", r"scrypt", r"pbkdf2",
            r"usedforsecurity\s*=\s*False",
        ],
    }

    def __init__(self, llm_client, docker_sandbox=None):
        super().__init__(llm_client)
        self._docker_sandbox = docker_sandbox
        self._bandit_available = self._check_tool("bandit")
        self._safety_available = self._check_tool("safety")
        self._safe_pattern_regex = re.compile(
            '|'.join(self.SAFE_CONFIG_PATTERNS), re.IGNORECASE
        )

    def _check_tool(self, tool_name: str) -> bool:
        """Check if a CLI security tool is available."""
        try:
            result = subprocess.run(  # nosec B603 B607
                [tool_name, "--version"], capture_output=True, timeout=5
            )
            return result.returncode == 0
        except Exception:
            return False

    def _run_docker_scan(self, repo_path: str, language: str = "python", service_dir: str = "") -> list:
        """Run security scanning via Docker sandbox (bandit + pip-audit).

        Returns a list of SecurityIssue objects from the Docker scan.
        """
        if not self._docker_sandbox:
            return []

        try:
            scan_result = self._docker_sandbox.run_security_scan(
                repo_path=repo_path,
                language=language,
                service_dir=service_dir,
            )
        except Exception as e:
            logger.error(f"[Security] Docker security scan failed: {e}")
            return []

        issues = []
        # Convert bandit results
        for item in scan_result.get("bandit", {}).get("issues", []):
            test_id = item.get("test_id", "")
            issues.append(SecurityIssue(
                severity=item.get("severity", "medium"),
                category=f"bandit_{test_id}",
                title=item.get("title", "Security Issue"),
                description=item.get("title", ""),
                file_path=item.get("file_path", ""),
                line_number=item.get("line_number"),
                code_snippet=item.get("code"),
                recommendation=f"See: {item.get('more_info', 'bandit documentation')}",
                cwe_id=BANDIT_CWE_MAP.get(test_id),
                owasp_category=BANDIT_OWASP_MAP.get(test_id),
                source="bandit-docker",
            ))

        # Convert dependency audit results
        for item in scan_result.get("dep_audit", {}).get("issues", []):
            issues.append(SecurityIssue(
                severity=item.get("severity", "high") if item.get("severity") in ("critical", "high", "medium", "low") else "high",
                category="vulnerable_dependency",
                title=item.get("title", "Vulnerable dependency"),
                description=item.get("description", "")[:200],
                file_path="requirements.txt",
                line_number=None,
                code_snippet=None,
                recommendation="Update to a patched version",
                cwe_id="CWE-1104",
                owasp_category="A06",
                source=item.get("source", "pip-audit-docker"),
            ))

        logger.info(f"[Security] Docker scan found {len(issues)} issues")
        return issues

    def _normalize_category(self, category: str) -> str:
        cat = category.lower().replace(" ", "_").replace("-", "_")
        bandit_map = {
            "bandit_b608": "sql_injection", "bandit_b311": "weak_randomness",
            "bandit_b314": "xml_external_entity", "bandit_b405": "xml_external_entity",
            "bandit_b301": "insecure_deserialization", "bandit_b307": "code_injection",
            "bandit_b324": "weak_hash",
        }
        return bandit_map.get(cat, cat)

    def _is_safe_code_pattern(self, issue: SecurityIssue) -> bool:
        snippet = issue.code_snippet or ""
        if not snippet:
            return False
        category = self._normalize_category(issue.category)
        if category in self.SAFE_CODE_PATTERNS:
            for pattern in self.SAFE_CODE_PATTERNS[category]:
                if re.search(pattern, snippet, re.IGNORECASE):
                    return True
        return False

    def _is_false_positive_config(self, issue: SecurityIssue) -> bool:
        snippet = issue.code_snippet or ""
        if self._safe_pattern_regex.search(snippet):
            title_lower = (issue.title or "").lower()
            if any(kw in title_lower for kw in [
                'hardcoded', 'hardcode', 'secret', 'credential', 'password', 'api key', 'apikey'
            ]):
                return True
        return False

    def _should_filter_issue(self, issue: SecurityIssue) -> bool:
        return self._is_false_positive_config(issue) or self._is_safe_code_pattern(issue)

    def _run_bandit(self, repo_path: str) -> list:
        """Run bandit static analysis on the codebase."""
        if not self._bandit_available:
            return []
        issues = []
        try:
            result = subprocess.run(  # nosec B603 B607
                ["bandit", "-r", repo_path, "-f", "json", "-q"],
                capture_output=True, text=True, timeout=300
            )
            if result.stdout:
                data = json.loads(result.stdout)
                for item in data.get("results", []):
                    test_id = item.get("test_id", "")
                    issues.append(SecurityIssue(
                        severity=item.get("issue_severity", "MEDIUM").lower(),
                        category=f"bandit_{test_id}",
                        title=item.get("issue_text", "Security Issue"),
                        description=item.get("issue_text", ""),
                        file_path=item.get("filename", "").replace(repo_path, "").lstrip("/\\"),
                        line_number=item.get("line_number"),
                        code_snippet=(item.get("code", "")[:100] if item.get("code") else None),
                        recommendation=f"See: {item.get('more_info', 'bandit documentation')}",
                        cwe_id=BANDIT_CWE_MAP.get(test_id),
                        owasp_category=BANDIT_OWASP_MAP.get(test_id),
                        source="bandit"
                    ))
        except Exception as e:
            logger.error(f"Bandit analysis failed: {e}")
        return issues

    def _run_safety(self, repo_path: str) -> list:
        """Run safety check for vulnerable dependencies."""
        if not self._safety_available:
            return []
        issues = []
        requirements_path = Path(repo_path) / "requirements.txt"
        if not requirements_path.exists():
            return []
        try:
            result = subprocess.run(  # nosec B603 B607
                ["safety", "check", "-r", str(requirements_path), "--json"],
                capture_output=True, text=True, timeout=60
            )
            if result.stdout:
                try:
                    data = json.loads(result.stdout)
                    vulnerabilities = data if isinstance(data, list) else data.get("vulnerabilities", [])
                    for vuln in vulnerabilities:
                        if isinstance(vuln, dict):
                            pkg = vuln.get("package_name", vuln.get("name", "unknown"))
                            vuln_id = vuln.get("vulnerability_id", vuln.get("id", ""))
                            desc = vuln.get("advisory", vuln.get("description", ""))
                        else:
                            pkg, vuln_id, desc = str(vuln), "", ""
                        issues.append(SecurityIssue(
                            severity="high", category="vulnerable_dependency",
                            title=f"Vulnerable package: {pkg}",
                            description=str(desc)[:200] or f"Known vulnerability in {pkg}",
                            file_path="requirements.txt", line_number=None, code_snippet=None,
                            recommendation=f"Update {pkg} to a patched version. CVE: {vuln_id}",
                            cwe_id="CWE-1104", owasp_category="A06", soc2_control="CC8.1",
                            source="safety"
                        ))
                except json.JSONDecodeError:
                    pass
        except Exception as e:
            logger.error(f"Safety check failed: {e}")
        return issues

    def _scan_fix_changes(self, fix_plan: dict) -> list:
        """
        Scan the generated fix changes for security issues introduced by the patch.
        This is the most critical check — ensuring the fix itself is secure.
        """
        issues = []
        for change in fix_plan.get("files_to_modify", []):
            file_path = change.get("file_path", "")
            fixed_code = change.get("fixed_code", "")
            if not fixed_code:
                continue

            # Check for common dangerous patterns in the fix itself
            dangerous_patterns = [
                (r'eval\s*\(', "Code Injection via eval()", "A03", "CWE-94"),
                (r'exec\s*\(', "Code Injection via exec()", "A03", "CWE-94"),
                (r'shell\s*=\s*True', "Command Injection via shell=True", "A03", "CWE-78"),
                (r'(?i)(password|secret|api_key|token)\s*=\s*["\'][^"\']{8,}', "Possible Hardcoded Credential", "A02", "CWE-798"),
                (r'DEBUG\s*=\s*True', "Debug Mode Enabled", "A05", None),
                (r'verify\s*=\s*False', "SSL Certificate Verification Disabled", "A02", "CWE-295"),
                (r'yaml\.load\s*\([^)]*Loader', "Unsafe YAML load (use safe_load)", "A08", "CWE-502"),
            ]

            for pattern, title, owasp, cwe in dangerous_patterns:
                if re.search(pattern, fixed_code, re.IGNORECASE):
                    issues.append(SecurityIssue(
                        severity="high",
                        category="fix_security_review",
                        title=f"Fix introduces: {title}",
                        description=f"The generated fix in {file_path} contains a potentially insecure pattern: {title}",
                        file_path=file_path,
                        line_number=None,
                        code_snippet=fixed_code[:200],
                        recommendation=f"Review and address: {title}",
                        cwe_id=cwe,
                        owasp_category=owasp,
                        source="custom"
                    ))

        return issues

    def _llm_security_review(self, fix_plan: dict, root_cause: dict) -> list:
        """Use LLM to review the fix for security implications."""
        fix_summary = []
        for change in fix_plan.get("files_to_modify", []):
            fix_summary.append(
                f"File: {change.get('file_path', '')}\n"
                f"Change: {change.get('rationale', '')}\n"
                f"Fixed Code:\n```\n{change.get('fixed_code', '')[:600]}\n```"
            )

        if not fix_summary:
            return []

        prompt = f"""You are a **Principal Application Security Engineer** (CISSP, OSCP certified) reviewing a
code fix for security implications before it is applied to production.

=== ROOT CAUSE ===
{root_cause.get('hypothesis', '')}

=== PROPOSED FIX ===
{chr(10).join(fix_summary)}

=== YOUR SECURITY REVIEW ===
Apply STRIDE threat modeling. Check:
1. **Injection** (A03): Does the fix use parameterized queries? Avoid string concatenation in SQL/commands.
2. **Authentication** (A07): Does the fix properly validate identity? No auth bypass?
3. **Cryptography** (A02): No hardcoded secrets? Using strong algorithms (SHA-256+, AES-256)?
4. **Access Control** (A01): Does the fix maintain proper authorization checks?
5. **Input Validation** (A04): Are inputs from external sources validated/sanitized?
6. **Logging** (A09): Are sensitive fields masked? No credentials in logs?

IMPORTANT: Only flag REAL issues introduced or left unaddressed by THIS specific fix.
Do not flag issues with proper patterns (parameterized queries, secrets from env, etc).

Return JSON array of CRITICAL/HIGH issues only (max 3, empty array [] if fix is secure):
[{{
    "severity": "critical|high",
    "title": "Specific issue title",
    "cwe": "CWE-XXX",
    "owasp": "A01-A10",
    "description": "What exactly is insecure and how could it be exploited",
    "recommendation": "Specific code-level fix"
}}]"""

        try:
            messages = [
                {"role": "system", "content": "You are a Principal Application Security Engineer reviewing production code fixes."},
                {"role": "user", "content": prompt}
            ]
            response = self.llm.chat(messages)
            content = response.content if hasattr(response, 'content') else str(response)
            start = content.find("[")
            end = content.rfind("]") + 1
            if start >= 0 and end > start:
                data = json.loads(content[start:end])
                issues = []
                for item in data:
                    issues.append(SecurityIssue(
                        severity=item.get("severity", "high"),
                        category="llm_fix_review",
                        title=item.get("title", "Security Issue"),
                        description=item.get("description", ""),
                        file_path="fix_patch",
                        line_number=None,
                        code_snippet=None,
                        recommendation=item.get("recommendation", ""),
                        cwe_id=item.get("cwe"),
                        owasp_category=item.get("owasp"),
                        source="llm"
                    ))
                return issues
        except Exception as e:
            logger.error(f"LLM security review failed: {e}")
        return []

    def _generate_compliance_report(self, issues: list) -> dict:
        owasp_summary = {k: {"name": v, "count": 0} for k, v in OWASP_TOP_10.items()}
        for issue in issues:
            if issue.owasp_category and issue.owasp_category in owasp_summary:
                owasp_summary[issue.owasp_category]["count"] += 1
        source_breakdown = {}
        for issue in issues:
            source_breakdown[issue.source] = source_breakdown.get(issue.source, 0) + 1
        return {
            "owasp_top_10": owasp_summary,
            "tools_used": source_breakdown,
            "compliant": sum(1 for i in issues if i.severity in ["critical", "high"]) == 0
        }

    def execute(self, state: PipelineState) -> AgentResponse:
        fix_plan = state.get("fix_plan", {}) or {}
        root_cause = state.get("root_cause", {}) or {}
        repo_path = state.get("repo_path", "")

        all_issues = []

        # 1. Scan the fix itself (most important — always run)
        logger.info("[Security] Scanning fix changes for introduced vulnerabilities...")
        fix_issues = self._scan_fix_changes(fix_plan)
        all_issues.extend(fix_issues)

        # 2. LLM deep security review of the fix
        logger.info("[Security] Running LLM security review of proposed fix...")
        llm_issues = self._llm_security_review(fix_plan, root_cause)
        all_issues.extend(llm_issues)

        # 3. Docker sandbox scan (preferred) or local fallback
        if repo_path:
            skip_patterns = ["venv", ".venv", "node_modules", "site-packages", "__pycache__"]

            if self._docker_sandbox:
                # Prefer Docker-based security scan (isolated, reproducible)
                logger.info("[Security] Running Docker-based security scan (bandit + pip-audit)...")
                docker_issues = [
                    i for i in self._run_docker_scan(repo_path, language="python")
                    if not any(p in i.file_path.lower() for p in skip_patterns)
                ]
                all_issues.extend(docker_issues)
            else:
                # Fallback: local bandit + safety
                logger.info("[Security] No Docker sandbox — falling back to local tools...")
                logger.info("[Security] Running bandit static analysis...")
                bandit_issues = [
                    i for i in self._run_bandit(repo_path)
                    if not any(p in i.file_path.lower() for p in skip_patterns)
                ]
                all_issues.extend(bandit_issues)

                logger.info("[Security] Running safety dependency check...")
                safety_issues = self._run_safety(repo_path)
                all_issues.extend(safety_issues)

        # 4. Filter false positives
        original_count = len(all_issues)
        all_issues = [i for i in all_issues if not self._should_filter_issue(i)]
        filtered = original_count - len(all_issues)
        if filtered > 0:
            logger.info(f"[Security] Filtered {filtered} false positives")

        # Sort by severity
        all_issues.sort(key=lambda x: {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(x.severity, 4))

        compliance = self._generate_compliance_report(all_issues)
        critical = sum(1 for i in all_issues if i.severity == "critical")
        high = sum(1 for i in all_issues if i.severity == "high")

        tools_used = ", ".join(compliance["tools_used"].keys()) or "custom+llm"
        message = f"🔒 Security scan: {len(all_issues)} issue(s) ({critical} critical, {high} high) | Tools: {tools_used}"

        # Gate: if critical issues found in the fix itself, flag for retry
        fix_critical = [i for i in all_issues if i.severity == "critical" and i.source in ("custom", "llm")]
        needs_retry = len(fix_critical) > 0

        if needs_retry:
            logger.warning(f"[Security] BLOCKED — {len(fix_critical)} critical issue(s) in fix. Retrying fix generation.")

        return AgentResponse(
            success=not needs_retry,
            message=message,
            data={
                "issues": [
                    {
                        "severity": i.severity,
                        "category": i.category,
                        "title": i.title,
                        "description": i.description,
                        "file": i.file_path,
                        "line": i.line_number,
                        "snippet": i.code_snippet,
                        "recommendation": i.recommendation,
                        "cwe": i.cwe_id,
                        "owasp": i.owasp_category,
                        "source": i.source,
                    }
                    for i in all_issues
                ],
                "summary": {
                    "total": len(all_issues),
                    "critical": critical,
                    "high": high,
                    "medium": sum(1 for i in all_issues if i.severity == "medium"),
                    "low": sum(1 for i in all_issues if i.severity == "low"),
                    "compliant": compliance["compliant"],
                },
                "compliance": compliance,
            },
            next_agent=AgentType.SYNTHESIS.value if not needs_retry else AgentType.FIX_WRITER.value,
            needs_retry=needs_retry,
        )

    async def aexecute(self, state: PipelineState) -> AgentResponse:
        return self.execute(state)

    def get_system_prompt(self) -> str:
        return (
            "You are the Security Agent in Amaze on Work — a Principal Application Security Engineer. "
            "You act as the security gatekeeper before any fix is applied to production. "
            "You apply STRIDE threat modeling and OWASP Top 10 methodology to review generated fixes. "
            "You run bandit, safety, and LLM-based analysis to ensure the fix introduces no vulnerabilities. "
            "Critical findings in the fix itself trigger a re-generation cycle."
        )
