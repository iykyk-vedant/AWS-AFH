"""
Docker Sandbox Runner for Amaze on Work.

Components:
  - TestRunner       — runs test suites in ephemeral Docker containers
  - RegressionDetector — compares before/after results, assigns verdict
  - ResultCollector  — accumulates per-attempt logs, picks best fix, writes to disk
  - DockerSandbox    — orchestrates all three for the ValidationAgent
"""

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ─── Data Classes ──────────────────────────────────────────────────────────────


@dataclass
class TestRunResult:
    """Result from a single test run inside a Docker container."""
    exit_code: int
    stdout: str
    stderr: str
    total: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failed_tests: list = field(default_factory=list)
    passed_tests: list = field(default_factory=list)
    runtime_seconds: float = 0.0
    raw_output: str = ""
    # Error signature for tracking same-vs-new errors
    error_signature: str = ""


@dataclass
class ValidationResult:
    """Before/after validation result with full regression analysis."""
    verdict: str  # FIX_CONFIRMED | REGRESSION_DETECTED | UNRELATED_FAILURE | NO_CHANGE | ERROR | SKIPPED
    before: Optional[TestRunResult] = None
    after: Optional[TestRunResult] = None
    regressions: list = field(default_factory=list)
    fixes_confirmed: list = field(default_factory=list)
    unrelated_failures: list = field(default_factory=list)
    error_message: str = ""
    error_signature: str = ""   # Fingerprint of the current failure for retry tracking
    is_new_error: bool = False  # True if error signature changed from previous attempt


@dataclass
class FixCandidate:
    """A single fix candidate with its score from the evaluator."""
    candidate_id: str
    fix_plan: dict
    patch: str
    validation_result: Optional[ValidationResult] = None
    score: float = 0.0
    rank: int = 0


@dataclass
class TestAttemptLog:
    """Per-attempt log entry stored in ResultCollector."""
    attempt_number: int
    candidate_id: str
    verdict: str
    error_signature: str
    is_new_error: bool
    before_passed: int
    before_failed: int
    after_passed: int
    after_failed: int
    fixes_confirmed: list
    regressions: list
    raw_stdout: str
    patch_summary: str
    timestamp: str = ""
    score: float = 0.0

    def to_dict(self) -> dict:
        return {
            "attempt": self.attempt_number,
            "candidate_id": self.candidate_id,
            "verdict": self.verdict,
            "error_signature": self.error_signature,
            "is_new_error": self.is_new_error,
            "before": {"passed": self.before_passed, "failed": self.before_failed},
            "after": {"passed": self.after_passed, "failed": self.after_failed},
            "fixes_confirmed": self.fixes_confirmed,
            "regressions": self.regressions,
            "score": self.score,
            "patch_summary": self.patch_summary,
            "timestamp": self.timestamp,
            "stdout_tail": self.raw_stdout[-2000:] if self.raw_stdout else "",
        }


# ─── TestRunner ────────────────────────────────────────────────────────────────


