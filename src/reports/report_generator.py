"""
Report Generator for Amaze on Work.

Renders resolution reports using Jinja2 templates
for different output targets (GitHub PR, Slack, issue comments).
"""

import logging
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"


class ReportGenerator:
    """Renders resolution reports from templates."""

    def __init__(self, templates_dir: Optional[str] = None):
        template_path = Path(templates_dir) if templates_dir else TEMPLATES_DIR
        self.env = Environment(
            loader=FileSystemLoader(str(template_path)),
            autoescape=select_autoescape(default=False),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def render_pr_body(self, report: dict, incident: dict) -> str:
        """Render a GitHub PR body from a resolution report."""
        template = self.env.get_template("github_pr.md.j2")
        validation = report.get("validation", {})
        risk = report.get("risk", {})
        before = validation.get("before", {})
        after = validation.get("after", {})

        return template.render(
            incident_id=report.get("incident_id", ""),
            title=report.get("title", ""),
            service=incident.get("affected_service", ""),
            environment=incident.get("environment", ""),
            severity=incident.get("severity", ""),
            resolution_time=f"{report.get('resolution_time_seconds', 0):.1f}",
            confidence=f"{report.get('confidence_score', 0) * 100:.0f}",
            root_cause=report.get("root_cause", ""),
            changes=report.get("changes_made", []),
            diff=report.get("diff", ""),
            before_total=before.get("total", 0),
            before_passed=before.get("passed", 0),
            before_failed=before.get("failed", 0),
            after_total=after.get("total", 0),
            after_passed=after.get("passed", 0),
            after_failed=after.get("failed", 0),
            verdict=validation.get("verdict", ""),
            regressions_count=len(validation.get("regressions", [])),
            fixes_confirmed_count=len(validation.get("fixes_confirmed", [])),
            risk_level=risk.get("level", ""),
            blast_radius=risk.get("blast_radius", 0),
            change_size=risk.get("change_size", 0),
            policy=risk.get("policy", ""),
            reasoning_chain=report.get("reasoning_chain", []),
        )

    def render_slack_message(self, report: dict, incident: dict, pr_url: str = "") -> str:
        """Render a Slack notification message."""
        template = self.env.get_template("slack_message.j2")
        validation = report.get("validation", {})
        risk = report.get("risk", {})
        verdict = validation.get("verdict", "")

        verdict_emoji = {"FIX_CONFIRMED": "✅", "REGRESSION_DETECTED": "❌", "NO_CHANGE": "⚠️"}.get(verdict, "❓")
        risk_level = risk.get("level", "MEDIUM")
        risk_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(risk_level, "⚪")

        return template.render(
            incident_id=report.get("incident_id", ""),
            title=report.get("title", ""),
            service=incident.get("affected_service", ""),
            severity=incident.get("severity", ""),
            root_cause=report.get("root_cause", ""),
            verdict=verdict,
            verdict_emoji=verdict_emoji,
            confidence=f"{report.get('confidence_score', 0) * 100:.0f}",
            risk_level=risk_level,
            risk_emoji=risk_emoji,
            resolution_time=f"{report.get('resolution_time_seconds', 0):.1f}",
            pr_url=pr_url,
        )

    def render_issue_comment(self, report: dict, incident: dict, pr_url: str = "", pr_number: int = 0) -> str:
        """Render a GitHub issue comment."""
        template = self.env.get_template("issue_comment.md.j2")
        validation = report.get("validation", {})
        before = validation.get("before", {})
        after = validation.get("after", {})

        return template.render(
            incident_id=report.get("incident_id", ""),
            title=report.get("title", ""),
            root_cause=report.get("root_cause", ""),
            fix_description=report.get("changes_made", [{}])[0].get("rationale", "") if report.get("changes_made") else "",
            verdict=validation.get("verdict", ""),
            before_total=before.get("total", 0),
            before_passed=before.get("passed", 0),
            after_total=after.get("total", 0),
            after_passed=after.get("passed", 0),
            regressions_count=len(validation.get("regressions", [])),
            confidence=f"{report.get('confidence_score', 0) * 100:.0f}",
            pr_url=pr_url,
            pr_number=pr_number,
        )
