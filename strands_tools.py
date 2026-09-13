"""
Amaze on Work — Strands Agents SDK Domain Tools.

Real `@tool`-decorated functions that wrap the existing agent backends.
These are wired into Strands Agent nodes inside the GraphBuilder orchestrator.

CRITICAL FIX: `run_sandbox_tests_tool` now calls the REAL DockerSandbox
with before/after test comparison — no more hardcoded "FIX_CONFIRMED".
"""

import json
import logging
import os
import sys
from typing import Optional

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strands import tool

logger = logging.getLogger(__name__)


# ─── Incident Parsing Tools ──────────────────────────────────────────────────

@tool
def parse_incident_tool(incident_id: str, owner: str, repo: str) -> str:
    """Parse a GitHub issue or local incident ticket into structured context.

    Extracts error symptoms, affected services, stack traces, severity,
    suspect files, and failure type classification using LLM analysis.

    Args:
        incident_id: The incident identifier (e.g., "INC-001").
        owner: GitHub repository owner (e.g., "iykyk-vedant").
        repo: GitHub repository name (e.g., "AFH-DEMO").

    Returns:
        JSON string with structured incident context including id, title,
        severity, service, error_type, suspect_files, and failure_type.
    """
    from src.agents.incident_parser import IncidentParserAgent
    from src.llm.client_factory import get_llm_client
    from src.mcp.github_tools import get_github_tools

    llm = get_llm_client()
    github = get_github_tools()
    parser = IncidentParserAgent(llm, github)
    incident = parser.parse_from_github(owner, repo, incident_id)

    return json.dumps({
        "incident_id": incident.get("id"),
        "title": incident.get("title"),
        "description": incident.get("description", ""),
        "severity": incident.get("severity"),
        "service": incident.get("service") or incident.get("affected_service"),
        "error_type": incident.get("error_type"),
        "failure_type": incident.get("failure_type"),
        "suspect_files": incident.get("suspect_files", []),
        "stack_traces": incident.get("stack_traces", []),
        "environment": incident.get("environment", "production"),
    }, indent=2, default=str)


@tool
def fetch_github_file_tool(owner: str, repo: str, file_path: str) -> str:
    """Fetch the contents of a specific file from a GitHub repository.

    Args:
        owner: GitHub repository owner.
        repo: GitHub repository name.
        file_path: Path to the file within the repository.

    Returns:
        The file contents as a string, or an error message if not found.
    """
    from src.mcp.github_tools import get_github_tools

    github = get_github_tools()
    try:
        content = github.get_file_content(owner, repo, file_path)
        return content if content else f"File not found: {file_path}"
    except Exception as e:
        return f"Error fetching {file_path}: {str(e)}"


# ─── Codebase Analysis Tools ─────────────────────────────────────────────────

@tool
def analyze_codebase_tool(
    incident_context: str,
    repo_url: str
) -> str:
    """Analyze the codebase to identify root cause of an incident.

    Uses the Codebase Analyst agent with Knowledge Graph (Neo4j/NetworkX)
    to perform graph-first root cause localization, AST-level analysis,
    and dependency tracing.

    Args:
        incident_context: JSON string of the parsed incident context.
        repo_url: Full URL of the target repository.

    Returns:
        JSON string with root cause analysis including suspect_files,
        root_cause_description, affected_functions, and blast_radius.
    """
    from src.agents.codebase_analyst import CodebaseAnalystAgent
    from src.agents.state import create_initial_state
    from src.llm.client_factory import get_llm_client
    from src.mcp.github_tools import get_github_tools
    from src.graph.factory import create_graph_backend

    llm = get_llm_client()
    github = get_github_tools()

    try:
        graph = create_graph_backend(prefer="auto")
    except Exception:
        graph = None

    analyst = CodebaseAnalystAgent(llm, github, graph=graph)

    incident = json.loads(incident_context) if isinstance(incident_context, str) else incident_context
    state = create_initial_state(incident, repo_url)
    response = analyst.execute(state)

    return json.dumps({
        "success": response.success,
        "analysis": response.message,
        "data": response.data if response.data else {},
        "suspect_files": state.get("suspect_files", []),
        "root_cause": state.get("root_cause_analysis", ""),
    }, indent=2, default=str)


