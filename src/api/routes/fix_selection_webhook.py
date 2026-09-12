"""
Fix Selection Webhook for Amaze on Work.

Handles interactive fix selection from Slack via the #fix-selection channel.
Users can reply with `!fix <number> <INC-ID>` to:
  - Select a different fix candidate for an incident
  - Close the existing PR (if any) and create a new one with the selected fix
  - Confirm a fix for HIGH-risk incidents (which don't auto-create PRs)

Usage in Slack:
  !fix 2 INC-0014     → Create PR with fix candidate #2 for INC-0014
  !fix 1 INC-0014     → Create PR with fix candidate #1 (or keep existing)
"""

import json
import logging
import os
import re
import threading
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Candidate Storage ────────────────────────────────────────────────
# Persisted to disk so candidates survive server restarts.
# Path: data/fix_candidates/<INC-ID>.json

_CANDIDATES_DIR = Path("data/fix_candidates")


def store_candidates(incident_id: str, candidates: list[dict], repo_url: str = "") -> None:
    """Persist fix candidates for an incident."""
    _CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    path = _CANDIDATES_DIR / f"{incident_id}.json"
    data = {
        "incident_id": incident_id,
        "repo_url": repo_url,
        "candidates": candidates,
    }
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.info(f"[FixSelection] Stored {len(candidates)} candidates for {incident_id}")


def load_candidates(incident_id: str) -> dict:
    """Load stored candidates for an incident."""
    path = _CANDIDATES_DIR / f"{incident_id}.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


# ── Webhook Endpoint ─────────────────────────────────────────────────

FIX_SELECTION_CHANNEL = os.getenv("SLACK_FIX_SELECTION_CHANNEL_ID", "")


@router.post("/api/webhooks/fix-selection")
async def fix_selection_webhook(request: Request):
    """Handle fix selection commands from the #fix-selection Slack channel.

    Expects Slack Events API format (same as slack_webhook).
    Listens for messages matching: !fix <number> <INC-ID>
    """
    body = await request.json()

    # Slack URL verification challenge
    if body.get("type") == "url_verification":
        return JSONResponse({"challenge": body.get("challenge", "")})

    event = body.get("event", {})
    if event.get("type") != "message" or event.get("subtype"):
        return JSONResponse({"status": "ignored"})

    text = event.get("text", "").strip()
    channel = event.get("channel", "")
    thread_ts = event.get("thread_ts") or event.get("ts", "")
    user = event.get("user", "")

    # Parse !fix command
    match = re.match(r"!fix\s+(\d+)\s+(INC-\d+)", text, re.IGNORECASE)
    if not match:
        return JSONResponse({"status": "ignored", "reason": "not a fix command"})

    fix_number = int(match.group(1))
    incident_id = match.group(2).upper()

    logger.info(
        f"[FixSelection] User {user} selected fix #{fix_number} for {incident_id}"
    )

    # Run in background to respond quickly
    def _process_selection():
        try:
            _handle_fix_selection(
                incident_id=incident_id,
                fix_number=fix_number,
                channel=channel,
                thread_ts=thread_ts,
                user=user,
            )
        except Exception as e:
            logger.error(f"[FixSelection] Failed: {e}")
            try:
                from src.mcp.client_bridge import ClientBridge
                bridge = ClientBridge()
                bridge.post_slack_message(
                    channel=channel,
                    text=f"❌ Failed to process fix selection: {str(e)[:200]}",
                    thread_ts=thread_ts,
                )
            except Exception:
                pass

    thread = threading.Thread(target=_process_selection, daemon=True)
    thread.start()

    return JSONResponse({"status": "processing", "incident": incident_id, "fix": fix_number})


