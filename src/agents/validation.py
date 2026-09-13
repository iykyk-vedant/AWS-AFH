"""
Validation Agent for Amaze on Work — Full Rebuild.

Pipeline:
  1. TestWriterAgent generates targeted tests for the incident
  2. MultiFixEvaluator generates N fix candidates + runs all through DockerSandbox
  3. RegressionDetector compares before/after, assigns verdict
  4. ResultCollector writes per-attempt JSON logs to reports/{incident_id}/
  5. Smart retry orchestration:
       - FIX_CONFIRMED / UNRELATED_FAILURE / SKIPPED → proceed to security
       - same error, attempt < 3 → pass best candidate to fix_writer with feedback
       - same error, attempt >= 3 → trigger WebResearcherAgent → fix_writer
       - new error → reset attempt counter → fix_writer with new error context
"""

import logging
import time
from pathlib import Path
from typing import Optional

from src.agents.base import BaseAgent, AgentResponse
from src.agents.state import PipelineState, AgentType
from src.agents.test_writer import TestWriterAgent
from src.llm.base_client import BaseLLMClient
from src.sandbox.docker_runner import DockerSandbox, FixCandidate, ValidationResult
from src.sandbox.multi_fix_evaluator import MultiFixEvaluator
from src.utils.language_detector import detect_service_language, get_service_dir

logger = logging.getLogger(__name__)

MAX_SAME_ERROR_RETRIES = 3


