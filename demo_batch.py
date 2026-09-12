"""
Amaze on Work -- Batch Demo Runner

Resolves ALL incidents from the shopstack-platform repo sequentially,
posts results to Slack, and generates a summary table at the end.

Usage:
    python demo_batch.py --slack-channel C0AL8NG5J79
"""

import sys
import os
import json
import time

sys.path.insert(0, ".")
os.environ["PYTHONIOENCODING"] = "utf-8"

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.live import Live

console = Console()


def resolve_one(incident_id, owner, repo, llm, bridge, gh):
    """Resolve a single incident and return summary dict."""
    from src.agents.incident_parser import IncidentParserAgent
    from src.agents.codebase_analyst import CodebaseAnalystAgent
    from src.agents.critic import CriticAgent
    from src.agents.fix_writer import FixWriterAgent
    from src.agents.validation import ValidationAgent
    from src.agents.risk_scorer import RiskScorerAgent
    from src.agents.synthesis import SynthesisAgent
    from src.agents.state import create_initial_state

    start = time.time()
    result = {
        "id": incident_id, "title": "", "root_cause": "", "fix": "",
        "verdict": "ERROR", "risk": "N/A", "confidence": 0.0,
        "time": 0.0, "status": "FAIL",
    }

    try:
        # Step 1: Parse
        parser_agent = IncidentParserAgent(llm, gh)
        incident = parser_agent.parse_from_github(owner, repo, incident_id)
        result["title"] = incident.get("title", "")[:60]

        state = create_initial_state(incident, f"https://github.com/{owner}/{repo}")
        state["repo_owner"] = owner
        state["repo_name"] = repo

        # Step 2: Analyze
        analyst = CodebaseAnalystAgent(llm, gh)
        analysis = analyst.execute(state)
        root_cause_data = analysis.data or {}
        state["root_cause"] = root_cause_data
        result["root_cause"] = root_cause_data.get("hypothesis", "N/A")[:80]

        # Step 3: Critic
        critic = CriticAgent(llm)
        critic.execute(state)

        # Step 4: Fix
        fix_writer = FixWriterAgent(llm, gh)
        fix_result = fix_writer.execute(state)
        fix_data = fix_result.data or {}
        state["fix_plan"] = fix_data
        result["fix"] = fix_data.get("description", "N/A")[:60]

        # Step 5: Validation
        validator = ValidationAgent(llm)
        val_result = validator.execute(state)
        val_data = val_result.data or {}
        state["validation_result"] = val_data
        result["verdict"] = val_data.get("verdict", "N/A")

        # Step 6: Risk
        risk_scorer = RiskScorerAgent(llm)
        risk_result = risk_scorer.execute(state)
        risk_data = risk_result.data or {}
        state["risk_assessment"] = risk_data
        result["risk"] = risk_data.get("level", "N/A")

        # Step 7: Synthesis
        synth = SynthesisAgent(llm)
        state["start_time"] = start
        synth_result = synth.execute(state)
        report_data = (synth_result.data or {}).get("report", {})
        result["confidence"] = report_data.get("confidence_score", 0)

        # Save report
        os.makedirs("reports", exist_ok=True)
        with open(f"reports/{incident_id}_report.json", "w", encoding="utf-8") as f:
            json.dump({
                "incident": incident, "root_cause": root_cause_data,
                "fix_plan": fix_data, "validation": val_data,
                "risk": risk_data, "report": report_data,
            }, f, indent=2, default=str)

        result["status"] = "OK"

    except Exception as e:
        result["root_cause"] = f"Error: {str(e)[:60]}"

    result["time"] = time.time() - start
    return result


