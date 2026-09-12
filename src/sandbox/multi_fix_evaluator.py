"""
Multi-Fix Evaluator for Amaze on Work.

Instructs the FixWriterAgent to generate N diverse fix candidates,
then runs each through the Docker sandbox and returns the best-scoring one.

Scoring criteria:
  40pts — Tests fixed (before_failed → after_passed)
  30pts — Zero regressions
  20pts — Patch size (smaller = more surgical)
  10pts — Error signature cleared

Short-circuits if a perfect FIX_CONFIRMED + no regressions candidate is found.
"""

import logging
from typing import Optional

from src.llm.base_client import BaseLLMClient
from src.sandbox.docker_runner import DockerSandbox, FixCandidate, ResultCollector

logger = logging.getLogger(__name__)

_NUM_CANDIDATES = 3


class MultiFixEvaluator:
    """
    Generates and evaluates multiple fix candidates to find the optimal fix.

    Usage:
        evaluator = MultiFixEvaluator(llm_client, sandbox)
        best, collector = evaluator.run(
            state=state,
            repo_path=repo_path,
            language="python",
            service_dir="python-service",
            generated_test_file=test_code,
            incident_id="INC-0009",
        )
        if best:
            optimal_fix_plan = best.fix_plan
    """

    def __init__(self, llm_client: BaseLLMClient, sandbox: DockerSandbox):
        self.llm = llm_client
        self.sandbox = sandbox

    def run(
        self,
        state: dict,
        repo_path: str,
        language: str,
        service_dir: str = "",
        generated_test_file: str = "",
        last_error_signature: str = "",
        incident_id: str = "unknown",
        num_candidates: int = _NUM_CANDIDATES,
    ) -> tuple[Optional[FixCandidate], ResultCollector]:
        """
        Generate N fix candidates and evaluate each in the sandbox.

        Returns:
            (best_candidate, result_collector) — best may be None if all fail.
        """
        logger.info(f"[MultiFixEvaluator] Generating {num_candidates} fix candidates...")

        candidates = self._generate_candidates(state, num_candidates, language)

        if not candidates:
            logger.warning("[MultiFixEvaluator] No candidates generated — falling back to existing fix_plan")
            existing_fix = state.get("fix_plan", {}) or {}
            candidates = [
                FixCandidate(
                    candidate_id="c1_existing",
                    fix_plan=existing_fix,
                    patch=existing_fix.get("patch", ""),
                )
            ]

        logger.info(f"[MultiFixEvaluator] Running {len(candidates)} candidate(s) through sandbox...")

        best, collector = self.sandbox.run_validation_multi(
            repo_path=repo_path,
            candidates=candidates,
            language=language,
            service_dir=service_dir,
            generated_test_file=generated_test_file,
            last_error_signature=last_error_signature,
            incident_id=incident_id,
        )

        # Collect ALL scored candidates for the risk scorer to rank
        all_scored = []
        for c in collector._candidates if hasattr(collector, '_candidates') else []:
            entry = {
                "candidate_id": c.candidate_id,
                "fix_plan": c.fix_plan,
                "patch": c.patch,
                "score": c.score,
                "rank": c.rank,
                "verdict": c.validation_result.verdict if c.validation_result else "UNKNOWN",
            }
            all_scored.append(entry)

        if best:
            logger.info(
                f"[MultiFixEvaluator] Best: {best.candidate_id} "
                f"(score={best.score:.1f}, verdict={best.validation_result.verdict})"
            )
        else:
            logger.warning("[MultiFixEvaluator] No viable fix candidate found")

        return best, collector, all_scored

    def _generate_candidates(
        self, state: dict, n: int, language: str
    ) -> list[FixCandidate]:
        """
        Generate N diverse fix candidates via LLM prompting with different strategies.

        Strategy variation (one per candidate):
          1. Minimal/surgical fix — change as few lines as possible
          2. Defensive fix — add guards, validation, idempotency checks
          3. Refactored approach — slightly restructure the offending function
        """
        incident = state.get("incident", {}) or {}
        root_cause = state.get("root_cause", {}) or {}
        existing_fix = state.get("fix_plan", {}) or {}
        code_snippets = root_cause.get("code_snippets", {}) or {}

        # Build context
        title = incident.get("title", "")
        hypothesis = root_cause.get("hypothesis", "") or root_cause.get("root_cause_summary", "")
        suspect_files = root_cause.get("suspect_files", [])

        file_ctx = ""
        for fp, content in list(code_snippets.items())[:4]:
            file_ctx += f"\n=== {fp} ===\n{str(content)[:4000]}\n"

        # Variation strategies
        strategies = [
            ("minimal",
             "Apply the SMALLEST possible change — fix only the exact lines causing the bug. "
             "DO NOT refactor, rename, or restructure anything."),
            ("defensive",
             "Apply a DEFENSIVE fix — add guards, idempotency checks, null checks, or validation "
             "to prevent the bug class from recurring. Still keep it minimal."),
            ("restructured",
             "Apply a CLEAN fix — you may slightly restructure the offending function if it makes "
             "the fix clearer, but keep all changes within the same file."),
        ]

        candidates = []
        for i, (strategy_name, strategy_desc) in enumerate(strategies[:n], 1):
            candidate = self._generate_one_candidate(
                candidate_id=f"c{i}_{strategy_name}",
                strategy_desc=strategy_desc,
                title=title,
                hypothesis=hypothesis,
                suspect_files=suspect_files,
                file_ctx=file_ctx,
                existing_fix=existing_fix,
                language=language,
            )
            if candidate:
                candidates.append(candidate)

        return candidates

    def _generate_one_candidate(
        self,
        candidate_id: str,
        strategy_desc: str,
        title: str,
        hypothesis: str,
        suspect_files: list,
        file_ctx: str,
        existing_fix: dict,
        language: str,
    ) -> Optional[FixCandidate]:
        """LLM call to generate a single fix candidate with a given strategy."""

        existing_summary = ""
        for ch in existing_fix.get("files_to_modify", []):
            existing_summary += (
                f"File: {ch.get('file_path')}\n"
                f"Original: {ch.get('original_code', '')[:200]}\n"
                f"Fixed: {ch.get('fixed_code', '')[:200]}\n"
            )

        prompt = f"""You are a Senior SFE writing a production fix for an incident.

=== INCIDENT ===
{title}

=== ROOT CAUSE ===
{hypothesis}

=== SUSPECT FILES ===
{', '.join(suspect_files[:5])}

=== CODE CONTEXT ===
{file_ctx}

=== PREVIOUS FIX ATTEMPT (for reference, generate a DIFFERENT approach) ===
{existing_summary or 'None'}

=== YOUR STRATEGY FOR THIS ATTEMPT ===
{strategy_desc}

Return ONLY valid JSON in this exact format (no markdown, no explanation):
{{
  "description": "One sentence summary of this fix",
  "files_to_modify": [
    {{
      "file_path": "relative/path/to/file.py",
      "original_code": "EXACT lines to replace (verbatim from source)",
      "fixed_code": "Replacement code (same indentation)",
      "rationale": "Why this change fixes the root cause"
    }}
  ],
  "patch": "unified diff string (optional, can be empty)"
}}"""

        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a Senior SFE generating a targeted code fix. "
                        "Return ONLY valid JSON. No markdown fences, no extra text."
                    ),
                },
                {"role": "user", "content": prompt},
            ]
            response = self.llm.complete(messages)
            content = response.content if hasattr(response, "content") else str(response)

            # Extract JSON
            import json, re
            json_match = re.search(r"\{.*\}", content, re.DOTALL)
            if not json_match:
                logger.warning(f"[MultiFixEvaluator] No JSON in response for {candidate_id}")
                return None

            fix_plan = json.loads(json_match.group(0))
            return FixCandidate(
                candidate_id=candidate_id,
                fix_plan=fix_plan,
                patch=fix_plan.get("patch", ""),
            )
        except Exception as e:
            logger.error(f"[MultiFixEvaluator] Candidate {candidate_id} generation failed: {e}")
            return None
