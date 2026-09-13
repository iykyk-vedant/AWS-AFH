"""
Amaze on Work — Strands Agents SDK Graph Orchestrator.

Replaces the custom SupervisorAgent while-loop with a genuine Strands
GraphBuilder DAG with conditional edges for retry logic.

This is the PRIMARY orchestration engine for the hackathon demo.
The legacy SupervisorAgent in src/agents/supervisor.py is kept as fallback.

Architecture:
    Node: parse  →  Node: analyze  →  Node: review  →  Node: fix
      →  Node: validate  →(pass)→  Node: score
                          →(fail)→  Node: fix  (retry cycle, max 3)

Each node is a Strands Agent with domain-specific @tool functions from
strands_tools.py, so the LLM drives multi-step reasoning within each node.
"""

import json
import logging
import os
import sys
import time
from typing import Any, Optional

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from strands import Agent
from strands.multiagent import GraphBuilder
from strands.multiagent.graph import GraphState

from strands_tools import (
    parse_incident_tool,
    fetch_github_file_tool,
    analyze_codebase_tool,
    query_blast_radius_tool,
    review_analysis_tool,
    generate_fix_tool,
    run_sandbox_tests_tool,
    assess_risk_and_report_tool,
)

logger = logging.getLogger(__name__)

# ─── Default Configuration ────────────────────────────────────────────────────

DEFAULT_OWNER = os.getenv("GITHUB_OWNER", "iykyk-vedant")
DEFAULT_REPO = os.getenv("GITHUB_REPO", "AFH-DEMO")
DEFAULT_REPO_URL = f"https://github.com/{DEFAULT_OWNER}/{DEFAULT_REPO}"


# ─── Model Configuration ─────────────────────────────────────────────────────

def get_strands_model():
    """Get the Strands-compatible model provider.

    Priority:
    1. Amazon Bedrock (if BEDROCK_API_KEY is set and accessible)
    2. Falls back to Bedrock default (Claude Sonnet 4.6)

    Note: Strands Agent defaults to BedrockModel which uses the configured
    AWS credentials. We keep it as default for hackathon judging.
    """
    from dotenv import load_dotenv
    load_dotenv()

    # Try Bedrock first (preferred for hackathon)
    try:
        from strands.models import BedrockModel
        bedrock_key = os.getenv("BEDROCK_API_KEY")
        region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")

        if bedrock_key:
            model = BedrockModel(
                api_key=bedrock_key,
                region_name=region,
            )
            logger.info(f"[Model] Using Bedrock with API key (region={region})")
            return model
        else:
            # Use default AWS credentials
            model = BedrockModel(region_name=region)
            logger.info(f"[Model] Using Bedrock with IAM credentials (region={region})")
            return model
    except Exception as e:
        logger.warning(f"[Model] Bedrock not available: {e}")
        logger.info("[Model] Strands Agent will use default model provider")
        return None


# ─── Specialist Agent Definitions ─────────────────────────────────────────────

