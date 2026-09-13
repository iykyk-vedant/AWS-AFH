"""
Neo4j graph backend for Amaze on Work knowledge graph.

Provides Code Property Graph (CPG) storage and Cypher-based blast radius
queries via Neo4j, including Incident, Fix, StackFrame, and TestResult nodes.
"""

import logging
import os
from typing import Any, Optional

from dotenv import load_dotenv

from src.graph.base import GraphBackend, Node, Edge, NodeType, EdgeType

load_dotenv()
logger = logging.getLogger(__name__)


class Neo4jBackend(GraphBackend):
    """Neo4j graph database backend."""

    def __init__(self, uri: str, username: str, password: str):
        try:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(uri, auth=(username, password))
            self._driver.verify_connectivity()
            logger.info(f"Connected to Neo4j at {uri}")
        except Exception as e:
            logger.error(f"Failed to connect to Neo4j at {uri}: {e}")
            raise

    @classmethod
    def from_env(cls) -> "Neo4jBackend":
        return cls(
            uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            username=os.getenv("NEO4J_USER", "neo4j"),
            password=os.getenv("NEO4J_PASSWORD", ""),
        )

    def add_node(self, node: Node) -> str:
        with self._driver.session() as session:
            label = node.type.value.capitalize()
            props = {
                "id": node.id,
                "name": node.name,
                **node.properties,
            }
            if node.enriched:
                for key, value in node.enriched.items():
                    props[f"enriched_{key}"] = value
                props["enriched_confidence"] = node.enriched_confidence

            # Build property assignment string
            prop_assignments = ", ".join(
                f"n.{k} = ${k}" for k in props.keys()
            )
            query = f"""
                MERGE (n:{label} {{id: $id}})
                SET {prop_assignments}
                RETURN n.id AS id
            """
            result = session.run(query, **props)
            record = result.single()
            return record["id"] if record else node.id

    def add_edge(self, edge: Edge) -> None:
        with self._driver.session() as session:
            rel_type = edge.type.value.upper()
            props = edge.properties or {}
            prop_str = ""
            if props:
                prop_assignments = ", ".join(
                    f"{k}: ${k}" for k in props.keys()
                )
                prop_str = f" {{{prop_assignments}}}"

            query = f"""
                MATCH (a {{id: $source_id}})
                MATCH (b {{id: $target_id}})
                MERGE (a)-[r:{rel_type}{prop_str}]->(b)
                RETURN type(r) AS rel_type
            """
            session.run(query, source_id=edge.source_id, target_id=edge.target_id, **props)

    def get_node(self, node_id: str) -> Node | None:
        with self._driver.session() as session:
            result = session.run(
                "MATCH (n {id: $id}) RETURN n, labels(n) AS labels",
                id=node_id,
            )
            record = result.single()
            if not record:
                return None

            neo4j_node = record["n"]
            labels = record["labels"]
            props = dict(neo4j_node)

            # Extract enriched properties
            enriched = {}
            enriched_confidence = 0.0
            core_props = {}
            for key, value in props.items():
                if key.startswith("enriched_"):
                    if key == "enriched_confidence":
                        enriched_confidence = value
                    else:
                        enriched[key.replace("enriched_", "")] = value
                elif key not in ("id", "name"):
                    core_props[key] = value

            # Determine NodeType from labels
            node_type = NodeType.FUNCTION  # default
            for label in labels:
                try:
                    node_type = NodeType(label.lower())
                    break
                except ValueError:
                    continue

            return Node(
                id=props.get("id", node_id),
                type=node_type,
                name=props.get("name", ""),
                properties=core_props,
                enriched=enriched if enriched else None,
                enriched_confidence=enriched_confidence,
            )

    def get_nodes_by_type(self, node_type: NodeType) -> list[Node]:
        label = node_type.value.capitalize()
        with self._driver.session() as session:
            result = session.run(f"MATCH (n:{label}) RETURN n")
            nodes = []
            for record in result:
                neo4j_node = record["n"]
                props = dict(neo4j_node)
                core_props = {
                    k: v for k, v in props.items()
                    if k not in ("id", "name") and not k.startswith("enriched_")
                }
                nodes.append(Node(
                    id=props.get("id", ""),
                    type=node_type,
                    name=props.get("name", ""),
                    properties=core_props,
                ))
            return nodes

    def get_edges_from(self, node_id: str) -> list[Edge]:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (a {id: $id})-[r]->(b)
                RETURN type(r) AS rel_type, properties(r) AS props, b.id AS target_id
                """,
                id=node_id,
            )
            edges = []
            for record in result:
                try:
                    edge_type = EdgeType(record["rel_type"].lower())
                except ValueError:
                    edge_type = EdgeType.USES
                edges.append(Edge(
                    source_id=node_id,
                    target_id=record["target_id"] or "",
                    type=edge_type,
                    properties=dict(record["props"] or {}),
                ))
            return edges

    def get_edges_to(self, node_id: str) -> list[Edge]:
        with self._driver.session() as session:
            result = session.run(
                """
                MATCH (a)-[r]->(b {id: $id})
                RETURN type(r) AS rel_type, properties(r) AS props, a.id AS source_id
                """,
                id=node_id,
            )
            edges = []
            for record in result:
                try:
                    edge_type = EdgeType(record["rel_type"].lower())
                except ValueError:
                    edge_type = EdgeType.USES
                edges.append(Edge(
                    source_id=record["source_id"] or "",
                    target_id=node_id,
                    type=edge_type,
                    properties=dict(record["props"] or {}),
                ))
            return edges

    def query(self, cypher: str, params: dict | None = None) -> list[dict]:
        """Execute a raw Cypher query."""
        with self._driver.session() as session:
            result = session.run(cypher, **(params or {}))
            return [dict(record) for record in result]

    def clear(self) -> None:
        with self._driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")
        logger.info("Neo4j database cleared")

    def stats(self) -> dict:
        with self._driver.session() as session:
            node_count = session.run("MATCH (n) RETURN count(n) AS count").single()["count"]
            edge_count = session.run("MATCH ()-[r]->() RETURN count(r) AS count").single()["count"]
            return {
                "backend": "neo4j",
                "nodes": node_count,
                "edges": edge_count,
            }

    def repo_exists(self, repo_path: str) -> bool:
        with self._driver.session() as session:
            result = session.run(
                "MATCH (n:File {repo: $repo}) RETURN count(n) AS count",
                repo=repo_path,
            )
            return result.single()["count"] > 0

    def get_repo_stats(self, repo_path: str) -> dict | None:
        if not self.repo_exists(repo_path):
            return None
        with self._driver.session() as session:
            files = session.run(
                "MATCH (n:File {repo: $repo}) RETURN count(n) AS count",
                repo=repo_path,
            ).single()["count"]
            functions = session.run(
                "MATCH (n:Function {repo: $repo}) RETURN count(n) AS count",
                repo=repo_path,
            ).single()["count"]
            return {
                "files": files,
                "functions": functions,
            }

    def close(self) -> None:
        if self._driver:
            self._driver.close()
            logger.info("Neo4j connection closed")
