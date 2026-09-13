"""
Trigger Agent for Amaze on Work.

Processes incoming incidents from Slack and Jira, assigns INC-XXXX numbers,
posts immediate acknowledgements with live progress updates, and launches
the supervisor pipeline.

This is the front-door to the entire Amaze on Work system.
"""

import json
import logging
import os
import re
import threading
from src.config import GITHUB_REPO_FULL
from typing import Optional

from src.utils.incident_counter import next_incident_id, _default_counter

logger = logging.getLogger(__name__)


# Progress step messages shown live in Slack thread + Jira comments
AGENT_STATUS_MESSAGES = {
    "incident_parser":      "[Status] Incident Parser extracting context...",
    "knowledge_retriever":  "[Status] Knowledge Retriever checking past incidents...",
    "codebase_analyst":     "[Status] Codebase Analyst querying GraphRAG for root cause...",
    "critic":               "[Status] Critic reviewing root cause analysis...",
    "fix_writer":           "[Status] Fix Planner & Patch Writer generating fix...",
    "validation":           "[Status] Validation Agent running sandbox tests...",
    "web_research":         "[Status] Web Research Agent searching StackOverflow for community fixes...",
    "synthesis":            "[Status] Synthesis Agent assembling resolution report...",
    "risk_scorer":          "[Status] Risk Scorer evaluating fix safety...",
    "done":                 "[Done] Amaze on Work pipeline complete.",
}