def _create_specialist_agents(model=None):
    """Create the 6 specialist Strands Agents for the graph pipeline.

    Each agent has a focused system prompt and domain-specific tools.
    The model parameter allows switching between Bedrock and other providers.
    """
    common_kwargs = {}
    if model is not None:
        common_kwargs["model"] = model

    incident_parser_agent = Agent(
        name="incident_parser",
        description="Parses incident tickets and extracts structured context",
        system_prompt=(
            "You are the Incident Parser agent in Amaze on Work. "
            "Your job is to parse incident tickets from GitHub Issues and extract "
            "structured context: error symptoms, affected services, stack traces, "
            "severity, suspect files, and failure type classification.\n\n"
            "Use the parse_incident_tool to fetch and parse the incident. "
            "Store the incident context in your response for downstream agents.\n\n"
            "IMPORTANT: Extract the incident_id, owner, and repo from the task "
            "description and pass them to the parse_incident_tool."
        ),
        tools=[parse_incident_tool, fetch_github_file_tool],
        **common_kwargs,
    )

    codebase_analyst_agent = Agent(
        name="codebase_analyst",
        description="Analyzes codebase to identify root cause using knowledge graph",
        system_prompt=(
            "You are the Codebase Analyst agent in Amaze on Work. "
            "Your job is to analyze the codebase to identify the root cause of an "
            "incident using the Code Property Graph (Neo4j/NetworkX) for graph-first "
            "root cause localization.\n\n"
            "Use the analyze_codebase_tool with the incident context from the previous "
            "step and the repo URL. Also use query_blast_radius_tool to trace "
            "downstream callers of suspect files.\n\n"
            "Output a detailed root cause analysis with specific file paths, "
            "function names, and the exact bug pattern."
        ),
        tools=[analyze_codebase_tool, query_blast_radius_tool, fetch_github_file_tool],
        **common_kwargs,
    )

    critic_agent = Agent(
        name="critic",
        description="Adversarial Tech Lead review of root cause analysis",
        system_prompt=(
            "You are the Adversarial Critic agent in Amaze on Work, acting as a "
            "senior Tech Lead reviewer. Your job is to rigorously review the root "
            "cause analysis from the previous step.\n\n"
            "Use review_analysis_tool to evaluate the analysis. Challenge assumptions, "
            "check for missed edge cases, and verify the diagnosis is specific enough "
            "to produce a targeted fix.\n\n"
            "Either APPROVE the analysis (if it's solid) or flag it for REVISION "
            "with specific, actionable feedback."
        ),
        tools=[review_analysis_tool],
        **common_kwargs,
    )

    fix_writer_agent = Agent(
        name="fix_writer",
        description="Generates minimal targeted code patches",
        system_prompt=(
            "You are the Fix Writer agent in Amaze on Work. "
            "Your job is to generate the smallest possible code patch that resolves "
            "the incident while minimizing blast radius.\n\n"
            "Use generate_fix_tool with the incident context, root cause analysis, "
            "and repo URL. If this is a RETRY attempt, include the previous_feedback "
            "parameter with details about what went wrong in the last validation.\n\n"
            "The patch must be a valid unified diff that can be applied with `git apply`. "
            "Focus on surgical precision — change only what is necessary."
        ),
        tools=[generate_fix_tool, fetch_github_file_tool],
        **common_kwargs,
    )

    validation_agent = Agent(
        name="validation",
        description="Runs tests in Docker sandbox to validate fixes",
        system_prompt=(
            "You are the Validation Agent in Amaze on Work. "
            "Your job is to validate the generated fix by running tests in an "
            "ephemeral Docker sandbox container.\n\n"
            "Use run_sandbox_tests_tool with the repo URL, service name, and patch "
            "diff from the Fix Writer. This tool runs REAL tests before and after "
            "the patch and performs regression analysis.\n\n"
            "CRITICAL: Report the exact verdict from the tool output. The possible "
            "verdicts are:\n"
            "- FIX_CONFIRMED: Tests pass, no regressions detected\n"
            "- REGRESSION_DETECTED: The fix introduced new test failures\n"
            "- SKIPPED: Docker not available (acceptable for demo)\n"
            "- ERROR: Test execution failed\n\n"
            "If the verdict is REGRESSION_DETECTED, describe the specific regressions "
            "so the Fix Writer can address them in the next attempt."
        ),
        tools=[run_sandbox_tests_tool],
        **common_kwargs,
    )

    risk_scorer_agent = Agent(
        name="risk_scorer",
        description="Evaluates deployment risk and generates resolution report",
        system_prompt=(
            "You are the Risk Scorer agent in Amaze on Work. "
            "Your job is to compute a composite risk score for the proposed fix "
            "and generate the final resolution report.\n\n"
            "Use assess_risk_and_report_tool with all the accumulated pipeline context: "
            "incident context, root cause analysis, patch diff, and validation results.\n\n"
            "The risk score determines the deployment action:\n"
            "- LOW (0-24): Auto-create PR with the fix\n"
            "- MEDIUM (25-49): PR with options, notify on Slack\n"
            "- HIGH (50-100): Slack notification only, human reviews"
        ),
        tools=[assess_risk_and_report_tool],
        **common_kwargs,
    )

    return {
        "parse": incident_parser_agent,
        "analyze": codebase_analyst_agent,
        "review": critic_agent,
        "fix": fix_writer_agent,
        "validate": validation_agent,
        "score": risk_scorer_agent,
    }


# ─── Graph Builder ────────────────────────────────────────────────────────────