class ValidationAgent(BaseAgent):
    """
    Validates fixes using Docker sandbox before/after test comparison.

    Orchestrates: TestWriterAgent → MultiFixEvaluator → RegressionDetector → ResultCollector
    Implements smart retry: same error → max 3 tries → WebResearcher fallback.
    """
    agent_type = AgentType.VALIDATION

    def __init__(self, llm_client: BaseLLMClient, sandbox: Optional[DockerSandbox] = None):
        super().__init__(llm_client)
        self.sandbox = sandbox or DockerSandbox()
        self.test_writer = TestWriterAgent(llm_client)
        self.multi_fix_evaluator = MultiFixEvaluator(llm_client, self.sandbox)

    def execute(self, state: PipelineState) -> AgentResponse:
        incident = state.get("incident", {}) or {}
        fix_plan = state.get("fix_plan", {}) or {}
        root_cause = state.get("root_cause", {}) or {}
        repo_url = state.get("repo_url", "")

        incident_id = incident.get("id", "unknown")
        service = incident.get("affected_service", "")
        language = detect_service_language(service)
        service_dir = get_service_dir(service)

        # Fetch retry counters from state
        attempt_number = (state.get("validation_attempt") or 0) + 1
        last_error_sig = state.get("last_error_signature", "") or ""
        validation_logs = state.get("validation_logs") or []

        patch = fix_plan.get("patch", "")
        has_fix = bool(fix_plan.get("files_to_modify") or patch)

        # ── Docker health check ────────────────────────────────────────────────
        try:
            health = self.sandbox.health_check()
        except Exception as e:
            health = {"docker_available": False}
            logger.warning(f"[Validation] Docker health check exception: {e}")

        if not health.get("docker_available"):
            msg = "Docker not available — skipping sandbox validation"
            logger.warning(f"[Validation] {msg}")
            return self._skip(
                state, message=f"Validation SKIPPED ({msg})", incident_id=incident_id
            )

        if not has_fix:
            msg = "No patch/files_to_modify in fix_plan — skipping validation"
            logger.warning(f"[Validation] {msg}")
            return self._skip(
                state, message=f"Validation SKIPPED ({msg})", incident_id=incident_id
            )

        # ── Step 1: Generate targeted tests ──────────────────────────────────
        logger.info(f"[Validation] Attempt #{attempt_number} | Generating test cases...")
        generated_test = ""
        try:
            generated_test = self.test_writer.generate(incident, root_cause, fix_plan, language)
            if generated_test:
                state["characterization_test"] = generated_test
                logger.info(f"[Validation] TestWriter produced {len(generated_test)} chars of test code")
            else:
                logger.warning("[Validation] TestWriter returned empty — will run existing repo tests only")
        except Exception as e:
            logger.warning(f"[Validation] TestWriter failed: {e} — continuing without generated tests")

        # ── Step 2: Clone repo ────────────────────────────────────────────────
        repo_path = None
        try:
            logger.info(f"[Validation] Cloning {repo_url}...")
            repo_path = self.sandbox.clone_repo(repo_url)
        except Exception as e:
            logger.error(f"[Validation] Repo clone failed: {e}")
            return self._skip(
                state,
                message=f"Validation SKIPPED (clone failed: {str(e)[:100]})",
                incident_id=incident_id,
            )

        # ── Step 3: Multi-fix evaluation ──────────────────────────────────────
        logger.info(f"[Validation] Running MultiFixEvaluator (3 candidates)...")
        try:
            best, collector, all_scored = self.multi_fix_evaluator.run(
                state=state,
                repo_path=repo_path,
                language=language,
                service_dir=service_dir,
                generated_test_file=generated_test,
                last_error_signature=last_error_sig,
                incident_id=incident_id,
                num_candidates=3,
            )
        except Exception as e:
            logger.error(f"[Validation] MultiFixEvaluator failed: {e}")
            return self._error(state, f"Sandbox evaluation failed: {str(e)[:200]}", incident_id)

        # Cleanup
        import shutil
        shutil.rmtree(repo_path, ignore_errors=True)

        # ── Step 4: Determine result ──────────────────────────────────────────
        if not best or not best.validation_result:
            logger.warning("[Validation] No valid candidate result — treating as ERROR")
            return self._error(state, "No valid candidate results from evaluator", incident_id)

        result = best.validation_result
        current_sig = result.error_signature or ""
        is_new_error = result.is_new_error and bool(last_error_sig)

        # Update state with best fix candidate
        if best.fix_plan:
            state["fix_plan"] = best.fix_plan  # update state with best fix

        # Store ALL scored candidates for the risk scorer to evaluate
        state["all_fix_candidates"] = all_scored

        # Append attempt to validation_logs
        attempt_entries = collector.get_attempt_history()
        state["validation_logs"] = (validation_logs or []) + attempt_entries
        state["last_error_signature"] = current_sig
        state["validation_attempt"] = attempt_number

        # ── Step 5: Smart retry decision ─────────────────────────────────────
        verdict = result.verdict

        verdict_icons = {
            "FIX_CONFIRMED": "[OK]",
            "FIX_APPLIED_NO_TESTS": "[APPLIED]",
            "REGRESSION_DETECTED": "[REGRESSION]",
            "NO_CHANGE": "[NO CHANGE]",
            "UNRELATED_FAILURE": "[NO REGRESSION]",
            "ERROR": "[ERROR]",
        }
        icon = verdict_icons.get(verdict, "")

        if verdict == "FIX_CONFIRMED":
            msg = (
                f"{icon} FIX_CONFIRMED (candidate: {best.candidate_id}, "
                f"score: {best.score:.0f}/100) — "
                f"{len(result.fixes_confirmed)} test(s) fixed, 0 regressions"
            )
            logger.info(f"[Validation] {msg}")
            return AgentResponse(
                success=True,
                message=msg,
                data=self._build_state_result(result, best, collector.get_attempt_history()),
                next_agent=AgentType.SECURITY.value,
                needs_retry=False,
            )

        if verdict == "FIX_APPLIED_NO_TESTS":
            msg = (
                f"{icon} Fix applied but no tests found to validate. "
                f"Proceeding to security review (candidate: {best.candidate_id}, "
                f"score: {best.score:.0f}/100)"
            )
            logger.info(f"[Validation] {msg}")
            return AgentResponse(
                success=True,
                message=msg,
                data=self._build_state_result(result, best, collector.get_attempt_history()),
                next_agent=AgentType.SECURITY.value,
                needs_retry=False,
            )

        if verdict in ("UNRELATED_FAILURE", "ERROR"):
            msg = (
                f"{icon} {verdict} — pre-existing failures unchanged, fix safe to proceed"
                if verdict == "UNRELATED_FAILURE"
                else f"{icon} Infra error — proceeding to synthesis"
            )
            logger.info(f"[Validation] {msg}")
            return AgentResponse(
                success=True,
                message=msg,
                data=self._build_state_result(result, best, collector.get_attempt_history()),
                next_agent=AgentType.SECURITY.value,
                needs_retry=False,
            )

        # REGRESSION_DETECTED or NO_CHANGE → retry logic
        if verdict in ("REGRESSION_DETECTED", "NO_CHANGE"):
            if is_new_error:
                # New error — reset counter, go back to fix_writer
                state["validation_attempt"] = 0
                state["last_error_signature"] = current_sig
                msg = (
                    f"{icon} {verdict} — NEW error detected (sig changed). "
                    f"Resetting retry counter → fix_writer for new error"
                )
                logger.warning(f"[Validation] {msg}")
                return AgentResponse(
                    success=False,
                    message=msg,
                    data=self._build_state_result(result, best, collector.get_attempt_history()),
                    next_agent=AgentType.FIX_WRITER.value,
                    needs_retry=True,
                )

            if attempt_number >= MAX_SAME_ERROR_RETRIES:
                # 3 strikes — trigger WebResearcher fallback
                msg = (
                    f"{icon} {verdict} — Same error after {attempt_number} attempt(s). "
                    f"Triggering WebResearcher (StackOverflow fallback)..."
                )
                logger.warning(f"[Validation] {msg}")
                return AgentResponse(
                    success=False,
                    message=msg,
                    data=self._build_state_result(result, best, collector.get_attempt_history()),
                    next_agent=AgentType.WEB_RESEARCHER.value,
                    needs_retry=True,
                )

            # Still retries remaining — pass feedback to fix_writer
            feedback = self._build_retry_feedback(result, best, collector.get_attempt_history())
            state["validation_feedback"] = feedback
            msg = (
                f"{icon} {verdict} — Same error, attempt {attempt_number}/{MAX_SAME_ERROR_RETRIES}. "
                f"Passing diagnosis to fix_writer..."
            )
            logger.warning(f"[Validation] {msg}")
            return AgentResponse(
                success=False,
                message=msg,
                data=self._build_state_result(result, best, collector.get_attempt_history()),
                next_agent=AgentType.FIX_WRITER.value,
                needs_retry=True,
            )

        # Fallback
        return AgentResponse(
            success=True,
            message=f"Validation complete: {verdict}",
            data=self._build_state_result(result, best, collector.get_attempt_history()),
            next_agent=AgentType.SECURITY.value,
            needs_retry=False,
        )

    async def aexecute(self, state: PipelineState) -> AgentResponse:
        return self.execute(state)

    def _skip(self, state: dict, message: str, incident_id: str) -> AgentResponse:
        state["validation_attempt"] = state.get("validation_attempt", 0)
        return AgentResponse(
            success=True,
            message=message,
            data={
                "verdict": "SKIPPED",
                "before": {},
                "after": {},
                "regressions": [],
                "fixes_confirmed": [],
                "unrelated_failures": [],
                "attempt_logs": [],
            },
            next_agent=AgentType.SECURITY.value,
            needs_retry=False,
        )

    def _error(self, state: dict, message: str, incident_id: str) -> AgentResponse:
        return AgentResponse(
            success=True,
            message=f"Validation ERROR (proceeding): {message}",
            data={
                "verdict": "ERROR",
                "before": {},
                "after": {},
                "regressions": [],
                "fixes_confirmed": [],
                "unrelated_failures": [],
                "attempt_logs": [],
            },
            next_agent=AgentType.SECURITY.value,
            needs_retry=False,
        )

    @staticmethod
    def _build_state_result(
        result: ValidationResult, best: FixCandidate, attempt_logs: list
    ) -> dict:
        before = result.before or type("_", (), {"passed": 0, "failed": 0, "errors": 0, "total": 0, "failed_tests": [], "stdout": "", "runtime_seconds": 0})()
        after = result.after or type("_", (), {"passed": 0, "failed": 0, "errors": 0, "total": 0, "failed_tests": [], "stdout": "", "runtime_seconds": 0})()
        return {
            "verdict": result.verdict,
            "best_candidate": best.candidate_id,
            "best_score": best.score,
            "before": {
                "total": before.total,
                "passed": before.passed,
                "failed": before.failed,
                "errors": before.errors,
                "failed_tests": before.failed_tests,
                "stdout": (before.stdout or "")[:2000],
                "runtime_seconds": before.runtime_seconds,
            },
            "after": {
                "total": after.total,
                "passed": after.passed,
                "failed": after.failed,
                "errors": after.errors,
                "failed_tests": after.failed_tests,
                "stdout": (after.stdout or "")[:2000],
                "runtime_seconds": after.runtime_seconds,
            },
            "fixes_confirmed": result.fixes_confirmed,
            "regressions": result.regressions,
            "unrelated_failures": result.unrelated_failures,
            "error_signature": result.error_signature,
            "is_new_error": result.is_new_error,
            "attempt_logs": attempt_logs,
        }

    @staticmethod
    def _build_retry_feedback(
        result: ValidationResult, best: FixCandidate, attempt_logs: list
    ) -> str:
        """Build a feedback string for fix_writer to improve its next fix."""
        lines = []
        if result.regressions:
            lines.append(f"REGRESSIONS introduced: {', '.join(result.regressions[:5])}")
        if result.verdict == "NO_CHANGE":
            lines.append("The fix had NO EFFECT on test outcomes. The change was likely not applied to the right code path.")
        after = result.after
        if after and after.failed_tests:
            lines.append(f"Still failing tests: {', '.join(after.failed_tests[:5])}")
        if after and after.stdout:
            # Include last 500 chars of test output as context
            output_tail = after.stdout[-500:].strip()
            if output_tail:
                lines.append(f"Test output tail:\n{output_tail}")
        return "\n".join(lines) or "Previous fix did not pass validation. Rethink the approach."

    def get_system_prompt(self) -> str:
        return (
            "You are the Validation Agent in Amaze on Work. "
            "You orchestrate test generation, multi-fix candidate evaluation, "
            "and smart retry with StackOverflow fallback to ensure every fix is thoroughly validated."
        )
