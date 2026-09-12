"""
Slack MCP Tools for Amaze on Work.

Provides Slack API access through the MCP protocol.
Handles message reading, posting resolutions, and DMs.
"""

import logging
import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)


class SlackMCPTools:
    """
    Slack tools exposed via MCP protocol.

    Provides message reading and posting for incident workflows.
    """

    def __init__(self, bot_token: Optional[str] = None):
        self._token = bot_token or os.getenv("SLACK_BOT_TOKEN", "")
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from slack_sdk import WebClient
            self._client = WebClient(token=self._token)
        return self._client

    def is_configured(self) -> bool:
        """Check if Slack is properly configured."""
        return bool(self._token)

    # ─── Message Operations ───────────────────────────────────────

    def read_message(self, channel: str, ts: str) -> dict:
        """Fetch a specific Slack message by channel and timestamp."""
        try:
            result = self.client.conversations_history(
                channel=channel,
                latest=ts,
                inclusive=True,
                limit=1,
            )
            messages = result.get("messages", [])
            if messages:
                msg = messages[0]
                return {
                    "text": msg.get("text", ""),
                    "user": msg.get("user", ""),
                    "ts": msg.get("ts", ""),
                    "thread_ts": msg.get("thread_ts", ""),
                    "channel": channel,
                }
            return {}
        except Exception as e:
            logger.error(f"Failed to read Slack message: {e}")
            return {"error": str(e)}

    def post_message(
        self,
        channel: str,
        text: str,
        thread_ts: Optional[str] = None,
        blocks: Optional[list] = None,
    ) -> dict:
        """Post a message to a Slack channel."""
        try:
            kwargs = {"channel": channel, "text": text}
            if thread_ts:
                kwargs["thread_ts"] = thread_ts
            if blocks:
                kwargs["blocks"] = blocks

            result = self.client.chat_postMessage(**kwargs)
            return {
                "ok": result.get("ok", False),
                "ts": result.get("ts", ""),
                "channel": result.get("channel", ""),
            }
        except Exception as e:
            logger.error(f"Failed to post Slack message: {e}")
            return {"ok": False, "error": str(e)}

    def post_dm(self, user_id: str, text: str) -> dict:
        """Send a direct message to a user."""
        try:
            # Open DM channel
            dm = self.client.conversations_open(users=[user_id])
            channel = dm.get("channel", {}).get("id", "")
            if not channel:
                return {"ok": False, "error": "Could not open DM channel"}

            return self.post_message(channel, text)
        except Exception as e:
            logger.error(f"Failed to send DM: {e}")
            return {"ok": False, "error": str(e)}

    # ─── Resolution Notifications ─────────────────────────────────

    def post_resolution(
        self,
        channel: str,
        incident_id: str,
        title: str,
        root_cause: str,
        verdict: str,
        confidence: float,
        risk_level: str,
        pr_url: str = "",
        thread_ts: Optional[str] = None,
    ) -> dict:
        """Post a formatted resolution notification."""
        risk_emoji = {"LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴"}.get(risk_level, "⚪")
        verdict_emoji = {"FIX_CONFIRMED": "✅", "REGRESSION_DETECTED": "❌", "NO_CHANGE": "⚠️"}.get(verdict, "❓")

        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"🔧 {incident_id} — Resolution"}
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Incident:*\n{title}"},
                    {"type": "mrkdwn", "text": f"*Verdict:*\n{verdict_emoji} {verdict}"},
                    {"type": "mrkdwn", "text": f"*Confidence:*\n{confidence:.0%}"},
                    {"type": "mrkdwn", "text": f"*Risk:*\n{risk_emoji} {risk_level}"},
                ]
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Root Cause:*\n{root_cause[:300]}"}
            },
        ]

        if pr_url:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Pull Request:* <{pr_url}|View PR>"}
            })

        text = f"Resolution for {incident_id}: {verdict} ({confidence:.0%} confidence)"
        return self.post_message(channel, text, thread_ts=thread_ts, blocks=blocks)

    def post_incident_started(
        self,
        channel: str,
        incident_id: str,
        title: str,
        thread_ts: Optional[str] = None,
    ) -> dict:
        """Notify that Amaze on Work has started working on an incident."""
        text = f"🤖 Amaze on Work is analyzing incident *{incident_id}*: _{title}_"
        return self.post_message(channel, text, thread_ts=thread_ts)


def get_slack_tools(token: Optional[str] = None) -> SlackMCPTools:
    """Get a configured Slack MCP tools instance."""
    return SlackMCPTools(token)