def build_incident_graph(model=None):
    """Build the Strands GraphBuilder DAG for incident resolution.

    Topology:
        parse → analyze → review → fix → validate → score
                                         ↑ (retry) ↓
                                         fix ← validate (on failure)

    The retry loop is a conditional edge cycle — the canonical Strands Graph
    pattern for iterative refinement workflows.

    Returns:
        A compiled Strands Graph ready for execution.
    """
    agents = _create_specialist_agents(model=model)

    builder = GraphBuilder()

    # Add nodes
    parse_node = builder.add_node(agents["parse"], "parse")
    analyze_node = builder.add_node(agents["analyze"], "analyze")
    review_node = builder.add_node(agents["review"], "review")
    fix_node = builder.add_node(agents["fix"], "fix")
    validate_node = builder.add_node(agents["validate"], "validate")
    score_node = builder.add_node(agents["score"], "score")

    # Linear pipeline edges
    builder.add_edge(parse_node, analyze_node)
    builder.add_edge(analyze_node, review_node)
    builder.add_edge(review_node, fix_node)
    builder.add_edge(fix_node, validate_node)

    # Conditional edges from validate node
    def validation_passed(state: GraphState) -> bool:
        """Check if sandbox validation passed (FIX_CONFIRMED or SKIPPED)."""
        result = state.results.get("validate")
        if not result:
            return True  # No result means we proceed (defensive)
        try:
            text = str(result.result)
            return ("FIX_CONFIRMED" in text or "SKIPPED" in text)
        except Exception:
            return True

    def validation_failed(state: GraphState) -> bool:
        """Check if sandbox validation failed and needs retry."""
        result = state.results.get("validate")
        if not result:
            return False
        try:
            text = str(result.result)
            # Only retry on explicit regression, not on skip/success
            return ("REGRESSION_DETECTED" in text or "ERROR" in text) and "FIX_CONFIRMED" not in text and "SKIPPED" not in text
        except Exception:
            return False

    builder.add_edge(validate_node, score_node, condition=validation_passed)
    builder.add_edge(validate_node, fix_node, condition=validation_failed)

    # Entry point
    builder.set_entry_point("parse")

    # Safety limits
    builder.set_max_node_executions(15)  # Max 3 retry cycles × 5 nodes + buffer
    builder.set_execution_timeout(600)    # 10 minute timeout

    graph = builder.build()
    logger.info("[Graph] Strands incident resolution graph built successfully")
    logger.info(f"[Graph] Nodes: parse → analyze → review → fix → validate → score")
    logger.info(f"[Graph] Retry edge: validate → fix (on REGRESSION_DETECTED)")

    return graph


# ─── Pipeline Execution ──────────────────────────────────────────────────────

