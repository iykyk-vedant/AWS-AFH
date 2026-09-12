"""
Risk Scorer Agent for Amaze on Work.

Final gate before code is applied. Computes composite risk score using
principled multi-factor analysis and determines the deployment action:

  LOW  (0-24)  → auto_pr:           Auto-create PR with best fix
  MEDIUM (25-49) → pr_with_options:  PR with best fix + Slack with alternatives
  HIGH (50-100) → options_only:     Slack notification only, user picks fix

Scoring factors (100 pts total):
  30 — Blast Radius:        downstream callers from knowledge graph
  20 — Test Coverage:       whether tests exist for the modified area
  15 — Coupling Score:      fan_in × fan_out (structural risk)
  15 — Change Size:         lines changed (defect correlation)
  10 — Environment:         prod > staging > dev
  10 — Cyclomatic Delta:    complexity added by the fix

Weights derived from failure mode impact analysis:
  - Blast radius is #1 because broken widely-called functions cascade failures
  - Test coverage is #2 because untested changes are 4x more likely to regress
  - Coupling and change size are moderate structural risk signals
  - Environment and complexity are constant multipliers, not code quality signals
"""

import logging
import re
from typing import Optional

from src.agents.base import BaseAgent, AgentResponse
from src.agents.state import (
    PipelineState,
    AgentType,
    RiskLevel,
    RiskAssessment,
)
from src.llm.base_client import BaseLLMClient
from src.graph.query_interface import KnowledgeGraphQuery
from src.mcp.github_tools import GitHubMCPTools
from src.utils.patch_writer import count_changed_lines

logger = logging.getLogger(__name__)


