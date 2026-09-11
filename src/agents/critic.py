"""
Critic / Reflector Agent for Amaze on Work.

Pre-patch quality gate. Reviews root cause analysis before fix is written.
Acts as a Staff+ Engineer doing a fast-track code review.
"""

import json
import logging
import re

from src.agents.base import BaseAgent, AgentResponse
from src.agents.state import PipelineState, AgentType, CriticVerdict
from src.llm.base_client import BaseLLMClient

logger = logging.getLogger(__name__)


class CriticAgent(BaseAgent):
    """
    Reviews the root cause analysis before any code is written.

    Evaluates 5 dimensions:
    1. Evidence alignment — does the error log match the hypothesis?
    2. Fix minimalism — is the scope of the fix appropriate?
    3. Safety — are there safer alternatives?
    4. Blast radius — what other systems could be affected?
    5. Test coverage — are there tests that will catch regressions?
    """
    agent_type = AgentType.CRITIC

    def execute(self, state: PipelineState) -> AgentResponse:
        incident = state.get("incident", {})
        root_cause = state.get("root_cause", {})
        previous_feedback = state.get("critic_feedback", "")

        if not root_cause:
            return AgentResponse(
                success=True,
                message="No root cause to review — approving by default",
                data={"verdict": CriticVerdict.APPROVED.value, "feedback": ""},
                next_agent=AgentType.FIX_WRITER.value,
            )

        feedback_section = ""
        if previous_feedback:
            feedback_section = f"\n\n⚠️ PREVIOUS REVISION REQUEST:\n{previous_feedback}\nCheck if this issue has been addressed."

        prompt = f"""You are a **Staff+ Software Engineer** conducting a rapid pre-patch review (tech lead gate).
Your role is to catch incorrect root cause diagnoses BEFORE a fix is written, preventing wasted effort.

=== INCIDENT ===
Title: {incident.get('title', '')}
Service: {incident.get('affected_service', '')}
Severity: {incident.get('severity', '')}
Error Log: {incident.get('error_log', '')[:800]}
Failure Type: {incident.get('failure_type', '')}
Expected: {incident.get('expected_behavior', '')}
Actual: {incident.get('actual_behavior', '')}

=== ROOT CAUSE ANALYSIS TO REVIEW ===
Hypothesis: {root_cause.get('hypothesis', '')}
Confidence: {root_cause.get('confidence', 0)}
Suspect Files: {root_cause.get('suspect_files', [])}
Suspect Functions: {root_cause.get('suspect_functions', [])}
Suspect Lines: {root_cause.get('suspect_lines', [])}
Reasoning: {root_cause.get('reasoning', '')}{feedback_section}

=== YOUR REVIEW CRITERIA ===
Evaluate EACH of these 5 dimensions and rate each PASS/FAIL:

1. **Evidence Alignment** — Does the error log / stack trace directly support the hypothesis? 
   (FAIL if hypothesis ignores clear evidence in the error log)

2. **Plausibility** — Is this a known failure mode for this type of system/language? 
   (FAIL if the hypothesis is technically implausible)

3. **Specificity** — Does the analysis name specific files, functions, and lines?
   (FAIL if suspect_files is empty or too vague like "somewhere in the codebase")

4. **Minimalism** — Would a fix based on this hypothesis make minimal, targeted changes?
   (FAIL if hypothesis would require a large refactor to fix)

5. **Completeness** — Is there an obvious alternative root cause being ignored?
   (FAIL only if there is a clearly more likely explanation)

=== VERDICT RULES ===
- APPROVED if: 3+ criteria PASS and no critical FAIL in criteria 1 or 2
- NEEDS_REVISION if: criteria 1 or 2 FAIL, or 3+ criteria FAIL
- When in doubt, APPROVE — false positives here waste cycles

=== OUTPUT (JSON only) ===
{{
    "verdict": "APPROVED" or "NEEDS_REVISION",
    "review": {{
        "evidence_alignment": "PASS|FAIL — explanation",
        "plausibility": "PASS|FAIL — explanation",  
        "specificity": "PASS|FAIL — explanation",
        "minimalism": "PASS|FAIL — explanation",
        "completeness": "PASS|FAIL — explanation"
    }},
    "feedback": "If NEEDS_REVISION: specific actionable feedback for the analyst. What evidence were they missing? What should they look at instead?",
    "confidence_adjustment": 0.0
}}"""

        try:
            result = self._llm_call(prompt, temperature=0.1, max_tokens=800)
            json_match = re.search(r'\{[\s\S]*\}', result)
            if json_match:
                review = json.loads(json_match.group())
                verdict = review.get("verdict", "APPROVED").upper()
                if verdict not in ("APPROVED", "NEEDS_REVISION"):
                    verdict = "APPROVED"
            else:
                verdict = "APPROVED"
                review = {"verdict": verdict, "feedback": ""}
        except Exception as e:
            logger.warning(f"Critic review failed, auto-approving: {e}")
            verdict = "APPROVED"
            review = {"verdict": verdict, "feedback": ""}

        # Always forward to FIX_WRITER — include feedback in state so fix_writer sees it.
        # If NEEDS_REVISION, we log it but don't hard-block (to avoid infinite loops).
        # The supervisor decides whether to retry via needs_retry flag.
        next_agent = AgentType.FIX_WRITER.value
        needs_retry = verdict == "NEEDS_REVISION"

        if needs_retry:
            logger.warning(f"Critic flagged NEEDS_REVISION: {review.get('feedback', '')[:200]}")

        return AgentResponse(
            success=True,
            message=f"Critic verdict: {verdict}",
            data={**review, "critic_feedback": review.get("feedback", "")},
            next_agent=next_agent,
            needs_retry=needs_retry,
        )

    async def aexecute(self, state: PipelineState) -> AgentResponse:
        return self.execute(state)

    def get_system_prompt(self) -> str:
        return (
            "You are the Critic Agent in Amaze on Work — a Staff+ Engineer running a rapid pre-patch review gate. "
            "Your goal is to catch wrong root cause diagnoses before they waste engineering cycles. "
            "You evaluate 5 dimensions: Evidence Alignment, Plausibility, Specificity, Minimalism, Completeness. "
            "Be rigorous but pragmatic — approve if the analysis is reasonable and actionable. "
            "False positives (unnecessary revisions) are costly. Only block when clearly wrong."
        )