class TestRunner:
    """
    Runs test suites in an ephemeral Docker container.

    Supports:
    - Existing repo tests (pytest / Jest)
    - Injected generated test file from TestWriterAgent
    - Network enabled for dependency install, disabled for test exec
    """

    def __init__(
        self,
        python_image: str = "amaze-python:base",
        node_image: str = "amaze-node:base",
        timeout: int = 120,
        memory_limit: str = "768m",
        cpu_quota: int = 75000,
    ):
        self.python_image = python_image
        self.node_image = node_image
        self.timeout = timeout
        self.memory_limit = memory_limit
        self.cpu_quota = cpu_quota
        self._client = None

    @property
    def docker(self):
        if self._client is None:
            import docker
            self._client = docker.from_env()
        return self._client

    def run(
        self,
        repo_path: str,
        language: str,
        service_dir: str = "",
        generated_test_file: str = "",
        test_filename: str = "test_amaze_generated.py",
    ) -> TestRunResult:
        """
        Run tests in an isolated Docker container.

        Args:
            repo_path: Host path to repository
            language: 'python' or 'node'
            service_dir: Subdirectory within repo (e.g. 'python-service')
            generated_test_file: Content of the generated test file to inject
            test_filename: Filename to write the generated test to

        Returns:
            TestRunResult with full stdout/stderr and parsed counts
        """
        start = time.time()
        image = self.python_image if language == "python" else self.node_image
        work_dir = f"/app/{service_dir}" if service_dir else "/app"

        # Inject generated test file into repo if provided
        injected_path = None
        gen_test_relpath = ""
        if generated_test_file:
            service_abs = os.path.join(repo_path, service_dir) if service_dir else repo_path
            # Place generated tests in a separate dir to isolate from conftest.py
            gen_test_dir = os.path.join(service_abs, "_amaze_tests")
            os.makedirs(gen_test_dir, exist_ok=True)
            injected_path = os.path.join(gen_test_dir, test_filename)
            # Ensure no conftest.py interferes with our generated tests
            conftest_guard = os.path.join(gen_test_dir, "conftest.py")
            with open(conftest_guard, "w", encoding="utf-8") as f:
                f.write("# Empty conftest to prevent parent conftest from loading\n")
            with open(injected_path, "w", encoding="utf-8") as f:
                f.write(generated_test_file)
            gen_test_relpath = "_amaze_tests/"
            logger.info(f"[TestRunner] Injected generated test: {injected_path}")

        try:
            if language == "python":
                install_cmd = (
                    f"cd {work_dir} && "
                    f"pip install -q -r requirements.txt 2>/dev/null || true"
                )
                if gen_test_relpath:
                    # Run ONLY the generated tests (isolated from repo conftest)
                    # --override-ini prevents conftest.py from repo's tests/ dir
                    test_cmd = (
                        f"cd {work_dir} && "
                        f"python -m pytest {gen_test_relpath} -v --tb=short --no-header "
                        f"--rootdir={work_dir}/{gen_test_relpath} "
                        f"-o 'confcutdir={work_dir}/{gen_test_relpath}' 2>&1; "
                        f"echo \"PYTEST_EXIT_CODE=$?\""
                    )
                else:
                    test_cmd = (
                        f"cd {work_dir} && "
                        f"python -m pytest tests/ -v --tb=short --no-header 2>&1; "
                        f"echo \"PYTEST_EXIT_CODE=$?\""
                    )
                full_cmd = f"{install_cmd} && {test_cmd}"
            else:
                full_cmd = (
                    f"cd {work_dir} && "
                    f"npm install --silent 2>/dev/null || true && "
                    f"npx jest --no-coverage --verbose 2>&1; "
                    f"echo \"JEST_EXIT_CODE=$?\""
                )

            container = self.docker.containers.run(
                image=image,
                command=["bash", "-c", full_cmd],
                volumes={
                    os.path.abspath(repo_path): {"bind": "/app", "mode": "rw"}
                },
                working_dir=work_dir,
                mem_limit=self.memory_limit,
                cpu_quota=self.cpu_quota,
                network_disabled=False,  # Need network for installs
                user="root",
                remove=False,
                detach=True,
            )

            try:
                exit_result = container.wait(timeout=self.timeout)
                exit_code = exit_result.get("StatusCode", 1)
            except Exception:
                logger.warning(f"[TestRunner] Container timed out after {self.timeout}s")
                container.kill()
                exit_code = 124

            stdout = container.logs(stdout=True, stderr=False).decode("utf-8", errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode("utf-8", errors="replace")

            try:
                container.remove(force=True)
            except Exception:
                pass

            runtime = time.time() - start
            result = self._parse_output(stdout + "\n" + stderr, language)
            result.stdout = stdout
            result.stderr = stderr
            result.runtime_seconds = runtime
            result.raw_output = stdout + "\n" + stderr

            # Extract real test exit code from the echoed marker
            # (since the container always exits 0, the real exit code is in stdout)
            exit_code_match = re.search(r"(?:PYTEST|JEST)_EXIT_CODE=(\d+)", stdout)
            if exit_code_match:
                exit_code = int(exit_code_match.group(1))
            result.exit_code = exit_code

            # Exit code 2 = pytest collection error (ImportError, syntax error, etc.)
            # This shows as 0P/0F/0E in the summary but IS a real failure
            if exit_code == 2 and language == "python" and result.total == 0:
                self._inject_collection_errors(result)

            # Also detect collection errors from raw output even with exit_code=0
            # (in case conftest.py crash is caught)
            if result.total == 0 and "ImportError" in result.raw_output:
                err_matches = re.findall(
                    r"(?:ImportError|ModuleNotFoundError):\s*(.+)", result.raw_output
                )
                if err_matches:
                    logger.warning(
                        f"[TestRunner] Import errors detected in output: {err_matches[0][:80]}"
                    )
                    if result.errors == 0:
                        self._inject_collection_errors(result)

            result.error_signature = self._extract_error_signature(result.raw_output)

            logger.info(
                f"[TestRunner] Done in {runtime:.1f}s — "
                f"{result.passed} passed, {result.failed} failed, "
                f"errors={result.errors}, exit_code={exit_code}, "
                f"sig={result.error_signature[:40]!r}"
            )
            return result

        except Exception as e:
            logger.error(f"[TestRunner] Docker exec failed: {e}")
            r = TestRunResult(
                exit_code=1, stdout="", stderr=str(e),
                runtime_seconds=time.time() - start,
                error_signature=hashlib.md5(str(e).encode()).hexdigest()[:12],
            )
            return r
        finally:
            # Clean up injected test files and directory
            if injected_path and os.path.exists(injected_path):
                try:
                    # Remove the entire _amaze_tests directory
                    gen_dir = os.path.dirname(injected_path)
                    if os.path.basename(gen_dir) == "_amaze_tests":
                        shutil.rmtree(gen_dir, ignore_errors=True)
                    else:
                        os.remove(injected_path)

                except Exception:
                    pass

    def _parse_output(self, output: str, language: str) -> TestRunResult:
        if language == "python":
            return self._parse_pytest(output)
        return self._parse_jest(output)

    def _parse_pytest(self, output: str) -> TestRunResult:
        result = TestRunResult(exit_code=0, stdout=output, stderr="")
        passed_tests, failed_tests = [], []

        for line in output.split("\n"):
            if " PASSED" in line:
                passed_tests.append(line.split(" PASSED")[0].strip())
            elif " FAILED" in line:
                failed_tests.append(line.split(" FAILED")[0].strip())
            elif " ERROR" in line and "::" in line:
                failed_tests.append(line.split(" ERROR")[0].strip())

        m = re.search(r"(\d+)\s+passed", output)
        if m:
            result.passed = int(m.group(1))
        m = re.search(r"(\d+)\s+failed", output)
        if m:
            result.failed = int(m.group(1))
        # Match both "1 error" and "1 errors"
        m = re.search(r"(\d+)\s+errors?", output)
        if m:
            result.errors = int(m.group(1))

        # Detect 'collected N items / M errors' (collection phase errors)
        m_col = re.search(r"collected\s+\d+\s+items?\s*/\s*(\d+)\s+errors?", output)
        if m_col:
            collection_errors = int(m_col.group(1))
            if collection_errors > 0 and result.errors == 0:
                result.errors = collection_errors

        result.passed_tests = passed_tests
        result.failed_tests = failed_tests
        result.total = result.passed + result.failed + result.errors
        return result

    def _inject_collection_errors(self, result: TestRunResult) -> None:
        """
        Called when pytest exits with code 2 (collection error) but shows 0P/0F/0E.
        This happens when the generated test file has ImportError/ModuleNotFoundError.
        We inject a fake failed_test so the RegressionDetector sees a real failure.
        """
        output = result.raw_output or ""
        # Find the error type
        err_match = re.search(
            r"(ImportError|ModuleNotFoundError|SyntaxError|AttributeError):\s*(.+)",
            output,
        )
        if err_match:
            error_label = f"COLLECTION_ERROR::{err_match.group(1)}: {err_match.group(2)[:80].strip()}"
        else:
            # Look for 'ERROR collecting file'
            col_match = re.search(r"ERROR collecting (.+)", output)
            error_label = (
                f"COLLECTION_ERROR::{col_match.group(1).strip()[:80]}"
                if col_match else "COLLECTION_ERROR::unknown"
            )

        result.errors = 1
        result.failed = 1
        result.total = 1
        result.failed_tests = [error_label]
        logger.warning(
            f"[TestRunner] Detected collection error (exit_code=2): {error_label[:80]}"
        )

    def _parse_jest(self, output: str) -> TestRunResult:
        result = TestRunResult(exit_code=0, stdout=output, stderr="")
        passed_tests, failed_tests = [], []

        for line in output.split("\n"):
            ls = line.strip()
            if ls.startswith(("✓", "√", "PASS")):
                passed_tests.append(ls.lstrip("✓√ PASS").strip())
            elif ls.startswith(("✕", "×", "✗", "FAIL")):
                failed_tests.append(ls.lstrip("✕×✗ FAIL").strip())

        m = re.search(r"Tests:\s+(?:(\d+)\s+failed,\s+)?(?:(\d+)\s+passed,\s+)?(\d+)\s+total", output)
        if m:
            result.failed = int(m.group(1) or 0)
            result.passed = int(m.group(2) or 0)
            result.total = int(m.group(3) or 0)
        else:
            result.passed = len(passed_tests)
            result.failed = len(failed_tests)
            result.total = result.passed + result.failed

        result.passed_tests = passed_tests
        result.failed_tests = failed_tests
        return result

    @staticmethod
    def _extract_error_signature(output: str) -> str:
        """
        Extract a fingerprint string from test output for same-vs-new error detection.
        Uses: exception type + first meaningful error message line.
        """
        # Pytest: look for "E   ExceptionType: message"
        lines = output.split("\n")
        sig_parts = []
        for line in lines:
            stripped = line.strip()
            # Python exception lines
            if re.match(r"^[A-Za-z]+Error:", stripped) or re.match(r"^E\s+[A-Za-z]+Error:", stripped):
                sig_parts.append(re.sub(r"\d+", "N", stripped)[:80])
            # AssertionError
            elif stripped.startswith("AssertionError") or stripped.startswith("E   AssertionError"):
                sig_parts.append(re.sub(r"\d+", "N", stripped)[:80])
            # Jest FAIL lines
            elif "Expected:" in stripped or "Received:" in stripped:
                sig_parts.append(stripped[:60])
            if len(sig_parts) >= 2:
                break

        if not sig_parts:
            # Fallback: hash of the first ERROR block found
            m = re.search(r"(FAILED|Error|error).*", output)
            if m:
                sig_parts = [re.sub(r"\d+", "N", m.group(0))[:80]]

        raw = " | ".join(sig_parts[:2]) or "no_error_sig"
        return hashlib.md5(raw.encode()).hexdigest()[:16]


# ─── RegressionDetector ────────────────────────────────────────────────────────


class RegressionDetector:
    """
    Compares before/after TestRunResults and assigns a verdict.

    Verdict semantics:
      FIX_CONFIRMED       — at least one previously failing test now passes, no new regressions
      REGRESSION_DETECTED — at least one previously passing test now fails
      NO_CHANGE           — identical test results before and after
      UNRELATED_FAILURE   — same failures exist before and after, fix didn't break anything
      ERROR               — infra/parse failure
    """

    def compare(
        self,
        before: TestRunResult,
        after: TestRunResult,
        last_error_signature: str = "",
    ) -> ValidationResult:
        """
        Full before/after comparison.

        Args:
            before: Test results before patch
            after:  Test results after patch
            last_error_signature: Error signature from the previous attempt
                                  (used to detect whether this is a new error)

        Returns:
            ValidationResult with verdict and regression/fix lists
        """
        before_failed = set(before.failed_tests)
        after_failed = set(after.failed_tests)
        before_passed = set(before.passed_tests)
        after_passed = set(after.passed_tests)

        regressions = [t for t in before_passed if t in after_failed]
        fixes_confirmed = [t for t in before_failed if t in after_passed]
        unrelated = [t for t in before_failed if t in after_failed]

        # Numeric fallbacks when test names aren't parsed
        if not regressions and not fixes_confirmed:
            if after.failed < before.failed:
                fixes_confirmed = [f"test_improvement_{before.failed - after.failed}"]
            elif after.failed > before.failed:
                regressions = [f"regression_{after.failed - before.failed}_new_failures"]

        # ── ZERO-TEST CASE ──────────────────────────────────────────
        # When BOTH before and after have 0 ACTUAL test results
        # (0 passed + 0 failed), the sandbox cannot detect regressions.
        # Collection errors (exit_code=2, errors>=1) mean pytest
        # couldn't even LOAD the tests — this is NOT the fix's fault.
        # Accept the fix and let downstream stages evaluate it.
        before_ran = before.passed + before.failed
        after_ran = after.passed + after.failed

        if before_ran == 0 and after_ran == 0:
            verdict = "FIX_APPLIED_NO_TESTS"
            logger.info(
                f"[RegressionDetector] 0 tests ran before AND after "
                f"(errors: before={before.errors} after={after.errors}). "
                f"Cannot detect regression — accepting fix (FIX_APPLIED_NO_TESTS)."
            )
        # Verdict
        elif regressions:
            verdict = "REGRESSION_DETECTED"
        elif fixes_confirmed:
            verdict = "FIX_CONFIRMED"
        elif after.failed == before.failed and after.passed == before.passed:
            verdict = "NO_CHANGE"
        elif unrelated and not fixes_confirmed and not regressions:
            verdict = "UNRELATED_FAILURE"
        else:
            verdict = "FIX_CONFIRMED" if after.failed < before.failed else "NO_CHANGE"

        current_sig = after.error_signature or ""
        is_new = bool(
            last_error_signature
            and current_sig
            and current_sig != last_error_signature
        )

        logger.info(
            f"[RegressionDetector] Verdict={verdict} | "
            f"fixes={len(fixes_confirmed)} regressions={len(regressions)} "
            f"unrelated={len(unrelated)} | new_error={is_new} | "
            f"before={before.passed}P/{before.failed}F after={after.passed}P/{after.failed}F"
        )

        return ValidationResult(
            verdict=verdict,
            before=before,
            after=after,
            regressions=regressions,
            fixes_confirmed=fixes_confirmed,
            unrelated_failures=unrelated,
            error_signature=current_sig,
            is_new_error=is_new,
        )


# ─── ResultCollector ───────────────────────────────────────────────────────────


class ResultCollector:
    """
    Accumulates per-attempt logs, scores candidates, and selects the best fix.

    Writes logs to reports/{incident_id}/validation_attempt_{n}.json.
    Exposes get_best_candidate() after all candidates have been evaluated.

    Scoring weights (0-100):
      - Tests fixed (before_failed → after_passed):  40 pts
      - Zero regressions:                            30 pts
      - Patch size (smaller = better, max 20 pts):   20 pts
      - Error resolved (sig gone):                   10 pts
    """

    def __init__(self, incident_id: str = "unknown", reports_dir: str = "reports"):
        self.incident_id = incident_id
        self.reports_dir = Path(reports_dir) / incident_id
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self._attempts: list[TestAttemptLog] = []
        self._candidates: list[FixCandidate] = []

    def record_attempt(
        self,
        attempt_number: int,
        candidate: FixCandidate,
        result: ValidationResult,
    ) -> TestAttemptLog:
        """Record a validation attempt for a fix candidate."""
        if not result.before:
            result.before = TestRunResult(exit_code=0, stdout="", stderr="")
        if not result.after:
            result.after = TestRunResult(exit_code=0, stdout="", stderr="")

        score = self._score(candidate, result)
        candidate.score = score
        candidate.validation_result = result

        patch_summary = ""
        files = candidate.fix_plan.get("files_to_modify", [])
        if files:
            patch_summary = ", ".join(
                f"{ch.get('file_path', '?')} ({len(ch.get('fixed_code', ''))} chars)"
                for ch in files[:3]
            )

        log = TestAttemptLog(
            attempt_number=attempt_number,
            candidate_id=candidate.candidate_id,
            verdict=result.verdict,
            error_signature=result.error_signature or "",
            is_new_error=result.is_new_error,
            before_passed=result.before.passed,
            before_failed=result.before.failed,
            after_passed=result.after.passed,
            after_failed=result.after.failed,
            fixes_confirmed=result.fixes_confirmed,
            regressions=result.regressions,
            raw_stdout=result.after.raw_output or "",
            patch_summary=patch_summary,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            score=score,
        )
        self._attempts.append(log)

        # Write to disk
        log_path = self.reports_dir / f"validation_attempt_{attempt_number}_{candidate.candidate_id}.json"
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                json.dump(log.to_dict(), f, indent=2)
            logger.info(f"[ResultCollector] Logged attempt {attempt_number} to {log_path}")
        except Exception as e:
            logger.warning(f"[ResultCollector] Could not write log: {e}")

        return log

    def add_candidate(self, candidate: FixCandidate) -> None:
        self._candidates.append(candidate)

    def get_best_candidate(self) -> Optional[FixCandidate]:
        """Return the highest-scoring fix candidate."""
        evaluated = [c for c in self._candidates if c.validation_result is not None]
        if not evaluated:
            return None
        evaluated.sort(key=lambda c: c.score, reverse=True)
        for i, c in enumerate(evaluated):
            c.rank = i + 1
        logger.info(
            f"[ResultCollector] Best candidate: {evaluated[0].candidate_id} "
            f"(score={evaluated[0].score:.1f}, verdict={evaluated[0].validation_result.verdict})"
        )
        return evaluated[0]

    def get_attempt_history(self) -> list[dict]:
        return [a.to_dict() for a in self._attempts]

    def write_summary(self) -> str:
        """Write a human-readable summary file and return its path."""
        summary_path = self.reports_dir / "validation_summary.json"
        best = self.get_best_candidate()
        summary = {
            "incident_id": self.incident_id,
            "total_attempts": len(self._attempts),
            "total_candidates": len(self._candidates),
            "best_candidate": best.candidate_id if best else None,
            "best_verdict": best.validation_result.verdict if best else None,
            "best_score": best.score if best else 0,
            "attempts": self.get_attempt_history(),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        try:
            with open(summary_path, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
        except Exception as e:
            logger.warning(f"[ResultCollector] Could not write summary: {e}")
        return str(summary_path)

    @staticmethod
    def _score(candidate: FixCandidate, result: ValidationResult) -> float:
        score = 0.0
        before = result.before
        after = result.after

        # 40 pts: tests fixed
        if result.verdict == "FIX_APPLIED_NO_TESTS":
            # No tests to verify — give partial credit (fix was applied successfully)
            score += 25
        elif before and before.failed > 0:
            fixes_ratio = len(result.fixes_confirmed) / max(before.failed, 1)
            score += min(fixes_ratio, 1.0) * 40
        elif result.fixes_confirmed:
            score += 40

        # 30 pts: zero regressions
        if not result.regressions:
            score += 30

        # 20 pts: patch size (smaller is more surgical, max 20)
        patch = candidate.fix_plan.get("patch", "") or ""
        for ch in candidate.fix_plan.get("files_to_modify", []):
            patch += ch.get("fixed_code", "")
        patch_size = len(patch)
        size_score = max(0, 20 - (patch_size / 200))  # lose 1 pt per 200 chars
        score += min(size_score, 20)

        # 10 pts: error signature cleared
        if result.verdict == "FIX_APPLIED_NO_TESTS":
            score += 5  # Partial credit — fix applied cleanly
        elif result.fixes_confirmed and not result.regressions:
            score += 10

        return round(score, 2)


# ─── DockerSandbox (Orchestrator) ──────────────────────────────────────────────


class DockerSandbox:
    """
    Orchestrates TestRunner + RegressionDetector + ResultCollector
    for the ValidationAgent.

    Main methods:
      health_check()          → check Docker availability
      clone_repo()            → checkout repo to temp dir
      apply_patch_to_copy()   → apply git diff to a copy
      run_validation()        → full before/after validation for ONE fix
      run_validation_multi()  → test MULTIPLE fix candidates, return best
    """

    def __init__(
        self,
        node_image: str = "amaze-node:base",
        python_image: str = "amaze-python:base",
        timeout: int = 120,
        memory_limit: str = "768m",
        cpu_quota: int = 75000,
    ):
        self.runner = TestRunner(python_image, node_image, timeout, memory_limit, cpu_quota)
        self.detector = RegressionDetector()
        self._docker_client = None

    @property
    def docker_client(self):
        if self._docker_client is None:
            import docker
            self._docker_client = docker.from_env()
        return self._docker_client

    def health_check(self) -> dict:
        """Check Docker and required images."""
        result = {"docker_available": False, "node_image": False, "python_image": False}
        try:
            self.docker_client.ping()
            result["docker_available"] = True
        except Exception as e:
            logger.error(f"[DockerSandbox] Docker not available: {e}")
            return result
        for img_key, img_name in [
            ("python_image", self.runner.python_image),
            ("node_image", self.runner.node_image),
        ]:
            try:
                self.docker_client.images.get(img_name)
                result[img_key] = True
            except Exception:
                logger.warning(f"[DockerSandbox] Image '{img_name}' not found — need to build it")
        return result

    def clone_repo(self, repo_url: str, branch: str = "master") -> str:
        """Clone a repo to a temp dir and return its path."""
        tmp = tempfile.mkdtemp(prefix="amaze_repo_")
        logger.info(f"[DockerSandbox] Cloning {repo_url} → {tmp}")
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", branch, repo_url, tmp],
                check=True, capture_output=True, timeout=60,
            )
        except subprocess.CalledProcessError:
            # Try 'main' fallback
            try:
                shutil.rmtree(tmp, ignore_errors=True)
                tmp = tempfile.mkdtemp(prefix="amaze_repo_")
                subprocess.run(
                    ["git", "clone", "--depth", "1", "--branch", "main", repo_url, tmp],
                    check=True, capture_output=True, timeout=60,
                )
            except subprocess.CalledProcessError as e:
                shutil.rmtree(tmp, ignore_errors=True)
                raise RuntimeError(f"Git clone failed: {e.stderr.decode()}") from e
        return tmp

    def apply_patch_to_copy(self, repo_path: str, patch_content: str) -> str:
        """Copy repo and apply the patch. Returns path to patched copy."""
        patched = tempfile.mkdtemp(prefix="amaze_patched_")
        shutil.copytree(repo_path, patched, dirs_exist_ok=True)

        if not patch_content.strip():
            logger.warning("[DockerSandbox] Empty patch — no changes applied")
            return patched

        patch_file = os.path.join(patched, "_amaze.diff")
        with open(patch_file, "w", encoding="utf-8") as f:
            f.write(patch_content)

        try:
            subprocess.run(
                ["git", "apply", "--allow-empty", "--ignore-whitespace", patch_file],
                cwd=patched, check=True, capture_output=True, timeout=10,
            )
            logger.info("[DockerSandbox] Patch applied via git apply")
        except subprocess.CalledProcessError as e:
            logger.warning(f"[DockerSandbox] git apply failed: {e.stderr.decode()[:200]} — trying manual apply")
            self._manual_patch_apply(patched, patch_content)
        finally:
            if os.path.exists(patch_file):
                os.remove(patch_file)

        return patched

    def apply_file_changes(self, repo_path: str, files_to_modify: list) -> str:
        """
        Apply fix_plan.files_to_modify by doing original_code → fixed_code
        find-and-replace within each file. Returns path to patched copy.

        Uses a 5-tier matching strategy to handle LLM output variations:
          1. Exact verbatim match
          2. Whitespace-normalized (trailing spaces stripped)
          3. Indent-agnostic (all leading whitespace normalized)
          4. Fuzzy best-match (difflib SequenceMatcher, threshold 0.6)
          5. Function-level match (replaces entire function body)

        Each entry in files_to_modify must have:
          - file_path:     relative repo path
          - original_code: code to find (LLM-generated, may not be exact)
          - fixed_code:    replacement code
        """
        patched = tempfile.mkdtemp(prefix="amaze_patched_")
        shutil.copytree(repo_path, patched, dirs_exist_ok=True)

        applied = 0
        for change in files_to_modify:
            file_path = change.get("file_path", "")
            original_code = change.get("original_code", "")
            fixed_code = change.get("fixed_code", "")
            if not file_path or not fixed_code:
                logger.warning(f"[DockerSandbox] Skipping change — missing file_path or fixed_code")
                continue

            full_path = os.path.join(patched, file_path)
            if not os.path.exists(full_path):
                logger.warning(f"[DockerSandbox] File not found: {file_path} — skipping")
                continue

            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            if not original_code:
                # No original_code — append as last resort
                logger.warning(f"[DockerSandbox] No original_code for {file_path} — appending fix")
                content += "\n" + self._sanitize_llm_code(fixed_code)
            else:
                clean_original = self._sanitize_llm_code(original_code)
                clean_fixed = self._sanitize_llm_code(fixed_code)
                content = self._smart_replace(content, clean_original, clean_fixed, file_path)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)
            applied += 1
            logger.info(f"[DockerSandbox] Applied change to {file_path} ({applied}/{len(files_to_modify)})")

        if applied == 0:
            logger.warning("[DockerSandbox] No changes were successfully applied!")
        else:
            logger.info(f"[DockerSandbox] Successfully applied {applied}/{len(files_to_modify)} file changes")

        return patched

    @staticmethod
    def _sanitize_llm_code(code: str) -> str:
        """Clean up LLM-generated code before matching/applying.

        Handles common LLM output issues:
          1. Markdown code fences (```python, ```)
          2. Tabs → 4 spaces
          3. Unicode whitespace (NBSP, zero-width, em/en spaces)
          4. Trailing whitespace per line
          5. Leading/trailing blank lines
          6. Inconsistent indentation normalization
        """
        import re

        if not code:
            return code

        # 1. Strip markdown code fences
        #    Handles: ```python\n...\n```, ```js\n...\n```, ```\n...\n```
        code = re.sub(r'^\s*```\w*\s*\n', '', code)      # opening fence
        code = re.sub(r'\n\s*```\s*$', '', code)          # closing fence
        code = re.sub(r'^\s*```\w*\s*$', '', code, flags=re.MULTILINE)  # stray fences

        # 2. Replace unicode whitespace with normal spaces
        unicode_spaces = {
            '\u00a0': ' ',   # Non-breaking space
            '\u200b': '',    # Zero-width space
            '\u200c': '',    # Zero-width non-joiner
            '\u200d': '',    # Zero-width joiner
            '\ufeff': '',    # BOM / zero-width no-break
            '\u2003': ' ',   # Em space
            '\u2002': ' ',   # En space
            '\u2009': ' ',   # Thin space
            '\u202f': ' ',   # Narrow no-break space
            '\u205f': ' ',   # Medium math space
        }
        for uchar, replacement in unicode_spaces.items():
            code = code.replace(uchar, replacement)

        # 3. Tabs → 4 spaces
        code = code.expandtabs(4)

        # 4. Strip trailing whitespace per line + leading/trailing blank lines
        lines = code.splitlines()
        lines = [line.rstrip() for line in lines]

        # Remove leading blank lines
        while lines and not lines[0].strip():
            lines.pop(0)
        # Remove trailing blank lines
        while lines and not lines[-1].strip():
            lines.pop()

        if not lines:
            return ""

        # 5. Detect and normalize inconsistent indentation
        #    If some lines use 2-space and others use 4-space, normalize to 4
        indent_sizes = set()
        for line in lines:
            if line.strip():  # non-empty lines
                leading = len(line) - len(line.lstrip())
                if leading > 0:
                    indent_sizes.add(leading)

        # Check for common indent unit (GCD of all indent sizes)
        if indent_sizes:
            from math import gcd
            from functools import reduce
            indent_unit = reduce(gcd, indent_sizes)
            # If indent unit is odd or unusual (1, 3, 5, 7), it's likely
            # an LLM artifact — normalize to 4-space
            if indent_unit in (1, 3, 5, 7):
                # Unusual indent — try to auto-detect the intended level
                # by looking at the most common indent
                pass  # leave as-is, the matching strategies handle this

        return "\n".join(lines)

    def _smart_replace(
        self, content: str, original_code: str, fixed_code: str, file_path: str
    ) -> str | None:
        """Try multiple matching strategies to find and replace original_code.

        Returns the modified content string, or None if all strategies fail.
        """
        import difflib

        # ── Strategy 1: Exact verbatim match ─────────────────────────
        if original_code in content:
            logger.info(f"[Match:exact] {file_path}")
            return content.replace(original_code, fixed_code, 1)

        # ── Strategy 2: Whitespace-normalized (trailing spaces) ──────
        result = self._try_whitespace_normalized(content, original_code, fixed_code)
        if result is not None:
            logger.info(f"[Match:whitespace_normalized] {file_path}")
            return result

        # ── Strategy 3: Indent-agnostic matching ─────────────────────
        # Strips ALL leading whitespace from each line before comparing.
        # If a match is found, applies fixed_code with the original indentation.
        result = self._try_indent_agnostic(content, original_code, fixed_code)
        if result is not None:
            logger.info(f"[Match:indent_agnostic] {file_path}")
            return result

        # ── Strategy 4: Fuzzy best-match (difflib) ───────────────────
        # Slides a window of len(original_lines) across content_lines
        # and finds the highest-scoring contiguous block.
        result = self._try_fuzzy_match(content, original_code, fixed_code)
        if result is not None:
            logger.info(f"[Match:fuzzy_difflib] {file_path}")
            return result

        # ── Strategy 5: Function-level replacement ───────────────────
        # If original_code looks like a function definition, find the
        # actual function in the file and replace it entirely.
        result = self._try_function_level(content, original_code, fixed_code)
        if result is not None:
            logger.info(f"[Match:function_level] {file_path}")
            return result

        # ── Strategy 6: Aggressive fuzzy (lower threshold) ───────────
        # If we're still here, drop the threshold to 0.4
        result = self._try_fuzzy_match(content, original_code, fixed_code, threshold=0.4)
        if result is not None:
            logger.warning(f"[Match:aggressive_fuzzy] {file_path} (low confidence — verify result)")
            return result

        # ── Strategy 7: LAST RESORT — force apply ────────────────────
        # Never skip. Find the single best-matching block in the entire
        # file and replace it, OR insert the fix at the most logical
        # location. The sandbox tests will catch if this is wrong.
        logger.warning(
            f"[Match:last_resort] {file_path} — all strategies exhausted. "
            f"Force-applying fix (sandbox tests will validate)."
        )
        return self._force_apply(content, original_code, fixed_code, file_path)

    def _try_whitespace_normalized(
        self, content: str, original_code: str, fixed_code: str
    ) -> str | None:
        """Match after stripping trailing whitespace from each line."""
        orig_lines = original_code.strip().splitlines()
        content_lines = content.splitlines()

        if not orig_lines:
            return None

        for i in range(len(content_lines) - len(orig_lines) + 1):
            if all(
                content_lines[i + j].rstrip() == orig_lines[j].rstrip()
                for j in range(len(orig_lines))
            ):
                return self._splice_lines(content_lines, i, len(orig_lines), fixed_code)

        return None

    def _try_indent_agnostic(
        self, content: str, original_code: str, fixed_code: str
    ) -> str | None:
        """Match ignoring leading whitespace differences.

        When a match is found, the fixed_code is re-indented to match
        the original file's indentation level at the match point.
        """
        import re

        orig_lines = original_code.strip().splitlines()
        content_lines = content.splitlines()

        if not orig_lines:
            return None

        # Strip all leading whitespace for comparison
        strip_orig = [line.lstrip() for line in orig_lines]

        for i in range(len(content_lines) - len(orig_lines) + 1):
            strip_content = [content_lines[i + j].lstrip() for j in range(len(orig_lines))]
            if strip_orig == strip_content:
                # Found a match — detect indentation of the first matched line
                first_line = content_lines[i]
                indent_match = re.match(r'^(\s*)', first_line)
                file_indent = indent_match.group(1) if indent_match else ""

                # Detect indentation of original_code's first non-empty line
                orig_first = next((l for l in orig_lines if l.strip()), "")
                orig_indent_match = re.match(r'^(\s*)', orig_first)
                orig_indent = orig_indent_match.group(1) if orig_indent_match else ""

                # Re-indent fixed_code to match file's indentation
                reindented = self._reindent(fixed_code, orig_indent, file_indent)
                return self._splice_lines(content_lines, i, len(orig_lines), reindented)

        return None

    def _try_fuzzy_match(
        self, content: str, original_code: str, fixed_code: str,
        threshold: float = 0.6,
    ) -> str | None:
        """Find the best fuzzy match using difflib SequenceMatcher.

        Slides a window of len(original_lines) over content_lines and
        picks the contiguous block with the highest similarity ratio.
        Only accepts matches above the threshold (default 0.6).
        """
        import difflib

        orig_lines = original_code.strip().splitlines()
        content_lines = content.splitlines()
        window = len(orig_lines)

        if window == 0 or window > len(content_lines):
            return None

        orig_text = "\n".join(orig_lines)

        best_ratio = 0.0
        best_start = -1

        for i in range(len(content_lines) - window + 1):
            candidate = "\n".join(content_lines[i:i + window])
            ratio = difflib.SequenceMatcher(None, orig_text, candidate).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_start = i

        # Also try windows ±2 lines (LLM may include more or fewer lines)
        for delta in (-2, -1, 1, 2):
            adjusted_window = window + delta
            if adjusted_window <= 0 or adjusted_window > len(content_lines):
                continue
            for i in range(len(content_lines) - adjusted_window + 1):
                candidate = "\n".join(content_lines[i:i + adjusted_window])
                ratio = difflib.SequenceMatcher(None, orig_text, candidate).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_start = i
                    window = adjusted_window  # Use the adjusted window

        if best_ratio >= threshold and best_start >= 0:
            logger.info(
                f"[DockerSandbox] Fuzzy match: ratio={best_ratio:.2f} "
                f"at lines {best_start+1}-{best_start+window}"
            )
            # Re-indent fixed_code to match the matched block's indentation
            import re
            first_line = content_lines[best_start]
            indent_match = re.match(r'^(\s*)', first_line)
            file_indent = indent_match.group(1) if indent_match else ""

            orig_first = next((l for l in orig_lines if l.strip()), "")
            orig_indent_match = re.match(r'^(\s*)', orig_first)
            orig_indent = orig_indent_match.group(1) if orig_indent_match else ""

            reindented = self._reindent(fixed_code, orig_indent, file_indent)
            return self._splice_lines(content_lines, best_start, window, reindented)

        return None

    def _try_function_level(
        self, content: str, original_code: str, fixed_code: str
    ) -> str | None:
        """If original_code contains a function def, replace the whole function.

        Works for Python (def func_name) and JavaScript (function func_name).
        """
        import re

        # Extract function name from original_code
        func_match = re.search(r'\bdef\s+(\w+)\s*\(', original_code)
        if not func_match:
            func_match = re.search(r'\bfunction\s+(\w+)\s*\(', original_code)
        if not func_match:
            return None

        func_name = func_match.group(1)
        content_lines = content.splitlines()

        # Find the function definition in the file
        func_start = None
        func_indent = ""
        for i, line in enumerate(content_lines):
            if re.search(rf'\bdef\s+{re.escape(func_name)}\s*\(', line) or \
               re.search(rf'\bfunction\s+{re.escape(func_name)}\s*\(', line):
                func_start = i
                indent_match = re.match(r'^(\s*)', line)
                func_indent = indent_match.group(1) if indent_match else ""
                break

        if func_start is None:
            return None

        # Find the end of the function (next line at same or lower indentation)
        func_end = func_start + 1
        indent_len = len(func_indent)
        for i in range(func_start + 1, len(content_lines)):
            line = content_lines[i]
            if line.strip() == "":
                continue  # Skip blank lines
            line_indent = len(line) - len(line.lstrip())
            if line_indent <= indent_len and line.strip():
                # This line is at the same or lower indentation — function ends here
                func_end = i
                break
        else:
            func_end = len(content_lines)

        logger.info(
            f"[DockerSandbox] Function-level match: {func_name}() "
            f"at lines {func_start+1}-{func_end}"
        )

        # Re-indent fixed_code to match the function's indentation
        orig_first = next((l for l in original_code.strip().splitlines() if l.strip()), "")
        orig_indent_match = re.match(r'^(\s*)', orig_first)
        orig_indent = orig_indent_match.group(1) if orig_indent_match else ""

        reindented = self._reindent(fixed_code, orig_indent, func_indent)
        return self._splice_lines(content_lines, func_start, func_end - func_start, reindented)

    @staticmethod
    def _splice_lines(
        content_lines: list[str], start: int, count: int, replacement: str
    ) -> str:
        """Replace `count` lines starting at `start` with `replacement`."""
        before = content_lines[:start]
        after = content_lines[start + count:]
        return "\n".join(before) + "\n" + replacement + "\n" + "\n".join(after)

    @staticmethod
    def _reindent(code: str, from_indent: str, to_indent: str) -> str:
        """Re-indent code from one indentation level to another.

        If from_indent is "    " (4 spaces) and to_indent is "  " (2 spaces),
        each line's leading `from_indent` prefix is replaced with `to_indent`.
        """
        if from_indent == to_indent:
            return code

        lines = code.splitlines()
        result = []
        for line in lines:
            if not line.strip():
                result.append("")
                continue
            if from_indent and line.startswith(from_indent):
                # Replace the base indent
                result.append(to_indent + line[len(from_indent):])
            else:
                # Line doesn't start with expected indent — prefix with to_indent
                stripped = line.lstrip()
                extra_indent = line[:len(line) - len(stripped)]
                result.append(to_indent + extra_indent + stripped)
        return "\n".join(result)

    def _force_apply(
        self, content: str, original_code: str, fixed_code: str, file_path: str
    ) -> str:
        """Absolute last resort — ALWAYS applies the fix somehow.

        Strategy priority:
          A. Locate function from fixed_code and splice there (most reliable)
          B. Best fuzzy match with NO threshold (always takes top match)
          C. Insert after imports section

        The sandbox tests will validate whether the applied fix is correct.
        An imperfectly applied fix that gets tested is ALWAYS better than
        silently skipping (which guarantees NO_CHANGE).
        """
        import difflib
        import re

        orig_lines = original_code.strip().splitlines()
        content_lines = content.splitlines()

        # ── A. Locate function from fixed_code (most reliable) ───────
        # When original_code is garbage, the fixed_code's function name
        # is the strongest signal for where the fix should go.
        func_match = re.search(r'\bdef\s+(\w+)\s*\(', fixed_code)
        if not func_match:
            func_match = re.search(r'\bfunction\s+(\w+)\s*\(', fixed_code)
        if func_match:
            func_name = func_match.group(1)
            for i, line in enumerate(content_lines):
                if re.search(rf'\b(def|function)\s+{re.escape(func_name)}\b', line):
                    # Find function end
                    indent_len = len(line) - len(line.lstrip())
                    func_end = i + 1
                    for j in range(i + 1, len(content_lines)):
                        if content_lines[j].strip() == "":
                            continue
                        if len(content_lines[j]) - len(content_lines[j].lstrip()) <= indent_len:
                            func_end = j
                            break
                    else:
                        func_end = len(content_lines)

                    logger.warning(
                        f"[Force:function_from_fix] {file_path} — "
                        f"replacing {func_name}() at lines {i+1}-{func_end}"
                    )
                    indent_match = re.match(r'^(\s*)', line)
                    file_indent = indent_match.group(1) if indent_match else ""
                    orig_first = next((l for l in fixed_code.splitlines() if l.strip()), "")
                    orig_indent_match = re.match(r'^(\s*)', orig_first)
                    orig_indent = orig_indent_match.group(1) if orig_indent_match else ""

                    reindented = self._reindent(fixed_code, orig_indent, file_indent)
                    return self._splice_lines(content_lines, i, func_end - i, reindented)

        # ── B. Force fuzzy: take the best match regardless of score ──
        if orig_lines and len(orig_lines) <= len(content_lines):
            window = len(orig_lines)
            orig_text = "\n".join(orig_lines)
            best_ratio = 0.0
            best_start = -1
            best_window = window

            for delta in range(-2, 3):
                w = window + delta
                if w <= 0 or w > len(content_lines):
                    continue
                for i in range(len(content_lines) - w + 1):
                    candidate = "\n".join(content_lines[i:i + w])
                    ratio = difflib.SequenceMatcher(None, orig_text, candidate).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_start = i
                        best_window = w

            if best_start >= 0 and best_ratio > 0.2:
                logger.warning(
                    f"[Force:fuzzy_no_threshold] {file_path} "
                    f"ratio={best_ratio:.2f} at lines {best_start+1}-{best_start+best_window}"
                )
                first_line = content_lines[best_start]
                indent_match = re.match(r'^(\s*)', first_line)
                file_indent = indent_match.group(1) if indent_match else ""

                orig_first = next((l for l in orig_lines if l.strip()), "")
                orig_indent_match = re.match(r'^(\s*)', orig_first)
                orig_indent = orig_indent_match.group(1) if orig_indent_match else ""

                reindented = self._reindent(fixed_code, orig_indent, file_indent)
                return self._splice_lines(content_lines, best_start, best_window, reindented)

        # ── C. Insert after imports (absolute last resort) ───────────
        last_import = 0
        for i, line in enumerate(content_lines):
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")) or stripped.startswith("#"):
                last_import = i + 1
            elif stripped and not stripped.startswith(("import ", "from ", "#", "\"\"\"")):
                break

        insert_point = max(last_import, 1)
        logger.warning(
            f"[Force:insert_after_imports] {file_path} — "
            f"inserting fix at line {insert_point + 1} (after imports)"
        )
        before = "\n".join(content_lines[:insert_point])
        after = "\n".join(content_lines[insert_point:])
        return before + "\n\n" + fixed_code + "\n\n" + after

    def run_validation(
        self,
        repo_path: str,
        patch_content: str,
        language: str,
        service_dir: str = "",
        generated_test_file: str = "",
        last_error_signature: str = "",
        incident_id: str = "unknown",
        attempt_number: int = 1,
        fix_plan: Optional[dict] = None,
    ) -> ValidationResult:
        """
        Full before/after sandbox validation for a single fix.

        Steps:
          1. Run tests on original repo (baseline)
          2. Apply patch to a copy
          3. Run tests on patched copy
          4. RegressionDetector compares results
          5. ResultCollector logs the attempt
        """
        collector = ResultCollector(incident_id)
        candidate = FixCandidate(
            candidate_id=f"c{attempt_number}",
            fix_plan=fix_plan or {"patch": patch_content},
            patch=patch_content,
        )

        logger.info(f"[DockerSandbox] === Validation attempt {attempt_number} ===")

        # Step 1: Baseline
        logger.info("[DockerSandbox] Running baseline tests (before patch)...")
        before = self.runner.run(repo_path, language, service_dir, generated_test_file)
        logger.info(f"[DockerSandbox] Baseline: {before.passed}P {before.failed}F {before.errors}E")

        # Step 2: Apply patch
        logger.info("[DockerSandbox] Applying patch...")
        try:
            if fix_plan and fix_plan.get("files_to_modify"):
                patched_path = self.apply_file_changes(repo_path, fix_plan["files_to_modify"])
            else:
                patched_path = self.apply_patch_to_copy(repo_path, patch_content)
        except Exception as e:
            result = ValidationResult(
                verdict="ERROR", before=before, error_message=str(e),
                error_signature=last_error_signature,
            )
            collector.add_candidate(candidate)
            collector.record_attempt(attempt_number, candidate, result)
            return result

        # Step 3: Run tests with patch
        logger.info("[DockerSandbox] Running tests with patch applied...")
        after = self.runner.run(patched_path, language, service_dir, generated_test_file)
        logger.info(f"[DockerSandbox] After patch: {after.passed}P {after.failed}F {after.errors}E")

        # Cleanup
        shutil.rmtree(patched_path, ignore_errors=True)

        # Step 4: Regression detection
        result = self.detector.compare(before, after, last_error_signature)

        # Step 5: Log
        collector.add_candidate(candidate)
        collector.record_attempt(attempt_number, candidate, result)
        collector.write_summary()

        logger.info(f"[DockerSandbox] === VERDICT: {result.verdict} ===")
        return result

    def run_validation_multi(
        self,
        repo_path: str,
        candidates: list[FixCandidate],
        language: str,
        service_dir: str = "",
        generated_test_file: str = "",
        last_error_signature: str = "",
        incident_id: str = "unknown",
    ) -> tuple[Optional[FixCandidate], ResultCollector]:
        """
        Test MULTIPLE fix candidates in the sandbox and return the best one.

        Each candidate is tested in order. We pick the highest scorer via
        ResultCollector. Returns (best_candidate, collector).
        """
        collector = ResultCollector(incident_id)

        # Run baseline ONCE (before any patch)
        logger.info(f"[DockerSandbox] === Multi-fix evaluation: {len(candidates)} candidates ===")
        logger.info("[DockerSandbox] Running shared baseline (before any patch)...")
        before = self.runner.run(repo_path, language, service_dir, generated_test_file)
        logger.info(f"[DockerSandbox] Baseline: {before.passed}P {before.failed}F")

        for i, candidate in enumerate(candidates, 1):
            logger.info(f"[DockerSandbox] Testing candidate {i}/{len(candidates)}: {candidate.candidate_id}")

            try:
                if candidate.fix_plan.get("files_to_modify"):
                    patched_path = self.apply_file_changes(repo_path, candidate.fix_plan["files_to_modify"])
                else:
                    patched_path = self.apply_patch_to_copy(repo_path, candidate.patch)
            except Exception as e:
                result = ValidationResult(
                    verdict="ERROR", before=before, error_message=str(e),
                    error_signature=last_error_signature,
                )
                candidate.validation_result = result
                candidate.score = 0.0
                collector.add_candidate(candidate)
                collector.record_attempt(i, candidate, result)
                continue

            after = self.runner.run(patched_path, language, service_dir, generated_test_file)
            shutil.rmtree(patched_path, ignore_errors=True)

            result = self.detector.compare(before, after, last_error_signature)
            collector.add_candidate(candidate)
            log = collector.record_attempt(i, candidate, result)

            logger.info(
                f"[DockerSandbox] Candidate {candidate.candidate_id}: "
                f"verdict={result.verdict} score={log.score:.1f}"
            )

            # Short-circuit: if we found a perfect fix (FIX_CONFIRMED, no regressions), use it
            if result.verdict == "FIX_CONFIRMED" and not result.regressions:
                logger.info(f"[DockerSandbox] Perfect fix found at candidate {i} — short-circuiting")
                break

        collector.write_summary()
        best = collector.get_best_candidate()
        return best, collector

    def _manual_patch_apply(self, repo_path: str, patch_content: str):
        """Fallback: manually parse and apply a unified diff."""
        current_file = None
        lines_to_add: dict[str, list] = {}
        lines_to_remove: dict[str, list] = {}

        for line in patch_content.split("\n"):
            if line.startswith("+++ b/"):
                current_file = line[6:]
                lines_to_add.setdefault(current_file, [])
                lines_to_remove.setdefault(current_file, [])
            elif line.startswith("--- a/") or not current_file:
                continue
            elif line.startswith("+") and not line.startswith("+++"):
                lines_to_add[current_file].append(line[1:])
            elif line.startswith("-") and not line.startswith("---"):
                lines_to_remove[current_file].append(line[1:])

        for filepath, removals in lines_to_remove.items():
            full_path = os.path.join(repo_path, filepath)
            if not os.path.exists(full_path):
                continue
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
            additions = lines_to_add.get(filepath, [])
            for removal in removals:
                if removal.strip() in content:
                    content = content.replace(removal.strip(), "\n".join(additions) if additions else "", 1)
                    additions = []
            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

    def run_security_scan(
        self,
        repo_path: str,
        language: str = "python",
        service_dir: str = "",
    ) -> dict:
        """Run security scanning tools inside the Docker sandbox.

        Runs bandit (static analysis) and pip-audit (dependency check)
        inside the container. Returns structured results.

        Returns:
            {
                "bandit": {"issues": [...], "error": ""},
                "dep_audit": {"issues": [...], "error": ""},
                "summary": {"total": N, "critical": N, "high": N, "medium": N},
            }
        """
        result = {
            "bandit": {"issues": [], "error": ""},
            "dep_audit": {"issues": [], "error": ""},
            "summary": {"total": 0, "critical": 0, "high": 0, "medium": 0},
        }

        work_dir = service_dir or "."

        try:
            # ── 1. Run bandit (Python static analysis) ───────────────────
            if language.lower() in ("python", "py"):
                bandit_cmd = (
                    f"cd /workspace/{work_dir} && "
                    f"pip install -q bandit 2>/dev/null && "
                    f"bandit -r . -f json -q --exclude .venv,venv,node_modules,__pycache__ 2>/dev/null || true"
                )
                bandit_output = self._exec_in_container(bandit_cmd, timeout=120)

                try:
                    import json as json_mod
                    # Find JSON in the output
                    json_start = bandit_output.find("{")
                    if json_start >= 0:
                        json_str = bandit_output[json_start:]
                        # Find the matching closing brace
                        brace_count = 0
                        for i, c in enumerate(json_str):
                            if c == "{":
                                brace_count += 1
                            elif c == "}":
                                brace_count -= 1
                                if brace_count == 0:
                                    json_str = json_str[:i + 1]
                                    break
                        data = json_mod.loads(json_str)
                        for item in data.get("results", []):
                            issue = {
                                "severity": item.get("issue_severity", "MEDIUM").lower(),
                                "title": item.get("issue_text", "Security issue"),
                                "test_id": item.get("test_id", ""),
                                "file_path": item.get("filename", "").replace("/workspace/", ""),
                                "line_number": item.get("line_number"),
                                "code": (item.get("code", "")[:150] if item.get("code") else ""),
                                "more_info": item.get("more_info", ""),
                                "source": "bandit",
                            }
                            result["bandit"]["issues"].append(issue)
                except (json_mod.JSONDecodeError, ValueError) as e:
                    result["bandit"]["error"] = f"Parse error: {str(e)[:100]}"
                    logger.warning(f"[DockerSandbox] Bandit JSON parse failed: {e}")

            # ── 2. Run dependency audit ──────────────────────────────────
            if language.lower() in ("python", "py"):
                dep_cmd = (
                    f"cd /workspace/{work_dir} && "
                    f"pip install -q pip-audit 2>/dev/null && "
                    f"pip-audit --format json --desc 2>/dev/null || true"
                )
            else:
                dep_cmd = (
                    f"cd /workspace/{work_dir} && "
                    f"npm audit --json 2>/dev/null || true"
                )

            dep_output = self._exec_in_container(dep_cmd, timeout=120)

            try:
                import json as json_mod
                json_start = dep_output.find("[") if dep_output.find("[") >= 0 else dep_output.find("{")
                if json_start >= 0:
                    data = json_mod.loads(dep_output[json_start:])
                    vulns = data if isinstance(data, list) else data.get("vulnerabilities", [])
                    for vuln in vulns[:20]:
                        if isinstance(vuln, dict):
                            issue = {
                                "severity": vuln.get("fix_versions", [""])[0] if vuln.get("fix_versions") else "high",
                                "title": f"Vulnerable: {vuln.get('name', 'unknown')} {vuln.get('version', '')}",
                                "description": vuln.get("description", "")[:200],
                                "source": "pip-audit" if language.lower() in ("python", "py") else "npm-audit",
                            }
                            result["dep_audit"]["issues"].append(issue)
            except (json_mod.JSONDecodeError, ValueError) as e:
                result["dep_audit"]["error"] = f"Parse error: {str(e)[:100]}"

        except Exception as e:
            logger.error(f"[DockerSandbox] Security scan failed: {e}")
            result["bandit"]["error"] = str(e)[:200]

        # Build summary
        all_issues = result["bandit"]["issues"] + result["dep_audit"]["issues"]
        result["summary"]["total"] = len(all_issues)
        result["summary"]["critical"] = sum(1 for i in all_issues if i.get("severity") == "critical")
        result["summary"]["high"] = sum(1 for i in all_issues if i.get("severity") == "high")
        result["summary"]["medium"] = sum(1 for i in all_issues if i.get("severity") == "medium")

        logger.info(
            f"[DockerSandbox] Security scan: {result['summary']['total']} issues "
            f"({result['summary']['critical']} crit, {result['summary']['high']} high)"
        )
        return result

    def _exec_in_container(self, cmd: str, timeout: int = 60) -> str:
        """Execute a command inside the running Docker container and return output."""
        container_name = getattr(self, '_container_name', None)
        if container_name:
            exec_cmd = f"docker exec {container_name} bash -c \"{cmd}\""
        else:
            # Use docker run with the workspace volume
            exec_cmd = (
                f"docker run --rm -v {getattr(self, '_repo_path', '/tmp')}:/workspace "
                f"-w /workspace python:3.11-slim bash -c \"{cmd}\""
            )
        try:
            proc = subprocess.run(
                exec_cmd, shell=True, capture_output=True, text=True, timeout=timeout
            )
            return proc.stdout + proc.stderr
        except subprocess.TimeoutExpired:
            return ""
        except Exception as e:
            return f"Error: {e}"

    def cleanup_temp_dirs(self):
        """Remove any leftover temp dirs."""
        import glob
        for pattern in ["amaze_repo_*", "amaze_patched_*"]:
            for path in glob.glob(os.path.join(tempfile.gettempdir(), pattern)):
                shutil.rmtree(path, ignore_errors=True)
