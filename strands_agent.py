"""
Amaze on Work — Strands Agents SDK Implementation.

Autonomous Incident-to-Fix Engineering Agent built with Strands Agents SDK.
Uses the Strands GraphBuilder multi-agent orchestration pattern with
6 specialist agents, conditional retry edges, and real Docker sandbox
validation.

Execution Modes:
    --incident INC-001              GraphBuilder DAG pipeline execution
    --incident INC-001 --fallback   Legacy SupervisorAgent fallback
    --prompt "Fix 500 error"        Natural language via agents-as-tools

Usage:
    python strands_agent.py --incident INC-001
    python strands_agent.py --incident INC-004 --repo iykyk-vedant/AFH-DEMO
    python strands_agent.py --prompt "Fix production 500 error in auth login"
    python strands_agent.py --incident INC-001 --fallback
"""

import sys
import os
import json
import argparse
import logging
from typing import Optional

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ─── Strands SDK Import ───────────────────────────────────────────────────────
from strands import Agent, tool
HAS_STRANDS = True

# ─── Strands Orchestrator Import ──────────────────────────────────────────────
from strands_orchestrator import (
    run_incident_pipeline,
    create_orchestrator_agent,
    build_incident_graph,
    get_strands_model,
)

# ─── Strands Tools Import (Real domain tools) ────────────────────────────────
from strands_tools import (
    parse_incident_tool,
    fetch_github_file_tool,
    analyze_codebase_tool,
    query_blast_radius_tool,
    review_analysis_tool,
    generate_fix_tool,
    run_sandbox_tests_tool,
    assess_risk_and_report_tool,
    knowledge_retriever_tool,
    security_review_tool,
    web_research_tool,
    create_pr_tool,
)

logger = logging.getLogger(__name__)

# ─── Default Target Repo ─────────────────────────────────────────────────────
DEFAULT_REPO = (
    os.getenv("GITHUB_REPO_FULL")
    or f"{os.getenv('GITHUB_OWNER', 'iykyk-vedant')}/{os.getenv('GITHUB_REPO', 'AFH-DEMO')}"
)


# ─── Legacy Fallback ─────────────────────────────────────────────────────────

_supervisor = None


def get_supervisor():
    """Lazy-load the legacy SupervisorAgent as fallback."""
    global _supervisor
    if _supervisor is None:
        from src.agents.supervisor import SupervisorAgent
        _supervisor = SupervisorAgent()
    return _supervisor


def run_fallback(incident_id: str, repo: str) -> str:
    """Execute incident resolution via the legacy SupervisorAgent.

    This is the fallback path when --fallback is specified or when
    Strands graph execution encounters an unrecoverable error.
    """
    owner, repo_name = repo.split("/")
    engine = get_supervisor()
    parser = engine.agents["incident_parser"]
    incident = parser.parse_from_github(owner, repo_name, incident_id)
    repo_url = f"https://github.com/{owner}/{repo_name}"
    result = engine.resolve_incident(incident, repo_url=repo_url)
    return result.get("formatted_markdown") or json.dumps(
        result.get("resolution_report", {}), indent=2
    )


# ─── CLI Entrypoint ──────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Amaze on Work — Strands Agents SDK Autonomous Incident Resolver"
    )
    parser.add_argument(
        "--incident", default=None,
        help="Incident ID (e.g., INC-001, INC-004)"
    )
    parser.add_argument(
        "--repo", default=DEFAULT_REPO,
        help="Target repository (owner/name)"
    )
    parser.add_argument(
        "--prompt", default=None,
        help="Natural language incident prompt"
    )
    parser.add_argument(
        "--fallback", action="store_true",
        help="Use legacy SupervisorAgent instead of Strands Graph"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Build graph but don't execute (validation only)"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging"
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO)

    print("=" * 65)
    print("  Amaze on Work — Strands Agents SDK")
    print("  AWS Agents for Humans Hackathon (Professional Agents Track)")
    print("=" * 65)
    print(f"  Strands SDK:   [LOADED] v{_get_strands_version()}")
    print(f"  Orchestrator:  {'[FALLBACK] SupervisorAgent' if args.fallback else '[GRAPH] Strands GraphBuilder DAG'}")
    print(f"  Pipeline:      parse → analyze → review → fix → validate → score")
    print(f"  Retry Logic:   Conditional edge: validate → fix (on regression)")
    print("=" * 65)

    if args.incident:
        owner, repo_name = args.repo.split("/")

        if args.fallback:
            # Legacy path
            print(f"\n[Fallback] Resolving {args.incident} via SupervisorAgent...")
            report = run_fallback(args.incident, args.repo)
            print("\n" + report)
        else:
            # Primary path: Strands GraphBuilder
            print(f"\n[Strands Graph] Resolving {args.incident} via GraphBuilder pipeline...")
            try:
                result = run_incident_pipeline(
                    incident_id=args.incident,
                    owner=owner,
                    repo=repo_name,
                    dry_run=args.dry_run,
                )

                if result.get("status") == "error":
                    print(f"\n[Strands Graph] Pipeline error: {result.get('error')}")
                    print("[Strands Graph] Falling back to SupervisorAgent...")
                    report = run_fallback(args.incident, args.repo)
                    print("\n" + report)
                else:
                    # Print execution summary
                    print(f"\n  Pipeline Status: {result.get('status')}")
                    print(f"  Total Time:      {result.get('elapsed_seconds', 0)}s")
                    print(f"  Nodes Executed:  {result.get('execution_count', 0)}")

            except Exception as e:
                print(f"\n[Strands Graph] Unexpected error: {e}")
                print("[Strands Graph] Falling back to SupervisorAgent...")
                report = run_fallback(args.incident, args.repo)
                print("\n" + report)

    elif args.prompt:
        # Natural language mode — Strands agents-as-tools pattern
        print(f"\n[Strands Agent] Processing prompt: '{args.prompt}'...")
        model = get_strands_model()
        orchestrator = create_orchestrator_agent(model=model)
        response = orchestrator(args.prompt)
        print("\n" + str(response))

    else:
        # Default demo run
        print("\nDefault demo run (INC-001 via Strands GraphBuilder):")
        result = run_incident_pipeline(
            incident_id="INC-001",
            dry_run=args.dry_run,
        )
        print(f"\nPipeline completed with status: {result.get('status')}")


def _get_strands_version() -> str:
    """Get installed strands-agents version."""
    try:
        import importlib.metadata
        return importlib.metadata.version("strands-agents")
    except Exception:
        return "unknown"


if __name__ == "__main__":
    main()
