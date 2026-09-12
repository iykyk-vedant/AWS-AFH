"""
Codebase Analyst Agent for Amaze on Work.

Analyzes the target codebase to localize the root cause of an incident.
Uses GitHub MCP to fetch files, performs AST-level analysis for Python
and regex-based analysis for JavaScript files.
"""

import json
import logging
import re
from typing import Optional

from src.agents.base import BaseAgent, AgentResponse
from src.agents.state import (
    PipelineState,
    AgentType,
    RootCauseAnalysis,
)
from src.llm.base_client import BaseLLMClient
from src.mcp.github_tools import GitHubMCPTools
from src.graph.query_interface import KnowledgeGraphQuery
from src.graph.base import GraphBackend

logger = logging.getLogger(__name__)


class CodebaseAnalystAgent(BaseAgent):
    """
    Analyzes codebase to localize root cause of an incident.

    Strategy (in priority order):
    1. Graph-first: query the pre-built knowledge graph (exact file, function, line)
    2. Stack-trace guided navigation (innermost frame)
    3. GitHub keyword search fallback (only if graph + stack trace both miss)
    4. LLM-powered root cause diagnosis using the fetched code
    """
    agent_type = AgentType.CODEBASE_ANALYST

    def __init__(
        self,
        llm_client: BaseLLMClient,
        github_tools: GitHubMCPTools,
        graph: Optional[GraphBackend] = None,
    ):
        super().__init__(llm_client)
        self.github = github_tools
        self.kg = KnowledgeGraphQuery(graph) if graph else None

    def execute(self, state: PipelineState) -> AgentResponse:
        """Analyze codebase and produce root cause hypothesis."""
        incident = state.get("incident", {})
        owner = state.get("repo_owner", "")
        repo = state.get("repo_name", "")

        if not incident or not owner or not repo:
            return AgentResponse(
                success=False,
                message="Missing incident or repo info",
                error="Incomplete pipeline state",
            )

        try:
            root_cause = self._analyze(incident, owner, repo)
            return AgentResponse(
                success=True,
                message=f"Root cause identified: {root_cause.get('hypothesis', 'unknown')[:100]}",
                data=root_cause,
                next_agent=AgentType.CRITIC.value,
            )
        except Exception as e:
            logger.error(f"Codebase analysis failed: {e}")
            return AgentResponse(
                success=False,
                message=f"Analysis failed: {str(e)}",
                error=str(e),
            )

    async def aexecute(self, state: PipelineState) -> AgentResponse:
        return self.execute(state)

    def _analyze(self, incident: dict, owner: str, repo: str) -> RootCauseAnalysis:
        """Full analysis flow with graph-first localization."""
        service = incident.get("affected_service", "")

        # ── Step 1: Graph-first localization ──────────────────────────────────
        graph_files: list[str] = []     # exact file paths from graph
        graph_functions: list[str] = [] # exact function names from graph
        graph_context: str = ""         # rich context string for LLM prompt

        if self.kg:
            graph_stats = self.kg.graph.stats()
            logger.info(
                f"[CodebaseAnalyst] Graph has {graph_stats.get('nodes', 0)} nodes, "
                f"{graph_stats.get('edges', 0)} edges"
            )
            if graph_stats.get("nodes", 0) > 0:
                keywords = self._extract_keywords(incident)
                logger.info(f"[CodebaseAnalyst] Graph search keywords: {keywords}")

                matched_files = self.kg.search_files_by_keywords(keywords, service=service)
                matched_funcs = self.kg.search_functions_by_keywords(keywords, service=service)

                if matched_files or matched_funcs:
                    logger.info(
                        f"[CodebaseAnalyst] Graph found {len(matched_files)} files, "
                        f"{len(matched_funcs)} functions"
                    )
                    graph_files = [f.file for f in matched_files if f.file]
                    graph_functions = [f.name for f in matched_funcs]

                    # CRITICAL: resolve function matches to their actual file paths
                    # so fetch-from-GitHub targets the real files, not hallucinated ones
                    for f in matched_funcs:
                        if f.file and f.file not in graph_files:
                            graph_files.append(f.file)
                            logger.info(
                                f"[CodebaseAnalyst] Function '{f.name}' resolved to {f.file}:{f.line}"
                            )

                    ctx_lines = ["### Knowledge Graph Matches"]
                    for f in matched_files[:5]:
                        ctx_lines.append(f.to_prompt_context())
                    for f in matched_funcs[:8]:
                        ctx_lines.append(f.to_prompt_context())
                    graph_context = "\n".join(ctx_lines)
                    logger.info(
                        f"[CodebaseAnalyst] Graph files: {graph_files[:5]}"
                    )
                else:
                    logger.info("[CodebaseAnalyst] Graph matched nothing — falling back")

        # Build complete file manifest from graph for allowlist constraint
        repo_file_manifest: list[str] = []
        if self.kg:
            all_graph_files = self.kg.get_all_files()
            repo_file_manifest = [f.file for f in all_graph_files if f.file]

        # ── Step 2: Stack trace file extraction ───────────────────────────────
        suspect_files = self._identify_suspect_files(incident)
        logger.info(f"Suspect files from stack trace: {suspect_files}")

        # Merge: graph files take priority, then stack trace
        all_candidate_files: list[str] = []
        seen: set[str] = set()
        for f in graph_files + suspect_files:
            if f and f not in seen:
                all_candidate_files.append(f)
                seen.add(f)

        # ── Step 3: Fetch file contents from GitHub ────────────────────────────
        code_snippets: dict[str, str] = {}
        for file_path in all_candidate_files[:6]:
            try:
                content = self.github.get_file_content(owner, repo, file_path)
                if isinstance(content, str) and content.strip():
                    code_snippets[file_path] = content
                    logger.info(f"Fetched {file_path} ({len(content)} chars)")
                elif isinstance(content, dict):
                    text = content.get("content", "")
                    if text:
                        code_snippets[file_path] = text
            except Exception as e:
                logger.warning(f"Could not fetch {file_path}: {e}")

        # ── Step 4: GitHub keyword search (only if graph + stack trace both miss) ──
        if not code_snippets:
            logger.info("No files from graph or stack trace — searching GitHub by keywords...")
            code_snippets = self._search_relevant_files(incident, owner, repo)

        logger.info(f"Total files for analysis: {len(code_snippets)}")

        # ── Step 5: LLM diagnosis with graph context + file allowlist injected ──
        root_cause = self._diagnose_root_cause(
            incident, code_snippets,
            graph_context=graph_context,
            graph_functions=graph_functions,
            repo_file_manifest=repo_file_manifest,
        )
        return root_cause

    def _identify_suspect_files(self, incident: dict) -> list[str]:
        """Extract file paths from stack traces and error logs."""
        files = []

        # From stack traces
        for frame in incident.get("stack_traces", []):
            file_path = frame.get("file", "")
            if file_path:
                # Normalize path — remove leading slashes, node_modules, etc.
                file_path = self._normalize_path(file_path, incident.get("affected_service", ""))
                if file_path:
                    files.append(file_path)

        # From error log
        error_log = incident.get("error_log", "")
        if error_log:
            # Extract file references
            file_refs = re.findall(r'(?:src/|app/|routes/|models/|services/|middleware/|utils/)\S+\.(?:py|js|ts)', error_log)
            for ref in file_refs:
                normalized = self._normalize_path(ref, incident.get("affected_service", ""))
                if normalized and normalized not in files:
                    files.append(normalized)

        # From description
        description = incident.get("description", "")
        if description:
            desc_refs = re.findall(r'`([^`]+\.(?:py|js|ts))`', description)
            for ref in desc_refs:
                normalized = self._normalize_path(ref, incident.get("affected_service", ""))
                if normalized and normalized not in files:
                    files.append(normalized)

        return files

    def _normalize_path(self, file_path: str, service: str) -> str:
        """Normalize a file path for the shopstack-platform repo structure."""
        # Remove absolute path prefixes
        path = file_path.strip()
        path = re.sub(r'^.*?(?=src/|app/|routes/|models/|tests/|services/|middleware/|utils/)', '', path)

        if not path:
            return ""

        # Determine service prefix
        service_dir = ""
        if "python" in service.lower() or path.endswith(".py"):
            service_dir = "python-service"
        elif "node" in service.lower() or path.endswith((".js", ".ts")):
            service_dir = "node-service"

        # Add service directory prefix if not already present
        if service_dir and not path.startswith(service_dir):
            path = f"{service_dir}/{path}"

        return path

    def _search_relevant_files(self, incident: dict, owner: str, repo: str) -> dict[str, str]:
        """Search for relevant files when stack trace is insufficient."""
        code_snippets = {}
        service = incident.get("affected_service", "")

        if "python" in service.lower():
            service_dir = "python-service"
        elif "node" in service.lower():
            service_dir = "node-service"
        else:
            service_dir = ""

        # Search by error keywords
        keywords = self._extract_keywords(incident)
        for keyword in keywords[:3]:
            try:
                results = self.github.search_code(owner, repo, keyword, per_page=3)
                if isinstance(results, str):
                    import json as _json
                    results = _json.loads(results)
                for item in results:
                    path = item.get("path", "")
                    if path and path not in code_snippets:
                        if service_dir and not path.startswith(service_dir):
                            continue
                        try:
                            content = self.github.get_file_content(owner, repo, path)
                            # get_file_content returns raw text string
                            if isinstance(content, str) and content.strip():
                                code_snippets[path] = content
                            elif isinstance(content, dict):
                                text = content.get("content", "")
                                if text:
                                    code_snippets[path] = text
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"Code search for '{keyword}' failed: {e}")

        # Fallback: fetch key files for the service
        if not code_snippets:
            key_files = self._get_key_files(service_dir, incident)
            for path in key_files:
                try:
                    content = self.github.get_file_content(owner, repo, path)
                    if isinstance(content, str) and content.strip():
                        code_snippets[path] = content
                    elif isinstance(content, dict):
                        text = content.get("content", "")
                        if text:
                            code_snippets[path] = text
                except Exception:
                    pass

        return code_snippets

    def _extract_keywords(self, incident: dict) -> list[str]:
        """Extract search keywords from incident data for graph + GitHub search."""
        keywords = []
        error_log = incident.get("error_log", "")
        title = incident.get("title", "")
        description = incident.get("description", "")
        tags = incident.get("tags", [])

        # 1. Extract function/class names from error log (most precise)
        func_names = re.findall(r'(?:in |at )\s*(\w+)', error_log)
        keywords.extend(func_names[:3])

        # 2. Extract identifier-style words from error (e.g. apply_discount, DiscountService)
        identifiers = re.findall(r'\b([a-z][a-z_]+[a-z])\b', error_log, re.IGNORECASE)
        for ident in identifiers:
            if len(ident) > 4 and ident not in keywords:
                keywords.append(ident)

        # 3. Title words (meaningful, longer than 3 chars)
        title_words = [w.lower() for w in re.findall(r'\b\w+\b', title) if len(w) > 3 and w.isalpha()]
        for tw in title_words:
            if tw not in keywords and tw not in {"with", "from", "have", "this", "that", "been", "when", "there", "which", "their", "about", "after", "before", "production"}:
                keywords.append(tw)

        # 4. Description words (code-relevant terms)
        desc_words = re.findall(r'\b([a-z][a-z_]+)\b', description.lower())
        for dw in desc_words:
            if len(dw) > 4 and dw not in keywords and dw not in {"service", "issue", "error", "problem", "should", "being", "where", "which", "there"}:
                keywords.append(dw)

        # 5. Tags are highly relevant
        for tag in tags:
            tag_lower = tag.lower().replace("-", "_")
            if tag_lower not in keywords:
                keywords.append(tag_lower)

        # Deduplicate preserving order, max 10
        return list(dict.fromkeys(keywords))[:10]

    def _get_key_files(self, service_dir: str, incident: dict) -> list[str]:
        """Get key files to inspect based on service and tags."""
        tags = [t.lower() for t in incident.get("tags", [])]

        if service_dir == "python-service":
            files = ["python-service/app/__init__.py", "python-service/app/config.py"]
            if any(t in tags for t in ["authentication", "login", "auth"]):
                files.append("python-service/app/routes/auth.py")
            if any(t in tags for t in ["order", "checkout", "payment"]):
                files.extend([
                    "python-service/app/routes/orders.py",
                    "python-service/app/routes/payments.py",
                    "python-service/app/services/payment_service.py",
                ])
            if any(t in tags for t in ["product", "search", "sql"]):
                files.append("python-service/app/routes/products.py")
            files.append("python-service/requirements.txt")
        elif service_dir == "node-service":
            files = ["node-service/src/index.js", "node-service/src/config.js"]
            if any(t in tags for t in ["authentication", "login", "auth"]):
                files.append("node-service/src/routes/auth.js")
            if any(t in tags for t in ["user", "profile"]):
                files.extend([
                    "node-service/src/routes/users.js",
                    "node-service/src/services/userService.js",
                ])
            if any(t in tags for t in ["product", "search", "pagination"]):
                files.append("node-service/src/routes/products.js")
            if any(t in tags for t in ["report", "sales"]):
                files.append("node-service/src/routes/reports.js")
            if any(t in tags for t in ["cors", "validation", "middleware"]):
                files.extend([
                    "node-service/src/middleware/validation.js",
                ])
            files.append("node-service/package.json")
        else:
            files = []

        return files

    def _diagnose_root_cause(
        self,
        incident: dict,
        code_snippets: dict[str, str],
        graph_context: str = "",
        graph_functions: list | None = None,
        repo_file_manifest: list | None = None,
    ) -> RootCauseAnalysis:
        """Use LLM to diagnose root cause from incident + code context + graph data."""

        # Build code context — truncate large files but retain structure
        code_context = ""
        total_len = 0
        for path, content in code_snippets.items():
            if total_len > 8000:
                break
            if len(content) > 2000:
                content = content[:2000] + "\n... (truncated for context window)"
            snippet = f"\n### File: {path}\n```\n{content}\n```\n"
            code_context += snippet
            total_len += len(snippet)

        if not code_context:
            code_context = "(No source code could be fetched — base analysis on incident data only)"

        graph_section = ""
        if graph_context:
            graph_section = f"""
=== KNOWLEDGE GRAPH CONTEXT (pre-built AST analysis of this repo) ===
{graph_context[:2000]}

The graph has identified the above files and functions as matching the incident keywords.
Prioritize these when forming your hypothesis.
"""

        # Build strict file allowlist so LLM cannot invent paths
        file_allowlist_section = ""
        if repo_file_manifest:
            manifest_str = "\n".join(f"  - {p}" for p in repo_file_manifest[:60])
            file_allowlist_section = f"""
=== FILE ALLOWLIST (Key files in this repository) ===
{manifest_str}

CRITICAL: The above list contains files in this repository.
You MUST ONLY reference file paths from this list in your suspect_files output.
Do NOT invent, assume, or fabricate any file path that is not in this list.
"""

        prompt = f"""You are a **Senior Site Reliability Engineer (SRE) and Principal Software Engineer** with 15 years of
experience debugging production incidents at scale (Google SRE model). Your job is to perform a rigorous,
evidence-based root cause analysis (RCA) of the following incident.

=== INCIDENT DETAILS ===
ID: {incident.get('id', 'unknown')}
Title: {incident.get('title', '')}
Affected Service: {incident.get('affected_service', '')}
Severity: {incident.get('severity', '')}
Environment: {incident.get('environment', '')}
Description: {incident.get('description', '')}
Error Log / Stack Trace:
{incident.get('error_log', '')[:2000]}
Recent Changes: {incident.get('recent_changes', '')}
Tags: {incident.get('tags', [])}
Expected Behavior: {incident.get('expected_behavior', '')}
Actual Behavior: {incident.get('actual_behavior', '')}
{graph_section}{file_allowlist_section}
=== SOURCE CODE ===
{code_context}

=== YOUR ANALYSIS PROCESS ===
Follow this structured RCA methodology:

**Step 1 — Error Taxonomy**: Classify the error (NullPointer, SQL, Import, Config, Logic, Dependency, Security)
**Step 2 — Stack Trace Navigation**: Start at innermost frame, identify the exact line that throws
**Step 3 — Code Path Tracing**: Trace the execution path from entry point to the failure site
**Step 4 — Blast Radius**: Identify all callers/dependents of the failing function
**Step 5 — Root Cause Hypothesis**: State the single most-probable defect with line-level precision
**Step 6 — Confidence Assessment**: Rate confidence 0.0-1.0 based on evidence quality

=== OUTPUT FORMAT (respond with ONLY valid JSON) ===
{{
    "hypothesis": "Precise technical root cause statement referencing the EXACT file path from the allowlist.",
    "root_cause_summary": "One-sentence executive summary for Slack/Jira",
    "confidence": 0.9,
    "suspect_files": ["MUST be exact paths from the FILE ALLOWLIST above"],
    "suspect_functions": ["exact_function_name from the source code"],
    "suspect_lines": [
        {{"file": "exact/path/from/allowlist.py", "line": 47, "issue": "description"}}
    ],
    "failure_type": "runtime_crash",
    "reasoning": "Evidence chain referencing actual files and line numbers.",
    "fix_direction": "Concise description of the minimal fix."
}}

CRITICAL RULES:
1. suspect_files MUST contain ONLY paths from the FILE ALLOWLIST. Do NOT invent file paths.
2. If the Knowledge Graph found matching functions, USE those exact files.
3. suspect_functions must be EXACT function names from the code shown above.
4. If no source code was fetched, use a lower confidence (0.3-0.5).
5. NEVER output a file path like 'python-service/app/services/discount_service.py' unless it appears in the FILE ALLOWLIST."""

        try:
            result = self._llm_call(prompt, temperature=0.1, max_tokens=2048)
            json_match = re.search(r'\{[\s\S]*\}', result)
            if json_match:
                analysis = json.loads(json_match.group())
                analysis["code_snippets"] = {k: v[:500] for k, v in code_snippets.items()}

                # POST-LLM VALIDATION: filter suspect_files against real repo files
                llm_suspect_files = analysis.get("suspect_files", [])
                if repo_file_manifest and llm_suspect_files:
                    valid_files = [f for f in llm_suspect_files if f in repo_file_manifest]
                    invalid_files = [f for f in llm_suspect_files if f not in repo_file_manifest]
                    if invalid_files:
                        logger.warning(
                            f"[CodebaseAnalyst] LLM hallucinated files NOT in repo: {invalid_files}"
                        )
                    if valid_files:
                        analysis["suspect_files"] = valid_files
                    else:
                        # All LLM files were hallucinated — fall back to actually fetched files
                        logger.warning(
                            "[CodebaseAnalyst] ALL LLM suspect_files were hallucinated — "
                            "using actually-fetched files instead"
                        )
                        analysis["suspect_files"] = list(code_snippets.keys())

                # Map fields to RootCauseAnalysis TypedDict keys
                return RootCauseAnalysis(
                    hypothesis=analysis.get("hypothesis", ""),
                    confidence=float(analysis.get("confidence", 0.5)),
                    suspect_files=analysis.get("suspect_files", list(code_snippets.keys())),
                    suspect_functions=analysis.get("suspect_functions", []),
                    suspect_lines=analysis.get("suspect_lines", []),
                    failure_type=analysis.get("failure_type", incident.get("failure_type", "unknown")),
                    reasoning=analysis.get("reasoning", ""),
                    code_snippets={k: v[:500] for k, v in code_snippets.items()},
                )

        except Exception as e:
            logger.error(f"LLM diagnosis failed: {e}")

        # Fallback — provide best-effort analysis from incident data
        return RootCauseAnalysis(
            hypothesis=f"Unable to auto-determine root cause. Error: {incident.get('error_log', '')[:200]}",
            confidence=0.1,
            suspect_files=list(code_snippets.keys()),
            suspect_functions=[],
            suspect_lines=[],
            failure_type=incident.get("failure_type", "unknown"),
            reasoning="Automated analysis failed — manual review needed",
            code_snippets={k: v[:500] for k, v in code_snippets.items()},
        )

    def get_system_prompt(self) -> str:
        return (
            "You are the Codebase Analyst Agent in Amaze on Work — a Principal SRE performing production RCA. "
            "You fetch and analyze source code from GitHub to identify the exact line, function, and file "
            "where the defect originates. You use structured 5-step RCA methodology (Error Taxonomy → "
            "Stack Trace Navigation → Code Path Tracing → Blast Radius → Root Cause Hypothesis). "
            "You are expert in Python/Flask/SQLAlchemy, Node.js/Express/Sequelize, "
            "and microservice architectures. Always provide evidence-based hypotheses with line-level precision."
        )
