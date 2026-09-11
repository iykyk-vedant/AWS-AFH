"""
KG Builder Agent for Amaze on Work.

Ingests a codebase into the Neo4j knowledge graph:
- Files, functions, classes as nodes
- Calls, imports, defined_in, belongs_to as edges
- Incident and Fix nodes for learning

Fetches code via GitHub MCP and parses with Python ast / regex.
"""

import ast
import logging
import re
from typing import Optional

from src.agents.base import BaseAgent, AgentResponse
from src.agents.state import PipelineState, AgentType
from src.llm.base_client import BaseLLMClient
from src.graph.base import Node, Edge, NodeType, EdgeType, GraphBackend
from src.mcp.github_tools import GitHubMCPTools

logger = logging.getLogger(__name__)


class KGBuilderAgent(BaseAgent):
    """
    Builds the knowledge graph from a codebase.

    Strategy:
    1. Fetch repo file tree via GitHub MCP
    2. Filter to source files (.py, .js)
    3. Parse Python with ast module, JS with regex
    4. Create nodes (File, Function, Class, Service)
    5. Create edges (CALLS, IMPORTS, DEFINED_IN, BELONGS_TO)
    """
    agent_type = AgentType.KG_BUILDER

    def __init__(
        self,
        llm_client: BaseLLMClient,
        graph: GraphBackend,
        github_tools: GitHubMCPTools,
    ):
        super().__init__(llm_client)
        self.graph = graph
        self.github = github_tools

    def execute(self, state: PipelineState) -> AgentResponse:
        owner = state.get("repo_owner", "")
        repo = state.get("repo_name", "")

        if not owner or not repo:
            return AgentResponse(
                success=False,
                message="Missing repo info",
                error="repo_owner and repo_name required",
            )

        try:
            stats = self._build_graph(owner, repo)
            return AgentResponse(
                success=True,
                message=f"Knowledge graph built: {stats}",
                data=stats,
            )
        except Exception as e:
            logger.error(f"KG build failed: {e}")
            return AgentResponse(success=False, message=str(e), error=str(e))

    async def aexecute(self, state: PipelineState) -> AgentResponse:
        return self.execute(state)

    def _build_graph(self, owner: str, repo: str) -> dict:
        """Build graph from remote repo."""
        # Check if already built
        repo_path = f"{owner}/{repo}"
        if self.graph.repo_exists(repo_path):
            logger.info(f"Graph already exists for {repo_path}")
            return self.graph.stats()

        # Fetch file tree
        tree = self.github.get_repo_tree(owner, repo)
        source_files = [
            f for f in tree
            if f.get("type") == "blob" and self._is_source_file(f.get("path", ""))
        ]

        logger.info(f"Found {len(source_files)} source files in {repo_path}")

        # Create service nodes
        services = set()
        for f in source_files:
            path = f.get("path", "")
            service = path.split("/")[0] if "/" in path else "root"
            services.add(service)

        for service in services:
            self.graph.add_node(Node(
                id=f"svc:{service}",
                type=NodeType.SERVICE,
                name=service,
                properties={"repo": repo_path},
            ))

        # Process each file
        processed = 0
        for file_info in source_files[:50]:  # Limit to avoid rate limits
            path = file_info.get("path", "")
            try:
                file_data = self.github.get_file_content(owner, repo, path)
                if file_data.get("type") != "file":
                    continue

                content = file_data.get("content", "")
                service = path.split("/")[0] if "/" in path else "root"

                # Create file node
                file_id = f"file:{path}"
                self.graph.add_node(Node(
                    id=file_id,
                    type=NodeType.FILE,
                    name=path.split("/")[-1],
                    properties={"file": path, "repo": repo_path, "service": service},
                ))

                # Link file to service
                self.graph.add_edge(Edge(
                    source_id=file_id,
                    target_id=f"svc:{service}",
                    type=EdgeType.BELONGS_TO,
                    properties={},
                ))

                # Parse based on language
                if path.endswith(".py"):
                    self._parse_python(content, path, repo_path)
                elif path.endswith(".js"):
                    self._parse_javascript(content, path, repo_path)

                processed += 1
            except Exception as e:
                logger.debug(f"Skipping {path}: {e}")

        stats = self.graph.stats()
        stats["files_processed"] = processed
        logger.info(f"KG built: {stats}")
        return stats

    def _is_source_file(self, path: str) -> bool:
        """Check if a file is a parseable source file."""
        if any(skip in path for skip in ["node_modules", "__pycache__", ".git", "venv", ".env"]):
            return False
        return path.endswith((".py", ".js")) and not path.endswith((".min.js", ".test.js", ".spec.js", "_test.py"))

    def _parse_python(self, content: str, file_path: str, repo: str):
        """Parse Python file with ast module."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
                func_id = f"func:{file_path}:{node.name}"
                self.graph.add_node(Node(
                    id=func_id,
                    type=NodeType.FUNCTION,
                    name=node.name,
                    properties={
                        "file": file_path,
                        "line": node.lineno,
                        "repo": repo,
                        "args": [a.arg for a in node.args.args],
                        "is_async": isinstance(node, ast.AsyncFunctionDef),
                    },
                ))
                # Link to file
                self.graph.add_edge(Edge(
                    source_id=f"file:{file_path}",
                    target_id=func_id,
                    type=EdgeType.CONTAINS,
                    properties={},
                ))

                # Find function calls within this function
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        callee_name = self._get_call_name(child)
                        if callee_name:
                            callee_id = f"func:{file_path}:{callee_name}"
                            self.graph.add_edge(Edge(
                                source_id=func_id,
                                target_id=callee_id,
                                type=EdgeType.CALLS,
                                properties={"line": getattr(child, "lineno", 0)},
                            ))

            elif isinstance(node, ast.ClassDef):
                class_id = f"class:{file_path}:{node.name}"
                self.graph.add_node(Node(
                    id=class_id,
                    type=NodeType.CLASS,
                    name=node.name,
                    properties={
                        "file": file_path,
                        "line": node.lineno,
                        "repo": repo,
                    },
                ))
                self.graph.add_edge(Edge(
                    source_id=f"file:{file_path}",
                    target_id=class_id,
                    type=EdgeType.CONTAINS,
                    properties={},
                ))

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imp_id = f"import:{file_path}:{alias.name}"
                    self.graph.add_node(Node(
                        id=imp_id,
                        type=NodeType.IMPORT,
                        name=alias.name,
                        properties={"file": file_path, "repo": repo},
                    ))
                    self.graph.add_edge(Edge(
                        source_id=f"file:{file_path}",
                        target_id=imp_id,
                        type=EdgeType.IMPORTS,
                        properties={},
                    ))

            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in (node.names or []):
                    imp_id = f"import:{file_path}:{module}.{alias.name}"
                    self.graph.add_node(Node(
                        id=imp_id,
                        type=NodeType.IMPORT,
                        name=f"{module}.{alias.name}",
                        properties={"file": file_path, "repo": repo, "module": module},
                    ))
                    self.graph.add_edge(Edge(
                        source_id=f"file:{file_path}",
                        target_id=imp_id,
                        type=EdgeType.IMPORTS,
                        properties={},
                    ))

    def _parse_javascript(self, content: str, file_path: str, repo: str):
        """Parse JavaScript file with regex patterns."""
        # Find function declarations
        func_patterns = [
            r'(?:async\s+)?function\s+(\w+)\s*\(',
            r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>',
            r'(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?function',
            r'(\w+)\s*:\s*(?:async\s+)?function',
            r'(\w+)\s*:\s*(?:async\s+)?\([^)]*\)\s*=>',
        ]

        for pattern in func_patterns:
            for match in re.finditer(pattern, content):
                func_name = match.group(1)
                line = content[:match.start()].count("\n") + 1
                func_id = f"func:{file_path}:{func_name}"
                self.graph.add_node(Node(
                    id=func_id,
                    type=NodeType.FUNCTION,
                    name=func_name,
                    properties={"file": file_path, "line": line, "repo": repo},
                ))
                self.graph.add_edge(Edge(
                    source_id=f"file:{file_path}",
                    target_id=func_id,
                    type=EdgeType.CONTAINS,
                    properties={},
                ))

        # Find require/import statements
        require_pattern = r"(?:const|let|var)\s+\w+\s*=\s*require\(['\"]([^'\"]+)['\"]\)"
        for match in re.finditer(require_pattern, content):
            module = match.group(1)
            imp_id = f"import:{file_path}:{module}"
            self.graph.add_node(Node(
                id=imp_id,
                type=NodeType.IMPORT,
                name=module,
                properties={"file": file_path, "repo": repo},
            ))
            self.graph.add_edge(Edge(
                source_id=f"file:{file_path}",
                target_id=imp_id,
                type=EdgeType.IMPORTS,
                properties={},
            ))

        import_pattern = r"import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]"
        for match in re.finditer(import_pattern, content):
            module = match.group(1)
            imp_id = f"import:{file_path}:{module}"
            self.graph.add_node(Node(
                id=imp_id,
                type=NodeType.IMPORT,
                name=module,
                properties={"file": file_path, "repo": repo},
            ))
            self.graph.add_edge(Edge(
                source_id=f"file:{file_path}",
                target_id=imp_id,
                type=EdgeType.IMPORTS,
                properties={},
            ))

    def _get_call_name(self, node: ast.Call) -> str | None:
        """Extract function name from an ast.Call node."""
        if isinstance(node.func, ast.Name):
            return node.func.id
        elif isinstance(node.func, ast.Attribute):
            return node.func.attr
        return None

    def record_incident(self, incident: dict, fix_plan: dict, validation: dict):
        """Record an incident resolution in the KG for learning."""
        incident_id = incident.get("id", "unknown")

        # Create incident node
        self.graph.add_node(Node(
            id=f"incident:{incident_id}",
            type=NodeType.INCIDENT,
            name=incident.get("title", ""),
            properties={
                "service": incident.get("affected_service", ""),
                "severity": incident.get("severity", ""),
                "failure_type": incident.get("failure_type", ""),
                "resolution_summary": fix_plan.get("description", ""),
                "confidence": validation.get("confidence_score", 0),
                "affected_files": [c.get("file_path", "") for c in fix_plan.get("files_to_modify", [])],
            },
        ))

        # Create fix node
        self.graph.add_node(Node(
            id=f"fix:{incident_id}",
            type=NodeType.FIX,
            name=f"Fix for {incident_id}",
            properties={
                "patch_summary": fix_plan.get("description", ""),
                "modified_files": [c.get("file_path", "") for c in fix_plan.get("files_to_modify", [])],
                "failure_type": incident.get("failure_type", ""),
                "confidence": validation.get("confidence_score", 0),
            },
        ))

        # Link incident → fix
        self.graph.add_edge(Edge(
            source_id=f"incident:{incident_id}",
            target_id=f"fix:{incident_id}",
            type=EdgeType.RESOLVED_BY,
            properties={},
        ))

        # Link fix → modified files
        for change in fix_plan.get("files_to_modify", []):
            file_path = change.get("file_path", "")
            file_id = f"file:{file_path}"
            self.graph.add_edge(Edge(
                source_id=f"fix:{incident_id}",
                target_id=file_id,
                type=EdgeType.MODIFIES,
                properties={},
            ))

        logger.info(f"Recorded incident {incident_id} in knowledge graph")

    def get_system_prompt(self) -> str:
        return (
            "You are the Knowledge Graph Builder Agent in Amaze on Work. "
            "You parse codebases and build structural knowledge graphs."
        )
