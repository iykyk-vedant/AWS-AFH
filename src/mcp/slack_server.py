"""
Slack MCP Server for Amaze on Work.

Exposes Slack tools as an MCP server using the FastMCP SDK.
Can be run standalone or imported into the main application.

Usage (standalone):
    python -m src.mcp.slack_server

Usage (from code):
    from src.mcp.slack_server import mcp as slack_mcp
"""

import os
import json
import logging
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

load_dotenv()
logger = logging.getLogger(__name__)

# ─── MCP Server Instance ──────────────────────────────────────────
mcp = FastMCP(
    "Amaze on Work-Slack",
    instructions="Slack integration tools for Amaze on Work incident notifications",
)

BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
REPORTS_CHANNEL_ID = os.getenv("SLACK_REPORTS_CHANNEL_ID", "")


def _get_client():
    """Lazy-init the Slack WebClient."""
    from slack_sdk import WebClient
    return WebClient(token=BOT_TOKEN)


# ─── MCP Tools ─────────────────────────────────────────────────────


@mcp.tool()
def post_message(channel: str, text: str, thread_ts: str = "") -> str:
    """Post a message to a Slack channel.
    Optionally reply in a thread by providing thread_ts.
    """
    client = _get_client()
    kwargs = {"channel": channel, "text": text}
    if thread_ts:
        kwargs["thread_ts"] = thread_ts

    result = client.chat_postMessage(**kwargs)
    return json.dumps({
        "ok": result.get("ok", False),
        "ts": result.get("ts", ""),
        "channel": result.get("channel", ""),
    })


@mcp.tool()
def read_message(channel: str, ts: str) -> str:
    """Read a specific Slack message by channel and timestamp."""
    client = _get_client()
    result = client.conversations_history(
        channel=channel, latest=ts, inclusive=True, limit=1
    )
    messages = result.get("messages", [])
    if messages:
        msg = messages[0]
        return json.dumps({
            "text": msg.get("text", ""),
            "user": msg.get("user", ""),
            "ts": msg.get("ts", ""),
            "thread_ts": msg.get("thread_ts", ""),
        })
    return json.dumps({"error": "Message not found"})


@mcp.tool()
def send_dm(user_id: str, text: str) -> str:
    """Send a direct message to a Slack user."""
    client = _get_client()
    dm = client.conversations_open(users=[user_id])
    channel_id = dm.get("channel", {}).get("id", "")
    if not channel_id:
        return json.dumps({"ok": False, "error": "Could not open DM channel"})

    result = client.chat_postMessage(channel=channel_id, text=text)
    return json.dumps({
        "ok": result.get("ok", False),
        "ts": result.get("ts", ""),
        "channel": channel_id,
    })


@mcp.tool()
def post_incident_started(channel: str, incident_id: str, title: str, thread_ts: str = "") -> str:
    """Notify a channel that Amaze on Work has started working on an incident."""
    text = f"[Amaze on Work] Analyzing incident *{incident_id}*: _{title}_"
    return post_message(channel, text, thread_ts)


@mcp.tool()
def post_progress_update(channel: str, thread_ts: str, status_msg: str, incident_id: str = "") -> str:
    """
    Post a live agent progress update to a Slack thread.

    Called by the supervisor at each pipeline stage transition.
    All updates appear in the same thread as the original incident message
    so the entire Amaze on Work story is visible in one place.

    Args:
        channel: Slack channel ID (e.g., 'C0AL8NG5J79').
        thread_ts: Timestamp of the original incident message (thread parent).
        status_msg: Human-readable status string (e.g., '[Status] Codebase Analyst querying GraphRAG...').
        incident_id: Optional INC-XXXX prefix for clarity.

    Returns:
        JSON with ok, ts, channel.
    """
    prefix = f"*{incident_id}*  " if incident_id else ""
    text = f"{prefix}{status_msg}"
    return post_message(channel, text, thread_ts)


@mcp.tool()
def post_resolution(
    channel: str,
    incident_id: str,
    title: str,
    root_cause: str,
    verdict: str,
    confidence: float,
    risk_level: str,
    pr_url: str = "",
    thread_ts: str = "",
) -> str:
    """Post a formatted resolution notification to a Slack channel.
    Includes incident details, verdict, confidence, risk level, and optional PR link.
    """
    risk_tag = {"LOW": "[LOW]", "MEDIUM": "[MED]", "HIGH": "[HIGH]"}.get(risk_level, "[MED]")
    verdict_tag = {
        "FIX_CONFIRMED": "[FIXED]",
        "REGRESSION_DETECTED": "[REGRESSION]",
        "NO_CHANGE": "[NO_CHANGE]",
        "SKIPPED": "[SKIPPED]",
    }.get(verdict, "[RESOLVED]")

    client = _get_client()
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"[FIX] {incident_id} -- Resolution"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Incident:*\n{title[:200]}"},
                {"type": "mrkdwn", "text": f"*Verdict:*\n{verdict_tag} {verdict}"},
                {"type": "mrkdwn", "text": f"*Confidence:*\n{confidence:.0%}"},
                {"type": "mrkdwn", "text": f"*Risk:*\n{risk_tag} {risk_level}"},
            ]
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Root Cause:*\n{root_cause[:600]}"}
        },
    ]

    if pr_url:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Pull Request:* <{pr_url}|View PR>"}
        })

    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": f"Full report posted in <#{REPORTS_CHANNEL_ID}|reports>"
        }
    })

    fallback_text = f"Resolution for {incident_id}: {verdict} ({confidence:.0%} confidence)"

    kwargs = {"channel": channel, "text": fallback_text, "blocks": blocks}
    if thread_ts:
        kwargs["thread_ts"] = thread_ts

    result = client.chat_postMessage(**kwargs)
    return json.dumps({
        "ok": result.get("ok", False),
        "ts": result.get("ts", ""),
        "channel": result.get("channel", ""),
    })