def main():
    import argparse
    default_repo = os.getenv("GITHUB_REPO_FULL") or f"{os.getenv('GITHUB_OWNER', 'iykyk-vedant')}/{os.getenv('GITHUB_REPO', 'AFH-DEMO')}"
    parser = argparse.ArgumentParser(description="Amaze on Work Batch Demo")
    parser.add_argument("--repo", default=default_repo)
    parser.add_argument("--slack-channel", default="", help="Slack channel ID")
    args = parser.parse_args()

    owner, repo_name = args.repo.split("/")
    slack_channel = args.slack_channel

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]Amaze on Work -- Batch Resolution[/bold cyan]\n"
        f"[dim]Resolving all incidents from {owner}/{repo_name}[/dim]",
        border_style="cyan", padding=(1, 4),
    ))
    console.print()

    from src.llm.client_factory import get_llm_client
    from src.mcp.client_bridge import get_mcp_bridge
    from src.mcp.github_tools import get_github_tools

    llm = get_llm_client()
    bridge = get_mcp_bridge()
    gh = get_github_tools()

    # Get all incidents
    incidents = bridge.list_incidents(owner, repo_name)
    console.print(f"  Found [bold]{len(incidents)}[/bold] incidents to resolve\n")

    # Slack: batch started
    slack_ts = None
    if slack_channel and os.getenv("SLACK_BOT_TOKEN"):
        try:
            from slack_sdk import WebClient
            sc = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
            try:
                sc.conversations_join(channel=slack_channel)
            except Exception:
                pass
            r = sc.chat_postMessage(
                channel=slack_channel,
                text=f"[Amaze on Work] Batch resolution started -- {len(incidents)} incidents queued"
            )
            slack_ts = r.get("ts", "")
        except Exception as e:
            console.print(f"  [yellow]Slack error: {e}[/yellow]")

    # Resolve each incident
    results = []
    total_start = time.time()

    for i, inc_id in enumerate(incidents, 1):
        console.print(f"  [{i}/{len(incidents)}] Resolving [bold]{inc_id}[/bold]...", end=" ")
        r = resolve_one(inc_id, owner, repo_name, llm, bridge, gh)
        results.append(r)

        status_color = "green" if r["status"] == "OK" else "red"
        console.print(
            f"[{status_color}]{r['status']}[/{status_color}] "
            f"({r['time']:.1f}s) -- {r['verdict']} -- {r['root_cause'][:50]}"
        )

    total_time = time.time() - total_start

    # ─── Summary Table ─────────────────────────────────────────────
    console.print()
    console.rule("[bold green]Batch Resolution Complete", style="green")
    console.print()

    summary = Table(
        box=box.ROUNDED, title="All Incident Resolutions",
        title_style="bold green", padding=(0, 1),
    )
    summary.add_column("#", style="dim", width=4)
    summary.add_column("Incident", style="bold", width=8)
    summary.add_column("Title", width=30)
    summary.add_column("Root Cause", width=40)
    summary.add_column("Verdict", width=12)
    summary.add_column("Risk", width=6)
    summary.add_column("Conf", width=5)
    summary.add_column("Time", width=6)

    ok_count = 0
    for i, r in enumerate(results, 1):
        if r["status"] == "OK":
            ok_count += 1
        v_color = {
            "FIX_CONFIRMED": "green", "NO_CHANGE": "yellow",
            "REGRESSION_DETECTED": "red",
        }.get(r["verdict"], "dim")
        r_color = {"LOW": "green", "MEDIUM": "yellow", "HIGH": "red"}.get(r["risk"], "dim")

        summary.add_row(
            str(i), r["id"], r["title"],
            r["root_cause"][:40],
            f"[{v_color}]{r['verdict']}[/{v_color}]",
            f"[{r_color}]{r['risk']}[/{r_color}]",
            f"{r['confidence']*100:.0f}%",
            f"{r['time']:.0f}s",
        )

    console.print(summary)
    console.print()
    console.print(f"  Resolved: [bold green]{ok_count}/{len(results)}[/bold green] incidents")
    console.print(f"  Total time: [bold]{total_time:.0f}s[/bold] ({total_time/60:.1f} min)")
    console.print(f"  Reports saved to: [bold]reports/[/bold]")
    console.print()

    # Slack: batch complete
    if slack_channel and os.getenv("SLACK_BOT_TOKEN"):
        try:
            lines = [f"*Batch Resolution Complete* -- {ok_count}/{len(results)} incidents resolved in {total_time:.0f}s\n"]
            for r in results:
                emoji = {"FIX_CONFIRMED": "[FIXED]", "NO_CHANGE": "[NO_CHG]"}.get(r["verdict"], "[" + r["verdict"][:6] + "]")
                lines.append(f"{emoji} *{r['id']}*: {r['root_cause'][:60]}")

            sc = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
            sc.chat_postMessage(
                channel=slack_channel,
                text="\n".join(lines),
                thread_ts=slack_ts or "",
            )
            console.print(f"  [green]>> Batch summary posted to Slack[/green]\n")
        except Exception as e:
            console.print(f"  [yellow]Slack post failed: {e}[/yellow]\n")


if __name__ == "__main__":
    main()
