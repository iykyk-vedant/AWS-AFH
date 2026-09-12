"""
Incident Parser Agent for Amaze on Work.

First agent in the pipeline. Reads incident tickets (JSON files from
the incidents/ directory) and parses them into canonical IncidentContext.
Classifies failure type using LLM.
"""

import json
import logging
from typing import Optional

from src.agents.base import BaseAgent, AgentResponse
from src.agents.state import (
    PipelineState,
    IncidentContext,
    AgentType,
    FailureType,
)
from src.llm.base_client import BaseLLMClient
from src.mcp.github_tools import GitHubMCPTools

logger = logging.getLogger(__name__)


class IncidentParserAgent(BaseAgent):
    """
    Parses incident tickets into structured IncidentContext.

    Can read from:
    - Local JSON files
    - GitHub incidents/ directory via MCP
    - Raw Slack messages (future)
    """
    agent_type = AgentType.INCIDENT_PARSER

    def __init__(self, llm_client: BaseLLMClient, github_tools: Optional[GitHubMCPTools] = None):
        super().__init__(llm_client)
        self.github = github_tools

    def execute(self, state: PipelineState) -> AgentResponse:
        """Parse an incident from the pipeline state."""
        incident = state.get("incident", {})

        if not incident:
            return AgentResponse(
                success=False,
                message="No incident data provided",
                error="Missing incident in pipeline state",
            )

        # If incident already has structured data, just classify failure type
        if incident.get("id") and incident.get("title"):
            classified = self._classify_failure_type(incident)
            return AgentResponse(
                success=True,
                message=f"Parsed incident {classified.get('id')}: {classified.get('title')}",
                data=classified,
                next_agent=AgentType.SUPERVISOR.value,
            )

        return AgentResponse(
            success=False,
            message="Could not parse incident",
            error="Incident data in unexpected format",
        )

    async def aexecute(self, state: PipelineState) -> AgentResponse:
        return self.execute(state)

    def parse_from_json(self, json_data: dict) -> IncidentContext:
        """Parse a raw incident JSON into IncidentContext."""
        # Extract stack traces from error_log if present
        stack_traces = self._extract_stack_traces(
            json_data.get("error_log", "")
        )

        incident = IncidentContext(
            id=json_data.get("id", ""),
            title=json_data.get("title", ""),
            description=json_data.get("description", ""),
            stack_traces=stack_traces,
            affected_service=json_data.get("service", ""),
            environment=json_data.get("environment", "staging"),
            severity=json_data.get("severity", "P2"),
            failure_type="unknown",  # Will be classified by LLM
            error_log=json_data.get("error_log", ""),
            tags=json_data.get("tags", []),
            steps_to_reproduce=json_data.get("steps_to_reproduce", []),
            expected_behavior=json_data.get("expected_behavior", ""),
            actual_behavior=json_data.get("actual_behavior", ""),
            recent_changes=json_data.get("recent_changes", ""),
            linked_commits=json_data.get("linked_commits", []),
            linked_issue_url=json_data.get("linked_issue_url", ""),
            reported_by=json_data.get("reported_by", ""),
            timestamp=json_data.get("timestamp", ""),
        )

        return self._classify_failure_type(incident)

    def parse_from_github(
        self, owner: str, repo: str, incident_id: str
    ) -> IncidentContext:
        """Fetch and parse an incident from GitHub with local cache fallback."""
        if not self.github:
            raise ValueError("GitHub tools not configured")

        try:
            json_data = self.github.get_incident(owner, repo, incident_id)
            return self.parse_from_json(json_data)
        except Exception as e:
            logger.warning(f"Could not fetch {incident_id} from GitHub ({e}), checking local fallback...")
            import json
            from pathlib import Path
            inc_file = Path("incidents.json")
            if inc_file.exists():
                try:
                    data = json.loads(inc_file.read_text(encoding="utf-8"))
                    for k, v in data.get("incidents", {}).items():
                        text = v.get("text", "")
                        if incident_id.upper() in k.upper() or incident_id in text:
                            start = text.find("{")
                            end = text.rfind("}") + 1
                            if start >= 0 and end > start:
                                return self.parse_from_json(json.loads(text[start:end]))
                except Exception:
                    pass
            raise

    def list_incidents(self, owner: str, repo: str) -> list[str]:
        """List all incident IDs from the GitHub repo."""
        if not self.github:
            return []
        files = self.github.get_incident_files(owner, repo)
        return [
            f["name"].replace(".json", "")
            for f in files
            if f.get("name", "").endswith(".json")
        ]

    def _extract_stack_traces(self, error_log: str) -> list[dict]:
        """Extract stack trace frames from error log text."""
        frames = []
        if not error_log:
            return frames

        import re

        # Python traceback pattern (supports both single and double quotes)
        py_pattern = r'File ["\']([^"\']+)["\'], line (\d+), in (\w+)'
        for match in re.finditer(py_pattern, error_log):
            frames.append({
                "file": match.group(1),
                "line": int(match.group(2)),
                "function": match.group(3),
            })

        # Node.js stack trace pattern
        node_pattern = r'at\s+(?:(\w+)\s+)?\(([^:]+):(\d+):\d+\)'
        for match in re.finditer(node_pattern, error_log):
            frames.append({
                "file": match.group(2),
                "line": int(match.group(3)),
                "function": match.group(1) or "anonymous",
            })

        # Simple "file:line" pattern
        simple_pattern = r'(\S+\.(?:py|js|ts)):(\d+)'
        if not frames:
            for match in re.finditer(simple_pattern, error_log):
                frames.append({
                    "file": match.group(1),
                    "line": int(match.group(2)),
                    "function": "unknown",
                })

        return frames

    def _classify_failure_type(self, incident: IncidentContext) -> IncidentContext:
        """Use LLM to classify the failure type."""
        tags = incident.get("tags", [])
        error_log = incident.get("error_log", "")
        description = incident.get("description", "")
        title = incident.get("title", "")

        # Quick classification from tags first
        tag_mapping = {
            "runtime-crash": FailureType.RUNTIME_CRASH,
            "misconfiguration": FailureType.CONFIGURATION,
            "config": FailureType.CONFIGURATION,
            "logic-bug": FailureType.LOGICAL_ERROR,
            "logic": FailureType.LOGICAL_ERROR,
            "security": FailureType.SECURITY,
            "sql-injection": FailureType.SECURITY,
            "performance": FailureType.PERFORMANCE,
            "import-error": FailureType.MISSING_IMPORT,
            "incorrect-import": FailureType.MISSING_IMPORT,
            "missing-import": FailureType.MISSING_IMPORT,
            "missing-dependency": FailureType.MISSING_DEPENDENCY,
            "dependency-mismatch": FailureType.DEPENDENCY,
            "dependency": FailureType.DEPENDENCY,
            "type-error": FailureType.TYPE_ERROR,
            "auth": FailureType.SECURITY,
            "authentication": FailureType.SECURITY,
            "login": FailureType.SECURITY,
            "billing": FailureType.LOGICAL_ERROR,
            "checkout": FailureType.LOGICAL_ERROR,
            "discount": FailureType.LOGICAL_ERROR,
            "zerodivision": FailureType.LOGICAL_ERROR,
            "zerodivisionerror": FailureType.LOGICAL_ERROR,
            "keyerror": FailureType.LOGICAL_ERROR,
            "indexerror": FailureType.LOGICAL_ERROR,
            "attributeerror": FailureType.LOGICAL_ERROR,
            "validation": FailureType.LOGICAL_ERROR,
            "pagination": FailureType.LOGICAL_ERROR,
            "math": FailureType.LOGICAL_ERROR,
            "precision": FailureType.LOGICAL_ERROR,
            "rounding": FailureType.LOGICAL_ERROR,
            "float": FailureType.LOGICAL_ERROR,
            "webhook": FailureType.CONFIGURATION,
            "rate-limit": FailureType.PERFORMANCE,
            "rate_limit": FailureType.PERFORMANCE,
            "ratelimit": FailureType.PERFORMANCE,
            "audit": FailureType.LOGICAL_ERROR,
            "serialization": FailureType.LOGICAL_ERROR,
            "json": FailureType.LOGICAL_ERROR,
        }

        for tag in tags:
            tag_lower = tag.lower().strip()
            if tag_lower in tag_mapping:
                incident["failure_type"] = tag_mapping[tag_lower].value
                return incident

        # Quick check on error_log and title for obvious patterns
        lower_log = (error_log + " " + title).lower()
        if "zerodivision" in lower_log or "division by zero" in lower_log or "assertionerror" in lower_log:
            incident["failure_type"] = FailureType.LOGICAL_ERROR.value
            return incident
        if "typeerror" in lower_log or "is not json serializable" in lower_log:
            incident["failure_type"] = FailureType.TYPE_ERROR.value
            return incident
        if "keyerror" in lower_log or "indexerror" in lower_log or "attributeerror" in lower_log:
            incident["failure_type"] = FailureType.LOGICAL_ERROR.value
            return incident
        if "valueerror" in lower_log:
            incident["failure_type"] = FailureType.LOGICAL_ERROR.value
            return incident

        # LLM classification for ambiguous cases
        prompt = f"""Classify this software incident into exactly one failure type.

Title: {title}
Description: {description}
Error Log: {error_log[:500]}
Tags: {tags}

Failure types (respond with ONLY one of these):
- logical_error
- type_error
- runtime_crash
- configuration
- missing_dependency
- security
- performance

Respond with ONLY the failure type name:"""

        try:
            result = self._llm_call(prompt, temperature=0.0, max_tokens=20)
            cleaned = result.strip().lower().replace(" ", "_")
            for ft in FailureType:
                if ft.value in cleaned:
                    incident["failure_type"] = ft.value
                    return incident
        except Exception as e:
            logger.warning(f"LLM failure type classification error: {e}")

        # Default fallback
        incident["failure_type"] = FailureType.LOGICAL_ERROR.value
        return incident

    def get_system_prompt(self) -> str:
        return (
            "You are the Incident Parser Agent in Amaze on Work. "
            "Your job is to parse incident tickets and classify their failure type. "
            "Be precise and concise in your classifications."
        )
