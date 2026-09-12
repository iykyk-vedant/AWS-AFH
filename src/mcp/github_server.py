"""
GitHub MCP Server for Amaze on Work.

Exposes GitHub tools as an MCP server using the FastMCP SDK.
Can be run standalone or imported into the main application.

Usage (standalone):
    python -m src.mcp.github_server

Usage (from code):
    from src.mcp.github_server import mcp as github_mcp
"""

import os
import json
import base64
import logging
from typing import Optional

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()
logger = logging.getLogger(__name__)

# ─── MCP Server Instance ──────────────────────────────────────────
mcp = FastMCP(
    "Amaze on Work-GitHub",
    instructions="GitHub integration tools for Amaze on Work incident resolution",
)

BASE_URL = "https://api.github.com"
TOKEN = os.getenv("GITHUB_TOKEN", "")


def _headers() -> dict:
    h = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Amaze on Work-AI"}
    if TOKEN:
        h["Authorization"] = f"token {TOKEN}"
    return h


def _get(url: str, params: dict | None = None) -> dict | list:
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url, headers=_headers(), params=params)
        resp.raise_for_status()
        return resp.json()


def _post(url: str, data: dict) -> dict:
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(url, headers=_headers(), json=data)
        if resp.status_code >= 400:
            scopes = resp.headers.get("X-OAuth-Scopes", "N/A")
            logger.error(
                f"[GitHub API] POST {url} → {resp.status_code} | "
                f"Body: {resp.text[:300]} | Token scopes: {scopes}"
            )
        resp.raise_for_status()
        return resp.json()


def _put(url: str, data: dict) -> dict:
    with httpx.Client(timeout=30.0) as client:
        resp = client.put(url, headers=_headers(), json=data)
        resp.raise_for_status()
        return resp.json()


# ─── MCP Tools ─────────────────────────────────────────────────────


@mcp.tool()
def get_file_content(owner: str, repo: str, path: str, ref: str = "master") -> str:
    """Fetch file contents from a GitHub repository at a specific path and ref.
    Returns the decoded file content as a string.
    """
    url = f"{BASE_URL}/repos/{owner}/{repo}/contents/{path}"
    try:
        data = _get(url, params={"ref": ref})
    except Exception:
        alt_ref = "main" if ref == "master" else ("master" if ref == "main" else None)
        if alt_ref:
            data = _get(url, params={"ref": alt_ref})
        else:
            raise

    if isinstance(data, list):
        # Directory listing
        items = [{"name": d["name"], "type": d["type"], "path": d["path"]} for d in data]
        return json.dumps({"type": "directory", "items": items}, indent=2)

    content = ""
    if data.get("encoding") == "base64" and data.get("content"):
        content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")

    return content


@mcp.tool()
def get_repo_tree(owner: str, repo: str, ref: str = "master", recursive: bool = True) -> str:
    """Fetch the full file tree for a repository.
    Returns list of file paths.
    """
    url = f"{BASE_URL}/repos/{owner}/{repo}/git/trees/{ref}"
    params = {"recursive": "1"} if recursive else {}
    try:
        data = _get(url, params=params)
    except Exception:
        alt_ref = "main" if ref == "master" else ("master" if ref == "main" else None)
        if alt_ref:
            url = f"{BASE_URL}/repos/{owner}/{repo}/git/trees/{alt_ref}"
            data = _get(url, params=params)
        else:
            raise
    tree = [
        {"path": item["path"], "type": item["type"], "size": item.get("size", 0)}
        for item in data.get("tree", [])
    ]
    return json.dumps(tree, indent=2)


@mcp.tool()
def search_code(owner: str, repo: str, query: str) -> str:
    """Search code in a repository.
    Returns list of matching file paths and snippets.
    """
    url = f"{BASE_URL}/search/code"
    params = {"q": f"{query} repo:{owner}/{repo}"}
    data = _get(url, params=params)
    results = [
        {"path": item["path"], "repository": item["repository"]["full_name"]}
        for item in data.get("items", [])
    ]
    return json.dumps(results, indent=2)


@mcp.tool()
def get_incident(owner: str, repo: str, incident_id: str, ref: str = "master") -> str:
    """Fetch an incident JSON file from the incidents/ directory.
    Returns the parsed incident data as JSON string.
    """
    path = f"incidents/{incident_id}.json"
    content = get_file_content(owner, repo, path, ref)
    return content


@mcp.tool()
def list_incidents(owner: str, repo: str, ref: str = "master") -> str:
    """List all incident files in the incidents/ directory.
    Returns JSON array of incident file names.
    """
    url = f"{BASE_URL}/repos/{owner}/{repo}/contents/incidents"
    data = None
    try:
        data = _get(url, params={"ref": ref})
    except Exception:
        alt_ref = "main" if ref == "master" else ("master" if ref == "main" else None)
        if alt_ref:
            try:
                data = _get(url, params={"ref": alt_ref})
            except Exception as e:
                return json.dumps({"error": str(e)})
        else:
            return json.dumps({"error": "Failed to list incidents"})
    if isinstance(data, list):
        incidents = [
            f["name"].replace(".json", "")
            for f in data if f["name"].endswith(".json")
        ]
        return json.dumps(incidents, indent=2)
    return json.dumps([])