class RiskScorerAgent(BaseAgent):
    """
    Computes composite risk score before any code is committed.

    Now evaluates ALL fix candidates (not just one) and assigns
    a deployment_action based on the composite risk level.
    """
    agent_type = AgentType.RISK_SCORER

    # ── Scoring weights (principled, see module docstring) ──────────
    W_BLAST_RADIUS = 30
    W_TEST_COVERAGE = 20
    W_COUPLING = 15
    W_CHANGE_SIZE = 15
    W_ENVIRONMENT = 10
    W_CYCLOMATIC = 10

    def __init__(
        self,
        llm_client: BaseLLMClient,
        kg_query: Optional[KnowledgeGraphQuery] = None,
        github_tools: Optional[GitHubMCPTools] = None,
    ):
        super().__init__(llm_client)
        self.kg_query = kg_query
        self.github = github_tools

    def execute(self, state: PipelineState) -> AgentResponse:
        incident = state.get("incident", {})
        fix_plan = state.get("fix_plan", {})
        validation = state.get("validation_result", {})
        # All candidates from MultiFixEvaluator (if available)
        all_candidates = state.get("all_fix_candidates", [])

        risk = self._compute_risk(incident, fix_plan, validation, all_candidates)

        return AgentResponse(
            success=True,
            message=(
                f"Risk: {risk.get('level', 'MEDIUM')} "
                f"(score={risk.get('composite_score', 0)}) → "
                f"{risk.get('deployment_action', 'options_only')}"
            ),
            data=risk,
        )

    async def aexecute(self, state: PipelineState) -> AgentResponse:
        return self.execute(state)

    def _compute_risk(
        self,
        incident: dict,
        fix_plan: dict,
        validation: dict,
        all_candidates: list[dict] | None = None,
    ) -> RiskAssessment:
        """Compute composite risk score with principled multi-factor analysis."""
        breakdown = {}

        # ── 1. Blast Radius (0-30 pts) ──────────────────────────────
        # How many downstream callers are affected if this fix is wrong?
        blast_radius = self._compute_blast_radius(fix_plan)
        if blast_radius > 10:
            blast_score = self.W_BLAST_RADIUS          # 30
        elif blast_radius > 5:
            blast_score = int(self.W_BLAST_RADIUS * 0.67)  # 20
        elif blast_radius > 2:
            blast_score = int(self.W_BLAST_RADIUS * 0.33)  # 10
        else:
            blast_score = 0
        breakdown["blast_radius"] = {
            "value": blast_radius, "score": blast_score,
            "max": self.W_BLAST_RADIUS,
        }

        # ── 2. Test Coverage (0-20 pts) ─────────────────────────────
        # Untested changes are 4x more likely to cause regressions
        has_tests = False
        after = validation.get("after", {}) if isinstance(validation, dict) else {}
        if isinstance(after, dict) and after.get("total", 0) > 0:
            has_tests = True
        test_score = 0 if has_tests else self.W_TEST_COVERAGE
        breakdown["test_coverage"] = {
            "has_tests": has_tests, "score": test_score,
            "max": self.W_TEST_COVERAGE,
        }

        # ── 3. Coupling Score (0-15 pts) ────────────────────────────
        # High fan_in × fan_out = function at a crossroads
        coupling = self._compute_coupling(fix_plan)
        if coupling > 20:
            coupling_score = self.W_COUPLING               # 15
        elif coupling > 10:
            coupling_score = int(self.W_COUPLING * 0.67)    # 10
        elif coupling > 5:
            coupling_score = int(self.W_COUPLING * 0.33)    # 5
        else:
            coupling_score = 0
        breakdown["coupling"] = {
            "value": coupling, "score": coupling_score,
            "max": self.W_COUPLING,
        }

        # ── 4. Change Size (0-15 pts) ───────────────────────────────
        # Lines changed correlates linearly with defect rate
        change_size = self._compute_change_size(fix_plan)
        if change_size > 30:
            size_score = self.W_CHANGE_SIZE               # 15
        elif change_size > 15:
            size_score = int(self.W_CHANGE_SIZE * 0.67)    # 10
        elif change_size > 5:
            size_score = int(self.W_CHANGE_SIZE * 0.33)    # 5
        else:
            size_score = 0
        breakdown["change_size"] = {
            "value": change_size, "score": size_score,
            "max": self.W_CHANGE_SIZE,
        }

        # ── 5. Environment (0-10 pts) ───────────────────────────────
        # Production carries inherent risk — no staging buffer
        environment = incident.get("environment", "staging").lower()
        if environment in ("production", "prod"):
            env_score = self.W_ENVIRONMENT                # 10
        elif environment == "staging":
            env_score = int(self.W_ENVIRONMENT * 0.5)      # 5
        else:
            env_score = 0
        breakdown["environment"] = {
            "value": environment, "score": env_score,
            "max": self.W_ENVIRONMENT,
        }

        # ── 6. Cyclomatic Delta (0-10 pts) ──────────────────────────
        # Complexity added makes code harder to reason about
        cyclomatic_delta = self._compute_cyclomatic_delta(fix_plan)
        if cyclomatic_delta > 5:
            cyclo_score = self.W_CYCLOMATIC                # 10
        elif cyclomatic_delta > 3:
            cyclo_score = int(self.W_CYCLOMATIC * 0.5)     # 5
        else:
            cyclo_score = 0
        breakdown["cyclomatic_delta"] = {
            "value": cyclomatic_delta, "score": cyclo_score,
            "max": self.W_CYCLOMATIC,
        }

        # ── Composite Score ─────────────────────────────────────────
        composite = (
            blast_score + test_score + coupling_score
            + size_score + env_score + cyclo_score
        )

        # ── Risk Level → Deployment Action ──────────────────────────
        if composite >= 50:
            level = RiskLevel.HIGH
            action = "options_only"
            policy = (
                "HIGH RISK — Slack notification with all fix options. "
                "No PR created. User must select a fix for manual deployment."
            )
        elif composite >= 25:
            level = RiskLevel.MEDIUM
            action = "pr_with_options"
            policy = (
                "MEDIUM RISK — Auto-PR created with optimal fix. "
                "Slack notification shows all alternatives. "
                "User can select a different fix to replace the PR."
            )
        else:
            level = RiskLevel.LOW
            action = "auto_pr"
            policy = (
                "LOW RISK — Auto-PR created with optimal fix. "
                "Summary posted to Slack."
            )

        # ── Rank all candidates ─────────────────────────────────────
        ranked_candidates = self._rank_candidates(all_candidates or [], fix_plan)

        best_id = ""
        if ranked_candidates:
            best_id = ranked_candidates[0].get("candidate_id", "")

        logger.info(
            f"[RiskScorer] Composite={composite}/100 → {level.value} "
            f"(blast={blast_score}, tests={test_score}, coupling={coupling_score}, "
            f"size={size_score}, env={env_score}, cyclo={cyclo_score}) "
            f"→ {action}"
        )

        return RiskAssessment(
            level=level.value,
            composite_score=composite,
            blast_radius=blast_radius,
            coupling_score=coupling,
            cyclomatic_delta=cyclomatic_delta,
            file_churn=0,  # TODO: wire from graph when available
            has_tests=has_tests,
            change_size=change_size,
            environment=environment,
            severity=incident.get("severity", "HIGH"),
            policy=policy,
            deployment_action=action,
            all_candidates=ranked_candidates,
            best_candidate_id=best_id,
            breakdown=breakdown,
        )

    # ── Factor computation helpers ──────────────────────────────────

    def _compute_blast_radius(self, fix_plan: dict) -> int:
        """Compute blast radius from knowledge graph."""
        if not self.kg_query:
            return 0

        max_blast = 0
        for change in fix_plan.get("files_to_modify", []):
            file_path = change.get("file_path", "")
            if not file_path:
                continue
            # Get blast radius for the file (all functions in it)
            try:
                result = self.kg_query.get_blast_radius(f"file:{file_path}")
                max_blast = max(max_blast, len(result.affected_nodes))
            except Exception:
                pass
            # Also try function-level if we can identify the function
            func_name = self._extract_function_name(change)
            if func_name:
                try:
                    func_ctx = self.kg_query.get_function_by_name(func_name, file_path)
                    if func_ctx:
                        result = self.kg_query.get_blast_radius(func_ctx.id)
                        max_blast = max(max_blast, len(result.affected_nodes))
                except Exception:
                    pass

        return max_blast

    def _compute_coupling(self, fix_plan: dict) -> int:
        """Compute coupling score from knowledge graph."""
        if not self.kg_query:
            return 0

        max_coupling = 0
        for change in fix_plan.get("files_to_modify", []):
            file_path = change.get("file_path", "")
            if file_path:
                try:
                    score = self.kg_query.get_coupling_score(file_path)
                    max_coupling = max(max_coupling, score)
                except Exception:
                    pass
        return max_coupling

    def _compute_change_size(self, fix_plan: dict) -> int:
        """Count lines changed in the fix."""
        change_size = fix_plan.get("change_size", 0)
        if not change_size and fix_plan.get("patch"):
            change_size = count_changed_lines(fix_plan["patch"])
        if not change_size:
            # Estimate from files_to_modify
            for change in fix_plan.get("files_to_modify", []):
                original = change.get("original_code", "")
                fixed = change.get("fixed_code", "")
                change_size += abs(
                    len(fixed.splitlines()) - len(original.splitlines())
                ) + max(len(original.splitlines()), len(fixed.splitlines()))
        return change_size

    def _compute_cyclomatic_delta(self, fix_plan: dict) -> int:
        """Compute cyclomatic complexity difference between original and fixed code."""
        total_delta = 0
        for change in fix_plan.get("files_to_modify", []):
            original = change.get("original_code", "")
            fixed = change.get("fixed_code", "")
            if original and fixed:
                original_complexity = KnowledgeGraphQuery.estimate_cyclomatic_complexity(original)
                fixed_complexity = KnowledgeGraphQuery.estimate_cyclomatic_complexity(fixed)
                total_delta += max(0, fixed_complexity - original_complexity)
        return total_delta

    @staticmethod
    def _extract_function_name(change: dict) -> str:
        """Try to extract a function name from the code change context."""
        original = change.get("original_code", "")
        # Look for Python def
        match = re.search(r'\bdef\s+(\w+)\s*\(', original)
        if match:
            return match.group(1)
        # Look for JS function
        match = re.search(r'\bfunction\s+(\w+)\s*\(', original)
        if match:
            return match.group(1)
        # Look for JS arrow/method: name(params) { or name = (params) =>
        match = re.search(r'(\w+)\s*(?:=\s*)?\(.*?\)\s*(?:=>|{)', original)
        if match:
            return match.group(1)
        return ""

    def _rank_candidates(
        self, candidates: list[dict], primary_fix: dict
    ) -> list[dict]:
        """Rank all fix candidates by their validation scores.

        If no evaluated candidates are available, wraps the primary fix_plan
        as the sole candidate.
        """
        if not candidates:
            # Wrap the primary fix_plan as a single candidate
            return [{
                "candidate_id": "c1_primary",
                "fix_plan": primary_fix,
                "score": 0,
                "rank": 1,
                "verdict": "UNKNOWN",
            }]

        # Sort by score descending
        ranked = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)
        for i, c in enumerate(ranked):
            c["rank"] = i + 1

        return ranked

    def get_system_prompt(self) -> str:
        return (
            "You are the Risk Scorer Agent in Amaze on Work. "
            "You assess the risk of applying code changes using principled "
            "multi-factor analysis before they are committed."
        )