@tool
def query_blast_radius_tool(file_path: str) -> str:
    """Query the knowledge graph for the blast radius of a file.

    Returns all downstream callers, affected services, and coupling metrics
    from the Neo4j or NetworkX code property graph.

    Args:
        file_path: Path to the file to analyze blast radius for.

    Returns:
        JSON string with blast radius metrics including affected nodes,
        affected files, coupling score, and risk classification.
    """
    from src.graph.factory import create_graph_backend
    from src.graph.query_interface import KnowledgeGraphQuery

    try:
        graph = create_graph_backend(prefer="auto")
        kg = KnowledgeGraphQuery(graph)
        result = kg.get_blast_radius(file_path)

        return json.dumps({
            "file": file_path,
            "affected_nodes": result.affected_nodes[:10] if hasattr(result, 'affected_nodes') else [],
            "affected_files": result.affected_files if hasattr(result, 'affected_files') else [],
            "total_affected": len(result.affected_nodes) if hasattr(result, 'affected_nodes') else 0,
            "high_risk": result.high_risk_affected if hasattr(result, 'high_risk_affected') else False,
            "coupling_score": getattr(result, 'coupling_score', 0),
        }, indent=2, default=str)
    except Exception as e:
        return json.dumps({
            "file": file_path,
            "affected_nodes": [],
            "affected_files": [],
            "total_affected": 0,
            "error": f"Graph query failed: {str(e)}",
        }, indent=2)


# ─── Critic Review Tool ──────────────────────────────────────────────────────

@tool
def review_analysis_tool(
    incident_context: str,
    root_cause_analysis: str,
    repo_url: str
) -> str:
    """Adversarial Tech Lead review of the root cause analysis.

    The Critic agent challenges assumptions, checks for missed edge cases,
    and either APPROVES the analysis or flags it for REVISION with specific
    feedback for the Codebase Analyst.

    Args:
        incident_context: JSON string of the parsed incident context.
        root_cause_analysis: The root cause analysis to review.
        repo_url: Full URL of the target repository.

    Returns:
        JSON string with verdict (APPROVED/NEEDS_REVISION), feedback,
        and confidence score.
    """
    from src.agents.critic import CriticAgent
    from src.agents.state import create_initial_state
    from src.llm.client_factory import get_llm_client

    llm = get_llm_client()
    critic = CriticAgent(llm)

    incident = json.loads(incident_context) if isinstance(incident_context, str) else incident_context
    state = create_initial_state(incident, repo_url)
    state["root_cause_analysis"] = root_cause_analysis

    response = critic.execute(state)

    return json.dumps({
        "verdict": state.get("critic_verdict", "APPROVED"),
        "feedback": response.message,
        "success": response.success,
        "needs_revision": response.needs_retry,
    }, indent=2, default=str)


# ─── Fix Generation Tool ─────────────────────────────────────────────────────

@tool
def generate_fix_tool(
    incident_context: str,
    root_cause_analysis: str,
    repo_url: str,
    previous_feedback: str = ""
) -> str:
    """Generate a minimal, targeted code fix as a unified diff patch.

    The Fix Writer agent produces the smallest possible patch that resolves
    the incident while minimizing blast radius. If previous_feedback is
    provided (from a failed validation), it incorporates the feedback
    to produce an improved fix.

    Args:
        incident_context: JSON string of the parsed incident context.
        root_cause_analysis: Root cause analysis driving the fix.
        repo_url: Full URL of the target repository.
        previous_feedback: Optional feedback from previous failed validation.

    Returns:
        JSON string with the generated patch diff, target files,
        and change description.
    """
    from src.agents.fix_writer import FixWriterAgent
    from src.agents.state import create_initial_state
    from src.llm.client_factory import get_llm_client
    from src.mcp.github_tools import get_github_tools

    llm = get_llm_client()
    github = get_github_tools()
    writer = FixWriterAgent(llm, github)

    incident = json.loads(incident_context) if isinstance(incident_context, str) else incident_context
    state = create_initial_state(incident, repo_url)
    state["root_cause_analysis"] = root_cause_analysis

    if previous_feedback:
        state["needs_retry"] = True
        state["error"] = previous_feedback
        state["validation_feedback"] = previous_feedback

    response = writer.execute(state)

    return json.dumps({
        "success": response.success,
        "fix_description": response.message,
        "patch_diff": state.get("patch_diff", ""),
        "fix_candidates": state.get("fix_candidates", []),
        "target_files": state.get("target_files", []),
    }, indent=2, default=str)


# ─── Sandbox Validation Tool (CRITICAL FIX) ──────────────────────────────────

