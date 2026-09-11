"""
Repository Indexer for Amaze on Work Knowledge Graph.

Clones (or fetches) a GitHub repository and indexes it into the knowledge graph:
- FILE nodes for every Python / JS / TS / JSON / YAML file
- FUNCTION / CLASS / METHOD nodes (via AST for Python, regex for JS/TS)
- CALLS edges between functions where determinable
- CONTAINS edges from files to their functions/classes

Runs on supervisor startup so that codebase_analyst can do graph-first
localization instead of hallucinating file paths.
"""

import ast
import hashlib
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from src.graph.base import GraphBackend, Node, Edge, NodeType, EdgeType

logger = logging.getLogger(__name__)

# Extensions we will index
PYTHON_EXTS = {".py"}
JS_EXTS = {".js", ".ts", ".jsx", ".tsx"}
CONFIG_EXTS = {".json", ".yaml", ".yml", ".toml", ".env.example"}
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".pytest_cache",
    "venv", ".venv", "dist", "build", ".next", "coverage",
}


class RepoIndexer:
    """
    Indexes a GitHub repository into the Amaze on Work knowledge graph.

    Usage:
        indexer = RepoIndexer(graph_backend)
        indexer.index_from_github("https://github.com/owner/repo", clone_token=token)
        # or if already cloned:
        indexer.index_directory("/path/to/repo", repo_url="https://...")
    """

    def __init__(self, graph: GraphBackend):
        self.graph = graph

    # ── Public entry points ────────────────────────────────────────────────────

    def index_from_github(
        self,
        repo_url: str,
        clone_token: Optional[str] = None,
        branch: str = "master",
    ) -> dict:
        """
        Clone repo to a temp directory and index it.
        Returns stats dict.
        """
        # If already indexed, skip
        stats = self.graph.repo_exists(repo_url)
        if stats:
            existing = self.graph.get_repo_stats(repo_url)
            if existing and existing.get("files", 0) > 0:
                logger.info(
                    f"[RepoIndexer] Repo already indexed: {existing}"
                )
                return existing

        with tempfile.TemporaryDirectory(prefix="Amaze on Work_index_") as tmpdir:
            clone_url = repo_url
            if clone_token:
                # Inject token into HTTPS URL
                clone_url = repo_url.replace(
                    "https://", f"https://{clone_token}@"
                )

            logger.info(f"[RepoIndexer] Cloning {repo_url} for indexing...")
            try:
                subprocess.run(
                    ["git", "clone", "--depth=1", f"--branch={branch}",
                     clone_url, tmpdir],
                    check=True,
                    capture_output=True,
                    timeout=120,
                )
            except subprocess.CalledProcessError:
                # Try main if master fails
                try:
                    subprocess.run(
                        ["git", "clone", "--depth=1", "--branch=main",
                         clone_url, tmpdir],
                        check=True,
                        capture_output=True,
                        timeout=120,
                    )
                except Exception as e:
                    logger.error(f"[RepoIndexer] Clone failed: {e}")
                    return {"error": str(e)}

            return self.index_directory(tmpdir, repo_url=repo_url)

    def index_directory(self, root: str, repo_url: str = "") -> dict:
        """
        Walk a local directory tree and index into the graph.
        """
        stats = {"files": 0, "functions": 0, "classes": 0, "edges": 0}
        root_path = Path(root)

        for file_path in self._walk_files(root_path):
            rel_path = str(file_path.relative_to(root_path)).replace("\\", "/")
            ext = file_path.suffix.lower()

            # Create FILE node
            file_node_id = f"file:{rel_path}"
            file_node = Node(
                id=file_node_id,
                type=NodeType.FILE,
                name=file_path.name,
                properties={
                    "file": rel_path,
                    "repo": repo_url,
                    "extension": ext,
                    "size_bytes": file_path.stat().st_size,
                },
            )
            self.graph.add_node(file_node)
            stats["files"] += 1

            # Parse content for function/class nodes
            try:
                content = file_path.read_text(encoding="utf-8", errors="replace")
                if ext in PYTHON_EXTS:
                    s, e = self._index_python(content, rel_path, file_node_id)
                elif ext in JS_EXTS:
                    s, e = self._index_js(content, rel_path, file_node_id)
                else:
                    s, e = 0, 0
                stats["functions"] += s
                stats["edges"] += e
            except Exception as ex:
                logger.debug(f"[RepoIndexer] Could not parse {rel_path}: {ex}")

        logger.info(
            f"[RepoIndexer] Indexed {repo_url}: "
            f"{stats['files']} files, {stats['functions']} functions, "
            f"{stats['edges']} edges"
        )
        return stats

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _walk_files(self, root: Path):
        """Yield all files that should be indexed."""
        for dirpath, dirs, files in os.walk(root):
            # Prune skipped directories in-place
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
            for fname in files:
                p = Path(dirpath) / fname
                if p.suffix.lower() in PYTHON_EXTS | JS_EXTS | CONFIG_EXTS:
                    yield p

    def _index_python(self, content: str, rel_path: str, file_node_id: str) -> tuple[int, int]:
        """Parse Python AST and add FUNCTION/CLASS nodes."""
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return 0, 0

        funcs = 0
        edges = 0

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_id = f"func:{rel_path}:{node.name}"
                func_node = Node(
                    id=func_id,
                    type=NodeType.FUNCTION,
                    name=node.name,
                    properties={
                        "file": rel_path,
                        "line": node.lineno,
                        "end_line": getattr(node, "end_lineno", node.lineno),
                        "is_async": isinstance(node, ast.AsyncFunctionDef),
                        "args": [a.arg for a in node.args.args],
                    },
                )
                self.graph.add_node(func_node)
                # CONTAINS edge: file → function
                self.graph.add_edge(Edge(
                    source_id=file_node_id,
                    target_id=func_id,
                    type=EdgeType.CONTAINS,
                    properties={"line": node.lineno},
                ))
                funcs += 1
                edges += 1

                # CALLS edges: look for Call nodes inside this function
                for child in ast.walk(node):
                    if isinstance(child, ast.Call):
                        callee = self._extract_call_name(child)
                        if callee:
                            callee_id = f"func:{rel_path}:{callee}"
                            self.graph.add_edge(Edge(
                                source_id=func_id,
                                target_id=callee_id,
                                type=EdgeType.CALLS,
                                properties={"line": getattr(child, "lineno", 0)},
                            ))
                            edges += 1

            elif isinstance(node, ast.ClassDef):
                class_id = f"class:{rel_path}:{node.name}"
                class_node = Node(
                    id=class_id,
                    type=NodeType.CLASS,
                    name=node.name,
                    properties={
                        "file": rel_path,
                        "line": node.lineno,
                        "bases": [self._extract_name(b) for b in node.bases],
                    },
                )
                self.graph.add_node(class_node)
                self.graph.add_edge(Edge(
                    source_id=file_node_id,
                    target_id=class_id,
                    type=EdgeType.CONTAINS,
                    properties={"line": node.lineno},
                ))
                funcs += 1
                edges += 1

        return funcs, edges

    def _index_js(self, content: str, rel_path: str, file_node_id: str) -> tuple[int, int]:
        """Regex-based JS/TS function extraction (no AST needed)."""
        funcs = 0
        edges = 0

        patterns = [
            # function foo()  /  async function foo()
            r"(?:async\s+)?function\s+(\w+)\s*\(",
            # const foo = () =>  /  const foo = async () =>
            r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\(",
            # foo: function()  /  foo: async function()
            r"(\w+)\s*:\s*(?:async\s+)?function\s*\(",
            # class Method  (simplified — matches method definitions)
            r"^\s{2,}(?:async\s+)?(\w+)\s*\(",
        ]

        lines = content.split("\n")
        for i, line in enumerate(lines, start=1):
            for pattern in patterns:
                m = re.search(pattern, line)
                if m:
                    func_name = m.group(1)
                    if func_name in {"if", "for", "while", "switch", "catch", "return"}:
                        continue
                    func_id = f"func:{rel_path}:{func_name}"
                    func_node = Node(
                        id=func_id,
                        type=NodeType.FUNCTION,
                        name=func_name,
                        properties={"file": rel_path, "line": i},
                    )
                    self.graph.add_node(func_node)
                    self.graph.add_edge(Edge(
                        source_id=file_node_id,
                        target_id=func_id,
                        type=EdgeType.CONTAINS,
                        properties={"line": i},
                    ))
                    funcs += 1
                    edges += 1
                    break

        return funcs, edges

    @staticmethod
    def _extract_call_name(call_node: ast.Call) -> Optional[str]:
        """Extract the simple name from an AST Call node."""
        if isinstance(call_node.func, ast.Name):
            return call_node.func.id
        if isinstance(call_node.func, ast.Attribute):
            return call_node.func.attr
        return None

    @staticmethod
    def _extract_name(node: ast.expr) -> str:
        """Extract string name from AST Name/Attribute node."""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""