def run_incident_pipeline(
    incident_id: str,
    owner: str = DEFAULT_OWNER,
    repo: str = DEFAULT_REPO,
    repo_url: str = "",
    dry_run: bool = False,
) -> dict:
    """Execute the full Strands Graph incident resolution pipeline.

    This is the main entry point for resolving incidents through the
    Strands multi-agent graph orchestrator.

    Args:
        incident_id: Incident identifier (e.g., "INC-001")
        owner: GitHub repository owner
        repo: GitHub repository name
        repo_url: Full repository URL (computed from owner/repo if empty)
        dry_run: If True, build graph but don't execute

    Returns:
        dict with pipeline results, timing, and execution metadata
    """
    if not repo_url:
        repo_url = f"https://github.com/{owner}/{repo}"

    print("=" * 65)
    print("  Amaze on Work — Strands Agents SDK Graph Orchestrator")
    print("  AWS Agents for Humans Hackathon (Professional Agents Track)")
    print("=" * 65)
    print(f"  Incident:  {incident_id}")
    print(f"  Repo:      {owner}/{repo}")
    print(f"  Repo URL:  {repo_url}")
    print(f"  Pipeline:  parse → analyze → review → fix → validate → score")
    print(f"  Retry:     validate → fix (on regression, max 3 attempts)")
    print("=" * 65)

    model = get_strands_model()
    graph = build_incident_graph(model=model)

    if dry_run:
        print("[DRY RUN] Graph built successfully, skipping execution.")
        return {"status": "dry_run", "graph": str(graph)}

    # Build the task prompt for the graph
    task = (
        f"Resolve incident {incident_id} on repository {owner}/{repo}.\n\n"
        f"Pipeline context:\n"
        f"- Repository URL: {repo_url}\n"
        f"- Owner: {owner}\n"
        f"- Repo: {repo}\n"
        f"- Incident ID: {incident_id}\n\n"
        f"Execute the full incident resolution pipeline:\n"
        f"1. Parse the incident ticket from GitHub Issues\n"
        f"2. Analyze the codebase for root cause using the knowledge graph\n"
        f"3. Review the root cause analysis (adversarial critic)\n"
        f"4. Generate a minimal, targeted code fix\n"
        f"5. Validate the fix in Docker sandbox (real tests)\n"
        f"6. Score deployment risk and generate resolution report\n\n"
        f"Pass all context between steps. If validation fails, retry with feedback."
    )

    start_time = time.time()

    try:
        result = graph(
            task,
            invocation_state={
                "incident_id": incident_id,
                "owner": owner,
                "repo": repo,
                "repo_url": repo_url,
            }
        )

        elapsed = time.time() - start_time

        print("\n" + "=" * 65)
        print("  Pipeline Execution Complete")
        print("=" * 65)
        print(f"  Status:         {result.status}")
        print(f"  Elapsed:        {elapsed:.1f}s")
        print(f"  Nodes executed: {result.execution_count}")
        print(f"  Completed:      {result.completed_nodes}")
        print(f"  Failed:         {result.failed_nodes}")
        print("=" * 65)

        # Extract results from each node
        node_results = {}
        for node_id, node_result in result.results.items():
            try:
                node_results[node_id] = {
                    "status": str(node_result.status),
                    "execution_count": node_result.execution_count,
                    "execution_time": node_result.execution_time,
                }
            except Exception:
                node_results[node_id] = {"status": "unknown"}

        return {
            "status": str(result.status),
            "incident_id": incident_id,
            "elapsed_seconds": round(elapsed, 1),
            "execution_count": result.execution_count,
            "completed_nodes": result.completed_nodes,
            "failed_nodes": result.failed_nodes,
            "node_results": node_results,
            "graph_result": result,
        }

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"[Pipeline] Graph execution failed after {elapsed:.1f}s: {e}")
        print(f"\n[Pipeline ERROR] {e}")
        return {
            "status": "error",
            "incident_id": incident_id,
            "error": str(e),
            "elapsed_seconds": round(elapsed, 1),
        }


# ─── Agents-as-Tools Pattern for Natural Language Mode ────────────────────────

def create_orchestrator_agent(model=None):
    """Create a top-level Strands Agent that uses specialist agents as tools.

    This enables natural-language interaction:
        agent("There's a 500 error in the auth service")
    The orchestrator agent will decide which specialist to invoke.

    Returns:
        A Strands Agent with specialist agents available as tools.
    """
    agents = _create_specialist_agents(model=model)

    # Convert each specialist agent to a tool using as_tool()
    agent_tools = []
    for name, agent in agents.items():
        agent_tools.append(agent.as_tool())

    common_kwargs = {}
    if model is not None:
        common_kwargs["model"] = model

    orchestrator = Agent(
        name="amaze_orchestrator",
        description="Top-level orchestrator for Amaze on Work incident resolution",
        system_prompt=(
            "You are Amaze on Work, an autonomous DevOps and software engineering agent.\n\n"
            "You have access to specialist agents as tools:\n"
            "- incident_parser: Parse incident tickets\n"
            "- codebase_analyst: Analyze code for root cause\n"
            "- critic: Review root cause analysis\n"
            "- fix_writer: Generate code patches\n"
            "- validation: Run tests in Docker sandbox\n"
            "- risk_scorer: Assess deployment risk\n\n"
            "For a full incident resolution, invoke them in order:\n"
            "parse → analyze → review → fix → validate → score\n\n"
            "If validation fails, retry fix_writer with feedback from validation."
        ),
        tools=agent_tools,
        **common_kwargs,
    )

    return orchestrator


# ─── Module-level Graph (for import) ──────────────────────────────────────────

# Lazy initialization to avoid import-time side effects
_graph = None


def get_graph():
    """Get or create the singleton Strands incident resolution graph."""
    global _graph
    if _graph is None:
        model = get_strands_model()
        _graph = build_incident_graph(model=model)
    return _graph
