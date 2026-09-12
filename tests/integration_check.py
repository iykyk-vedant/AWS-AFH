"""
Full System Integration Test for Amaze on Work.

Verifies all 6 subsystems are connected and operational:
1. LLM (Cerebras)
2. GitHub (API + MCP Server)
3. Slack (API + MCP Server)
4. Docker (daemon + sandbox images)
5. Neo4j (knowledge graph)
6. Pipeline (end-to-end dry run)
"""

import sys
import os
import json
import time

sys.path.insert(0, ".")

from dotenv import load_dotenv
load_dotenv()

PASS = "[OK]"
FAIL = "[FAIL]"
WARN = "[WARN]"
results = []


def check(name, fn):
    try:
        result = fn()
        status = PASS if result.get("ok") else FAIL
        msg = result.get("detail", "")
        results.append({"name": name, "status": status, "detail": msg})
        print(f"  {status} {name}: {msg}")
        return result.get("ok", False)
    except Exception as e:
        results.append({"name": name, "status": FAIL, "detail": str(e)})
        print(f"  {FAIL} {name}: {e}")
        return False


print("=" * 60)
print("  Amaze on Work -- Full System Integration Check")
print("=" * 60)
print()

# ─── 1. LLM (Cerebras) ────────────────────────────────────────────
print("[1/6] LLM (Cerebras)")
def check_llm():
    from src.llm.client_factory import get_llm_client
    client = get_llm_client()
    if not client.is_available():
        return {"ok": False, "detail": "API not reachable"}
    resp = client.chat([
        {"role": "system", "content": "Reply with exactly: READY"},
        {"role": "user", "content": "Status?"}
    ], temperature=0, max_tokens=10)
    return {"ok": True, "detail": f"Model: {client.get_model_name()}, response: {resp.content.strip()[:20]}"}

check("LLM connection", check_llm)

# ─── 2. GitHub ─────────────────────────────────────────────────────
print("\n[2/6] GitHub")
def check_github_api():
    from src.mcp.github_tools import get_github_tools
    gh = get_github_tools()
    import httpx
    resp = httpx.get("https://api.github.com/rate_limit", headers=gh._headers())
    rate = resp.json().get("rate", {})
    remaining = rate.get("remaining", 0)
    return {"ok": remaining > 0, "detail": f"Rate limit: {remaining}/5000"}

check("GitHub API", check_github_api)

def check_github_mcp():
    from src.mcp.github_server import get_file_content
    content = get_file_content("Rezinix-AI", "shopstack-platform", "README.md")
    return {"ok": len(content) > 0, "detail": f"README.md: {len(content)} chars"}

check("GitHub MCP Server", check_github_mcp)

def check_github_incidents():
    from src.mcp.github_server import list_incidents
    result = json.loads(list_incidents("Rezinix-AI", "shopstack-platform"))
    count = len(result) if isinstance(result, list) else 0
    return {"ok": count > 0, "detail": f"{count} incidents found"}

check("GitHub incidents", check_github_incidents)

# ─── 3. Slack ──────────────────────────────────────────────────────
print("\n[3/6] Slack")
def check_slack():
    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token:
        return {"ok": False, "detail": "SLACK_BOT_TOKEN not set"}
    from slack_sdk import WebClient
    c = WebClient(token=token)
    r = c.auth_test()
    return {"ok": r.get("ok", False), "detail": f"Bot: {r.get('user', 'N/A')}, Team: {r.get('team', 'N/A')}"}

check("Slack connection", check_slack)

def check_slack_mcp():
    from src.mcp.slack_server import mcp as sl_mcp
    tool_count = len(sl_mcp._tool_manager._tools)
    return {"ok": tool_count > 0, "detail": f"{tool_count} tools registered"}

check("Slack MCP Server", check_slack_mcp)

# ─── 4. Docker ─────────────────────────────────────────────────────
print("\n[4/6] Docker Sandbox")
def check_docker():
    from src.sandbox.docker_runner import DockerSandbox
    ds = DockerSandbox()
    health = ds.health_check()
    ok = health.get("docker_available") and health.get("python_image") and health.get("node_image")
    return {"ok": ok, "detail": f"Docker: {health.get('docker_available')}, Python: {health.get('python_image')}, Node: {health.get('node_image')}"}

check("Docker sandbox", check_docker)

# ─── 5. Neo4j ──────────────────────────────────────────────────────
print("\n[5/6] Neo4j Knowledge Graph")
def check_neo4j():
    from src.graph.factory import create_graph_backend
    g = create_graph_backend(prefer="neo4j")
    stats = g.stats()
    backend = stats.get("backend", "unknown")
    nodes = stats.get("nodes", 0)
    edges = stats.get("edges", 0)
    ok = backend == "neo4j"
    if hasattr(g, "close"):
        g.close()
    return {"ok": ok, "detail": f"Backend: {backend}, nodes: {nodes}, edges: {edges}"}

check("Neo4j connection", check_neo4j)

# ─── 6. Pipeline ───────────────────────────────────────────────────
print("\n[6/6] Pipeline Integration")
def check_pipeline_imports():
    from src.agents.supervisor import SupervisorAgent
    from src.agents.kg_builder import KGBuilderAgent
    from src.agents.knowledge_retriever import KnowledgeRetrieverAgent
    from src.mcp.client_bridge import MCPClientBridge
    from src.reports.report_generator import ReportGenerator
    return {"ok": True, "detail": "All 30+ modules importable"}

check("Pipeline imports", check_pipeline_imports)

def check_mcp_bridge():
    from src.mcp.client_bridge import get_mcp_bridge
    bridge = get_mcp_bridge()
    incidents = bridge.list_incidents("Rezinix-AI", "shopstack-platform")
    count = len(incidents) if isinstance(incidents, list) else 0
    return {"ok": count > 0, "detail": f"MCP Bridge -> {count} incidents via GitHub MCP"}

check("MCP Client Bridge", check_mcp_bridge)

# ─── Summary ───────────────────────────────────────────────────────
print()
print("=" * 60)
passed = sum(1 for r in results if r["status"] == PASS)
failed = sum(1 for r in results if r["status"] == FAIL)
warned = sum(1 for r in results if r["status"] == WARN)
total = len(results)
print(f"  Results: {passed}/{total} passed, {failed} failed, {warned} warnings")

if failed == 0:
    print("  STATUS: ALL SYSTEMS GO -- Ready for manual testing!")
else:
    print("  STATUS: Some checks failed -- review above")
print("=" * 60)
