"""
MCP Client Bridge for Amaze on Work.

Connects to MCP servers (GitHub, Slack) and provides a unified
interface for the pipeline agents to call MCP tools.

Supports two modes:
1. Direct mode: Use tool functions directly (for in-process calls)
2. MCP mode: Connect to MCP server via stdio/SSE transport
"""

import json
import logging
import os
import asyncio
from typing import Optional, Any
from src.config import GITHUB_API_BASE_URL

logger = logging.getLogger(__name__)


class MCPClientBridge:
    """
    Bridges MCP servers with the Amaze on Work pipeline.

    In direct mode (default), calls tool functions directly.
    In MCP mode, connects to running MCP servers via transport.
    """

    def __init__(self, mode: str = "direct"):
        self.mode = mode
        self._github_tools = None
        self._slack_tools = None
        self._jira_tools = None

    @property
    def github(self):
        """Get GitHub tools (lazy init)."""
        if self._github_tools is None:
            from src.mcp import github_server
            self._github_tools = github_server
        return self._github_tools

    @property
    def slack(self):
        """Get Slack tools (lazy init)."""
        if self._slack_tools is None:
            from src.mcp import slack_server
            self._slack_tools = slack_server
        return self._slack_tools

    @property
    def jira(self):
        """Get Jira tools (lazy init)."""
        if self._jira_tools is None:
            from src.mcp import jira_server
            self._jira_tools = jira_server
        return self._jira_tools

    # ─── GitHub Tool Wrappers ─────────────────────────────────────

    def get_file_content(self, owner: str, repo: str, path: str, ref: str = "master") -> str:
        """Fetch file content from GitHub."""
        return self.github.get_file_content(owner, repo, path, ref)

    def get_repo_tree(self, owner: str, repo: str, ref: str = "master") -> list:
        """Get repo file tree."""
        result = self.github.get_repo_tree(owner, repo, ref)
        return json.loads(result) if isinstance(result, str) else result

    def search_code(self, owner: str, repo: str, query: str, per_page: int = 10) -> list:
        """Search code in repo."""
        result = self.github.search_code(owner, repo, query, per_page)
        return json.loads(result) if isinstance(result, str) else result

    def get_incident(self, owner: str, repo: str, incident_id: str) -> dict:
        """Fetch incident JSON."""
        result = self.github.get_incident(owner, repo, incident_id)
        return json.loads(result) if isinstance(result, str) else result

    def list_incidents(self, owner: str, repo: str) -> list:
        """List all incidents."""
        result = self.github.list_incidents(owner, repo)
        return json.loads(result) if isinstance(result, str) else result

    def list_commits(self, owner: str, repo: str, path: str = "", per_page: int = 10) -> list:
        """List recent commits."""
        result = self.github.list_commits(owner, repo, path, per_page)
        return json.loads(result) if isinstance(result, str) else result

    def create_branch(self, owner: str, repo: str, branch_name: str, from_branch: str = "master") -> dict:
        """Create a new branch."""
        result = self.github.create_branch(owner, repo, branch_name, from_branch)
        return json.loads(result) if isinstance(result, str) else result

    def commit_file(
        self, owner: str, repo: str, path: str,
        content: str, message: str, branch: str, sha: str = ""
    ) -> dict:
        """Commit a file to a branch."""
        result = self.github.commit_file(owner, repo, path, content, message, branch, sha)
        return json.loads(result) if isinstance(result, str) else result

    def create_pull_request(
        self, owner: str, repo: str, title: str,
        body: str, head: str, base: str = "master",
        labels: list[str] | None = None,
    ) -> dict:
        """Create a pull request (optionally with labels)."""
        result = self.github.create_pull_request(owner, repo, title, body, head, base, labels)
        return json.loads(result) if isinstance(result, str) else result

    def comment_on_issue(self, owner: str, repo: str, issue_number: int, body: str) -> dict:
        """Comment on an issue."""
        result = self.github.comment_on_issue(owner, repo, issue_number, body)
        return json.loads(result) if isinstance(result, str) else result

    # ─── Slack Tool Wrappers ──────────────────────────────────────

    def post_slack_message(self, channel: str, text: str, thread_ts: str = "") -> dict:
        """Post a Slack message."""
        result = self.slack.post_message(channel, text, thread_ts)
        return json.loads(result) if isinstance(result, str) else result

    def post_slack_resolution(
        self, channel: str, incident_id: str, title: str,
        root_cause: str, verdict: str, confidence: float,
        risk_level: str, pr_url: str = "", thread_ts: str = ""
    ) -> dict:
        """Post a resolution notification to Slack."""
        result = self.slack.post_resolution(
            channel, incident_id, title, root_cause,
            verdict, confidence, risk_level, pr_url, thread_ts
        )
        return json.loads(result) if isinstance(result, str) else result

    def post_full_report_to_reports_channel(
        self,
        incident_id: str,
        title: str,
        root_cause: str,
        reasoning: str,
        changes_made: str,
        validation_verdict: str,
        risk_level: str,
        confidence: float,
        resolution_time: float,
        pr_url: str = "",
        patch_diff: str = "",
        security_summary: str = "",
    ) -> dict:
        """Post the full report to the #reports Slack channel."""
        result = self.slack.post_full_report(
            incident_id=incident_id,
            title=title,
            root_cause=root_cause,
            reasoning=reasoning,
            changes_made=changes_made,
            validation_verdict=validation_verdict,
            risk_level=risk_level,
            confidence=confidence,
            resolution_time=resolution_time,
            pr_url=pr_url,
            patch_diff=patch_diff,
            security_summary=security_summary,
        )
        return json.loads(result) if isinstance(result, str) else result

    def post_incident_started(self, channel: str, incident_id: str, title: str, thread_ts: str = "") -> dict:
        """Notify that Amaze on Work started working on an incident."""
        result = self.slack.post_incident_started(channel, incident_id, title, thread_ts)
        return json.loads(result) if isinstance(result, str) else result

    def post_progress_update(
        self,
        status_msg: str,
        slack_channel: str = "",
        slack_thread_ts: str = "",
        jira_ticket_id: str = "",
        incident_id: str = "",
    ) -> None:
        """
        Post live agent progress to Slack thread AND Jira ticket comment.

        Called by supervisor at each pipeline stage transition so the entire
        resolution story is visible in Slack and Jira without any terminal.
        Both posts are best-effort — errors are swallowed and logged.
        """
        if slack_channel and slack_thread_ts:
            try:
                result = self.slack.post_progress_update(
                    slack_channel, slack_thread_ts, status_msg, incident_id
                )
                if isinstance(result, str):
                    result = json.loads(result)
                if not result.get("ok"):
                    logger.warning(f"Slack progress update failed: {result}")
            except Exception as e:
                logger.warning(f"Slack post_progress_update error: {e}")

        if jira_ticket_id:
            try:
                prefix = f"*{incident_id}*  " if incident_id else ""
                from src.mcp.jira_server import jira_comment
                jira_comment(jira_ticket_id, f"{prefix}{status_msg}")
            except Exception as e:
                logger.warning(f"Jira post_progress_update error: {e}")

    # ─── Jira Tool Wrappers ───────────────────────────────────────

    def jira_get_ticket(self, ticket_id: str) -> dict:
        """Fetch a Jira ticket."""
        result = self.jira.get_ticket(ticket_id)
        return json.loads(result) if isinstance(result, str) else result

    def jira_create_ticket(
        self, summary: str, description: str,
        priority: str = "High", labels: str = "incident,Amaze on Work"
    ) -> dict:
        """Create a new Jira bug ticket."""
        result = self.jira.create_ticket(summary, description, priority, labels)
        return json.loads(result) if isinstance(result, str) else result

    def jira_update_status(self, ticket_id: str, status: str) -> dict:
        """Transition a Jira ticket status."""
        result = self.jira.update_ticket_status(ticket_id, status)
        return json.loads(result) if isinstance(result, str) else result

    def jira_add_comment(self, ticket_id: str, comment: str) -> dict:
        """Add comment to a Jira ticket."""
        result = self.jira.add_comment(ticket_id, comment)
        return json.loads(result) if isinstance(result, str) else result

    def jira_link_pr(self, ticket_id: str, pr_url: str, pr_title: str = "") -> dict:
        """Link a GitHub PR to a Jira ticket."""
        result = self.jira.link_pr(ticket_id, pr_url, pr_title)
        return json.loads(result) if isinstance(result, str) else result

    # ─── Auto PR Flow ─────────────────────────────────────────────

    def get_file_sha(self, owner: str, repo: str, path: str, ref: str = "master") -> str:
        """Get the current SHA of a file (needed for updates via GitHub API)."""
        import httpx
        url = f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/contents/{path}"
        token = os.getenv("GITHUB_TOKEN", "")
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Amaze on Work-AI",
        }
        if token:
            headers["Authorization"] = f"token {token}"
        try:
            resp = httpx.get(url, headers=headers, params={"ref": ref}, timeout=15)
            if resp.status_code == 200:
                return resp.json().get("sha", "")
        except Exception as e:
            logger.warning(f"get_file_sha failed for {path}: {e}")
    @staticmethod
    def _extract_changes_from_patch(patch_str: str) -> list[dict]:
        """Extract file_path, original_code, and fixed_code from a unified diff."""
        if not patch_str:
            return []
        import re
        chunks = []
        current_file = None
        orig_lines = []
        fixed_lines = []
        context_after = []

        for line in patch_str.splitlines():
            if line.startswith('--- '):
                if current_file and (orig_lines or fixed_lines):
                    orig_code = '\n'.join(orig_lines)
                    fixed_code = '\n'.join(fixed_lines)
                    if not orig_code and context_after:
                        anchor = context_after[0]
                        orig_code = anchor
                        fixed_code = fixed_code + '\n' + anchor
                    chunks.append({
                        'file_path': current_file,
                        'original_code': orig_code,
                        'fixed_code': fixed_code,
                        'rationale': 'Extracted from unified diff',
                    })
                    orig_lines, fixed_lines, context_after = [], [], []
                raw_path = line.split(' ', 1)[1].strip()
                current_file = re.sub(r'^[ab]/', '', raw_path)
            elif line.startswith('+++ '):
                pass
            elif line.startswith('@@'):
                if current_file and (orig_lines or fixed_lines):
                    orig_code = '\n'.join(orig_lines)
                    fixed_code = '\n'.join(fixed_lines)
                    if not orig_code and context_after:
                        anchor = context_after[0]
                        orig_code = anchor
                        fixed_code = fixed_code + '\n' + anchor
                    chunks.append({
                        'file_path': current_file,
                        'original_code': orig_code,
                        'fixed_code': fixed_code,
                        'rationale': 'Extracted from unified diff',
                    })
                    orig_lines, fixed_lines, context_after = [], [], []
            elif current_file:
                if line.startswith('-') and not line.startswith('---'):
                    orig_lines.append(line[1:])
                elif line.startswith('+') and not line.startswith('+++'):
                    fixed_lines.append(line[1:])
                elif line.startswith(' '):
                    if fixed_lines and not orig_lines:
                        context_after.append(line[1:])

        if current_file and (orig_lines or fixed_lines):
            orig_code = '\n'.join(orig_lines)
            fixed_code = '\n'.join(fixed_lines)
            if not orig_code and context_after:
                anchor = context_after[0]
                orig_code = anchor
                fixed_code = fixed_code + '\n' + anchor
            chunks.append({
                'file_path': current_file,
                'original_code': orig_code,
                'fixed_code': fixed_code,
                'rationale': 'Extracted from unified diff',
            })
        return chunks

    def create_fix_pr(
        self,
        owner: str,
        repo: str,
        incident_id: str,
        fix_plan: dict,
        report_body: str,
        risk_level: str = "MEDIUM",
        problem_summary: str = "",
        github_issue_number: Optional[int] = None,
    ) -> dict:
        """Full auto-PR flow: create branch -> commit files -> create PR.

        Uses the same sanitizer + smart matching as DockerSandbox to ensure
        code changes are applied correctly. Adds labels based on risk level.

        Returns dict with 'pr_url', 'pr_number', 'branch', 'files_committed'.
        """
        import httpx
        import base64

        branch = f"amaze-on-work/{incident_id.lower().replace(' ', '-').replace('/', '-')}"
        files_committed = []
        token = os.getenv("GITHUB_TOKEN", "")

        # Build PR title with one-line problem description
        if problem_summary:
            title = f"fix({incident_id}): {problem_summary[:80]}"
        else:
            title = f"fix({incident_id}): Automated resolution by Amaze on Work"

        # Build labels based on risk level
        labels = ["Amaze on Work-ai"]
        if risk_level == "LOW":
            labels.append("raised-by-agent")
        elif risk_level == "MEDIUM":
            labels.append("agent-assisted")
        elif risk_level == "HIGH":
            labels.append("manual-review")

        try:
            # 1. Auto-detect default branch from GitHub API
            default_branch = "main"  # fallback
            try:
                import httpx as _httpx
                _headers = {
                    "Accept": "application/vnd.github.v3+json",
                    "User-Agent": "Amaze on Work-AI",
                }
                if token:
                    _headers["Authorization"] = f"token {token}"
                repo_resp = _httpx.get(
                    f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}",
                    headers=_headers, timeout=10,
                )
                if repo_resp.status_code == 200:
                    default_branch = repo_resp.json().get("default_branch", "main")
                    logger.info(f"[PR] Detected default branch: {default_branch}")
            except Exception as e:
                logger.warning(f"[PR] Could not detect default branch: {e}")

            # 2. Create fix branch from default branch (idempotent)
            try:
                self.create_branch(owner, repo, branch, from_branch=default_branch)
                logger.info(f"Branch created: {branch} from {default_branch}")
            except Exception as e:
                err_text = str(e)
                if hasattr(e, "response") and hasattr(e.response, "text"):
                    err_text += " " + e.response.text
                if "already exists" in err_text.lower():
                    logger.info(f"Branch {branch} already exists — reusing existing branch")
                else:
                    logger.error(f"Branch creation failed: {e}")
                    return {"error": f"Branch creation failed (check GITHUB_TOKEN permissions): {e}"}

            # 2. Commit each modified file (using sanitizer + smart matching)
            changes = list(fix_plan.get("files_to_modify", []))

            # If changes is empty or missing fixed_code, try extracting from patch or candidate cache
            if not changes or any(not c.get("fixed_code") for c in changes):
                patch_str = fix_plan.get("patch", "")
                if patch_str:
                    extracted = self._extract_changes_from_patch(patch_str)
                    if extracted:
                        if not changes:
                            changes = extracted
                        else:
                            for idx, c in enumerate(changes):
                                if not c.get("fixed_code"):
                                    match = next((e for e in extracted if e.get("file_path") == c.get("file_path")), None)
                                    if match:
                                        changes[idx] = match

            if not changes or any(not c.get("fixed_code") for c in changes):
                # Fallback to local candidate file if available
                cand_path = Path(f"data/fix_candidates/{incident_id}.json")
                if cand_path.exists():
                    try:
                        cand_data = json.loads(cand_path.read_text(encoding="utf-8"))
                        for c in cand_data.get("candidates", []):
                            cand_files = c.get("fix_plan", {}).get("files_to_modify", [])
                            if cand_files and all(cf.get("fixed_code") for cf in cand_files):
                                changes = cand_files
                                logger.info(f"[PR] Loaded {len(changes)} verified changes from {cand_path}")
                                break
                    except Exception as ce:
                        logger.warning(f"[PR] Error reading candidate cache {cand_path}: {ce}")

            for change in changes:
                file_path = change.get("file_path", "")
                original_code = change.get("original_code", "")
                fixed_code = change.get("fixed_code", "")
                if not file_path or not fixed_code:
                    logger.warning(f"Skipping change with missing path or code (file={file_path})")
                    continue

                # Sanitize LLM output
                from src.sandbox.docker_runner import DockerSandbox
                original_code = DockerSandbox._sanitize_llm_code(original_code)
                fixed_code = DockerSandbox._sanitize_llm_code(fixed_code)

                # Fetch the current file content from GitHub
                sha = ""
                current_content = ""
                try:
                    headers = {
                        "Accept": "application/vnd.github.v3+json",
                        "User-Agent": "Amaze on Work-AI",
                    }
                    if token:
                        headers["Authorization"] = f"token {token}"
                    resp = httpx.get(
                        f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/contents/{file_path}",
                        headers=headers,
                        params={"ref": branch},
                        timeout=15,
                    )
                    if resp.status_code == 200:
                        file_data = resp.json()
                        sha = file_data.get("sha", "")
                        encoded = file_data.get("content", "")
                        current_content = base64.b64decode(encoded).decode("utf-8")
                except Exception as e:
                    logger.warning(f"Could not fetch {file_path} from GitHub: {e}")

                # Apply smart matching (same as DockerSandbox._smart_replace)
                if current_content and original_code:
                    sandbox = DockerSandbox.__new__(DockerSandbox)
                    commit_content = sandbox._smart_replace(
                        current_content, original_code, fixed_code, file_path
                    )
                    logger.info(f"[PR] Smart match applied for {file_path}")
                elif current_content and not original_code:
                    commit_content = current_content + "\n" + fixed_code
                    logger.warning(f"[PR] No original_code for {file_path} — appending")
                else:
                    commit_content = fixed_code
                    logger.warning(f"[PR] Could not fetch {file_path} — using fixed_code directly")

                try:
                    self.commit_file(
                        owner, repo, file_path, commit_content,
                        f"fix({incident_id}): {change.get('rationale', 'automated fix')[:60]}",
                        branch, sha
                    )
                    files_committed.append(file_path)
                    logger.info(f"Committed: {file_path}")
                except Exception as e:
                    logger.error(f"Commit failed for {file_path}: {e}")

            if not files_committed:
                logger.warning("No files committed -- fix_plan may have no files_to_modify")

            # 3. Create PR (or reuse existing PR if already open)
            # Use explicit github_issue_number if provided; otherwise fallback to extracting digits from incident_id
            if github_issue_number:
                issue_num_str = f"#{github_issue_number}"
            else:
                import re
                issue_match = re.search(r'\b(?:inc-?|issue-?#?|#)?(\d+)\b', str(incident_id), re.IGNORECASE)
                issue_num_str = f"#{int(issue_match.group(1))}" if issue_match else ""

            close_block = ""
            if issue_num_str:
                close_block = (
                    f"\n\n---\n"
                    f"### Automated Resolution\n"
                    f"- #close {issue_num_str}\n"
                    f"- Closes {issue_num_str}\n"
                    f"- Fixes {issue_num_str}\n"
                )

            pr_title = title
            pr_body = (report_body + close_block)[:65000]  # GitHub body limit
            pr_url = ""
            pr_number = 0
            try:
                pr = self.create_pull_request(
                    owner, repo, pr_title, pr_body,
                    head=branch, base=default_branch,
                    labels=labels,
                )
                pr_url = pr.get("html_url") or pr.get("pr_url") or pr.get("url") or ""
                pr_number = pr.get("number") or pr.get("pr_number", 0)
                logger.info(f"PR #{pr_number} created: {pr_url} [labels: {labels}]")
            except Exception as e:
                err_text = str(e)
                if hasattr(e, "response") and hasattr(e.response, "text"):
                    err_text += " " + e.response.text
                if "already exists" in err_text.lower() or "pull request already exists" in err_text.lower():
                    logger.info(f"PR for {branch} already exists, fetching existing PR...")
                    try:
                        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "Amaze on Work-AI"}
                        if token:
                            headers["Authorization"] = f"token {token}"
                        resp = httpx.get(
                            f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/pulls",
                            headers=headers,
                            params={"head": f"{owner}:{branch}", "state": "open"},
                            timeout=10,
                        )
                        if resp.status_code == 200 and resp.json():
                            pr_item = resp.json()[0]
                            pr_url = pr_item.get("html_url", "")
                            pr_number = pr_item.get("number", 0)
                            logger.info(f"Existing PR #{pr_number} found: {pr_url}")
                            # Append closing statement to existing PR body if not present
                            if close_block and pr_number:
                                try:
                                    curr_body = pr_item.get("body", "") or ""
                                    if issue_num_str and issue_num_str not in curr_body:
                                        new_body = (curr_body + "\n" + close_block)[:65000]
                                        httpx.patch(
                                            f"{GITHUB_API_BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}",
                                            headers=headers,
                                            json={"body": new_body},
                                            timeout=10,
                                        )
                                        logger.info(f"Updated PR #{pr_number} with closing statement for {issue_num_str}")
                                except Exception as ue:
                                    logger.warning(f"Could not append close_block to existing PR #{pr_number}: {ue}")
                    except Exception as fe:
                        logger.warning(f"Could not fetch existing PR: {fe}")
                else:
                    raise

            return {
                "pr_url": pr_url,
                "html_url": pr_url,
                "pr_number": pr_number,
                "branch": branch,
                "files_committed": files_committed,
            }

        except Exception as e:
            logger.error(f"[PR] create_fix_pr failed: {e}")
            return {"error": str(e)}



def get_mcp_bridge(mode: str = "direct") -> MCPClientBridge:
    """Get a configured MCP client bridge."""
    return MCPClientBridge(mode=mode)
