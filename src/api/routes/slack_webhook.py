import hashlib
import hmac
import json
import logging
import os
import time
import threading
from collections import OrderedDict

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

INCIDENTS_CHANNEL_NAME = os.getenv("SLACK_INCIDENTS_CHANNEL", "incidents")

# --- Deduplication cache for Slack event_ids ---
# Slack retries if server doesn't respond within 3s, causing duplicate incidents.
# We keep a sliding window of recent event_ids to reject retries.
_seen_events: OrderedDict = OrderedDict()
_SEEN_EVENTS_MAX = 200
_SEEN_EVENTS_TTL = 300  # 5 minutes

def _is_duplicate_event(event_id: str) -> bool:
    """Return True if we've already processed this event_id."""
    now = time.time()
    # Evict expired entries
    while _seen_events and next(iter(_seen_events.values())) < now - _SEEN_EVENTS_TTL:
        _seen_events.popitem(last=False)
    # Check for duplicate
    if event_id in _seen_events:
        return True
    _seen_events[event_id] = now
    # Cap size
    while len(_seen_events) > _SEEN_EVENTS_MAX:
        _seen_events.popitem(last=False)
    return False

try:
    from fastapi import APIRouter, Request, HTTPException
    from fastapi.responses import JSONResponse

    router = APIRouter(prefix="/api/webhooks/slack", tags=["slack"])

    @router.post("")
    async def slack_webhook(request: Request):
        """Handle incoming Slack events."""
        body = await request.body()
        body_str = body.decode("utf-8")

        # Verify Slack signature
        signing_secret = os.getenv("SLACK_SIGNING_SECRET", "")
        if signing_secret:
            timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
            signature = request.headers.get("X-Slack-Signature", "")
            if not _verify_slack_signature(signing_secret, timestamp, body_str, signature):
                raise HTTPException(status_code=401, detail="Invalid Slack signature")

        payload = json.loads(body_str)

        # Slack URL verification handshake
        if payload.get("type") == "url_verification":
            return JSONResponse({"challenge": payload.get("challenge", "")})

        # --- Deduplication: reject retries ---
        event_id = payload.get("event_id", "")
        if event_id and _is_duplicate_event(event_id):
            logger.info(f"Duplicate Slack event_id={event_id} — ignoring retry")
            return JSONResponse({"status": "duplicate_ignored"})

        event = payload.get("event", {})
        event_type = event.get("type", "")

        # Only handle human messages (not bot messages or edits)
        if event_type != "message":
            return JSONResponse({"status": "ignored"})

        if event.get("bot_id") or event.get("subtype"):
            return JSONResponse({"status": "ignored", "reason": "bot or edited message"})

        channel = event.get("channel", "")
        text = event.get("text", "").strip()
        thread_ts = event.get("thread_ts", event.get("ts", ""))
        user = event.get("user", "")

        logger.info(f"Slack message in channel={channel}: {text[:80]}")

        # Only trigger from the #incidents channel
        incidents_channel_id = os.getenv("SLACK_INCIDENTS_CHANNEL_ID", "")
        if incidents_channel_id and channel != incidents_channel_id:
            return JSONResponse({"status": "ignored", "reason": "wrong channel"})

        if not text:
            return JSONResponse({"status": "ignored", "reason": "empty message"})

        # Respond to Slack IMMEDIATELY (within 100ms) to prevent retries,
        # then run the pipeline in a background thread
        def _run_pipeline():
            try:
                from src.agents.trigger_agent import TriggerAgent
                agent = TriggerAgent()
                agent.handle_slack_incident(
                    text=text,
                    channel=channel,
                    thread_ts=thread_ts,
                    user=user,
                )
            except Exception as e:
                logger.error(f"TriggerAgent background pipeline failed: {e}")

        thread = threading.Thread(target=_run_pipeline, daemon=True)
        thread.start()

        return JSONResponse({"status": "queued", "event_id": event_id})


    def _verify_slack_signature(
        signing_secret: str, timestamp: str, body: str, signature: str
    ) -> bool:
        """Verify Slack request signature to prevent spoofing."""
        if not timestamp or not signature:
            return False
        # Reject requests older than 5 minutes
        try:
            if abs(time.time() - float(timestamp)) > 300:
                return False
        except ValueError:
            return False

        sig_basestring = f"v0:{timestamp}:{body}"
        computed = "v0=" + hmac.new(
            signing_secret.encode(),
            sig_basestring.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(computed, signature)

except ImportError:
    router = None
    logger.warning("FastAPI not available, Slack webhook disabled")