def _handle_fix_selection(
    incident_id: str,
    fix_number: int,
    channel: str,
    thread_ts: str,
    user: str,
) -> None:
    """Process a fix selection: close old PR → create new PR with selected fix."""
    from src.mcp.client_bridge import ClientBridge

    bridge = ClientBridge()

    # Load stored candidates
    data = load_candidates(incident_id)
    if not data:
        bridge.post_slack_message(
            channel=channel,
            text=f"❌ No fix candidates found for *{incident_id}*. The incident may have expired.",
            thread_ts=thread_ts,
        )
        return

    candidates = data.get("candidates", [])
    repo_url = data.get("repo_url", "")

    if fix_number < 1 or fix_number > len(candidates):
        bridge.post_slack_message(
            channel=channel,
            text=(
                f"❌ Invalid fix number *{fix_number}* for {incident_id}. "
                f"Available: 1–{len(candidates)}"
            ),
            thread_ts=thread_ts,
        )
        return

    selected = candidates[fix_number - 1]
    fix_plan = selected.get("fix_plan", {})
    cid = selected.get("candidate_id", f"c{fix_number}")

    # Acknowledge
    bridge.post_slack_message(
        channel=channel,
        text=(
            f"⏳ Processing fix selection: *Fix #{fix_number}* ({cid}) "
            f"for *{incident_id}*..."
        ),
        thread_ts=thread_ts,
    )

    # Parse repo URL
    if not repo_url:
        bridge.post_slack_message(
            channel=channel,
            text=f"❌ No repo URL found for {incident_id}.",
            thread_ts=thread_ts,
        )
        return

    parts = repo_url.rstrip("/").split("/")
    owner, repo = parts[-2], parts[-1]
    if repo.endswith(".git"):
        repo = repo[:-4]

    # Try to close existing PR for this incident
    _close_existing_pr(bridge, owner, repo, incident_id)

    # Create new PR with selected fix
    try:
        pr_result = bridge.create_fix_pr(
            owner=owner,
            repo=repo,
            incident_id=incident_id,
            fix_plan=fix_plan,
            report_body=(
                f"# {incident_id} — Fix #{fix_number} ({cid})\n\n"
                f"Selected by <@{user}> from fix options.\n\n"
                f"**Description:** {fix_plan.get('description', 'N/A')}\n\n"
                f"**Strategy:** {cid}\n"
            ),
        )
        pr_url = pr_result.get("pr_url") or pr_result.get("html_url") or ""

        bridge.post_slack_message(
            channel=channel,
            text=(
                f"✅ *{incident_id}* — PR created with Fix #{fix_number} ({cid})!\n"
                f"🔗 {pr_url}\n"
                f"_Selected by <@{user}>_"
            ),
            thread_ts=thread_ts,
        )
        logger.info(f"[FixSelection] PR created for {incident_id} fix #{fix_number}: {pr_url}")

    except Exception as e:
        bridge.post_slack_message(
            channel=channel,
            text=f"❌ Failed to create PR for {incident_id}: {str(e)[:200]}",
            thread_ts=thread_ts,
        )
        logger.error(f"[FixSelection] PR creation failed: {e}")


def _close_existing_pr(bridge, owner: str, repo: str, incident_id: str) -> None:
    """Try to close any existing PR for this incident (best-effort).

    Searches open PRs with the incident branch naming convention:
    Amaze on Work/<incident_id>-*
    """
    try:
        import httpx

        token = os.getenv("GITHUB_TOKEN", "")
        if not token:
            return

        headers = {
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github.v3+json",
        }

        # List open PRs
        resp = httpx.get(
            f"https://api.github.com/repos/{owner}/{repo}/pulls",
            headers=headers,
            params={"state": "open", "per_page": 20},
            timeout=15,
        )

        if resp.status_code != 200:
            return

        for pr in resp.json():
            head_ref = pr.get("head", {}).get("ref", "")
            if incident_id.lower() in head_ref.lower():
                pr_number = pr.get("number")
                # Close it
                close_resp = httpx.patch(
                    f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}",
                    headers=headers,
                    json={"state": "closed"},
                    timeout=15,
                )
                if close_resp.status_code == 200:
                    logger.info(
                        f"[FixSelection] Closed old PR #{pr_number} for {incident_id}"
                    )

    except Exception as e:
        logger.warning(f"[FixSelection] Could not close existing PR: {e}")
