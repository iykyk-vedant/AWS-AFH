"""
Graph backend factory.

Selects Neo4j or NetworkX based on availability and configuration.
"""

import logging
import subprocess

from src.graph.base import GraphBackend
from src.graph.networkx_backend import NetworkXBackend
from src.graph.neo4j_backend import Neo4jBackend

logger = logging.getLogger(__name__)


def _docker_available() -> bool:
    """Check if Docker is available on the system."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _neo4j_available() -> bool:
    """Check if Neo4j is reachable (locally, over SSH tunnel, or on AWS EC2)."""
    try:
        from src.config import settings
        backend = Neo4jBackend(
            uri=settings.neo4j.uri,
            username=settings.neo4j.username,
            password=settings.neo4j.password,
        )
        backend.close()
        return True
    except Exception as e:
        logger.debug(f"Neo4j not available: {e}")
        return False


def create_graph_backend(prefer: str = "auto") -> GraphBackend:
    """
    Create a graph backend.

    Args:
        prefer: "auto" (try Neo4j first), "neo4j" (require Neo4j), "networkx" (force in-memory)

    Returns:
        GraphBackend instance
    """
    from src.config import settings

    if prefer == "networkx":
        logger.info("Using NetworkX backend (forced)")
        return NetworkXBackend()

    if prefer == "neo4j" or (prefer == "auto" and _neo4j_available()):
        try:
            backend = Neo4jBackend(
                uri=settings.neo4j.uri,
                username=settings.neo4j.username,
                password=settings.neo4j.password,
            )
            logger.info("Using Neo4j backend")
            return backend
        except Exception as e:
            logger.warning(f"Neo4j failed, falling back to NetworkX: {e}")
            return NetworkXBackend()

    logger.info("Using NetworkX backend (Neo4j not available)")
    return NetworkXBackend()


if __name__ == "__main__":
    print("Testing graph backend factory...")
    print(f"Docker available: {_docker_available()}")
    print(f"Neo4j available: {_neo4j_available()}")

    backend = create_graph_backend(prefer="networkx")
    print(f"Created backend: {backend.stats()}")