class TriggerAgent:
    """
    Listens for incident from Slack or Jira and kicks off the pipeline.

    Usage:
        agent = TriggerAgent()
        agent.handle_slack_incident(text, channel, thread_ts)
        agent.handle_jira_incident(ticket_id, summary, description)

    The target repo can be configured via:
      1. DEFAULT_REPO_URL env var (system default)
      2. Constructor argument: TriggerAgent(repo_url="https://...")
      3. Inline in Slack message: "repo:owner/name" or a full GitHub URL
      4. Explicit kwarg: handle_slack_incident(..., repo_url="https://...")
    """

    DEFAULT_REPO = os.getenv(
        "DEFAULT_REPO_URL",
        "https://github.com/Rezinix-AI/shopstack-platform",
    )

    def __init__(self, repo_url: str = ""):
        self.repo_url = repo_url or self.DEFAULT_REPO
        self._bridge = None
        self._supervisor = None

    @property
    def bridge(self):
        if self._bridge is None:
            from src.mcp.client_bridge import get_mcp_bridge
            self._bridge = get_mcp_bridge()
        return self._bridge

    @property
    def supervisor(self):
        if self._supervisor is None:
            from src.agents.supervisor import SupervisorAgent
            self._supervisor = SupervisorAgent()
        return self._supervisor

    # ── Slack Entrypoint ────────────────────────────────────────

    def handle_slack_incident(
        self,
        text: str,
        channel: str,
        thread_ts: str = "",
        user: str = "",
        async_run: bool = False,
        repo_url: str = "",
    ) -> dict:
        """
        Handle an incident message posted to the #incidents Slack channel.

        Steps:
        1. Assign INC-XXXX
        2. Post acknowledgement in thread
        3. Parse NL text into IncidentContext via LLM
        4. Run pipeline (synchronously if called from webhook background thread,
           or in a new thread if async_run=True for CLI/test use)

        Args:
            async_run: If True, spawns a background thread for the pipeline.
            repo_url:  Override target repo for this incident.
                       Also auto-detected from inline "repo:owner/name" in text.
        """
        # ── Detect repo URL from message text ("repo:owner/name" or full URL) ──
        target_repo, clean_text = self._extract_repo_from_text(text)
        if repo_url:
            target_repo = repo_url  # explicit kwarg wins
        elif not target_repo:
            target_repo = self.repo_url  # instance default

        inc_id = next_incident_id(metadata={
            "source": "slack",
            "channel": channel,
            "thread_ts": thread_ts,
            "text": clean_text[:500],
            "user": user,
            "repo_url": target_repo,
        })
        logger.info(f"[TriggerAgent] Slack incident received -> {inc_id} (repo: {target_repo})")

        # Post immediate ack in the same thread
        ack_text = (
            f"*{inc_id} assigned* | Analyzing your incident now...\n"
            f"_Amaze on Work is on it. Live updates follow._"
        )
        try:
            self.bridge.post_slack_message(channel, ack_text, thread_ts)
        except Exception as e:
            logger.warning(f"Could not post Slack ack: {e}")

        # Parse the natural language incident text (use cleaned text without repo: prefix)
        incident = self._parse_slack_text(inc_id, clean_text)

        # Persist slack context on the counter record
        _default_counter.record(inc_id, "slack_channel", channel)
        _default_counter.record(inc_id, "slack_thread_ts", thread_ts)

        def _run():
            try:
                self.supervisor.resolve_incident(
                    incident=incident,
                    repo_url=target_repo,
                    slack_channel=channel,
                    slack_thread_ts=thread_ts,
                    jira_ticket_id=None,
                )
            except Exception as e:
                logger.error(f"[TriggerAgent] Pipeline failed for {inc_id}: {e}")
                try:
                    self.bridge.post_slack_message(
                        channel,
                        f"*{inc_id}* pipeline error: {str(e)[:200]}",
                        thread_ts,
                    )
                except Exception:
                    pass

        if async_run:
            # CLI/test mode: spawn background thread
            t = threading.Thread(target=_run, daemon=True, name=f"pipeline-{inc_id}")
            t.start()
        else:
            # Webhook mode: already in a background thread, run synchronously
            _run()

        return {"incident_id": inc_id, "status": "queued"}


    # ── Jira Entrypoint ─────────────────────────────────────────

    def handle_jira_incident(
        self,
        ticket_id: str,
        summary: str,
        description: str,
        priority: str = "Medium",
        labels: list | None = None,
    ) -> dict:
        """
        Handle a new Jira ticket (from jira_webhook.py).

        Steps:
        1. Assign INC-XXXX
        2. Transition ticket: Open -> In Progress
        3. Post acknowledgement comment on the Jira ticket
        4. Launch pipeline in background thread
        """
        inc_id = next_incident_id(metadata={
            "source": "jira",
            "ticket_id": ticket_id,
            "summary": summary,
        })
        logger.info(f"[TriggerAgent] Jira incident received: {ticket_id} -> {inc_id}")

        # Transition to In Progress
        try:
            from src.mcp.jira_server import jira_transition_status, jira_comment
            jira_transition_status(ticket_id, "In Progress")
            jira_comment(
                ticket_id,
                f"*{inc_id} assigned* | Amaze on Work has picked up this incident.\n"
                f"Live analysis updates will follow.",
            )
        except Exception as e:
            logger.warning(f"Could not update Jira ticket {ticket_id}: {e}")

        incident = self._parse_jira_ticket(inc_id, summary, description, priority, labels or [])

        _default_counter.record(inc_id, "jira_ticket_id", ticket_id)

        def _run():
            try:
                self.supervisor.resolve_incident(
                    incident=incident,
                    repo_url=self.repo_url,
                    slack_channel=None,
                    slack_thread_ts=None,
                    jira_ticket_id=ticket_id,
                )
            except Exception as e:
                logger.error(f"[TriggerAgent] Pipeline failed for {inc_id}: {e}")
                try:
                    jira_comment(ticket_id, f"*{inc_id}* pipeline error: {str(e)[:200]}")
                except Exception:
                    pass

        t = threading.Thread(target=_run, daemon=True, name=f"pipeline-{inc_id}")
        t.start()

        return {"incident_id": inc_id, "jira_ticket_id": ticket_id, "status": "queued"}

    def handle_github_issue(
        self,
        issue_number: int,
        title: str,
        body: str,
        labels: list | None = None,
        repo_owner: str = "",
        repo_name: str = "",
    ) -> dict:
        """
        Handle a new GitHub Issue (from github_webhook.py).

        Steps:
        1. Assign INC-XXXX (or match INC-00X from title)
        2. Parse issue title and body into IncidentContext
        3. Launch pipeline in background thread
        """
        target_repo = f"https://github.com/{repo_owner}/{repo_name}" if repo_owner and repo_name else self.repo_url
        inc_id = next_incident_id(metadata={
            "source": "github_issue",
            "issue_number": issue_number,
            "title": title,
            "repo": f"{repo_owner}/{repo_name}" if repo_owner and repo_name else self.repo_url,
        })
        logger.info(f"[TriggerAgent] GitHub issue received: #{issue_number} ({title}) -> {inc_id}")

        # Check for preset ID like INC-001 in title or match known incident scenario
        match = re.search(r'(INC-\d+)', title, re.IGNORECASE)
        preset_id = match.group(1).upper() if match else None
        if not preset_id:
            from pathlib import Path
            inc_dir = Path("incidents")
            if inc_dir.exists():
                for inc_file in inc_dir.glob("*.json"):
                    try:
                        inc_data = json.loads(inc_file.read_text(encoding="utf-8"))
                        inc_title = inc_data.get("title", "").lower()
                        if inc_title and (inc_title in title.lower() or title.lower() in inc_title):
                            preset_id = inc_data.get("id")
                            logger.info(f"[TriggerAgent] Matched known incident scenario from title: {preset_id} ({inc_title})")
                            break
                    except Exception:
                        pass

        incident = self._parse_slack_text(inc_id, f"{title}\n\n{body}")
        incident["linked_issue_url"] = f"https://github.com/{repo_owner}/{repo_name}/issues/{issue_number}" if repo_owner and repo_name else ""
        incident["github_issue_number"] = issue_number
        final_inc_id = preset_id or inc_id
        if preset_id:
            incident["id"] = preset_id

        # Register event with live dashboard stream
        try:
            from src.api.event_stream import record_event_start
            record_event_start(
                incident_id=final_inc_id,
                issue_number=issue_number,
                title=title,
                body=body,
                repo=f"{repo_owner}/{repo_name}" if repo_owner and repo_name else GITHUB_REPO_FULL,
            )
        except Exception:
            pass

        def _run():
            try:
                self.supervisor.resolve_incident(
                    incident=incident,
                    repo_url=target_repo,
                    slack_channel=None,
                    slack_thread_ts=None,
                )
            except Exception as e:
                logger.error(f"[TriggerAgent] Pipeline failed for GitHub issue #{issue_number} ({final_inc_id}): {e}")

        t = threading.Thread(target=_run, daemon=True, name=f"pipeline-{final_inc_id}")
        t.start()

        return {"incident_id": final_inc_id, "issue_number": issue_number, "status": "queued"}

    # ── Repo URL Extraction ────────────────────────────────────

    def _extract_repo_from_text(self, text: str) -> tuple[str, str]:
        """Extract a repo URL or owner/name from the message text.

        Supported formats:
          - repo:owner/name           → https://github.com/owner/name
          - repo:https://github.com/… → as-is
          - https://github.com/owner/name (standalone in text)

        Returns:
            (repo_url, clean_text) — repo_url is "" if not found,
            clean_text has the repo reference removed.
        """
        # Pattern 1: explicit "repo:" prefix
        match = re.search(
            r'repo:(https?://github\.com/[\w.-]+/[\w.-]+)',
            text, re.IGNORECASE,
        )
        if match:
            url = match.group(1).rstrip("/")
            clean = text[:match.start()] + text[match.end():]
            return url, clean.strip()

        # Pattern 2: repo:owner/name shorthand
        match = re.search(r'repo:([\w.-]+/[\w.-]+)', text, re.IGNORECASE)
        if match:
            url = f"https://github.com/{match.group(1)}"
            clean = text[:match.start()] + text[match.end():]
            return url, clean.strip()

        # Pattern 3: standalone GitHub URL in text
        match = re.search(
            r'(https?://github\.com/[\w.-]+/[\w.-]+)',
            text, re.IGNORECASE,
        )
        if match:
            url = match.group(1).rstrip("/")
            # Don't remove it from text — it's likely context, not a directive
            return url, text

        return "", text

    # ── NL Parsing ──────────────────────────────────────────────

    def _parse_slack_text(self, inc_id: str, text: str) -> dict:
        """Parse natural language incident text into an IncidentContext dict.

        Handles all input scenarios:
          - Vague:      "checkout is broken"
          - Semi:       "We're seeing 500s on the payment service after deploy"
          - Detailed:   Full stack trace + error log pasted by engineer
          - Mixed:      Description + pasted log excerpt

        Strategy:
          1. Regex pre-extraction (stack traces, file refs, error types, service hints)
          2. LLM structured extraction with examples
          3. Smart merge: regex fills gaps LLM missed
        """
        import re

        # ── Layer 1: Regex pre-extraction (works even if LLM fails) ────────

        # Extract Python stack trace frames
        py_frames = re.findall(r'File "([^"]+)", line (\d+), in (\w+)', text)
        # Extract Node.js stack frames
        node_frames = re.findall(r'at\s+(?:(\w+)\s+)?\(([^:]+):(\d+):\d+\)', text)
        # Extract simple file:line refs
        file_line_refs = re.findall(r'(\S+\.(?:py|js|ts)):(\d+)', text)
        # Extract file path mentions (services/routes/models etc.)
        file_mentions = re.findall(
            r'(?:src/|app/|routes/|models/|services/|middleware/|utils/)[\w/]+\.(?:py|js|ts)',
            text
        )

        # Build stack_traces list from regex
        pre_stack_traces = []
        for f, line, func in py_frames:
            pre_stack_traces.append({"file": f, "line": int(line), "function": func})
        for func, f, line in node_frames:
            pre_stack_traces.append({"file": f, "line": int(line), "function": func or "anonymous"})

        # Detect error type from common patterns
        pre_error_type = "unknown"
        error_patterns = [
            (r'(?:TypeError|type\s*error)', "TypeError"),
            (r'(?:AttributeError|attribute\s*error)', "AttributeError"),
            (r'(?:KeyError|key\s*error)', "KeyError"),
            (r'(?:ImportError|ModuleNotFoundError)', "ImportError"),
            (r'(?:ValueError|value\s*error)', "ValueError"),
            (r'(?:NameError|name\s*error)', "NameError"),
            (r'(?:ZeroDivisionError)', "ZeroDivisionError"),
            (r'(?:IndexError)', "IndexError"),
            (r'(?:RuntimeError)', "RuntimeError"),
            (r'(?:ConnectionError|ECONNREFUSED)', "ConnectionError"),
            (r'(?:TimeoutError|ETIMEDOUT|timed?\s*out)', "TimeoutError"),
            (r'(?:500|Internal Server Error)', "500"),
            (r'(?:404|Not Found)', "404"),
            (r'(?:403|Forbidden)', "403"),
            (r'(?:502|Bad Gateway)', "502"),
            (r'(?:503|Service Unavailable)', "503"),
            (r'(?:SyntaxError)', "SyntaxError"),
            (r'(?:NullPointerException|null\s*pointer)', "NullPointerException"),
            (r'(?:OutOfMemoryError|OOM|memory)', "OutOfMemoryError"),
        ]
        for pattern, err_type in error_patterns:
            if re.search(pattern, text, re.IGNORECASE):
                pre_error_type = err_type
                break

        # Detect service from text
        pre_service = "python-service"  # default
        text_lower = text.lower()
        if any(kw in text_lower for kw in ["node", "express", "javascript", "npm", ".js"]):
            pre_service = "node-service"
        elif any(kw in text_lower for kw in ["python", "flask", "django", "pip", ".py", "pytest"]):
            pre_service = "python-service"
        # Also check file extensions in stack traces
        for frame in pre_stack_traces:
            f = frame.get("file", "")
            if f.endswith((".js", ".ts")):
                pre_service = "node-service"
                break
            if f.endswith(".py"):
                pre_service = "python-service"
                break

        # Classify context quality so downstream agents know confidence
        has_stack = bool(pre_stack_traces)
        has_error = pre_error_type != "unknown"
        has_files = bool(file_mentions)
        if has_stack or (has_error and has_files):
            context_quality = "rich"
        elif has_error or has_files or len(text) > 200:
            context_quality = "partial"
        else:
            context_quality = "minimal"

        logger.info(
            f"[TriggerAgent] Pre-extraction: quality={context_quality}, "
            f"error_type={pre_error_type}, service={pre_service}, "
            f"stack_frames={len(pre_stack_traces)}, file_mentions={len(file_mentions)}"
        )

        # ── Layer 2: LLM structured extraction ──────────────────────────────

        try:
            from src.llm.client_factory import get_llm_client
            llm = get_llm_client()
            prompt = f"""You are a production incident parser. Extract structured fields from a human-written incident message.

The message may range from very vague (e.g. "checkout is broken") to very detailed (full stack trace).
Your job: extract everything you can, use reasonable defaults for what's missing.

=== RULES ===
1. title: A clear one-line summary of the incident. If the message is vague, rephrase it as a specific issue.
2. description: Full incident description. If vague, keep the original text.
3. error_type: Specific error class (e.g., TypeError, 500, timeout). Use "unknown" only if truly ambiguous.
4. error_log: Copy the raw error message, log output, or stack trace VERBATIM from the message. If the message IS the error description, use the full text.
5. affected_service: Service name. Look for hints like "python", "node", "Flask", "Express", file extensions (.py vs .js). Default: "python-service".
6. environment: prod/staging/dev. Default: "production".
7. severity: LOW/MEDIUM/HIGH/CRITICAL. Gauge from words like "critical", "blocker", "minor", deployment status, user impact.
8. stack_trace: Any stack trace excerpt verbatim. Empty string if none.
9. failure_type: One of: logic_error, runtime_error, configuration_error, dependency_error, type_error, security_error, performance_issue, unknown.
10. tags: List of relevant keywords for searching the codebase (e.g., ["payment", "tax", "calculate_tax"]).
11. affected_commits: List of git commit hashes mentioned. Empty list if none.

=== EXAMPLES ===
VAGUE: "checkout is broken" →
{{"title": "Checkout flow failing", "error_type": "unknown", "severity": "HIGH", "failure_type": "unknown", "tags": ["checkout", "order"]}}

SEMI: "Getting 500 errors on payment after yesterday's deploy" →
{{"title": "500 errors on payment endpoint after deployment", "error_type": "500", "severity": "HIGH", "failure_type": "runtime_error", "tags": ["payment", "deploy", "500"]}}

DETAILED: "TypeError: calculate_tax() got unexpected keyword argument 'rate' in payment_service.py:42" →
{{"title": "TypeError in calculate_tax function", "error_type": "TypeError", "failure_type": "type_error", "tags": ["calculate_tax", "payment_service", "tax"]}}

=== MESSAGE ===
\"\"\"{text}\"\"\"

Return ONLY a valid JSON object with ALL fields listed above. No markdown fences, no explanation."""

            raw = llm.complete(prompt)
            content = raw.content if hasattr(raw, "content") else str(raw)
            # Strip markdown fences if present
            json_match = re.search(r'```(?:json)?\s*(\{[\s\S]*\})\s*```', content)
            if json_match:
                content = json_match.group(1)
            else:
                # Try to find first JSON object
                brace_match = re.search(r'\{[\s\S]*\}', content)
                if brace_match:
                    content = brace_match.group()
            parsed = json.loads(content.strip())
        except Exception as e:
            logger.warning(f"LLM parse failed, using regex-only fallback: {e}")
            parsed = {}

        # ── Layer 3: Smart merge — regex fills gaps LLM missed ──────────────

        # error_log: LLM extracted > regex stack trace > raw text
        error_log = (
            parsed.get("error_log", "")
            or parsed.get("stack_trace", "")
            or ("\n".join(
                f'File "{f["file"]}", line {f["line"]}, in {f["function"]}'
                for f in pre_stack_traces
            ) if pre_stack_traces else "")
            or text
        )

        # error_type: LLM > regex
        error_type = parsed.get("error_type", "") or pre_error_type

        # service: LLM > regex
        llm_service = parsed.get("affected_service", "")
        if llm_service and llm_service not in ("unknown", ""):
            service = llm_service
        else:
            service = pre_service

        # tags: LLM + file mentions merged
        llm_tags = parsed.get("tags", []) or []
        if isinstance(llm_tags, str):
            llm_tags = [t.strip() for t in llm_tags.split(",")]
        # Add file-based tags
        for fm in file_mentions:
            basename = fm.split("/")[-1].replace(".py", "").replace(".js", "").replace(".ts", "")
            if basename and basename not in llm_tags:
                llm_tags.append(basename)

        result = {
            "id": inc_id,
            "title": parsed.get("title", "") or text[:100],
            "description": parsed.get("description", "") or text,
            "error_type": error_type,
            "error_log": error_log,
            "affected_service": service,
            "environment": parsed.get("environment", "") or "production",
            "severity": parsed.get("severity", "") or "HIGH",
            "stack_trace": parsed.get("stack_trace", ""),
            "stack_traces": pre_stack_traces,  # structured frames for codebase_analyst
            "failure_type": parsed.get("failure_type", "") or "unknown",
            "tags": llm_tags,
            "affected_commits": parsed.get("affected_commits", []) or [],
            "context_quality": context_quality,
        }

        logger.info(
            f"[TriggerAgent] Parsed {inc_id}: title={result['title'][:60]!r}, "
            f"error_type={error_type}, service={service}, quality={context_quality}, "
            f"tags={llm_tags[:5]}"
        )

        return result



    def _parse_jira_ticket(
        self, inc_id: str, summary: str, description: str,
        priority: str, labels: list
    ) -> dict:
        """Build an IncidentContext dict from a Jira ticket."""
        # Map Jira priority to severity
        severity_map = {
            "Critical": "CRITICAL",
            "High": "HIGH",
            "Medium": "MEDIUM",
            "Low": "LOW",
        }
        # Guess service from labels
        service = "python-service"
        for label in labels:
            if "node" in label.lower():
                service = "node-service"
                break
            if "python" in label.lower():
                service = "python-service"
                break

        return {
            "id": inc_id,
            "title": summary,
            "description": description,
            "error_type": "unknown",
            "error_log": description,  # Jira description is the closest to error log
            "affected_service": service,
            "environment": "production",
            "severity": severity_map.get(priority, "HIGH"),
            "stack_trace": "",
            "failure_type": "unknown",
            "affected_commits": [],
            "jira_ticket_id": None,  # will be set by supervisor from metadata
        }