@tool
def run_sandbox_tests_tool(
    repo_url: str,
    service: str,
    patch_diff: str,
    language: str = "python"
) -> str:
    """Execute tests BEFORE and AFTER a patch in an ephemeral Docker container.

    THIS IS THE CRITICAL FIX: This tool now calls the REAL DockerSandbox
    with actual before/after test comparison and regression analysis.
    No more hardcoded "FIX_CONFIRMED" — the verdict is based on real test
    results from isolated Docker containers.

    Args:
        repo_url: GitHub repository URL to clone into the sandbox.
        service: Name of the service/module being tested.
        patch_diff: The unified diff patch to apply before running tests.
        language: Programming language for test runner selection ("python" or "node").

    Returns:
        JSON string with real validation results including verdict
        (FIX_CONFIRMED/REGRESSION_DETECTED/SKIPPED), before/after test
        counts, regressions list, and fixes confirmed.
    """
    from src.sandbox.docker_runner import DockerSandbox

    sandbox = DockerSandbox()
    health = sandbox.health_check()

    if not health.get("docker_available"):
        logger.warning("[Sandbox] Docker not available — running in SKIPPED mode")
        return json.dumps({
            "verdict": "SKIPPED",
            "reason": "Docker daemon not available on this machine",
            "sandbox_status": "OFFLINE",
            "service_tested": service,
        }, indent=2)

    try:
        # Run the full before/after validation cycle
        owner = repo_url.rstrip("/").split("/")[-2]
        repo_name = repo_url.rstrip("/").split("/")[-1]

        validation_result = sandbox.validate_fix(
            repo_url=repo_url,
            service=service,
            patch_diff=patch_diff,
            language=language,
        )

        return json.dumps({
            "verdict": validation_result.verdict,
            "sandbox_status": "ONLINE",
            "service_tested": service,
            "before": {
                "total": validation_result.before.total if validation_result.before else 0,
                "passed": validation_result.before.passed if validation_result.before else 0,
                "failed": validation_result.before.failed if validation_result.before else 0,
            },
            "after": {
                "total": validation_result.after.total if validation_result.after else 0,
                "passed": validation_result.after.passed if validation_result.after else 0,
                "failed": validation_result.after.failed if validation_result.after else 0,
            },
            "regressions": validation_result.regressions,
            "fixes_confirmed": validation_result.fixes_confirmed,
            "regressions_detected": len(validation_result.regressions),
        }, indent=2, default=str)

    except Exception as e:
        logger.error(f"[Sandbox] Validation failed: {e}")
        return json.dumps({
            "verdict": "ERROR",
            "error": str(e),
            "sandbox_status": "ERROR",
            "service_tested": service,
        }, indent=2)


# ─── Risk Assessment & PR Tool ───────────────────────────────────────────────

@tool
def assess_risk_and_report_tool(
    incident_context: str,
    root_cause_analysis: str,
    patch_diff: str,
    validation_result: str,
    repo_url: str
) -> str:
    """Compute composite risk score and generate the final resolution report.

    Evaluates blast radius, test coverage, coupling, change size,
    environment, and cyclomatic delta to produce a risk level
    (LOW/MEDIUM/HIGH) and deployment recommendation.

    If risk is LOW, auto-creates a GitHub Pull Request with the fix.

    Args:
        incident_context: JSON string of the parsed incident context.
        root_cause_analysis: Root cause analysis summary.
        patch_diff: The unified diff patch that was validated.
        validation_result: JSON string of sandbox validation results.
        repo_url: Full URL of the target repository.

    Returns:
        JSON string with risk assessment (risk_level, risk_score,
        deployment_action), resolution report, and optional PR URL.
    """
    from src.agents.risk_scorer import RiskScorerAgent
    from src.agents.synthesis import SynthesisAgent
    from src.agents.state import create_initial_state
    from src.llm.client_factory import get_llm_client
    from src.mcp.github_tools import get_github_tools

    llm = get_llm_client()
    github = get_github_tools()

    incident = json.loads(incident_context) if isinstance(incident_context, str) else incident_context
    val_data = json.loads(validation_result) if isinstance(validation_result, str) else validation_result

    state = create_initial_state(incident, repo_url)
    state["root_cause_analysis"] = root_cause_analysis
    state["patch_diff"] = patch_diff
    state["validation_result"] = val_data

    # Run synthesis first (resolution report)
    synth = SynthesisAgent(llm)
    synth_response = synth.execute(state)

    # Then risk scoring
    scorer = RiskScorerAgent(llm, github_tools=github)
    risk_response = scorer.execute(state)

    return json.dumps({
        "success": risk_response.success,
        "risk_level": state.get("risk_assessment", {}).get("risk_level", "MEDIUM"),
        "risk_score": state.get("risk_assessment", {}).get("risk_score", 50),
        "deployment_action": state.get("risk_assessment", {}).get("deployment_action", "pr_with_options"),
        "resolution_report": state.get("resolution_report", synth_response.message),
        "pr_url": state.get("pr_url", ""),
        "formatted_markdown": state.get("formatted_markdown", ""),
    }, indent=2, default=str)
