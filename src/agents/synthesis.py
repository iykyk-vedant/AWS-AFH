"""
Synthesis Agent for Amaze on Work.

Aggregates outputs from all agents into structured Resolution Report.
Persists every report to reports/<incident_id>/ as JSON + Markdown.
"""

import json
import logging
import time
from pathlib import Path
from typing import Any

from src.agents.base import BaseAgent, AgentResponse
from src.agents.state import (
    PipelineState,
    AgentType,
    ResolutionReport,
)
from src.llm.base_client import BaseLLMClient

logger = logging.getLogger(__name__)


class SynthesisAgent(BaseAgent):
    """
    Generates the final Resolution Report.

    Aggregates: root cause, fix details, validation results,
    risk assessment, confidence score, and reasoning chain.
    """
    agent_type = AgentType.SYNTHESIS

    def execute(self, state: PipelineState) -> AgentResponse:
        incident = state.get("incident", {})
        root_cause = state.get("root_cause", {})
        fix_plan = state.get("fix_plan", {})
        validation = state.get("validation_result", {})
        risk = state.get("risk_assessment", {})
        start_time = state.get("start_time", time.time())

        resolution_time = time.time() - start_time

        # Build reasoning chain
        reasoning_chain = self._build_reasoning_chain(state)

        # Calculate confidence score
        confidence = self._calculate_confidence(root_cause, validation, risk)

        # Build changes summary
        changes_made = []
        for change in fix_plan.get("files_to_modify", []):
            changes_made.append({
                "file": change.get("file_path", ""),
                "change": change.get("fixed_code", "")[:100],
                "rationale": change.get("rationale", ""),
            })

        incident_id = incident.get("id", "unknown")

        report = dict(
            incident_id=incident_id,
            title=incident.get("title", ""),
            root_cause=root_cause.get("hypothesis") or root_cause.get("root_cause_summary", "Unknown"),
            changes_made=changes_made,
            diff=fix_plan.get("patch", ""),
            validation=dict(validation) if validation else {},
            risk=dict(risk) if risk else {},
            confidence_score=confidence,
            reasoning_chain=reasoning_chain,
            resolution_time_seconds=resolution_time,
        )

        # Generate formatted report
        formatted = self._format_report(report, incident)

        # Persist report to disk
        self._save_report(incident_id, report, formatted)

        return AgentResponse(
            success=True,
            message=f"Resolution report generated for {incident_id}",
            data={
                "report": report,
                "formatted_markdown": formatted,
            },
            next_agent=AgentType.RISK_SCORER.value,
        )

    async def aexecute(self, state: PipelineState) -> AgentResponse:
        return self.execute(state)

    def _build_reasoning_chain(self, state: PipelineState) -> list[str]:
        """Build the chain-of-thought reasoning trail."""
        chain = []
        incident = state.get("incident", {})
        root_cause = state.get("root_cause", {})
        fix_plan = state.get("fix_plan", {})
        validation = state.get("validation_result", {})

        # Step 1: Parse
        chain.append(
            f"Incident {incident.get('id', '')} parsed: "
            f"'{incident.get('title', '')}' -- "
            f"service: {incident.get('affected_service', '')}, "
            f"type: {incident.get('failure_type', '')}"
        )

        # Step 2: Knowledge Retrieval
        knowledge = state.get("knowledge_context", {})
        if knowledge and knowledge.get("similar_incidents"):
            inc_count = len(knowledge.get("similar_incidents", []))
            chain.append(f"Knowledge Retrieval: Found {inc_count} similar historical incident(s) for contextual matching")

        # Step 3: Analysis
        if root_cause.get("suspect_files"):
            chain.append(
                f"Stack trace analysis -> suspect files: {root_cause['suspect_files']}"
            )

        # Step 4: Root cause
        if root_cause.get("hypothesis"):
            chain.append(f"Root cause: {root_cause['hypothesis']}")

        # Step 5: Fix
        if fix_plan.get("description"):
            chain.append(f"Fix applied: {fix_plan['description']}")

        # Step 6: Validation
        if validation:
            before = validation.get("before", {})
            after = validation.get("after", {})
            chain.append(
                f"Validation: {validation.get('verdict', '')} -- "
                f"before: {before.get('passed', 0)}/{before.get('total', 0)} passed, "
                f"after: {after.get('passed', 0)}/{after.get('total', 0)} passed"
            )

        # Step 7: Security Review
        security = state.get("security_review", {})
        if security:
            risk_lvl = security.get("risk_level", "LOW")
            findings_count = len(security.get("findings", []))
            chain.append(f"Security Review: {risk_lvl} risk assessment with {findings_count} finding(s)")

        # Step 8: Risk Assessment
        risk = state.get("risk_assessment", {})
        if risk:
            score = risk.get("risk_score", 0)
            action = risk.get("deployment_action", "options_only")
            chain.append(f"Risk Assessment: score {score}/100 -> deployment policy: '{action}'")

        return chain

    def _calculate_confidence(self, root_cause: dict, validation: dict, risk: dict) -> float:
        """Calculate overall confidence score (0.0 - 1.0)."""
        score = 0.0

        # Root cause confidence (40%)
        rc_confidence = root_cause.get("confidence", 0.0)
        score += rc_confidence * 0.4

        # Validation result (40%)
        verdict = validation.get("verdict", "")
        if verdict == "FIX_CONFIRMED":
            score += 0.4
        elif verdict == "NO_CHANGE":
            score += 0.1
        elif verdict == "UNRELATED_FAILURE":
            score += 0.2

        # Test pass rate improvement (20%)
        before = validation.get("before", {})
        after = validation.get("after", {})
        before_pass_rate = before.get("passed", 0) / max(before.get("total", 1), 1)
        after_pass_rate = after.get("passed", 0) / max(after.get("total", 1), 1)
        improvement = max(0, after_pass_rate - before_pass_rate)
        score += improvement * 0.2

        return round(min(score, 1.0), 2)

    def _format_report(self, report: dict, incident: dict) -> str:
        """Format the resolution report as rich markdown."""
        validation = report.get("validation") or {}
        risk = report.get("risk") or {}
        before = validation.get("before") or {}
        after = validation.get("after") or {}

        risk_level = risk.get("level", "MEDIUM")
        risk_label = {"LOW": "[LOW]", "MEDIUM": "[MEDIUM]", "HIGH": "[HIGH]"}.get(risk_level, "[MEDIUM]")
        verdict = validation.get("verdict", "UNKNOWN")
        verdict_label = {
            "FIX_CONFIRMED": "[CONFIRMED]",
            "REGRESSION_DETECTED": "[REGRESSION]",
            "NO_CHANGE": "[NO CHANGE]",
            "SKIPPED": "[SKIPPED]",
            "ERROR": "[ERROR]",
            "UNRELATED_FAILURE": "[NO REGRESSION]",
        }.get(verdict, verdict)

        md = f"""# Amaze on Work -- Incident Resolution Report

**Incident ID:** `{report.get('incident_id', '')}`
**Title:** {report.get('title', '')}
**Service:** `{incident.get('affected_service', 'unknown')}` | **Env:** {incident.get('environment', 'production')} | **Severity:** {incident.get('severity', 'UNKNOWN')}
**Resolution Time:** {report.get('resolution_time_seconds', 0):.1f}s | **Confidence:** {report.get('confidence_score', 0) * 100:.0f}%

---

## Root Cause
{report.get('root_cause', 'Unknown -- check codebase_analyst output')}

---

## Changes Made

| File | Rationale |
|------|-----------|
"""
        files_changed = []
        for change in report.get("changes_made", []):
            f = change.get("file", "")
            r = change.get("rationale", "")
            if f:
                files_changed.append(f)
                md += f"| `{f}` | {r} |\n"

        if not files_changed:
            md += "| No files changed | — |\n"

        patch = report.get("diff", "")
        if patch:
            md += f"""

**Patch:**
```diff
{patch[:3000]}
```
"""

        md += f"""
---

## Validation Results

| Metric | Before | After |
|--------|--------|-------|
| Tests Run | {before.get('total', 0)} | {after.get('total', 0)} |
| Passed | {before.get('passed', 0)} | {after.get('passed', 0)} |
| Failed | {before.get('failed', 0)} | {after.get('failed', 0)} |

**Verdict:** {verdict_label}
**Fixes Confirmed:** {', '.join(validation.get('fixes_confirmed', [])) or 'None'}
**Regressions:** {', '.join(validation.get('regressions', [])) or 'None'}

---

## Risk Assessment
**Risk Level:** {risk_label}
- Blast radius: {risk.get('blast_radius', 'N/A')} downstream callers
- Files changed: {len(files_changed)}
- Change size: {risk.get('change_size', 'N/A')} lines

---

## Reasoning Chain
"""
        for i, step in enumerate(report.get("reasoning_chain", []), 1):
            md += f"{i}. {step}\n"

        return md

    def _save_report(self, incident_id: str, report: dict, formatted_md: str) -> None:
        """Persist the report to disk as JSON + Markdown in reports/<incident_id>/"""
        try:
            report_dir = Path("reports") / incident_id
            report_dir.mkdir(parents=True, exist_ok=True)

            # Save JSON
            json_path = report_dir / "report.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2, default=str)

            # Save Markdown
            md_path = report_dir / "report.md"
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(formatted_md)

            logger.info(f"Report saved: {report_dir.resolve()}")
        except Exception as e:
            logger.warning(f"Could not save report to disk: {e}")

    def get_system_prompt(self) -> str:
        return (
            "You are the Synthesis Agent in Amaze on Work. "
            "You aggregate analysis results into comprehensive resolution reports."
        )
