"""
Knowledge Retriever Agent for Amaze on Work.

Queries the knowledge graph for historical incident context:
- Similar past incidents for the same file/service
- Fix patterns that worked previously
- Blast radius for modified functions
"""

import logging
from typing import Optional

from src.agents.base import BaseAgent, AgentResponse
from src.agents.state import PipelineState, AgentType
from src.llm.base_client import BaseLLMClient
from src.graph.base import GraphBackend
from src.graph.query_interface import KnowledgeGraphQuery

logger = logging.getLogger(__name__)


class KnowledgeRetrieverAgent(BaseAgent):
    """
    Queries the knowledge graph for context relevant to the current incident.

    Provides historical context to improve root cause accuracy and fix quality.
    """
    agent_type = AgentType.KNOWLEDGE_RETRIEVER

    def __init__(self, llm_client: BaseLLMClient, graph: GraphBackend):
        super().__init__(llm_client)
        self.kg = KnowledgeGraphQuery(graph)

    def execute(self, state: PipelineState) -> AgentResponse:
        incident = state.get("incident", {})
        root_cause = state.get("root_cause", {})

        context = self._retrieve_context(incident, root_cause)

        return AgentResponse(
            success=True,
            message=f"Retrieved {len(context.get('similar_incidents', []))} similar incidents, "
                    f"{len(context.get('fix_patterns', []))} fix patterns",
            data=context,
        )

    async def aexecute(self, state: PipelineState) -> AgentResponse:
        return self.execute(state)

    def _retrieve_context(self, incident: dict, root_cause: dict) -> dict:
        """Retrieve all relevant context from the knowledge graph."""
        context = {
            "similar_incidents": [],
            "fix_patterns": [],
            "blast_radius": {},
            "file_churn": {},
            "related_functions": [],
        }

        service = incident.get("affected_service", "")
        suspect_files = root_cause.get("suspect_files", [])
        suspect_functions = root_cause.get("suspect_functions", [])

        # 1. Find similar incidents
        for file_path in suspect_files:
            history = self.kg.find_similar_incidents(file_path=file_path, service=service)
            context["similar_incidents"].extend(history.similar_incidents)
            context["fix_patterns"].extend(history.fix_patterns)

        if not context["similar_incidents"] and service:
            history = self.kg.find_similar_incidents(service=service)
            context["similar_incidents"].extend(history.similar_incidents)

        # 2. Get blast radius for suspect functions
        for func_name in suspect_functions:
            for file_path in suspect_files:
                func_ctx = self.kg.get_function_by_name(func_name, file_path)
                if func_ctx:
                    blast = self.kg.get_blast_radius(func_ctx.id)
                    context["blast_radius"][func_name] = {
                        "affected_nodes": len(blast.affected_nodes),
                        "affected_files": blast.affected_files,
                        "high_risk": blast.high_risk_affected,
                        "summary": blast.get_impact_summary(),
                    }
                    context["related_functions"].append(func_ctx.to_prompt_context())

        # 3. File churn
        for file_path in suspect_files:
            churn = self.kg.get_file_churn(file_path)
            context["file_churn"][file_path] = churn

        # Deduplicate
        seen_ids = set()
        unique_incidents = []
        for inc in context["similar_incidents"]:
            if inc.get("id") not in seen_ids:
                seen_ids.add(inc.get("id"))
                unique_incidents.append(inc)
        context["similar_incidents"] = unique_incidents

        return context

    def format_for_prompt(self, context: dict) -> str:
        """Format knowledge context for inclusion in LLM prompts."""
        parts = []

        if context.get("similar_incidents"):
            parts.append("### Past Similar Incidents")
            for inc in context["similar_incidents"][:3]:
                parts.append(
                    f"- **{inc.get('id', '')}**: {inc.get('title', '')} "
                    f"(resolved: {inc.get('resolution', 'N/A')[:100]})"
                )

        if context.get("fix_patterns"):
            parts.append("\n### Known Fix Patterns")
            for fix in context["fix_patterns"][:3]:
                parts.append(
                    f"- Type: {fix.get('failure_type', 'N/A')} — {fix.get('patch_summary', '')[:100]}"
                )

        if context.get("blast_radius"):
            parts.append("\n### Blast Radius")
            for func, data in context["blast_radius"].items():
                parts.append(f"- `{func}`: {data.get('summary', 'N/A')}")

        if context.get("file_churn"):
            parts.append("\n### File Churn (past fix count)")
            for file, count in context["file_churn"].items():
                if count > 0:
                    parts.append(f"- `{file}`: {count} previous fixes")

        return "\n".join(parts) if parts else "No historical context available."

    def get_system_prompt(self) -> str:
        return (
            "You are the Knowledge Retriever Agent in Amaze on Work. "
            "You query the knowledge graph for historical context."
        )
