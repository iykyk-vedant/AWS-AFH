"""
Amaze on Work — FastAPI Application Entry Point.

Provides REST API for incident resolution, webhook handlers,
and health check endpoints.
"""

import os
import json
import logging
import sys
import argparse
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)-25s | %(levelname)-8s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("Amaze on Work")

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.responses import JSONResponse, HTMLResponse
    from pydantic import BaseModel
    from pathlib import Path
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    logger.warning("FastAPI not installed. API server unavailable.")

from src.agents.supervisor import SupervisorAgent
from src.agents.incident_parser import IncidentParserAgent
from src.mcp.github_tools import get_github_tools
from src.sandbox.docker_runner import DockerSandbox
from src.llm.client_factory import get_llm_client
from src.config import settings

# Register webhook routers
try:
    from src.api.routes.slack_webhook import router as slack_router
    from src.api.routes.jira_webhook import router as jira_router
    from src.api.routes.github_webhook import router as github_router
    from src.api.routes.fix_selection_webhook import router as fix_selection_router
    _WEBHOOK_ROUTERS = [slack_router, jira_router, github_router, fix_selection_router]
except ImportError as e:
    _WEBHOOK_ROUTERS = []
    logger.warning(f"Webhook routers not loaded: {e}")


# ─── Pydantic Models ──────────────────────────────────────────────

