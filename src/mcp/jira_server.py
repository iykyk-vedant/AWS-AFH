"""
Jira MCP Server for Amaze on Work.

Exposes Jira tools as an MCP server using the FastMCP SDK.
Authenticates via Jira REST API v3 using email + API token (Basic auth).

Tools:
    get_incident_tickets  — fetch open incidents from a project
    get_ticket            — fetch a single ticket by ID
    create_ticket         — create a new incident ticket
    update_ticket_status  — transition status (Open -> In Progress -> Resolved)
    add_comment           — post a resolution comment to a ticket
    link_pr               — link a GitHub PR URL to a ticket

Usage (standalone):
    python -m src.mcp.jira_server

Usage (from code):
    from src.mcp.jira_server import jira_comment, jira_transition_status
"""

import json
import logging
import os
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()
logger = logging.getLogger(__name__)

# ── MCP Server Instance ──────────────────────────────────────────
mcp = FastMCP(
    "Amaze on Work-Jira",
    instructions="Jira integration tools for Amaze on Work incident management",
)

JIRA_URL = os.getenv("JIRA_URL", "")               # e.g. https://yourorg.atlassian.net
JIRA_EMAIL = os.getenv("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.getenv("JIRA_API_TOKEN", "")
JIRA_PROJECT_KEY = os.getenv("JIRA_PROJECT_KEY", "FIX")


def _headers() -> dict:
    """Return Basic-auth headers for Jira REST API."""
    import base64
    creds = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_API_TOKEN}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _get(path: str, params: dict | None = None) -> dict:
    import httpx
    url = f"{JIRA_URL}/rest/api/3/{path}"
    resp = httpx.get(url, headers=_headers(), params=params or {}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _post(path: str, body: dict) -> dict:
    import httpx
    url = f"{JIRA_URL}/rest/api/3/{path}"
    resp = httpx.post(url, headers=_headers(), json=body, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _put(path: str, body: dict) -> dict:
    import httpx
    url = f"{JIRA_URL}/rest/api/3/{path}"
    resp = httpx.put(url, headers=_headers(), json=body, timeout=15)
    if resp.status_code != 204:
        resp.raise_for_status()
    return resp.json() if resp.content else {}


# ── MCP Tools ────────────────────────────────────────────────────


@mcp.tool()
def get_incident_tickets(project_key: str = "", status: str = "Open") -> str:
    """
    Fetch open incident tickets from a Jira project.

    Args:
        project_key: Jira project key (e.g., 'FIX'). Defaults to env var.
        status: Issue status filter (e.g., 'Open', 'In Progress').

    Returns:
        JSON list of tickets with id, summary, description, priority, status.
    """
    project = project_key or JIRA_PROJECT_KEY
    jql = f'project = "{project}" AND status = "{status}" ORDER BY created DESC'
    try:
        data = _get("search", {"jql": jql, "maxResults": 50})
        tickets = [
            {
                "id": issue["key"],
                "summary": issue["fields"].get("summary", ""),
                "description": _extract_text(issue["fields"].get("description")),
                "priority": issue["fields"].get("priority", {}).get("name", "Medium"),
                "status": issue["fields"].get("status", {}).get("name", ""),
                "labels": issue["fields"].get("labels", []),
            }
            for issue in data.get("issues", [])
        ]
        return json.dumps(tickets, indent=2)
    except Exception as e:
        logger.error(f"Jira get_incident_tickets failed: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
def get_ticket(ticket_id: str) -> str:
    """
    Fetch a single Jira ticket by its ID.

    Args:
        ticket_id: Jira issue key (e.g., 'FIX-42').

    Returns:
        JSON with id, summary, description, priority, status, labels, comments.
    """
    try:
        issue = _get(f"issue/{ticket_id}")
        return json.dumps({
            "id": issue["key"],
            "summary": issue["fields"].get("summary", ""),
            "description": _extract_text(issue["fields"].get("description")),
            "priority": issue["fields"].get("priority", {}).get("name", "Medium"),
            "status": issue["fields"].get("status", {}).get("name", ""),
            "labels": issue["fields"].get("labels", []),
            "assignee": (issue["fields"].get("assignee") or {}).get("displayName", ""),
            "created": issue["fields"].get("created", ""),
            "updated": issue["fields"].get("updated", ""),
        }, indent=2)
    except Exception as e:
        logger.error(f"Jira get_ticket {ticket_id} failed: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
def create_ticket(
    summary: str,
    description: str,
    priority: str = "High",
    labels: str = "incident,Amaze on Work",
    project_key: str = "",
) -> str:
    """
    Create a new incident ticket in Jira.

    Args:
        summary: One-line ticket title.
        description: Full incident description.
        priority: 'Low', 'Medium', 'High', or 'Critical'.
        labels: Comma-separated labels (e.g., 'incident,Amaze on Work').
        project_key: Project key. Defaults to env var JIRA_PROJECT_KEY.

    Returns:
        JSON with the new ticket ID and URL.
    """
    project = project_key or JIRA_PROJECT_KEY
    label_list = [l.strip() for l in labels.split(",") if l.strip()]
    body = {
        "fields": {
            "project": {"key": project},
            "summary": summary,
            "description": _adf_doc(description),
            "issuetype": {"name": "Bug"},
            "priority": {"name": priority},
            "labels": label_list,
        }
    }
    try:
        result = _post("issue", body)
        ticket_id = result.get("key", "")
        url = f"{JIRA_URL}/browse/{ticket_id}"
        logger.info(f"Created Jira ticket: {ticket_id}")
        return json.dumps({"id": ticket_id, "url": url})
    except Exception as e:
        logger.error(f"Jira create_ticket failed: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
def update_ticket_status(ticket_id: str, status: str) -> str:
    """
    Transition a Jira ticket to a new status.

    Supported transitions: 'In Progress', 'In Review', 'Resolved', 'Done'.

    Args:
        ticket_id: Jira issue key (e.g., 'FIX-42').
        status: Target status name.

    Returns:
        JSON with success flag and message.
    """
    # Map status names to common Jira transition IDs
    # Note: actual IDs vary by workflow. We fetch available transitions first.
    try:
        trans_data = _get(f"issue/{ticket_id}/transitions")
        transitions = trans_data.get("transitions", [])
        target_name = status.lower().replace(" ", "")
        match = None
        for t in transitions:
            if t["name"].lower().replace(" ", "") == target_name:
                match = t
                break

        if not match:
            available = [t["name"] for t in transitions]
            return json.dumps({
                "ok": False,
                "error": f"Transition '{status}' not found. Available: {available}"
            })

        import httpx
        url = f"{JIRA_URL}/rest/api/3/issue/{ticket_id}/transitions"
        resp = httpx.post(
            url,
            headers=_headers(),
            json={"transition": {"id": match["id"]}},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info(f"Jira {ticket_id} transitioned to: {status}")
        return json.dumps({"ok": True, "ticket_id": ticket_id, "new_status": status})
    except Exception as e:
        logger.error(f"Jira update_ticket_status {ticket_id} -> {status} failed: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
def add_comment(ticket_id: str, comment: str) -> str:
    """
    Add a comment to a Jira ticket.

    Use this to post the resolution report as a structured Jira comment.

    Args:
        ticket_id: Jira issue key (e.g., 'FIX-42').
        comment: Plain-text comment body (will be wrapped in ADF format).

    Returns:
        JSON with comment ID.
    """
    try:
        body = {"body": _adf_doc(comment)}
        result = _post(f"issue/{ticket_id}/comment", body)
        logger.info(f"Added comment to Jira {ticket_id}")
        return json.dumps({"ok": True, "comment_id": result.get("id", "")})
    except Exception as e:
        logger.error(f"Jira add_comment {ticket_id} failed: {e}")
        return json.dumps({"error": str(e)})


@mcp.tool()
def link_pr(ticket_id: str, pr_url: str, pr_title: str = "") -> str:
    """
    Link a GitHub Pull Request to a Jira ticket via a web link.

    Args:
        ticket_id: Jira issue key (e.g., 'FIX-42').
        pr_url: Full GitHub PR URL.
        pr_title: Optional PR title for display.

    Returns:
        JSON with success flag.
    """
    try:
        title = pr_title or f"GitHub PR: {pr_url.split('/')[-1]}"
        body = {
            "object": {
                "url": pr_url,
                "title": title,
            }
        }
        _post(f"issue/{ticket_id}/remotelink", body)
        # Also add a comment with the link so it's visible in the audit trail
        add_comment(ticket_id, f"Pull Request created: [{title}]({pr_url})")
        logger.info(f"Linked PR to Jira {ticket_id}: {pr_url}")
        return json.dumps({"ok": True, "ticket_id": ticket_id, "pr_url": pr_url})
    except Exception as e:
        logger.error(f"Jira link_pr {ticket_id} failed: {e}")
        return json.dumps({"error": str(e)})


# ── Convenience functions (direct call, no MCP overhead) ─────────

def jira_comment(ticket_id: str, comment: str) -> bool:
    """Direct call wrapper for add_comment. Returns True if successful."""
    result = json.loads(add_comment(ticket_id, comment))
    return result.get("ok", False)


def jira_transition_status(ticket_id: str, status: str) -> bool:
    """Direct call wrapper for update_ticket_status. Returns True if successful."""
    result = json.loads(update_ticket_status(ticket_id, status))
    return result.get("ok", False)


# ── Helpers ───────────────────────────────────────────────────────

def _extract_text(description_adf) -> str:
    """Extract plain text from Atlassian Document Format (ADF) description."""
    if not description_adf:
        return ""
    if isinstance(description_adf, str):
        return description_adf
    # ADF is a nested dict
    texts = []
    def _walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                texts.append(node.get("text", ""))
            for child in node.get("content", []):
                _walk(child)
        elif isinstance(node, list):
            for item in node:
                _walk(item)
    _walk(description_adf)
    return " ".join(texts)


def _adf_doc(text: str) -> dict:
    """Wrap plain text in minimal Atlassian Document Format."""
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "paragraph",
                "content": [{"type": "text", "text": text}],
            }
        ],
    }


# ── Entry Point ───────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