@mcp.tool()
def get_issue(owner: str, repo: str, issue_number: int) -> str:
    """Fetch a GitHub issue by number.
    Returns issue title, body, labels, and state.
    """
    url = f"{BASE_URL}/repos/{owner}/{repo}/issues/{issue_number}"
    data = _get(url)
    return json.dumps({
        "number": data.get("number"),
        "title": data.get("title"),
        "body": data.get("body", "")[:2000],
        "state": data.get("state"),
        "labels": [l["name"] for l in data.get("labels", [])],
    }, indent=2)


@mcp.tool()
def comment_on_issue(owner: str, repo: str, issue_number: int, body: str) -> str:
    """Post a comment on a GitHub issue."""
    url = f"{BASE_URL}/repos/{owner}/{repo}/issues/{issue_number}/comments"
    result = _post(url, {"body": body})
    return json.dumps({"id": result.get("id"), "url": result.get("html_url")})


@mcp.tool()
def list_commits(owner: str, repo: str, path: str = "", per_page: int = 10) -> str:
    """List recent commits, optionally filtered by file path.
    Returns JSON array of commit sha, message, author, and date.
    """
    url = f"{BASE_URL}/repos/{owner}/{repo}/commits"
    params = {"per_page": per_page}
    if path:
        params["path"] = path
    data = _get(url, params=params)
    commits = [
        {
            "sha": c["sha"][:8],
            "message": c["commit"]["message"][:100],
            "author": c["commit"]["author"]["name"],
            "date": c["commit"]["author"]["date"],
        }
        for c in data
    ]
    return json.dumps(commits, indent=2)


@mcp.tool()
def create_branch(owner: str, repo: str, branch_name: str, from_branch: str = "master") -> str:
    """Create a new branch from an existing branch."""
    # Get SHA of source branch
    ref_url = f"{BASE_URL}/repos/{owner}/{repo}/git/refs/heads/{from_branch}"
    ref_data = _get(ref_url)
    sha = ref_data.get("object", {}).get("sha", "")

    # Create the branch
    url = f"{BASE_URL}/repos/{owner}/{repo}/git/refs"
    result = _post(url, {"ref": f"refs/heads/{branch_name}", "sha": sha})
    return json.dumps({"ref": result.get("ref"), "sha": sha})


@mcp.tool()
def commit_file(
    owner: str, repo: str, path: str,
    content: str, message: str, branch: str, sha: str = ""
) -> str:
    """Create or update a file via a commit on a specific branch.
    If updating, provide the current file SHA.
    """
    url = f"{BASE_URL}/repos/{owner}/{repo}/contents/{path}"
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    data = {"message": message, "content": encoded, "branch": branch}
    if sha:
        data["sha"] = sha
    result = _put(url, data)
    return json.dumps({
        "path": result.get("content", {}).get("path"),
        "sha": result.get("content", {}).get("sha"),
        "commit_sha": result.get("commit", {}).get("sha"),
    })


@mcp.tool()
def create_pull_request(
    owner: str, repo: str, title: str,
    body: str, head: str, base: str = "master",
    labels: list[str] | None = None,
) -> str:
    """Create a pull request on a GitHub repository.
    Optionally add labels after creation."""
    url = f"{BASE_URL}/repos/{owner}/{repo}/pulls"
    result = _post(url, {"title": title, "body": body, "head": head, "base": base})
    pr_number = result.get("number")

    # Add labels if provided (PRs are issues in GitHub's API)
    if labels and pr_number:
        try:
            label_url = f"{BASE_URL}/repos/{owner}/{repo}/issues/{pr_number}/labels"
            _post(label_url, {"labels": labels})
            logger.info(f"Labels {labels} added to PR #{pr_number}")
        except Exception as e:
            logger.warning(f"Failed to add labels to PR #{pr_number}: {e}")

    return json.dumps({
        "number": pr_number,
        "url": result.get("html_url"),
        "state": result.get("state"),
    })


@mcp.tool()
def add_labels_to_issue(owner: str, repo: str, issue_number: int, labels: list[str]) -> str:
    """Add labels to an issue or PR (PRs are issues in GitHub API)."""
    url = f"{BASE_URL}/repos/{owner}/{repo}/issues/{issue_number}/labels"
    result = _post(url, {"labels": labels})
    return json.dumps([{"name": l.get("name", "")} for l in result] if isinstance(result, list) else {"ok": True})


# ─── MCP Resources ────────────────────────────────────────────────

@mcp.resource("github://repo/{owner}/{repo}/info")
def get_repo_info(owner: str, repo: str) -> str:
    """Get basic repository information."""
    url = f"{BASE_URL}/repos/{owner}/{repo}"
    data = _get(url)
    return json.dumps({
        "name": data.get("name"),
        "full_name": data.get("full_name"),
        "description": data.get("description"),
        "default_branch": data.get("default_branch"),
        "language": data.get("language"),
        "stars": data.get("stargazers_count"),
        "open_issues": data.get("open_issues_count"),
    }, indent=2)


# ─── Entry Point ──────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
