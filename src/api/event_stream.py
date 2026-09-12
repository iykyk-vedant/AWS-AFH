"""
Amaze on Work — Real-Time Event Stream & Triage State Store.
Thread-safe in-memory store tracking incoming GitHub issues, pipeline stages,
and live resolution progress for the Mission Control Dashboard.
"""

import time
from typing import Dict, Any, List, Optional
import threading

_lock = threading.Lock()

_ACTIVE_EVENTS: List[Dict[str, Any]] = []
_CURRENT_EVENT: Optional[Dict[str, Any]] = None


def record_event_start(
    incident_id: str,
    issue_number: int,
    title: str,
    body: str = "",
    repo: str = "iykyk-vedant/AFH-DEMO"
) -> Dict[str, Any]:
    """Register a new incoming incident issue from GitHub webhook."""
    global _CURRENT_EVENT
    now_str = time.strftime("%H:%M:%S")
    event = {
        "id": incident_id,
        "issue_number": issue_number,
        "title": title,
        "body": body[:300],
        "repo": repo,
        "stage": 1,
        "stage_name": "Incident Parser",
        "status": "TRIAGING",
        "progress_pct": 15,
        "logs": [f"[{now_str}] Webhook received: GitHub Issue #{issue_number} detected ({title})"],
        "pr_url": None,
        "diff": None,
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    with _lock:
        _CURRENT_EVENT = event
        _ACTIVE_EVENTS.insert(0, event)
        if len(_ACTIVE_EVENTS) > 20:
            _ACTIVE_EVENTS.pop()
    return event


def update_event_stage(
    incident_id: str,
    stage: int,
    stage_name: str,
    log_msg: str,
    status: str = "TRIAGING",
    pr_url: Optional[str] = None,
    diff: Optional[str] = None
) -> None:
    """Update progress stage and logs for an active incident."""
    global _CURRENT_EVENT
    now_str = time.strftime("%H:%M:%S")
    with _lock:
        target = _CURRENT_EVENT if (_CURRENT_EVENT and _CURRENT_EVENT.get("id") == incident_id) else None
        if not target:
            for ev in _ACTIVE_EVENTS:
                if ev.get("id") == incident_id:
                    target = ev
                    break

        if target:
            target["stage"] = stage
            target["stage_name"] = stage_name
            target["status"] = status
            target["progress_pct"] = int((stage / 7.0) * 100)
            target["logs"].append(f"[{now_str}] {log_msg}")
            target["updated_at"] = time.time()
            if pr_url:
                target["pr_url"] = pr_url
            if diff:
                target["diff"] = diff
            if status == "RESOLVED":
                _CURRENT_EVENT = target


def get_current_event_state() -> Dict[str, Any]:
    """Return current active event and history for dashboard polling."""
    with _lock:
        return {
            "active": _CURRENT_EVENT,
            "recent": list(_ACTIVE_EVENTS[:10]),
        }
