"""
Jira Webhook Receiver for Amaze on Work.

Handles Jira webhook events (issue_created, issue_updated) and triggers
the incident resolution pipeline via TriggerAgent.

Configure in Jira:
  Settings -> System -> Webhooks -> Create webhook
  URL: https://<your-server>/api/webhooks/jira
  Events: "Issue: created", "Issue: updated"
"""

import json
import logging

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

INCIDENT_LABELS = {"incident", "bug", "Amaze on Work", "outage", "critical"}

try:
    from fastapi import APIRouter, Request
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix="/api/webhooks/jira", tags=["jira"])

    @router.post("")
    async def jira_webhook(request: Request):
        """Handle incoming Jira webhook events."""
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)

        event = payload.get("webhookEvent", "")
        issue = payload.get("issue", {})
        fields = issue.get("fields", {})

        # Only handle issue created/updated
        if event not in ("jira:issue_created", "jira:issue_updated"):
            return JSONResponse({"status": "ignored", "event": event})

        ticket_id = issue.get("key", "")
        summary = fields.get("summary", "")
        description_raw = fields.get("description") or ""
        # description may be ADF — extract plain text
        description = _extract_description(description_raw)

        priority = (fields.get("priority") or {}).get("name", "Medium")
        labels = fields.get("labels", [])
        status_name = (fields.get("status") or {}).get("name", "")
        issue_type = (fields.get("issuetype") or {}).get("name", "")

        logger.info(
            f"Jira webhook: {event} | ticket={ticket_id} | "
            f"status={status_name} | type={issue_type}"
        )

        # Filter: only take action if it's an incident/bug type AND not already resolved
        label_set = {l.lower() for l in labels}
        is_incident = (
            bool(label_set & INCIDENT_LABELS)
            or issue_type.lower() in ("bug", "incident")
        )
        is_resolved = status_name.lower() in ("done", "resolved", "closed")

        if not is_incident:
            return JSONResponse({"status": "ignored", "reason": "not an incident"})

        if is_resolved:
            return JSONResponse({"status": "ignored", "reason": "already resolved"})

        if not ticket_id or not summary:
            return JSONResponse({"status": "error", "message": "Missing ticket_id or summary"})

        # Trigger pipeline
        try:
            from src.agents.trigger_agent import TriggerAgent
            agent = TriggerAgent()
            result = agent.handle_jira_incident(
                ticket_id=ticket_id,
                summary=summary,
                description=description,
                priority=priority,
                labels=labels,
            )
            return JSONResponse({"status": "queued", **result})
        except Exception as e:
            logger.error(f"TriggerAgent failed for {ticket_id}: {e}")
            return JSONResponse(
                {"status": "error", "message": str(e)},
                status_code=500,
            )

    def _extract_description(raw) -> str:
        """Extract plain text from ADF description or return raw string."""
        if not raw:
            return ""
        if isinstance(raw, str):
            return raw
        # Walk ADF nodes
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
        _walk(raw)
        return " ".join(texts)

except ImportError:
    router = None
    logger.warning("FastAPI not available, Jira webhook disabled")