if HAS_FASTAPI:
    class IncidentRequest(BaseModel):
        """Request body for incident resolution."""
        incident: dict | None = None
        incident_id: str | None = None
        repo_url: str = "https://github.com/Rezinix-AI/shopstack-platform"
        repo_owner: str = "Rezinix-AI"
        repo_name: str = "shopstack-platform"

    class ResolveAllRequest(BaseModel):
        """Request body for resolving all incidents."""
        repo_url: str = "https://github.com/Rezinix-AI/shopstack-platform"
        repo_owner: str = "Rezinix-AI"
        repo_name: str = "shopstack-platform"

    class CreateIssueRequest(BaseModel):
        """Request body for creating a new issue from the dashboard."""
        title: str
        description: str
        severity: str = "P2 - High"
        environment: str = "Production"

    # ─── FastAPI App ──────────────────────────────────────────────

    app = FastAPI(
        title="Amaze on Work",
        description="Autonomous Incident-to-Fix Engineering Agent",
        version="1.0.0",
    )

    # Register webhook routers
    for _router in _WEBHOOK_ROUTERS:
        if _router is not None:
            app.include_router(_router)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard():
        """Serves the Amaze on Work SRE Mission Control Dashboard."""
        static_file = Path("src/api/static/index.html")
        if static_file.exists():
            return HTMLResponse(content=static_file.read_text(encoding="utf-8"))
        return HTMLResponse(content="<h1>Amaze on Work Mission Control</h1>")

    @app.get("/graph", response_class=HTMLResponse)
    async def knowledge_graph():
        """Serves the Amaze on Work Code Property Graph (GraphRAG Neo4j Explorer)."""
        graph_file = Path("src/api/static/graph.html")
        if graph_file.exists():
            return HTMLResponse(content=graph_file.read_text(encoding="utf-8"))
        return HTMLResponse(content="<h1>Graph visualization not found</h1>", status_code=404)

    @app.get("/api/graph")
    async def get_graph_data():
        """Returns the indexed Code Property Graph nodes and edges JSON."""
        data_file = Path("src/api/static/graph_data.json")
        if data_file.exists():
            return json.loads(data_file.read_text(encoding="utf-8"))
        return {"nodes": [], "edges": []}

    @app.get("/data/graph_export.cypher")
    async def get_cypher_export():
        """Returns the exported Neo4j Cypher statements script."""
        cypher_file = Path("data/graph_export.cypher")
        if cypher_file.exists():
            return HTMLResponse(content=cypher_file.read_text(encoding="utf-8"), media_type="text/plain")
        return HTMLResponse(content="// Cypher export not generated", status_code=404)


    @app.get("/api/incidents")
    async def list_github_issues():
        """Returns real issues dynamically fetched from the GitHub repository iykyk-vedant/AFH-DEMO
        with live cross-referenced Pull Requests raised by the autonomous agent system."""
        import httpx
        import re
        owner = os.getenv("GITHUB_OWNER", "iykyk-vedant")
        repo = os.getenv("GITHUB_REPO", "AFH-DEMO")
        token = os.getenv("GITHUB_TOKEN", "")

        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"

        real_issues = []
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    f"https://api.github.com/repos/{owner}/{repo}/issues",
                    params={"state": "all", "per_page": 30},
                    headers=headers,
                )

                # Fetch pull requests to match real GitHub PRs raised by our system
                pr_map = {}
                try:
                    r_pulls = await client.get(
                        f"https://api.github.com/repos/{owner}/{repo}/pulls",
                        params={"state": "all", "per_page": 30},
                        headers=headers,
                    )
                    if r_pulls.status_code == 200:
                        for pr_item in r_pulls.json():
                            pr_head_ref = (pr_item.get("head") or {}).get("ref") or ""
                            pr_body_text = pr_item.get("body") or ""
                            pr_title_text = pr_item.get("title") or ""
                            pr_num = pr_item.get("number")
                            pr_html = pr_item.get("html_url")
                            pr_state_val = pr_item.get("state")
                            
                            # Match by branch (e.g. amaze-on-work/inc-003 -> 3), #close #3, Closes #3, or title
                            matches = re.findall(
                                r'(?:amaze-on-work/(?:inc-|issue-)?|#close\s*#?|closes\s*#?|fixes\s*#?|resolves\s*#?|inc-0*|issue-#?0*)(\d+)',
                                f"{pr_head_ref} {pr_title_text} {pr_body_text}",
                                re.IGNORECASE
                            )
                            for m in matches:
                                try:
                                    inum = int(m)
                                    if inum not in pr_map:
                                        pr_map[inum] = {
                                            "pr_url": pr_html,
                                            "pr_number": pr_num,
                                            "pr_state": pr_state_val,
                                            "pr_title": pr_title_text,
                                        }
                                except ValueError:
                                    pass
                except Exception as pe:
                    logger.debug(f"Pull requests fetch: {pe}")

                if r.status_code == 200:
                    raw_items = r.json()
                    for item in raw_items:
                        # Skip Pull Requests (GitHub issues API includes PRs)
                        if "pull_request" in item:
                            continue
                        issue_num = item.get("number")
                        title = item.get("title", "")
                        body = item.get("body") or ""
                        state = item.get("state", "open")
                        labels = [l.get("name", "") for l in item.get("labels", [])]

                        pr_url = None
                        fix_diff = None
                        explanation = None

                        # Check if matched to a GitHub PR raised by our system
                        if issue_num in pr_map:
                            pr_url = pr_map[issue_num]["pr_url"]

                        # Check if tracked in event_stream (real-time in-flight triage)
                        try:
                            from src.api.event_stream import get_current_event_state
                            es = get_current_event_state()
                            for ev in es.get("recent", []):
                                if ev.get("issue_number") == issue_num:
                                    if ev.get("pr_url"):
                                        pr_url = ev.get("pr_url")
                                    if ev.get("diff"):
                                        fix_diff = ev.get("diff")
                                    break
                        except Exception:
                            pass

                        real_issues.append({
                            "id": f"ISSUE-#{issue_num}",
                            "issue_number": issue_num,
                            "title": title,
                            "state": state,
                            "severity": "P1 - Critical" if "critical" in str(labels).lower() else "P2 - High",
                            "service": f"{owner}/{repo}",
                            "description": body[:250] if body else "No issue description provided.",
                            "tags": labels or ["production-bug"],
                            "html_url": item.get("html_url"),
                            "pr_url": pr_url,
                            "fix_diff": fix_diff,
                            "fix_explanation": explanation or "Autonomous fix generated by Amaze on Work.",
                        })
        except Exception as e:
            logger.error(f"Failed to fetch live GitHub issues: {e}")

        return real_issues

    @app.get("/api/events")
    async def get_realtime_events():
        """Returns the real-time active triage event and recent history for the dashboard."""
        try:
            from src.api.event_stream import get_current_event_state
            return get_current_event_state()
        except Exception as e:
            return {"active": None, "recent": []}

    @app.get("/api/health")
    async def health_check():
        """Health check endpoint."""
        sandbox = DockerSandbox()
        docker_health = sandbox.health_check()

        return {
            "status": "healthy",
            "version": "1.0.0",
            "components": {
                "llm": _check_llm(),
                "github": _check_github(),
                "docker": docker_health,
            },
        }

    @app.post("/api/agentcore/invoke")
    async def agentcore_invoke(request: Request):
        """Amazon Bedrock AgentCore Serverless Invocation Gateway.
        
        Conforms to standard Bedrock AgentCore payload specification.
        Receives incident parameters, dispatches via AgentCore runtime adapter,
        and returns structured resolution report.
        """
        try:
            from agentcore_app import agent_invocation
            body = await request.json()
            result = agent_invocation(body, {"source": "bedrock_agentcore_gateway"})
            return JSONResponse(content=result)
        except Exception as e:
            logger.error(f"[AgentCore] Invocation failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/incidents/resolve")
    async def resolve_incident(request: IncidentRequest):
        """Resolve a single incident."""
        try:
            supervisor = SupervisorAgent()

            if request.incident:
                incident = request.incident
            elif request.incident_id:
                parser = IncidentParserAgent(
                    get_llm_client(), get_github_tools()
                )
                incident = parser.parse_from_github(
                    request.repo_owner, request.repo_name, request.incident_id
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail="Provide either 'incident' (JSON) or 'incident_id'",
                )

            result = supervisor.resolve_incident(incident, request.repo_url)
            return JSONResponse(content=result)

        except Exception as e:
            logger.error(f"Resolution failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/incidents/resolve-all")
    async def resolve_all(request: ResolveAllRequest):
        """Resolve all incidents in a repository."""
        try:
            supervisor = SupervisorAgent()
            results = supervisor.resolve_all_incidents(
                request.repo_owner,
                request.repo_name,
                request.repo_url,
            )
            return JSONResponse(content={"results": results, "total": len(results)})
        except Exception as e:
            logger.error(f"Resolve-all failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/issues/create")
    async def create_github_issue(payload: CreateIssueRequest):
        """Create a new issue on GitHub and kick off autonomous multi-agent triage."""
        import httpx
        owner = os.getenv("GITHUB_OWNER", "iykyk-vedant")
        repo = os.getenv("GITHUB_REPO", "AFH-DEMO")
        token = os.getenv("GITHUB_TOKEN", "")

        issue_number = None
        html_url = None
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                body_text = (
                    f"{payload.description}\n\n"
                    f"**Environment:** {payload.environment}\n"
                    f"**Reported Severity:** {payload.severity}\n\n"
                    f"*Autonomous self-healing dispatched via Amaze on Work.*"
                )
                r = await client.post(
                    f"https://api.github.com/repos/{owner}/{repo}/issues",
                    headers=headers,
                    json={
                        "title": payload.title,
                        "body": body_text,
                        "labels": ["Amaze on Work-ai", payload.severity.lower().split(" ")[0]],
                    },
                )
                if r.status_code == 201:
                    data = r.json()
                    issue_number = data.get("number")
                    html_url = data.get("html_url")
                else:
                    logger.warning(f"GitHub API returned {r.status_code}: {r.text}")
        except Exception as e:
            logger.error(f"Failed to create GitHub issue via API: {e}")

        if not issue_number:
            import time
            issue_number = int(time.time()) % 10000 + 100
            html_url = f"https://github.com/{owner}/{repo}/issues/{issue_number}"

        # Register event in event_stream to start live triage visualization immediately
        try:
            from src.api.event_stream import record_event_start
            record_event_start(
                event_id=f"evt-{issue_number}",
                issue_number=issue_number,
                title=payload.title,
                repo=f"{owner}/{repo}",
                author="dashboard-user",
            )
        except Exception as e:
            logger.error(f"Failed to record event in stream: {e}")

        # Launch autonomous triage immediately
        try:
            from src.agents.trigger_agent import TriggerAgent
            agent = TriggerAgent()
            agent.handle_github_issue(
                issue_number=issue_number,
                title=payload.title,
                body=payload.description,
                labels=["Amaze on Work-ai", payload.severity.lower().split(" ")[0]],
                repo_owner=owner,
                repo_name=repo,
            )
            logger.info(f"[MainAPI] Triage dispatched directly for Issue #{issue_number}")
        except Exception as te:
            logger.error(f"[MainAPI] Failed to dispatch TriggerAgent: {te}")

        return JSONResponse(content={
            "success": True,
            "issue_number": issue_number,
            "html_url": html_url,
            "title": payload.title,
            "message": "Incident reported. Autonomous triage initialized.",
        })

    @app.post("/api/issues/{issue_number}/triage")
    async def trigger_issue_triage(issue_number: int):
        """Dispatches autonomous multi-agent self-healing triage for a GitHub issue."""
        import httpx
        owner = os.getenv("GITHUB_OWNER", "iykyk-vedant")
        repo = os.getenv("GITHUB_REPO", "AFH-DEMO")
        token = os.getenv("GITHUB_TOKEN", "")
        headers = {"Accept": "application/vnd.github.v3+json"}
        if token:
            headers["Authorization"] = f"token {token}"
        
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}",
                headers=headers,
            )
            if r.status_code != 200:
                raise HTTPException(status_code=404, detail=f"GitHub Issue #{issue_number} not found")
            issue_data = r.json()
            title = issue_data.get("title", "")
            body = issue_data.get("body", "") or ""
            labels = [l.get("name", "") for l in issue_data.get("labels", [])]

        from src.agents.trigger_agent import TriggerAgent
        agent = TriggerAgent()
        res = agent.handle_github_issue(
            issue_number=issue_number,
            title=title,
            body=body,
            labels=labels,
            repo_owner=owner,
            repo_name=repo,
        )
        return JSONResponse({
            "success": True,
            "status": "queued",
            "issue_number": issue_number,
            "incident_id": res.get("incident_id"),
            "message": f"Autonomous triage started for Issue #{issue_number}",
        })

    _POLLED_ISSUES = set()

    async def _auto_poll_issues_worker():
        """Continuously checks for open GitHub issues that lack a PR and auto-triages them."""
        import asyncio
        import httpx
        import re
        await asyncio.sleep(6)
        while True:
            try:
                owner = os.getenv("GITHUB_OWNER", "iykyk-vedant")
                repo = os.getenv("GITHUB_REPO", "AFH-DEMO")
                token = os.getenv("GITHUB_TOKEN", "")
                headers = {"Accept": "application/vnd.github.v3+json"}
                if token:
                    headers["Authorization"] = f"token {token}"
                
                async with httpx.AsyncClient(timeout=12) as client:
                    # Check active PRs
                    r_pulls = await client.get(
                        f"https://api.github.com/repos/{owner}/{repo}/pulls",
                        params={"state": "all", "per_page": 40},
                        headers=headers,
                    )
                    addressed = set()
                    if r_pulls.status_code == 200:
                        for p in r_pulls.json():
                            ref = (p.get("head") or {}).get("ref") or ""
                            txt = f"{ref} {p.get('title', '')} {p.get('body', '')}"
                            nums = re.findall(r'(?:#close\s*#?|closes\s*#?|fixes\s*#?|inc-0*|issue-#?0*)(\d+)', txt, re.I)
                            for n in nums:
                                try:
                                    addressed.add(int(n))
                                except ValueError:
                                    pass

                    # Fetch open issues
                    r_issues = await client.get(
                        f"https://api.github.com/repos/{owner}/{repo}/issues",
                        params={"state": "open", "per_page": 20},
                        headers=headers,
                    )
                    if r_issues.status_code == 200:
                        for iss in r_issues.json():
                            if "pull_request" in iss:
                                continue
                            inum = iss.get("number")
                            if inum and inum not in addressed and inum not in _POLLED_ISSUES:
                                _POLLED_ISSUES.add(inum)
                                logger.info(f"[AutoPoller] Auto-triaging unaddressed GitHub Issue #{inum}: {iss.get('title')}")
                                from src.agents.trigger_agent import TriggerAgent
                                agent = TriggerAgent()
                                labels = [l.get("name", "") for l in iss.get("labels", [])]
                                agent.handle_github_issue(
                                    issue_number=inum,
                                    title=iss.get("title", ""),
                                    body=iss.get("body", "") or "",
                                    labels=labels,
                                    repo_owner=owner,
                                    repo_name=repo,
                                )
            except Exception as pe:
                logger.debug(f"[AutoPoller] Loop notice: {pe}")
            await asyncio.sleep(20)

    @app.on_event("startup")
    async def startup_event():
        import asyncio
        asyncio.create_task(_auto_poll_issues_worker())


