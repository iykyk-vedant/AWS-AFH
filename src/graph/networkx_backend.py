"""
NetworkX in-memory graph backend.

Fallback when Neo4j is unavailable. Mirrors the same GraphBackend interface.
"""

import logging
from typing import Any, Optional

import networkx as nx

from src.graph.base import GraphBackend, Node, Edge, NodeType, EdgeType

logger = logging.getLogger(__name__)


class NetworkXBackend(GraphBackend):
    """In-memory graph backend using NetworkX."""

    def __init__(self):
        self._graph = nx.DiGraph()
        self._nodes: dict[str, Node] = {}
        logger.info("NetworkX backend initialized (in-memory)")

    def add_node(self, node: Node) -> str:
        self._nodes[node.id] = node
        self._graph.add_node(
            node.id,
            type=node.type.value,
            name=node.name,
            **node.properties,
        )
        return node.id

    def add_edge(self, edge: Edge) -> None:
        self._graph.add_edge(
            edge.source_id,
            edge.target_id,
            type=edge.type.value,
            **edge.properties,
        )

    def get_node(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def get_nodes_by_type(self, node_type: NodeType) -> list[Node]:
        return [
            node for node in self._nodes.values()
            if node.type == node_type
        ]

    def get_edges_from(self, node_id: str) -> list[Edge]:
        edges = []
        if node_id in self._graph:
            for _, target, data in self._graph.edges(node_id, data=True):
                edge_type = data.pop("type", "uses")
                props = dict(data)
                edges.append(Edge(
                    source_id=node_id,
                    target_id=target,
                    type=EdgeType(edge_type),
                    properties=props,
                ))
        return edges

    def get_edges_to(self, node_id: str) -> list[Edge]:
        edges = []
        if node_id in self._graph:
            for source, _, data in self._graph.in_edges(node_id, data=True):
                edge_type = data.pop("type", "uses")
                props = dict(data)
                edges.append(Edge(
                    source_id=source,
                    target_id=node_id,
                    type=EdgeType(edge_type),
                    properties=props,
                ))
        return edges

    def query(self, pattern: str, params: dict | None = None) -> list[dict]:
        """Execute a pseudo-query on the NetworkX graph.

        Supports simplified patterns:
        - "MATCH (n:TYPE)" — returns all nodes of that type
        - "BLAST_RADIUS:node_id" — returns affected nodes
        - Other patterns return empty results
        """
        if pattern.startswith("BLAST_RADIUS:"):
            node_id = pattern.split(":", 1)[1]
            return self._get_blast_radius(node_id, params or {})

        if pattern.startswith("MATCH") and ":Function" in pattern:
            return [
                {"id": n.id, "name": n.name, "properties": n.properties}
                for n in self._nodes.values()
                if n.type == NodeType.FUNCTION
            ]

        if pattern.startswith("MATCH") and ":Incident" in pattern:
            return [
                {"id": n.id, "name": n.name, "properties": n.properties}
                for n in self._nodes.values()
                if n.type == NodeType.INCIDENT
            ]

        return []

    def _get_blast_radius(self, node_id: str, params: dict) -> list[dict]:
        max_depth = params.get("max_depth", 3)
        affected = []
        visited = set()

        def traverse(current: str, depth: int):
            if depth > max_depth or current in visited:
                return
            visited.add(current)
            for source, _ in self._graph.in_edges(current):
                if source not in visited:
                    node = self._nodes.get(source)
                    if node:
                        affected.append({
                            "id": source,
                            "name": node.name,
                            "depth": depth,
                        })
                    traverse(source, depth + 1)

        traverse(node_id, 1)
        return affected

    def clear(self) -> None:
        self._graph.clear()
        self._nodes.clear()

    def stats(self) -> dict:
        return {
            "backend": "networkx",
            "nodes": len(self._nodes),
            "edges": self._graph.number_of_edges(),
        }

    def repo_exists(self, repo_path: str) -> bool:
        return any(
            n.properties.get("repo") == repo_path
            for n in self._nodes.values()
            if n.type == NodeType.FILE
        )

    def get_repo_stats(self, repo_path: str) -> dict | None:
        if not self.repo_exists(repo_path):
            return None
        files = [
            n for n in self._nodes.values()
            if n.type == NodeType.FILE and n.properties.get("repo") == repo_path
        ]
        functions = [
            n for n in self._nodes.values()
            if n.type == NodeType.FUNCTION and n.properties.get("repo") == repo_path
        ]
        return {
            "files": len(files),
            "functions": len(functions),
            "total_nodes": len(self._nodes),
            "total_edges": self._graph.number_of_edges(),
        }

    def close(self) -> None:
        pass  # No cleanup needed for in-memory graph
