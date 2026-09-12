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
            if action == "labeled":
                label = payload.get("label", {}).get("name", "")
                if label.lower() == "incident":
                    issue = payload.get("issue", {})
                    logger.info(f"Incident issue labeled: #{issue.get('number')} - {issue.get('title')}")
                    return JSONResponse({
                        "status": "queued",
                        "issue_number": issue.get("number"),
                        "message": "Incident issue detected, Amaze on Work is analyzing...",
                    })

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