def _check_llm() -> dict:
    try:
        client = get_llm_client()
        return {
            "available": client.is_available(),
            "model": client.get_model_name(),
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


def _check_github() -> dict:
    try:
        gh = get_github_tools()
        # Simple check — try to get rate limit
        import httpx
        resp = httpx.get(
            "https://api.github.com/rate_limit",
            headers=gh._headers(),
        )
        data = resp.json()
        return {
            "available": resp.status_code == 200,
            "rate_remaining": data.get("rate", {}).get("remaining", 0),
        }
    except Exception as e:
        return {"available": False, "error": str(e)}


# ─── CLI Entry Point ──────────────────────────────────────────────

def main():
    """CLI entry point for Amaze on Work."""
    parser = argparse.ArgumentParser(description="Amaze on Work — Autonomous Incident Resolution")
    subparsers = parser.add_subparsers(dest="command")

    # resolve command
    resolve_parser = subparsers.add_parser("resolve", help="Resolve a single incident")
    resolve_parser.add_argument("--incident-id", required=True, help="Incident ID (e.g., INC-001)")
    resolve_parser.add_argument("--repo", default="Rezinix-AI/shopstack-platform",
                                 help="Repository (owner/name)")
    resolve_parser.add_argument("--output", default=None, help="Output file for report")

    # resolve-all command
    resolve_all_parser = subparsers.add_parser("resolve-all", help="Resolve all incidents")
    resolve_all_parser.add_argument("--repo", default="Rezinix-AI/shopstack-platform",
                                     help="Repository (owner/name)")
    resolve_all_parser.add_argument("--output", default=None, help="Output directory for reports")

    # server command
    server_parser = subparsers.add_parser("server", help="Start API server")
    server_parser.add_argument("--port", type=int, default=settings.app.port)
    server_parser.add_argument("--host", default="0.0.0.0")

    # health command
    subparsers.add_parser("health", help="Check system health")

    args = parser.parse_args()

    if args.command == "resolve":
        owner, repo = args.repo.split("/")
        supervisor = SupervisorAgent()
        parser_agent = supervisor.agents["incident_parser"]
        incident = parser_agent.parse_from_github(owner, repo, args.incident_id)
        result = supervisor.resolve_incident(incident)

        # Print formatted report
        if result.get("formatted_markdown"):
            print(result["formatted_markdown"])

        if args.output:
            with open(args.output, "w") as f:
                json.dump(result, f, indent=2, default=str)
            print(f"\nReport saved to {args.output}")

    elif args.command == "resolve-all":
        owner, repo = args.repo.split("/")
        supervisor = SupervisorAgent()
        results = supervisor.resolve_all_incidents(owner, repo)

        for r in results:
            print(f"\n{'='*60}")
            print(f"Incident: {r.get('incident_id', 'unknown')}")
            print(f"Error: {r.get('error', 'None')}")
            report = r.get("resolution_report", {})
            if report:
                print(f"Root Cause: {report.get('root_cause', 'N/A')}")
                print(f"Confidence: {report.get('confidence_score', 0):.0%}")

        if args.output:
            Path(args.output).mkdir(parents=True, exist_ok=True)
            for r in results:
                iid = r.get("incident_id", "unknown")
                filepath = Path(args.output) / f"{iid}_report.json"
                with open(filepath, "w") as f:
                    json.dump(r, f, indent=2, default=str)
            print(f"\nReports saved to {args.output}/")

    elif args.command == "server":
        if not HAS_FASTAPI:
            print("FastAPI not installed. Run: pip install fastapi uvicorn")
            sys.exit(1)
        import uvicorn
        print(f"Starting Amaze on Work server on {args.host}:{args.port}")
        uvicorn.run("src.api.main:app", host=args.host, port=args.port, reload=True)

    elif args.command == "health":
        print("Checking Amaze on Work health...\n")
        llm = _check_llm()
        gh = _check_github()
        sandbox = DockerSandbox()
        docker = sandbox.health_check()

        print(f"LLM:    {'[OK]' if llm.get('available') else '[FAIL]'} ({llm.get('model', 'N/A')})")
        print(f"GitHub: {'[OK]' if gh.get('available') else '[FAIL]'} (rate: {gh.get('rate_remaining', 'N/A')})")
        print(f"Docker: {'[OK]' if docker.get('docker_available') else '[FAIL]'}")
        print(f"  Node image:   {'[OK]' if docker.get('node_image') else '[FAIL]'}")
        print(f"  Python image: {'[OK]' if docker.get('python_image') else '[FAIL]'}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
