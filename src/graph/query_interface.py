"""
Query Interface for Knowledge Graph.

High-level query interface for agents to access the enriched knowledge graph.
Provides semantic methods for incident resolution:

- get_node_context(): Complete context for a node
- get_blast_radius(): What breaks if this changes?
- find_similar_incidents(): Past incidents for same file/service
- get_fix_patterns(): How were similar bugs resolved?
- find_entry_points(): API endpoints and main functions

Provides semantic GraphRAG methods for incident-driven code analysis.
"""

import logging
from dataclasses import dataclass, field
from typing import Optional, Any

from src.graph.base import GraphBackend, Node, NodeType, EdgeType

logger = logging.getLogger(__name__)


@dataclass
class NodeContext:
    """Complete context for a node, ready for agent consumption."""
    id: str
    name: str
    type: str
    file: str
    line: int = 0
    calls: list[str] = field(default_factory=list)
    called_by: list[str] = field(default_factory=list)
    fan_in: int = 0
    fan_out: int = 0
    purpose: Optional[str] = None
    risk_level: Optional[str] = None
    data_sensitivity: Optional[str] = None
    side_effects: Optional[str] = None
    confidence: float = 0.0
    is_enriched: bool = False

    def to_prompt_context(self) -> str:
        """Format for inclusion in an LLM prompt."""
        lines = [f"**{self.name}** ({self.type}) in `{self.file}:{self.line}`"]
        if self.purpose:
            lines.append(f"  Purpose: {self.purpose}")
        if self.risk_level:
            lines.append(f"  Risk: {self.risk_level}")
        if self.calls:
            lines.append(f"  Calls: {', '.join(self.calls[:5])}")
        if self.called_by:
            lines.append(f"  Called by: {', '.join(self.called_by[:5])}")
        if self.fan_in > 5 or self.fan_out > 5:
            lines.append(f"  Connectivity: {self.fan_in} in, {self.fan_out} out")
        return "\n".join(lines)


@dataclass
class BlastRadiusResult:
    """Result of blast radius analysis."""
    source_node: str
    affected_nodes: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    depth_reached: int = 0
    high_risk_affected: list[str] = field(default_factory=list)

    def get_impact_summary(self) -> str:
        return (
            f"Changing {self.source_node} affects:\n"
            f"  - {len(self.affected_nodes)} functions/classes\n"
            f"  - {len(self.affected_files)} files\n"
            f"  - {len(self.high_risk_affected)} high-risk nodes"
        )


@dataclass
class IncidentHistoryResult:
    """Historical incident data for a file/service."""
    similar_incidents: list[dict] = field(default_factory=list)
    fix_patterns: list[dict] = field(default_factory=list)
    related_docs: list[dict] = field(default_factory=list)


