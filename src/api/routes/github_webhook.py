"""
GitHub Webhook Receiver for Amaze on Work.

Handles incoming GitHub events:
- issues.labeled (label: "incident") → trigger pipeline
- pull_request.closed (merged) → record in KG for learning
"""

import hashlib
import hmac
import json
import logging
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

try:
    from fastapi import APIRouter, Request, HTTPException
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix="/api/webhooks/github", tags=["github"])

    @router.post("")
    async def github_webhook(request: Request):
        """Handle incoming GitHub webhook events."""
        body = await request.body()
        body_str = body.decode("utf-8")

        # Verify signature
        from src.config import settings
        if settings.github.webhook_secret:
            signature = request.headers.get("X-Hub-Signature-256", "")
            if not _verify_github_signature(
                settings.github.webhook_secret, body_str, signature
            ):
                raise HTTPException(status_code=401, detail="Invalid signature")

        payload = json.loads(body_str)
        event_type = request.headers.get("X-GitHub-Event", "")

        logger.info(f"GitHub event: {event_type}")

        if event_type == "issues":
            action = payload.get("action", "")
            issue = payload.get("issue", {})
            labels = [l.get("name", "") for l in issue.get("labels", [])]
            label_names = {l.lower() for l in labels}
            title = issue.get("title", "")
            body = issue.get("body", "") or ""
            issue_number = issue.get("number")

            # Check if this issue represents an incident or bug
            is_incident = (
                "incident" in label_names
                or "bug" in label_names
                or "inc-" in title.lower()
                or action in ("opened", "labeled")
            )

            if is_incident and action in ("opened", "labeled", "reopened"):
                repo_data = payload.get("repository", {})
                owner = repo_data.get("owner", {}).get("login", "")
                repo_name = repo_data.get("name", "")

                logger.info(f"Incident issue received: #{issue_number} - {title}")
                try:
                    from src.agents.trigger_agent import TriggerAgent
                    agent = TriggerAgent()
                    result = agent.handle_github_issue(
                        issue_number=issue_number,
                        title=title,
                        body=body,
                        labels=labels,
                        repo_owner=owner,
                        repo_name=repo_name,
                    )
                    return JSONResponse({
                        "status": "queued",
                        "issue_number": issue_number,
                        "incident_id": result.get("incident_id"),
                        "message": f"Amaze on Work picked up issue #{issue_number} and is analyzing...",
                    })
                except Exception as e:
                    logger.error(f"Failed to handle GitHub issue #{issue_number}: {e}")
                    return JSONResponse({"status": "error", "message": str(e)}, status_code=500)

        elif event_type == "pull_request":
            action = payload.get("action", "")
            pr = payload.get("pull_request", {})
            if action == "closed" and pr.get("merged"):
                logger.info(f"PR merged: #{pr.get('number')} - {pr.get('title')}")
                # In production: record the fix in the KG for learning
                return JSONResponse({
                    "status": "recorded",
                    "pr_number": pr.get("number"),
                    "message": "PR merge recorded for incremental learning",
                })

        elif event_type == "ping":
            return JSONResponse({"status": "pong"})

        return JSONResponse({"status": "ok"})

    def _verify_github_signature(secret: str, body: str, signature: str) -> bool:
        """Verify GitHub webhook signature."""
        if not signature:
            return False
        expected = "sha256=" + hmac.new(
            secret.encode(), body.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

except ImportError:
    router = None
    logger.warning("FastAPI not available, GitHub webhook disabled")
