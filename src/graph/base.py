from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class NodeType(str, Enum):
    """Types of nodes in the Amaze on Work knowledge graph."""
    # Core structural types
    FILE = "file"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    MODULE = "module"
    IMPORT = "import"
    VARIABLE = "variable"

    # Service-level
    SERVICE = "service"

    # Semantic types
    ENTRY_POINT = "entry_point"
    SECURITY_BOUNDARY = "security_boundary"

    # Incident-related types (Amaze on Work-specific)
    INCIDENT = "incident"
    STACK_FRAME = "stack_frame"
    FIX = "fix"
    TEST_RESULT = "test_result"
    DOC = "doc"


class EdgeType(str, Enum):
    """Types of edges (relationships) in the knowledge graph."""
    # Core structural relationships
    CONTAINS = "contains"
    CALLS = "calls"
    IMPORTS = "imports"
    INHERITS = "inherits"
    USES = "uses"
    DEFINED_IN = "defined_in"
    BELONGS_TO = "belongs_to"

    # Semantic relationships
    DATA_FLOWS_TO = "data_flows_to"
    GUARDS = "guards"
    HANDLES_ERROR_FROM = "handles_error_from"
    EXPOSES = "exposes"

    # Incident-related relationships (Amaze on Work-specific)
    INVOLVES = "involves"
    HAS_FRAME = "has_frame"
    POINTS_TO = "points_to"
    RESOLVED_BY = "resolved_by"
    MODIFIES = "modifies"
    VERIFIED_BY = "verified_by"
    ABOUT = "about"


@dataclass
class Node:
    """
    A node in the knowledge graph.

    Attributes:
        id: Unique identifier (e.g. "func:src/checkout.js:processOrder")
        type: The type of node
        name: Human-readable name
        properties: Core properties from AST/parsing (verified facts)
        enriched: Optional semantic properties from LLM analysis
        enriched_confidence: Confidence score for enriched properties (0.0-1.0)
        enriched_at: Timestamp when enrichment was performed
    """
    id: str
    type: NodeType
    name: str
    properties: dict[str, Any]

    # Enrichment fields (optional, from LLM or pattern analysis)
    enriched: Optional[dict[str, Any]] = None
    enriched_confidence: float = 0.0
    enriched_at: Optional[datetime] = None

    def is_enriched(self) -> bool:
        return self.enriched is not None and len(self.enriched) > 0

    def get_risk_level(self) -> Optional[str]:
        if self.enriched:
            return self.enriched.get("risk_level")
        return None

    def get_purpose(self) -> Optional[str]:
        if self.enriched:
            return self.enriched.get("purpose")
        return None


@dataclass
class Edge:
    """
    An edge (relationship) in the knowledge graph.
    """
    source_id: str
    target_id: str
    type: EdgeType
    properties: dict[str, Any]


class GraphBackend(ABC):
    """Abstract base class for graph backend implementations."""

    @abstractmethod
    def add_node(self, node: Node) -> str:
        pass

    @abstractmethod
    def add_edge(self, edge: Edge) -> None:
        pass

    @abstractmethod
    def get_node(self, node_id: str) -> Node | None:
        pass

    @abstractmethod
    def get_nodes_by_type(self, node_type: NodeType) -> list[Node]:
        pass

    @abstractmethod
    def get_edges_from(self, node_id: str) -> list[Edge]:
        pass

    @abstractmethod
    def get_edges_to(self, node_id: str) -> list[Edge]:
        pass

    @abstractmethod
    def query(self, pattern: str, params: dict | None = None) -> list[dict]:
        pass

    @abstractmethod
    def clear(self) -> None:
        pass

    @abstractmethod
    def stats(self) -> dict:
        pass

    @abstractmethod
    def repo_exists(self, repo_path: str) -> bool:
        pass

    @abstractmethod
    def get_repo_stats(self, repo_path: str) -> dict | None:
        pass

    @abstractmethod
    def close(self) -> None:
        pass