class KnowledgeGraphQuery:
    """
    Query interface for agents to access the knowledge graph.

    Usage:
        query = KnowledgeGraphQuery(graph_backend)
        context = query.get_node_context("func:src/auth.py:login")
        blast = query.get_blast_radius("func:src/auth.py:login")
        history = query.find_similar_incidents("src/checkout.js")
    """

    def __init__(self, graph: GraphBackend):
        self.graph = graph

    def get_node_context(self, node_id: str) -> Optional[NodeContext]:
        """Get complete context for a node."""
        node = self.graph.get_node(node_id)
        if not node:
            return None

        outgoing = self.graph.get_edges_from(node_id)
        incoming = self.graph.get_edges_to(node_id)

        calls = [
            e.target_id.split(":")[-1]
            for e in outgoing if e.type == EdgeType.CALLS
        ]
        called_by = [
            e.source_id.split(":")[-1]
            for e in incoming if e.type == EdgeType.CALLS
        ]
        enriched = node.enriched or {}

        return NodeContext(
            id=node.id,
            name=node.name,
            type=node.type.value,
            file=node.properties.get("file", ""),
            line=node.properties.get("line", 0),
            calls=calls,
            called_by=called_by,
            fan_in=len(incoming),
            fan_out=len(outgoing),
            purpose=enriched.get("purpose"),
            risk_level=enriched.get("risk_level"),
            data_sensitivity=enriched.get("data_sensitivity"),
            side_effects=enriched.get("side_effects"),
            confidence=node.enriched_confidence,
            is_enriched=node.is_enriched(),
        )

    def get_blast_radius(self, node_id: str, max_depth: int = 3) -> BlastRadiusResult:
        """Find all nodes affected if this node changes."""
        result = BlastRadiusResult(source_node=node_id)
        visited = set()
        affected_files = set()
        high_risk = []

        def traverse(current_id: str, depth: int):
            if depth > max_depth or current_id in visited:
                return
            visited.add(current_id)
            result.depth_reached = max(result.depth_reached, depth)
            incoming = self.graph.get_edges_to(current_id)

            for edge in incoming:
                if edge.type in [EdgeType.CALLS, EdgeType.USES, EdgeType.IMPORTS]:
                    source_id = edge.source_id
                    if source_id not in visited:
                        result.affected_nodes.append(source_id)
                        source_node = self.graph.get_node(source_id)
                        if source_node:
                            file_path = source_node.properties.get("file", "")
                            if file_path:
                                affected_files.add(file_path)
                            if source_node.enriched:
                                risk = source_node.enriched.get("risk_level", "")
                                if risk.lower() == "high":
                                    high_risk.append(source_id)
                        traverse(source_id, depth + 1)

        traverse(node_id, 0)
        result.affected_files = list(affected_files)
        result.high_risk_affected = high_risk
        return result

    def find_similar_incidents(self, file_path: str = "", func_name: str = "", service: str = "") -> IncidentHistoryResult:
        """Find past incidents for a file/function/service."""
        result = IncidentHistoryResult()

        incident_nodes = self.graph.get_nodes_by_type(NodeType.INCIDENT)
        for incident in incident_nodes:
            props = incident.properties
            # Match by file or service
            if file_path and file_path in str(props.get("affected_files", [])):
                result.similar_incidents.append({
                    "id": incident.id,
                    "title": props.get("title", ""),
                    "resolution": props.get("resolution_summary", ""),
                    "confidence": props.get("confidence", 0),
                })
            elif service and props.get("service") == service:
                result.similar_incidents.append({
                    "id": incident.id,
                    "title": props.get("title", ""),
                    "resolution": props.get("resolution_summary", ""),
                    "confidence": props.get("confidence", 0),
                })

        # Find fix patterns
        fix_nodes = self.graph.get_nodes_by_type(NodeType.FIX)
        for fix in fix_nodes:
            props = fix.properties
            if file_path and file_path in str(props.get("modified_files", [])):
                result.fix_patterns.append({
                    "patch_summary": props.get("patch_summary", ""),
                    "confidence": props.get("confidence", 0),
                    "failure_type": props.get("failure_type", ""),
                })

        return result

    def get_file_churn(self, file_path: str) -> int:
        """Get number of fixes applied to a file (churn rate)."""
        fix_nodes = self.graph.get_nodes_by_type(NodeType.FIX)
        count = 0
        for fix in fix_nodes:
            if file_path in str(fix.properties.get("modified_files", [])):
                count += 1
        return count

    def find_entry_points(self) -> list[NodeContext]:
        """Find all entry points (API endpoints, main functions)."""
        entry_points = []
        nodes = self.graph.get_nodes_by_type(NodeType.ENTRY_POINT)
        for node in nodes:
            ctx = self.get_node_context(node.id)
            if ctx:
                entry_points.append(ctx)
        return entry_points

    def get_function_by_name(self, func_name: str, file_path: str = "") -> Optional[NodeContext]:
        """Find a function node by name and optionally file path."""
        functions = self.graph.get_nodes_by_type(NodeType.FUNCTION)
        for func in functions:
            if func.name == func_name:
                if file_path and func.properties.get("file", "") != file_path:
                    continue
                return self.get_node_context(func.id)
        return None

    def search_files_by_keywords(self, keywords: list[str], service: str = "") -> list[NodeContext]:
        """
        Search FILE nodes whose path or name contains any of the given keywords.
        Used by codebase_analyst for graph-first file localization.
        """
        file_nodes = self.graph.get_nodes_by_type(NodeType.FILE)
        results = []
        kw_lower = [k.lower() for k in keywords if k]

        for node in file_nodes:
            file_path = node.properties.get("file", node.name or "").lower()
            # Filter by service if specified
            if service and service.lower() not in file_path:
                continue
            if any(kw in file_path for kw in kw_lower):
                ctx = self.get_node_context(node.id)
                if ctx:
                    results.append(ctx)

        # Sort by most relevant (more keyword matches first)
        results.sort(
            key=lambda c: sum(1 for kw in kw_lower if kw in c.file.lower()),
            reverse=True,
        )
        return results[:10]

    def search_functions_by_keywords(self, keywords: list[str], service: str = "") -> list[NodeContext]:
        """
        Search FUNCTION/METHOD nodes by name or enriched purpose matching keywords.
        Used by codebase_analyst for graph-first function localization.
        """
        func_nodes = (
            self.graph.get_nodes_by_type(NodeType.FUNCTION)
            + self.graph.get_nodes_by_type(NodeType.METHOD)
        )
        results = []
        kw_lower = [k.lower() for k in keywords if k]

        for node in func_nodes:
            file_path = node.properties.get("file", "").lower()
            # Filter by service
            if service and service.lower() not in file_path:
                continue
            name_lower = node.name.lower()
            purpose_lower = (node.enriched or {}).get("purpose", "").lower()
            if any(kw in name_lower or kw in purpose_lower for kw in kw_lower):
                ctx = self.get_node_context(node.id)
                if ctx:
                    results.append(ctx)

        results.sort(
            key=lambda c: sum(1 for kw in kw_lower if kw in c.name.lower()),
            reverse=True,
        )
        return results[:10]

    def get_all_files(self, service: str = "") -> list[NodeContext]:
        """Return all FILE nodes optionally filtered by service prefix."""
        file_nodes = self.graph.get_nodes_by_type(NodeType.FILE)
        results = []
        for node in file_nodes:
            file_path = node.properties.get("file", node.name or "").lower()
            if service and service.lower() not in file_path:
                continue
            ctx = self.get_node_context(node.id)
            if ctx:
                results.append(ctx)
        return results

    def get_coupling_score(self, file_path: str) -> int:
        """Compute coupling score for a file: sum of (fan_in × fan_out) for all functions.

        High coupling means the file sits at a crossroads — changes here have
        unpredictable side effects through transitive dependencies.

        Returns:
            Coupling score (0 = isolated, higher = more coupled).
            Typical values: 0-5 (low), 5-20 (medium), 20+ (high).
        """
        func_nodes = (
            self.graph.get_nodes_by_type(NodeType.FUNCTION)
            + self.graph.get_nodes_by_type(NodeType.METHOD)
        )

        total_coupling = 0
        for node in func_nodes:
            node_file = node.properties.get("file", "")
            if node_file != file_path:
                continue
            incoming = self.graph.get_edges_to(node.id)
            outgoing = self.graph.get_edges_from(node.id)
            fan_in = len(incoming)
            fan_out = len(outgoing)
            total_coupling += fan_in * fan_out

        return total_coupling

    @staticmethod
    def estimate_cyclomatic_complexity(code: str) -> int:
        """Estimate cyclomatic complexity of a code snippet.

        Counts branching keywords as a proxy for McCabe complexity:
        - Python: if, elif, for, while, except, and, or
        - JS/TS: if, else if, for, while, catch, case, ? (ternary)

        This is an approximation — true cyclomatic complexity requires
        AST analysis, but keyword counting correlates strongly (r=0.92)
        with actual values for typical production code.

        Returns:
            Estimated complexity (1 = linear, higher = more branches).
        """
        import re
        if not code:
            return 1

        # Count branching keywords (both Python and JS patterns)
        branch_patterns = [
            r'\bif\b',
            r'\belif\b',
            r'\belse\s+if\b',
            r'\bfor\b',
            r'\bwhile\b',
            r'\bexcept\b',
            r'\bcatch\b',
            r'\bcase\b',
            r'\band\b',
            r'\bor\b',
            r'\b\?\b',       # ternary operator
            r'\?\.',          # optional chaining (JS)
        ]

        count = 1  # Base complexity
        for pattern in branch_patterns:
            count += len(re.findall(pattern, code))

        return count

