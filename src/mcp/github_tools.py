"""
GitHub MCP Tools for Amaze on Work.

Provides GitHub API access through the Model Context Protocol (MCP).
Uses httpx to call GitHub REST API with proper authentication.
"""

import logging
import os
import base64
from typing import Optional
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

BASE_URL = "https://api.github.com"


class GitHubMCPTools:
    """
    GitHub tools exposed via MCP protocol.

    Provides file access, code search, commit history, branch/PR operations
    for the Amaze on Work agent pipeline.
    """

    def __init__(self, token: Optional[str] = None):
        self._token = token or os.getenv("GITHUB_TOKEN", "")
        self._timeout = 30.0

    def _headers(self) -> dict:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Amaze on Work-AI",
        }
        if self._token:
            headers["Authorization"] = f"token {self._token}"
        return headers

    def _get(self, url: str, params: dict | None = None) -> dict | list:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.get(url, headers=self._headers(), params=params)
            response.raise_for_status()
            return response.json()

    def _post(self, url: str, json_data: dict) -> dict:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.post(url, headers=self._headers(), json=json_data)
            response.raise_for_status()
            return response.json()

    def _put(self, url: str, json_data: dict) -> dict:
        with httpx.Client(timeout=self._timeout) as client:
            response = client.put(url, headers=self._headers(), json=json_data)
            response.raise_for_status()
            return response.json()

    # ─── File Access ──────────────────────────────────────────────

    def get_file_content(
        self, owner: str, repo: str, path: str, ref: str = "master"
    ) -> dict:
        """
        Fetch file contents at a specific path and ref.

        Returns:
            dict with 'content' (decoded string), 'sha', 'size', 'path'
        """
        url = f"{BASE_URL}/repos/{owner}/{repo}/contents/{path}"
        try:
            data = self._get(url, params={"ref": ref} if ref else None)
        except Exception:
            alt_ref = "main" if ref == "master" else ("master" if ref == "main" else None)
            if alt_ref:
                data = self._get(url, params={"ref": alt_ref})
            else:
                raise

        if isinstance(data, list):
            # It's a directory listing
            return {"type": "directory", "items": data, "path": path}

        content = ""
        if data.get("encoding") == "base64" and data.get("content"):
            content = base64.b64decode(data["content"]).decode("utf-8", errors="replace")

        return {
            "content": content,
            "sha": data.get("sha", ""),
            "size": data.get("size", 0),
            "path": data.get("path", path),
            "type": "file",
        }

    def get_repo_tree(
        self, owner: str, repo: str, ref: str = "master", recursive: bool = True
    ) -> list[dict]:
        """
        Fetch the full file tree for a repo.

        Returns:
            list of dicts with 'path', 'type' ('blob'/'tree'), 'size'
        """
        url = f"{BASE_URL}/repos/{owner}/{repo}/git/trees/{ref}"
        params = {"recursive": "1"} if recursive else {}
        try:
            data = self._get(url, params=params)
        except Exception:
            alt_ref = "main" if ref == "master" else ("master" if ref == "main" else None)
            if alt_ref:
                url = f"{BASE_URL}/repos/{owner}/{repo}/git/trees/{alt_ref}"
                data = self._get(url, params=params)
            else:
                raise
        return data.get("tree", [])

    def search_code(
        self, owner: str, repo: str, query: str, per_page: int = 10
    ) -> list[dict]:
        """
        Search code in a repository.

        Returns:
            list of matches with 'path', 'repository', 'text_matches'
        """
        url = f"{BASE_URL}/search/code"
        params = {
            "q": f"{query} repo:{owner}/{repo}",
            "per_page": per_page,
        }
        data = self._get(url, params=params)
        return data.get("items", [])

    # ─── Issues ───────────────────────────────────────────────────

    def get_issue(self, owner: str, repo: str, issue_number: int) -> dict:
        """Fetch a GitHub issue by number."""
        url = f"{BASE_URL}/repos/{owner}/{repo}/issues/{issue_number}"
        return self._get(url)

    def comment_on_issue(
        self, owner: str, repo: str, issue_number: int, body: str
    ) -> dict:
        """Post a comment on a GitHub issue."""
        url = f"{BASE_URL}/repos/{owner}/{repo}/issues/{issue_number}/comments"
        return self._post(url, {"body": body})

    # ─── Commits ──────────────────────────────────────────────────

    def list_commits(
        self,
        owner: str,
        repo: str,
        path: str = "",
        since: str = "",
        per_page: int = 30,
    ) -> list[dict]:
        """List commits, optionally filtered by file path."""
        url = f"{BASE_URL}/repos/{owner}/{repo}/commits"
        params = {"per_page": per_page}
        if path:
            params["path"] = path
        if since:
            params["since"] = since
        return self._get(url, params=params)

    def get_commit(self, owner: str, repo: str, sha: str) -> dict:
        """Get a specific commit with its diff."""
        url = f"{BASE_URL}/repos/{owner}/{repo}/commits/{sha}"
        return self._get(url)

    # ─── Branches & PRs ──────────────────────────────────────────

    def get_default_branch(self, owner: str, repo: str) -> str:
        """Get the default branch name."""
        url = f"{BASE_URL}/repos/{owner}/{repo}"
        data = self._get(url)
        return data.get("default_branch", "master")

    def get_branch_sha(self, owner: str, repo: str, branch: str) -> str:
        """Get the SHA of a branch HEAD with fallback between master and main."""
        url = f"{BASE_URL}/repos/{owner}/{repo}/git/refs/heads/{branch}"
        try:
            data = self._get(url)
            return data.get("object", {}).get("sha", "")
        except Exception:
            alt_branch = "main" if branch == "master" else ("master" if branch == "main" else None)
            if alt_branch:
                try:
                    url = f"{BASE_URL}/repos/{owner}/{repo}/git/refs/heads/{alt_branch}"
                    data = self._get(url)
                    return data.get("object", {}).get("sha", "")
                except Exception:
                    pass
            raise

    def create_branch(
        self, owner: str, repo: str, branch_name: str, from_sha: str = "", from_branch: str = ""
    ) -> dict:
        """Create a new branch from a commit SHA or existing branch name."""
        target_sha = from_sha or ""
        # If target_sha is not a 40-char commit SHA, treat it as a branch name
        if not target_sha or len(target_sha) != 40:
            branch_to_lookup = from_branch or target_sha or self.get_default_branch(owner, repo)
            target_sha = self.get_branch_sha(owner, repo, branch_to_lookup)

        url = f"{BASE_URL}/repos/{owner}/{repo}/git/refs"
        return self._post(url, {
            "ref": f"refs/heads/{branch_name}",
            "sha": target_sha,
        })

    def commit_file(
        self,
        owner: str,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        sha: str = "",
    ) -> dict:
        """Create or update a file via commit."""
        url = f"{BASE_URL}/repos/{owner}/{repo}/contents/{path}"
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        data = {
            "message": message,
            "content": encoded,
            "branch": branch,
        }
        if sha:
            data["sha"] = sha
        return self._put(url, data)

    def create_pull_request(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str = "master",
        labels: list[str] | None = None,
    ) -> dict:
        """Open a pull request with base fallback and optional labels."""
        import re
        # Ensure #close statement is present for automated resolution
        if not re.search(r'#close\s+#?\d+|closes\s+#?\d+|fixes\s+#?\d+', body or "", re.IGNORECASE):
            issue_match = re.search(r'(?:inc-?|issue-?#?|#)(\d+)', f"{head} {title}", re.IGNORECASE)
            if issue_match:
                issue_num = int(issue_match.group(1))
                body = (body or "") + (
                    f"\n\n---\n"
                    f"### Automated Resolution\n"
                    f"- #close #{issue_num}\n"
                    f"- Closes #{issue_num}\n"
                    f"- Fixes #{issue_num}\n"
                )

        url = f"{BASE_URL}/repos/{owner}/{repo}/pulls"
        try:
            result = self._post(url, {
                "title": title,
                "body": body,
                "head": head,
                "base": base,
            })
        except Exception:
            alt_base = "main" if base == "master" else ("master" if base == "main" else None)
            if alt_base:
                result = self._post(url, {
                    "title": title,
                    "body": body,
                    "head": head,
                    "base": alt_base,
                })
            else:
                raise

        if labels and isinstance(result, dict) and result.get("number"):
            try:
                label_url = f"{BASE_URL}/repos/{owner}/{repo}/issues/{result['number']}/labels"
                self._post(label_url, {"labels": labels})
            except Exception as e:
                logger.warning(f"Could not add labels to PR: {e}")

        return result

    # ─── Incident File Access ─────────────────────────────────────

    def get_incident_files(
        self, owner: str, repo: str, ref: str = "master"
    ) -> list[dict]:
        """List all incident JSON files in the incidents/ directory."""
        try:
            data = self.get_file_content(owner, repo, "incidents", ref)
            if data.get("type") == "directory":
                return [
                    item for item in data.get("items", [])
                    if item.get("name", "").endswith(".json")
                ]
        except Exception as e:
            logger.warning(f"Could not list incidents: {e}")
        return []

    def get_incident(
        self, owner: str, repo: str, incident_id: str, ref: str = "master"
    ) -> dict:
        """Fetch a specific incident JSON file."""
        import json
        clean_id = incident_id[:-5] if incident_id.endswith(".json") else incident_id
        path = f"incidents/{clean_id}.json"
        file_data = self.get_file_content(owner, repo, path, ref)
        content = file_data.get("content", "{}")
        return json.loads(content)


# Convenience function
def get_github_tools(token: Optional[str] = None) -> GitHubMCPTools:
    """Get a configured GitHub MCP tools instance."""
    return GitHubMCPTools(token)
