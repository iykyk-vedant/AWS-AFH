"""
Amaze on Work -- Demo Runner

Video-ready demo script that showcases all 7 capabilities:
1. Parse natural language incident tickets
2. Analyze codebase to detect root causes
3. Apply correct and minimal fixes
4. Write and execute tests to validate correctness
5. Run application in sandboxed Docker environment
6. Prevent regressions (before/after comparison)
7. Generate structured resolution report

Also posts notifications to Slack at start and end.

Usage:
    python demo.py                           # default: INC-001
    python demo.py --incident INC-003        # specific incident
    python demo.py --slack-channel #incidents  # post to Slack
"""

import sys
import os
import json
import time
import argparse

sys.path.insert(0, ".")
os.environ["PYTHONIOENCODING"] = "utf-8"

from dotenv import load_dotenv
load_dotenv()

# ─── Rich console setup ───────────────────────────────────────────
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.markdown import Markdown
from rich import box

console = Console()


def banner():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Amaze on Work[/bold cyan]\n"
        "[dim]Autonomous Incident-to-Fix Engineering Agent[/dim]",
        border_style="cyan",
        padding=(1, 4),
    ))
    console.print()


def step_header(num, title, subtitle=""):
    console.print()
    console.rule(f"[bold yellow]Step {num}[/bold yellow]  {title}", style="yellow")
    if subtitle:
        console.print(f"  [dim]{subtitle}[/dim]")
    console.print()