@mcp.tool()
def post_full_report(
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
    channel: str = "",
) -> str:
    """Post the full structured resolution report to the #reports channel.

    Sends the complete report as multiple Slack blocks, split into sections
    to avoid the 3001-character block text limit.

    Args:
        incident_id: e.g. INC-0008
        title: Incident title
        root_cause: Full root cause hypothesis text
        reasoning: Full reasoning chain (numbered steps)
        changes_made: Files modified with rationale
        validation_verdict: FIX_CONFIRMED / SKIPPED / etc.
        risk_level: LOW / MEDIUM / HIGH
        confidence: 0.0 - 1.0
        resolution_time: seconds
        pr_url: GitHub PR URL if created
        patch_diff: Unified diff of the fix (optional)
        security_summary: Summary from SecurityAgent (optional)
        channel: Override reports channel (uses env default if empty)
    """
    reports_channel = channel or REPORTS_CHANNEL_ID
    client = _get_client()

    verdict_label = {
        "FIX_CONFIRMED": "[CONFIRMED]",
        "SKIPPED": "[SKIPPED - Docker unavailable]",
        "ERROR": "[ERROR]",
        "UNRELATED_FAILURE": "[NO REGRESSION]",
        "NO_CHANGE": "[NO CHANGE]",
        "REGRESSION_DETECTED": "[REGRESSION]",
    }.get(validation_verdict, f"[{validation_verdict}]")

    risk_label = {"LOW": "[LOW RISK]", "MEDIUM": "[MEDIUM RISK]", "HIGH": "[HIGH RISK]"}.get(
        risk_level, f"[{risk_level}]"
    )

    # ── Block 1: Header ──────────────────────────────────────────────
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Amaze on Work -- {incident_id} Resolution Report"}
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Incident ID:*\n`{incident_id}`"},
                {"type": "mrkdwn", "text": f"*Resolution Time:*\n{resolution_time:.1f}s"},
                {"type": "mrkdwn", "text": f"*Confidence:*\n{confidence:.0%}"},
                {"type": "mrkdwn", "text": f"*Validation:*\n{verdict_label}"},
                {"type": "mrkdwn", "text": f"*Risk:*\n{risk_label}"},
            ]
        },
        {"type": "divider"},
    ]

    # ── Block 2: Title ───────────────────────────────────────────────
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"*Incident:*\n{title[:500]}"}
    })

    # ── Block 3: Root Cause (split if long) ──────────────────────────
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": "*Root Cause Analysis*"}
    })
    # Split root_cause into 2800-char chunks to stay under 3001 limit
    rc_text = root_cause or "No root cause determined"
    for chunk_start in range(0, len(rc_text), 2800):
        chunk = rc_text[chunk_start:chunk_start + 2800]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": chunk}
        })

    # ── Block 4: Changes Made ────────────────────────────────────────
    if changes_made:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Changes Made*\n{changes_made[:2800]}"}
        })

    # ── Block 5: Reasoning Chain ─────────────────────────────────────
    if reasoning:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Reasoning Chain*\n{reasoning[:2800]}"}
        })

    # ── Block 6: Security Summary ────────────────────────────────────
    if security_summary:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Security Review*\n{security_summary[:2800]}"}
        })

    # ── Block 7: Patch Diff (if provided) ───────────────────────────
    if patch_diff:
        blocks.append({"type": "divider"})
        # Code blocks in Slack use triple-backtick in mrkdwn
        diff_preview = patch_diff[:1500]
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Patch (preview)*\n```{diff_preview}```"}
        })

    # ── Block 8: PR link ─────────────────────────────────────────────
    if pr_url:
        blocks.append({"type": "divider"})
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Pull Request:* <{pr_url}|View PR on GitHub>"}
        })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": f"Generated by Amaze on Work | {incident_id}"}
        ]
    })

    fallback_text = f"Amaze on Work Report: {incident_id} | {verdict_label} | {risk_label}"

    # Slack has a limit of 50 blocks per message — split if needed
    MAX_BLOCKS = 48
    all_results = []

    for i in range(0, len(blocks), MAX_BLOCKS):
        batch = blocks[i:i + MAX_BLOCKS]
        try:
            result = client.chat_postMessage(
                channel=reports_channel,
                text=fallback_text,
                blocks=batch,
            )
            all_results.append({
                "ok": result.get("ok", False),
                "ts": result.get("ts", ""),
                "channel": result.get("channel", ""),
            })
        except Exception as e:
            logger.error(f"Failed to post report block batch {i}: {e}")
            all_results.append({"ok": False, "error": str(e)})

    return json.dumps(all_results[0] if all_results else {"ok": False, "error": "No blocks"})


@mcp.tool()
def list_channels(limit: int = 100) -> str:
    """List public Slack channels the bot has access to."""
    client = _get_client()
    result = client.conversations_list(types="public_channel", limit=limit)
    channels = [
        {"id": c["id"], "name": c["name"]}
        for c in result.get("channels", [])
    ]
    return json.dumps(channels, indent=2)


@mcp.tool()
def get_channel_history(channel: str, limit: int = 10) -> str:
    """Get recent messages from a Slack channel."""
    client = _get_client()
    result = client.conversations_history(channel=channel, limit=limit)
    messages = [
        {
            "text": m.get("text", "")[:200],
            "user": m.get("user", ""),
            "ts": m.get("ts", ""),
        }
        for m in result.get("messages", [])
    ]
    return json.dumps(messages, indent=2)


# ─── Entry Point ──────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run()
