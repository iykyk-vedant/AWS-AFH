"""
Amaze on Work — Amazon Bedrock AgentCore Deployment Adapter.

Hosts the Amaze on Work multi-agent pipeline and Strands Agents SDK integration
as a scalable, serverless agent runtime on Amazon Bedrock AgentCore.

Usage:
    # Run locally in AgentCore emulator
    python agentcore_app.py

    # Deploy via AgentCore CLI
    agentcore deploy
"""

import os
import sys
import json
from typing import Dict, Any

# Ensure project root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ["PYTHONIOENCODING"] = "utf-8"
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ─── Bedrock AgentCore SDK Import with Graceful Fallback ───────────────────────
try:
    from bedrock_agentcore.runtime import BedrockAgentCoreApp
    HAS_AGENTCORE = True
except ImportError:
    HAS_AGENTCORE = False
    class BedrockAgentCoreApp:
        def __init__(self):
            self._entrypoint_fn = None

        def entrypoint(self, fn):
            self._entrypoint_fn = fn
            return fn

        def run(self):
            print("[Bedrock AgentCore] Running local runtime listener emulator on port 8080...")
            sample_payload = {"incident_id": "INC-001", "repo": "Rezinix-AI/shopstack-platform"}
            if self._entrypoint_fn:
                print(f"[Bedrock AgentCore] Testing invocation with: {sample_payload}")
                res = self._entrypoint_fn(sample_payload, {"request_id": "req-agentcore-local-001"})
                print(f"[Bedrock AgentCore] Invocation Result: {res.get('status', 'OK')}")

app = BedrockAgentCoreApp()

# ─── Lazy Pipeline Loader ─────────────────────────────────────────────────────
_supervisor = None

def get_supervisor():
    global _supervisor
    if _supervisor is None:
        from src.agents.supervisor import SupervisorAgent
        _supervisor = SupervisorAgent()
    return _supervisor


# ─── AgentCore Invocation Handler ─────────────────────────────────────────────

@app.entrypoint
def agent_invocation(payload: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Standard Amazon Bedrock AgentCore invocation entrypoint.
    Receives incident payload or natural language prompts from AgentCore Gateways,
    executes the Amaze on Work multi-agent resolution loop, and returns structured outputs.
    """
    engine = get_supervisor()
    
    # 1. Direct incident dict provided
    incident = payload.get("incident")
    
    # 2. Or resolve by incident_id
    owner = os.getenv("GITHUB_OWNER", "iykyk-vedant")
    repo_name = os.getenv("GITHUB_REPO", "AFH-DEMO")
    if not incident and "incident_id" in payload:
        incident_id = payload["incident_id"]
        repo = payload.get("repo", f"{owner}/{repo_name}")
        if "/" in repo:
            owner, repo_name = repo.split("/")
        else:
            repo_name = repo
            
        parser = engine.agents["incident_parser"]
        try:
            incident = parser.parse_from_github(owner, repo_name, incident_id)
        except Exception:
            from pathlib import Path
            rep_file = Path(f"reports/{incident_id}/report.json")
            if rep_file.exists():
                incident = json.loads(rep_file.read_text(encoding="utf-8"))
        
    if not incident:
        return {
            "status": "error",
            "message": "Missing incident payload or incident_id. Provide {'incident_id': 'INC-001'}."
        }

    # Execute Amaze on Work multi-agent workflow
    repo_url = f"https://github.com/{owner}/{repo_name}"
    result = engine.resolve_incident(incident, repo_url=repo_url)
    
    return {
        "status": "success",
        "incident_id": incident.get("id") or incident.get("incident_id"),
        "resolution_verdict": result.get("validation_result", {}).get("verdict", "COMPLETED"),
        "risk_level": result.get("risk_assessment", {}).get("risk_level", "LOW"),
        "formatted_markdown": result.get("formatted_markdown", ""),
        "report": result.get("resolution_report", {})
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Amaze on Work — Amazon Bedrock AgentCore Deployment Entrypoint"
    )
    default_repo = os.getenv("GITHUB_REPO_FULL") or f"{os.getenv('GITHUB_OWNER', 'iykyk-vedant')}/{os.getenv('GITHUB_REPO', 'AFH-DEMO')}"
    parser.add_argument("--incident", default=None, help="Incident ID to resolve (e.g., INC-001)")
    parser.add_argument("--repo", default=default_repo, help="Target repository")
    parser.add_argument("--port", type=int, default=8090, help="Port to run AgentCore runtime (default: 8090)")
    args, unknown = parser.parse_known_args()

    if args.incident:
        payload = {"incident_id": args.incident, "repo": args.repo}
        print(f"[Bedrock AgentCore] Invoking agent for incident: {args.incident}...")
        res = agent_invocation(payload, {"source": "cli"})
        print(res.get("formatted_markdown", json.dumps(res, indent=2)))
    else:
        app.run(port=args.port)