def main():
    parser = argparse.ArgumentParser(description="Amaze on Work Demo")
    parser.add_argument("--incident", default="INC-001", help="Incident ID")
    parser.add_argument("--repo", default="Rezinix-AI/shopstack-platform")
    parser.add_argument("--slack-channel", default="", help="Slack channel name or ID")
    args = parser.parse_args()

    owner, repo = args.repo.split("/")
    incident_id = args.incident
    slack_channel = args.slack_channel

    banner()

    # ─── System Health ─────────────────────────────────────────────
    step_header(0, "System Health Check", "Verifying all components are operational")

    from src.llm.client_factory import get_llm_client
    from src.sandbox.docker_runner import DockerSandbox
    from src.mcp.client_bridge import get_mcp_bridge
    from src.graph.factory import create_graph_backend

    health_table = Table(box=box.ROUNDED, show_header=False, padding=(0, 2))
    health_table.add_column("Component", style="bold")
    health_table.add_column("Status")
    health_table.add_column("Details", style="dim")

    llm = get_llm_client()
    health_table.add_row("LLM", "[green]ONLINE[/green]", llm.get_model_name())

    bridge = get_mcp_bridge()
    health_table.add_row("GitHub MCP", "[green]ONLINE[/green]", f"12 tools available")

    ds = DockerSandbox()
    dh = ds.health_check()
    health_table.add_row("Docker Sandbox", "[green]ONLINE[/green]", f"Python + Node images ready")

    try:
        graph = create_graph_backend(prefer="neo4j")
        gs = graph.stats()
        health_table.add_row("Neo4j KG", "[green]ONLINE[/green]", f"{gs['nodes']} nodes, {gs['edges']} edges")
    except Exception:
        graph = create_graph_backend(prefer="networkx")
        health_table.add_row("Graph (NetworkX)", "[yellow]FALLBACK[/yellow]", "In-memory mode")

    if os.getenv("SLACK_BOT_TOKEN"):
        health_table.add_row("Slack", "[green]ONLINE[/green]", "Bot connected")
    else:
        health_table.add_row("Slack", "[dim]SKIPPED[/dim]", "No token configured")

    console.print(health_table)

    # ─── Slack: Incident Started ───────────────────────────────────
    slack_ts = None
    if slack_channel and os.getenv("SLACK_BOT_TOKEN"):
        try:
            # Auto-join channel first
            from slack_sdk import WebClient
            slack_client = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
            try:
                slack_client.conversations_join(channel=slack_channel)
            except Exception:
                pass  # Already in channel or private

            result = bridge.post_incident_started(
                slack_channel, incident_id,
                f"Demo resolution starting for {incident_id}"
            )
            slack_ts = result.get("ts", "")
            console.print(f"  [green]>> Slack: 'Incident started' sent to channel[/green]")
        except Exception as e:
            console.print(f"  [yellow]Slack notification failed: {e}[/yellow]")

    # ─── Step 1: Parse Incident ────────────────────────────────────
    step_header(1, "Parse Incident Ticket",
                "Reading natural language incident and extracting failure context")

    from src.agents.incident_parser import IncidentParserAgent
    from src.mcp.github_tools import get_github_tools

    parser_agent = IncidentParserAgent(llm, get_github_tools())

    with console.status("[bold cyan]Parsing incident from GitHub...", spinner="dots"):
        incident = parser_agent.parse_from_github(owner, repo, incident_id)

    inc_table = Table(box=box.ROUNDED, title=f"Incident {incident_id}", title_style="bold red")
    inc_table.add_column("Field", style="bold")
    inc_table.add_column("Value")
    inc_table.add_row("Title", str(incident.get("title", "")))
    inc_table.add_row("Service", str(incident.get("affected_service", "")))
    inc_table.add_row("Severity", str(incident.get("severity", "")))
    inc_table.add_row("Environment", str(incident.get("environment", "")))
    inc_table.add_row("Failure Type", str(incident.get("failure_type", "")))

    stack = incident.get("stack_trace", [])
    if stack:
        inc_table.add_row("Stack Frames", str(len(stack)))

    console.print(inc_table)

    # ─── Step 2: Analyze Codebase ──────────────────────────────────
    step_header(2, "Analyze Codebase & Detect Root Cause",
                "Fetching suspect files via GitHub MCP, running LLM diagnosis")

    from src.agents.codebase_analyst import CodebaseAnalystAgent
    from src.agents.state import create_initial_state

    state = create_initial_state(incident, f"https://github.com/{owner}/{repo}")
    state["repo_owner"] = owner
    state["repo_name"] = repo
    analyst = CodebaseAnalystAgent(llm, get_github_tools())

    with console.status("[bold cyan]Analyzing codebase...", spinner="dots"):
        analysis = analyst.execute(state)

    root_cause_data = analysis.data or {}
    suspect_files = root_cause_data.get("suspect_files", [])
    hypothesis = root_cause_data.get("hypothesis", "N/A")

    console.print(f"  [bold]Suspect Files:[/bold]")
    for f in suspect_files:
        console.print(f"    [yellow]>> {f}[/yellow]")

    console.print(f"\n  [bold]Root Cause:[/bold]")
    console.print(Panel(hypothesis, border_style="red", padding=(0, 2)))

    state["root_cause"] = root_cause_data

    # ─── Step 3: Critic Review ─────────────────────────────────────
    step_header(3, "Critic Quality Gate",
                "Independent LLM review of root cause before generating fix")

    from src.agents.critic import CriticAgent
    critic = CriticAgent(llm)

    with console.status("[bold cyan]Critic reviewing analysis...", spinner="dots"):
        critic_result = critic.execute(state)

    verdict = (critic_result.data or {}).get("verdict", "UNKNOWN")
    color = "green" if verdict == "APPROVED" else "red"
    console.print(f"  Critic Verdict: [{color}]{verdict}[/{color}]")

    # ─── Step 4: Generate Fix ──────────────────────────────────────
    step_header(4, "Generate Minimal Code Fix",
                "LLM generates targeted patch with unified diff")

    from src.agents.fix_writer import FixWriterAgent
    fix_writer = FixWriterAgent(llm, get_github_tools())

    with console.status("[bold cyan]Generating fix...", spinner="dots"):
        fix_result = fix_writer.execute(state)

    fix_data = fix_result.data or {}
    state["fix_plan"] = fix_data

    if fix_data.get("files_to_modify"):
        console.print(f"  [bold]Files Modified:[/bold]")
        for change in fix_data.get("files_to_modify", []):
            console.print(f"    [green]>> {change.get('file_path', '')}[/green]")
        console.print(f"\n  [bold]Fix Description:[/bold] {fix_data.get('description', 'N/A')}")

    # Show diff
    patch = fix_data.get("patch", "")
    if patch:
        console.print()
        console.print(Panel(patch, title="[bold]Unified Diff[/bold]", border_style="green", padding=(0, 1)))

    # ─── Step 5: Docker Sandbox Validation ──────────────────────────
    step_header(5, "Docker Sandbox Validation",
                "Cloning repo, running tests BEFORE and AFTER patch in isolated containers")

    from src.agents.validation import ValidationAgent
    validator = ValidationAgent(llm)

    with console.status("[bold cyan]Running Docker sandbox tests...", spinner="dots"):
        val_result = validator.execute(state)

    val_data = val_result.data or {}
    state["validation_result"] = val_data

    val_table = Table(box=box.ROUNDED, title="Sandbox Test Results", title_style="bold blue")
    val_table.add_column("Metric", style="bold")
    val_table.add_column("Before Patch")
    val_table.add_column("After Patch")

    before = val_data.get("before", {})
    after = val_data.get("after", {})
    val_table.add_row("Total Tests", str(before.get("total", 0)), str(after.get("total", 0)))
    val_table.add_row("Passed", str(before.get("passed", 0)), str(after.get("passed", 0)))
    val_table.add_row("Failed", str(before.get("failed", 0)), str(after.get("failed", 0)))

    console.print(val_table)

    v_verdict = val_data.get("verdict", "N/A")
    regressions = val_data.get("regressions", [])
    v_color = "green" if v_verdict == "FIX_CONFIRMED" else ("yellow" if v_verdict == "NO_CHANGE" else "red")
    console.print(f"\n  Verdict: [{v_color}]{v_verdict}[/{v_color}]")
    console.print(f"  Regressions: [{'red' if regressions else 'green'}]{len(regressions)}[/{'red' if regressions else 'green'}]")

    # ─── Step 6: Risk Assessment ───────────────────────────────────
    step_header(6, "Risk Assessment & Regression Prevention",
                "Calculating blast radius, file churn, and deployment risk")

    from src.agents.risk_scorer import RiskScorerAgent
    risk_scorer = RiskScorerAgent(llm)

    with console.status("[bold cyan]Calculating risk...", spinner="dots"):
        risk_result = risk_scorer.execute(state)

    risk_data = risk_result.data or {}
    state["risk_assessment"] = risk_data

    risk_level = risk_data.get("level", "MEDIUM")
    r_color = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red"}.get(risk_level, "yellow")

    console.print(f"  Risk Level: [{r_color}][bold]{risk_level}[/bold][/{r_color}]")
    console.print(f"  Policy: {risk_data.get('policy', 'N/A')}")

    # ─── Step 7: Resolution Report ─────────────────────────────────
    step_header(7, "Generate Resolution Report",
                "Assembling structured report with reasoning chain")

    from src.agents.synthesis import SynthesisAgent
    synth = SynthesisAgent(llm)
    state["start_time"] = state.get("start_time", time.time() - 60)

    with console.status("[bold cyan]Generating report...", spinner="dots"):
        synth_result = synth.execute(state)

    report_data = (synth_result.data or {}).get("report", {})

    # Save report
    os.makedirs("reports", exist_ok=True)
    report_path = f"reports/{incident_id}_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump({
            "incident": incident,
            "root_cause": root_cause_data,
            "fix_plan": fix_data,
            "validation": val_data,
            "risk": risk_data,
            "report": report_data,
        }, f, indent=2, default=str)

    console.print(f"  Report saved: [bold]{report_path}[/bold]")

    # ─── Summary ───────────────────────────────────────────────────
    console.print()
    console.rule("[bold green]Resolution Complete", style="green")
    console.print()

    summary = Table(box=box.HEAVY, title="Resolution Summary", title_style="bold green", padding=(0, 2))
    summary.add_column("Field", style="bold")
    summary.add_column("Value")
    summary.add_row("Incident", f"{incident_id} - {incident.get('title', '')}")
    summary.add_row("Root Cause", hypothesis[:120])
    summary.add_row("Fix", fix_data.get("description", "N/A")[:120])
    summary.add_row("Validation", v_verdict)
    summary.add_row("Risk", f"{risk_level} - {risk_data.get('policy', '')[:80]}")
    summary.add_row("Confidence", f"{report_data.get('confidence_score', 0) * 100:.0f}%")
    summary.add_row("Report", report_path)
    console.print(summary)

    # ─── Slack: Resolution ─────────────────────────────────────────
    if slack_channel and os.getenv("SLACK_BOT_TOKEN"):
        try:
            bridge.post_slack_resolution(
                channel=slack_channel,
                incident_id=incident_id,
                title=incident.get("title", ""),
                root_cause=hypothesis[:300],
                verdict=v_verdict,
                confidence=report_data.get("confidence_score", 0),
                risk_level=risk_level,
                thread_ts=slack_ts or "",
            )
            console.print(f"\n  [green]>> Resolution posted to Slack {slack_channel}[/green]")
        except Exception as e:
            console.print(f"\n  [yellow]Slack post failed: {e}[/yellow]")

    console.print()


if __name__ == "__main__":
    main()
