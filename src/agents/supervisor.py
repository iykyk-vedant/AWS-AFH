"""
Supervisor Agent — Orchestrator for Amaze on Work.

Coordinates the multi-agent pipeline, handles retries, fires live progress
updates to Slack and Jira at each stage, and triggers GitHub Auto-PR creation
after successful validation.

Pipeline:
    incident_parser -> knowledge_retriever -> codebase_analyst
    -> critic -> fix_writer -> validation
        |-- PASS -> synthesis -> risk_scorer -> [PR + Notify] -> END
        |-- FAIL (< max_retries) -> fix_writer (retry)
        |-- FAIL (>= max_retries) -> END with error report
"""

import logging
import os
import time
from typing import Optional

from src.agents.state import (
    PipelineState,
    IncidentContext,
    AgentType,
    create_initial_state,
    add_message,
)
from src.agents.base import AgentResponse
from src.agents.incident_parser import IncidentParserAgent
from src.agents.codebase_analyst import CodebaseAnalystAgent
from src.agents.critic import CriticAgent
from src.agents.fix_writer import FixWriterAgent
from src.agents.validation import ValidationAgent
from src.agents.security_agent import SecurityAgent
from src.agents.synthesis import SynthesisAgent
from src.agents.risk_scorer import RiskScorerAgent
from src.agents.web_researcher import WebResearcherAgent
from src.agents.knowledge_retriever import KnowledgeRetrieverAgent
from src.llm.client_factory import get_llm_client
from src.mcp.github_tools import GitHubMCPTools, get_github_tools
from src.mcp.client_bridge import MCPClientBridge, get_mcp_bridge
from src.sandbox.docker_runner import DockerSandbox
from src.graph.factory import create_graph_backend
from src.graph.base import GraphBackend
from src.graph.repo_indexer import RepoIndexer

logger = logging.getLogger(__name__)


# Maps each agent name to the human-readable status posted to Slack/Jira
_AGENT_STATUS = {
    AgentType.INCIDENT_PARSER.value:
        "[Status] Incident Parser extracting context from ticket...",
    "knowledge_retriever":
        "[Status] Knowledge Retriever checking past similar incidents...",
    AgentType.CODEBASE_ANALYST.value:
        "[Status] Codebase Analyst querying GraphRAG for root cause...",
    AgentType.CRITIC.value:
        "[Status] Tech Lead Critic reviewing root cause analysis...",
    AgentType.FIX_WRITER.value:
        "[Status] Fix Planner & Patch Writer generating minimal fix...",
    AgentType.VALIDATION.value:
        "[Status] Validation Agent running sandbox tests (TestWriter + MultiFixEvaluator)...",
    AgentType.SECURITY.value:
        "[Status] Security Agent running STRIDE/OWASP review of fix...",
    AgentType.SYNTHESIS.value:
        "[Status] Synthesis Agent assembling resolution report...",
    AgentType.RISK_SCORER.value:
        "[Status] Risk Scorer evaluating fix safety...",
    AgentType.WEB_RESEARCHER.value:
        "[Status] Web Researcher searching StackOverflow for real-world fixes (LLM fallback)...",
}


class SupervisorAgent:
    """
    Orchestrates the Amaze on Work multi-agent pipeline.

    Pipeline flow:
        incident_parser -> codebase_analyst -> critic -> fix_writer
        -> validation -> synthesis -> risk_scorer

    With retry loop: if validation fails, loops back to fix_writer
    with feedback (max 3 attempts), then creates GitHub PR + notifies
    Slack and Jira on success.
    """

    def __init__(
        self,
        github_tools: Optional[GitHubMCPTools] = None,
        sandbox: Optional[DockerSandbox] = None,
        llm_provider: Optional[str] = None,
        bridge: Optional[MCPClientBridge] = None,
        graph: Optional[GraphBackend] = None,
    ):
        self.llm = get_llm_client(llm_provider)
        self.github = github_tools or get_github_tools()
        self.sandbox = sandbox or DockerSandbox()
        self.bridge = bridge or get_mcp_bridge()

        # Initialize knowledge graph backend
        # Shared singleton across all agents so they all query the same indexed graph
        try:
            self.graph = graph or create_graph_backend(prefer="auto")
            logger.info(f"Graph backend ready: {self.graph.stats()}")
        except Exception as e:
            logger.warning(f"Graph backend init failed: {e} — graph-first localization disabled")
            self.graph = None

        # Initialize all agents
        self.agents = {
            AgentType.INCIDENT_PARSER.value: IncidentParserAgent(self.llm, self.github),
            AgentType.CODEBASE_ANALYST.value: CodebaseAnalystAgent(
                self.llm, self.github, graph=self.graph
            ),
            AgentType.CRITIC.value: CriticAgent(self.llm),
            AgentType.FIX_WRITER.value: FixWriterAgent(self.llm, self.github),
            AgentType.VALIDATION.value: ValidationAgent(self.llm, self.sandbox),
            AgentType.SECURITY.value: SecurityAgent(self.llm, docker_sandbox=self.sandbox),
            AgentType.SYNTHESIS.value: SynthesisAgent(self.llm),
            AgentType.RISK_SCORER.value: RiskScorerAgent(self.llm, github_tools=self.github),
            AgentType.WEB_RESEARCHER.value: WebResearcherAgent(self.llm),
        }
        if self.graph:
            self.agents[AgentType.KNOWLEDGE_RETRIEVER.value] = KnowledgeRetrieverAgent(
                self.llm, self.graph
            )

    def _ensure_repo_indexed(self, repo_url: str) -> None:
        """
        Index the target repo into the knowledge graph if not already done.
        Called at the start of every resolve_incident so codebase_analyst
        can query real file/function nodes instead of using LLM hallucination.
        """
        if not self.graph:
            return
        try:
            # Pass GitHub token so private repos can be cloned
            token = os.environ.get("GITHUB_TOKEN", "")
            indexer = RepoIndexer(self.graph)
            stats = indexer.index_from_github(repo_url, clone_token=token)
            logger.info(f"[Supervisor] Repo index stats: {stats}")
        except Exception as e:
            logger.warning(f"[Supervisor] Repo indexing failed (non-fatal): {e}")

    def resolve_incident(
        self,
        incident: IncidentContext,
        repo_url: str = "https://github.com/Rezinix-AI/shopstack-platform",
        slack_channel: str = "",
        slack_thread_ts: str = "",
        jira_ticket_id: str = "",
    ) -> dict:
        """
        Run the full incident resolution pipeline.

        Args:
            incident: Parsed incident context dict
            repo_url: Target repository URL to clone + test
            slack_channel: Slack channel ID for live progress updates
            slack_thread_ts: Timestamp of original Slack incident message
            jira_ticket_id: Jira ticket ID to update with progress + resolution

        Returns:
            dict with resolution_report, risk_assessment, pr_url, etc.
        """
        incident_id = incident.get("id", "")
        state = create_initial_state(incident, repo_url)

        # Carry Slack/Jira context in state for downstream access
        state["slack_channel"] = slack_channel
        state["slack_thread_ts"] = slack_thread_ts
        state["jira_ticket_id"] = jira_ticket_id

        logger.info(f"=== Pipeline Start: {incident_id} ===")

        # Index the target repo into the knowledge graph (skips if already indexed)
        self._ensure_repo_indexed(repo_url)

        # Define execution order
        pipeline = [
            AgentType.INCIDENT_PARSER.value,
            AgentType.CODEBASE_ANALYST.value,
            AgentType.CRITIC.value,
            AgentType.FIX_WRITER.value,
            AgentType.VALIDATION.value,
            AgentType.SECURITY.value,       # Security gate after validation
            AgentType.SYNTHESIS.value,
            AgentType.RISK_SCORER.value,
        ]

        attempt = 1
        max_retries = state.get("max_retries", 3)
        current_step = 0

        while current_step < len(pipeline) and attempt <= max_retries:
            agent_name = pipeline[current_step]
            state["current_agent"] = agent_name
            state["attempt"] = attempt

            logger.info(f"--- Step {current_step + 1}: {agent_name} (attempt {attempt}) ---")

            # Post live status update to Slack thread + Jira ticket
            status_msg = _AGENT_STATUS.get(agent_name, f"[Status] Running {agent_name}...")
            self._post_progress(
                status_msg,
                slack_channel=slack_channel,
                slack_thread_ts=slack_thread_ts,
                jira_ticket_id=jira_ticket_id,
                incident_id=incident_id,
            )

            agent = self.agents.get(agent_name)
            if not agent:
                logger.error(f"Agent {agent_name} not found!")
                current_step += 1
                continue

            try:
                response = agent.execute(state)
            except Exception as e:
                logger.error(f"Agent {agent_name} crashed: {e}")
                response = AgentResponse(
                    success=False,
                    message=f"Agent crashed: {str(e)}",
                    error=str(e),
                )

            # Log response and update state
            state = add_message(state, "assistant", response.message, agent_name)
            logger.info(f"  -> {response.message}")
            state = self._apply_response(state, agent_name, response)

            # Handle retry / progression
            next_agent = getattr(response, "next_agent", None) or AgentType.FIX_WRITER.value
            is_web_researcher_trigger = next_agent == AgentType.WEB_RESEARCHER.value
            # Allow retry if under max, OR this is the WebResearcher fallback (counts as a bonus pass)
            if response.needs_retry and (attempt < max_retries or is_web_researcher_trigger):
                logger.info(f"  -> Retry needed (attempt {attempt}/{max_retries}) -> {next_agent}")
                attempt += 1
                state["needs_retry"] = True
                state["error"] = response.error

                # Post per-attempt validation summary to Slack
                val_attempt = state.get("validation_attempt", 0)
                val_logs = state.get("validation_logs") or []
                if val_logs:
                    last_log = val_logs[-1] if val_logs else {}
                    verdict = last_log.get("verdict", "UNKNOWN")
                    after = last_log.get("after", {})
                    before = last_log.get("before", {})
                    score = last_log.get("score", 0)
                    attempt_msg = (
                        f"[Validation Attempt #{val_attempt}] "
                        f"Verdict: {verdict} | "
                        f"Before: {before.get('passed', 0)}P/{before.get('failed', 0)}F | "
                        f"After:  {after.get('passed', 0)}P/{after.get('failed', 0)}F | "
                        f"Score: {score:.0f}/100 | -> {next_agent}"
                    )
                    self._post_progress(
                        attempt_msg,
                        slack_channel=slack_channel,
                        slack_thread_ts=slack_thread_ts,
                        jira_ticket_id=jira_ticket_id,
                        incident_id=incident_id,
                    )

                # Route to next_agent (WEB_RESEARCHER or FIX_WRITER)
                if next_agent in pipeline:
                    current_step = pipeline.index(next_agent)
                elif next_agent == AgentType.WEB_RESEARCHER.value:
                    # WebResearcher not in main pipeline — run inline then route to fix_writer
                    web_agent = self.agents.get(AgentType.WEB_RESEARCHER.value)
                    if web_agent:
                        self._post_progress(
                            _AGENT_STATUS.get(AgentType.WEB_RESEARCHER.value, "[Status] Web Researcher running..."),
                            slack_channel=slack_channel,
                            slack_thread_ts=slack_thread_ts,
                            jira_ticket_id=jira_ticket_id,
                            incident_id=incident_id,
                        )
                        try:
                            wr_response = web_agent.execute(state)
                            state = self._apply_response(state, AgentType.WEB_RESEARCHER.value, wr_response)
                            logger.info(f"  -> WebResearcher: {wr_response.message}")
                        except Exception as e:
                            logger.warning(f"WebResearcher failed: {e}")
                    current_step = pipeline.index(AgentType.FIX_WRITER.value)
                else:
                    current_step = pipeline.index(AgentType.FIX_WRITER.value)

                retry_msg = (
                    f"[Status] Validation failed ({verdict if val_logs else 'error'}) — "
                    f"retrying fix (attempt {attempt}/{max_retries}) via {next_agent}..."
                )
                self._post_progress(
                    retry_msg,
                    slack_channel=slack_channel,
                    slack_thread_ts=slack_thread_ts,
                    jira_ticket_id=jira_ticket_id,
                    incident_id=incident_id,
                )
                continue

            if not response.success and not response.needs_retry:
                logger.warning(f"  -> Agent {agent_name} failed (non-retryable)")
                state["error"] = response.error

            current_step += 1

        # Pipeline complete
        state["is_complete"] = True
        resolution_time = time.time() - state.get("start_time", time.time())
        logger.info(
            f"=== Pipeline Complete: {incident_id} -- "
            f"{resolution_time:.1f}s, {attempt} attempt(s) ==="
        )

        result = {
            "incident_id": incident_id,
            "resolution_report": state.get("resolution_report", {}),
            "risk_assessment": state.get("risk_assessment", {}),
            "validation_result": state.get("validation_result", {}),
            "fix_plan": state.get("fix_plan", {}),
            "root_cause": state.get("root_cause", {}),
            "formatted_markdown": state.get("formatted_markdown", ""),
            "attempts": attempt,
            "resolution_time_seconds": resolution_time,
            "error": state.get("error"),
            "pr_url": "",
        }

        # ── Post-pipeline actions: Risk-tiered deployment ──────────────
        validation_result = state.get("validation_result", {}) or {}
        verdict = validation_result.get("verdict", "") if isinstance(validation_result, dict) else ""

        fix_plan = state.get("fix_plan", {}) or {}
        has_fix = bool(fix_plan.get("files_to_modify") or fix_plan.get("patch"))

        fix_worked = verdict in ("FIX_CONFIRMED", "UNRELATED_FAILURE", "FIX_APPLIED_NO_TESTS", "SKIPPED")

        # Get risk assessment for deployment routing
        risk_assessment = state.get("risk_assessment", {}) or {}
        deployment_action = risk_assessment.get("deployment_action", "")
        risk_level = risk_assessment.get("level", "MEDIUM")
        all_candidates = risk_assessment.get("all_candidates", [])

        logger.info(
            f"[Supervisor] Post-pipeline gate: verdict={verdict!r} "
            f"fix_worked={fix_worked} has_fix={has_fix} "
            f"deployment_action={deployment_action!r} risk_level={risk_level!r}"
        )

        # Persist candidates for interactive fix selection via #fix-selection channel
        if all_candidates:
            try:
                from src.api.routes.fix_selection_webhook import store_candidates
                store_candidates(incident_id, all_candidates, repo_url)
            except Exception as e:
                logger.warning(f"Could not persist fix candidates: {e}")

        pr_url = ""

        # Extract one-line problem summary for PR title
        root_cause = state.get("root_cause", {}) or {}
        problem_summary = ""
        if isinstance(root_cause, dict):
            problem_summary = (
                root_cause.get("root_cause_summary", "")
                or root_cause.get("summary", "")
                or root_cause.get("hypothesis", "")
            )
        # Truncate to one line
        if problem_summary:
            problem_summary = problem_summary.split("\n")[0].strip()[:80]

        incident_ctx = state.get("incident", {}) or {}
        github_issue_number = (
            incident_ctx.get("github_issue_number")
            or incident_ctx.get("issue_number")
            or state.get("github_issue_number")
        )

        if fix_worked and has_fix:
            if deployment_action == "auto_pr":
                # LOW RISK: Auto-create PR with best fix + summary
                pr_url = self._create_pr(
                    incident_id=incident_id,
                    fix_plan=fix_plan,
                    report_body=state.get("formatted_markdown", ""),
                    repo_url=repo_url,
                    risk_level=risk_level,
                    problem_summary=problem_summary,
                    github_issue_number=github_issue_number,
                )
                result["pr_url"] = pr_url
                self._post_final_success(
                    incident_id=incident_id, state=state, pr_url=pr_url,
                    slack_channel=slack_channel, slack_thread_ts=slack_thread_ts,
                    jira_ticket_id=jira_ticket_id, resolution_time=resolution_time,
                )

            elif deployment_action == "pr_with_options":
                # MEDIUM RISK: PR with best fix + Slack with alternatives
                pr_url = self._create_pr(
                    incident_id=incident_id,
                    fix_plan=fix_plan,
                    report_body=state.get("formatted_markdown", ""),
                    repo_url=repo_url,
                    risk_level=risk_level,
                    problem_summary=problem_summary,
                    github_issue_number=github_issue_number,
                )
                result["pr_url"] = pr_url
                self._post_final_success(
                    incident_id=incident_id, state=state, pr_url=pr_url,
                    slack_channel=slack_channel, slack_thread_ts=slack_thread_ts,
                    jira_ticket_id=jira_ticket_id, resolution_time=resolution_time,
                )
                # Also post fix options for user to pick alternatives
                self._post_fix_options(
                    incident_id=incident_id,
                    all_candidates=all_candidates,
                    risk_level=risk_level,
                    risk_assessment=risk_assessment,
                    current_pr_url=pr_url,
                    slack_channel=slack_channel,
                    slack_thread_ts=slack_thread_ts,
                )

            elif deployment_action == "options_only":
                # HIGH RISK: Post fix options to Slack if available
                has_slack = bool(slack_channel or os.getenv("SLACK_BOT_TOKEN"))
                if not has_slack:
                    # Headless / CLI execution without Slack — create review PR so human can review on GitHub
                    logger.info("[Supervisor] Headless mode without Slack: opening PR for human review on GitHub")
                    pr_url = self._create_pr(
                        incident_id=incident_id,
                        fix_plan=fix_plan,
                        report_body=state.get("formatted_markdown", ""),
                        repo_url=repo_url,
                        risk_level=risk_level,
                        problem_summary=f"[HIGH RISK REVIEW] {problem_summary}",
                        github_issue_number=github_issue_number,
                    )
                    result["pr_url"] = pr_url

                self._post_fix_options(
                    incident_id=incident_id,
                    all_candidates=all_candidates,
                    risk_level=risk_level,
                    risk_assessment=risk_assessment,
                    current_pr_url=pr_url,
                    slack_channel=slack_channel,
                    slack_thread_ts=slack_thread_ts,
                )
                self._post_final_success(
                    incident_id=incident_id, state=state, pr_url=pr_url,
                    slack_channel=slack_channel, slack_thread_ts=slack_thread_ts,
                    jira_ticket_id=jira_ticket_id, resolution_time=resolution_time,
                )

            else:
                # Fallback: no risk assessment available — use old behavior
                pr_url = self._create_pr(
                    incident_id=incident_id,
                    fix_plan=fix_plan,
                    report_body=state.get("formatted_markdown", ""),
                    repo_url=repo_url,
                    risk_level=risk_level,
                    problem_summary=problem_summary,
                )
                result["pr_url"] = pr_url
                self._post_final_success(
                    incident_id=incident_id, state=state, pr_url=pr_url,
                    slack_channel=slack_channel, slack_thread_ts=slack_thread_ts,
                    jira_ticket_id=jira_ticket_id, resolution_time=resolution_time,
                )

        else:
            self._post_progress(
                f"[Result] Could not generate a fix after {attempt} attempt(s). "
                f"Manual review required. Check reports/ directory for details.",
                slack_channel=slack_channel,
                slack_thread_ts=slack_thread_ts,
                jira_ticket_id=jira_ticket_id,
                incident_id=incident_id,
            )

        return result

    # ── Auto-PR Creation ─────────────────────────────────────────────

    def _create_pr(
        self,
        incident_id: str,
        fix_plan: dict,
        report_body: str,
        repo_url: str,
        risk_level: str = "MEDIUM",
        problem_summary: str = "",
        github_issue_number: Optional[int] = None,
    ) -> str:
        """Create a GitHub PR with the fix.

        Returns the PR URL, or empty string on failure.
        Delegates to bridge.create_fix_pr with:
          - Smart matching (sanitizer + 7-tier matching)
          - Risk-based labels (LOW → 'raised-by-agent')
          - One-line problem description in title
        """
        if not repo_url:
            logger.warning("[Supervisor] No repo_url — cannot create PR")
            return ""

        # Parse owner/repo from URL
        parts = repo_url.rstrip("/").split("/")
        owner, repo = parts[-2], parts[-1]
        if repo.endswith(".git"):
            repo = repo[:-4]

        try:
            result = self.bridge.create_fix_pr(
                owner=owner,
                repo=repo,
                incident_id=incident_id,
                fix_plan=fix_plan,
                report_body=report_body,
                risk_level=risk_level,
                problem_summary=problem_summary,
                github_issue_number=github_issue_number,
            )
            pr_url = result.get("pr_url") or result.get("html_url") or ""
            if result.get("error"):
                logger.error(f"[Supervisor] PR creation error: {result['error']}")
                return ""
            logger.info(f"[Supervisor] PR created: {pr_url} [labels: {result.get('labels', [])}]")
            return pr_url
        except Exception as e:
            logger.error(f"[Supervisor] PR creation failed: {e}")
            return ""

    # ── Live Progress Posting ───────────────────────────────────────

    def _post_progress(
        self,
        status_msg: str,
        slack_channel: str = "",
        slack_thread_ts: str = "",
        jira_ticket_id: str = "",
        incident_id: str = "",
    ) -> None:
        """Post live agent progress to Slack thread + Jira ticket + Live Dashboard."""
        try:
            self.bridge.post_progress_update(
                status_msg=status_msg,
                slack_channel=slack_channel,
                slack_thread_ts=slack_thread_ts,
                jira_ticket_id=jira_ticket_id,
                incident_id=incident_id,
            )
        except Exception as e:
            logger.debug(f"Progress update error (non-fatal): {e}")

        # Update Live Dashboard Event Stream
        if incident_id:
            try:
                from src.api.event_stream import update_event_stage
                stage_map = [
                    ("Incident Parser", 1, "Incident Parser"),
                    ("Codebase Analyst", 2, "Codebase Analyst"),
                    ("Critic", 3, "Adversarial Critic"),
                    ("Fix Writer", 4, "Fix Writer"),
                    ("Validation", 5, "Docker Sandbox"),
                    ("Risk", 6, "Risk Scorer"),
                    ("Synthesis", 7, "Ops Delivery"),
                ]
                stage_num = 1
                stage_lbl = "Incident Parser"
                for kw, s_num, s_lbl in stage_map:
                    if kw.lower() in status_msg.lower():
                        stage_num, stage_lbl = s_num, s_lbl
                        break
                update_event_stage(incident_id, stage_num, stage_lbl, status_msg)
            except Exception:
                pass

    def _post_final_success(
        self,
        incident_id: str,
        state: PipelineState,
        pr_url: str,
        slack_channel: str,
        slack_thread_ts: str,
        jira_ticket_id: str,
        resolution_time: float,
    ) -> None:
        """Post the final resolution summary to Slack + Jira + Live Dashboard."""
        root_cause = state.get("root_cause", {})
        cause_summary = (
            root_cause.get("root_cause_summary", "")
            or root_cause.get("summary", "")
            if isinstance(root_cause, dict) else str(root_cause)
        )[:300]

        # Update Live Dashboard Event Stream to RESOLVED
        try:
            from src.api.event_stream import update_event_stage
            fix_plan = state.get("fix_plan", {}) or {}
            diff = fix_plan.get("diff", "")
            update_event_stage(
                incident_id,
                stage=7,
                stage_name="Ops Delivery",
                log_msg=f"Successfully resolved in {resolution_time:.1f}s. Pull Request opened: {pr_url}",
                status="RESOLVED",
                pr_url=pr_url,
                diff=diff,
            )
        except Exception:
            pass

        risk = state.get("risk_assessment", {})
        risk_level = risk.get("risk_level", "UNKNOWN") if isinstance(risk, dict) else "UNKNOWN"

        validation_result = state.get("validation_result", {})
        fixes_confirmed = (
            validation_result.get("fixes_confirmed", [])
            if isinstance(validation_result, dict) else []
        )

        success_msg = (
            f"[RESOLVED] *{incident_id}* fixed in {resolution_time:.1f}s\n"
            f"Root cause: {cause_summary}\n"
            f"Tests confirmed fixed: {len(fixes_confirmed)}\n"
            f"Risk level: {risk_level}\n"
        )
        if pr_url:
            success_msg += f"PR: {pr_url}"

        # Slack — post to thread
        if slack_channel and slack_thread_ts:
            try:
                root_cause_str = cause_summary
                confidence = risk.get("confidence", 0.8) if isinstance(risk, dict) else 0.8
                self.bridge.post_slack_resolution(
                    channel=slack_channel,
                    incident_id=incident_id,
                    title=state.get("incident", {}).get("title", incident_id),
                    root_cause=root_cause_str,
                    verdict="FIX_CONFIRMED",
                    confidence=confidence,
                    risk_level=risk_level,
                    pr_url=pr_url,
                    thread_ts=slack_thread_ts,
                )
            except Exception as e:
                logger.warning(f"Final Slack resolution post failed: {e}")
                try:
                    self.bridge.post_slack_message(slack_channel, success_msg, slack_thread_ts)
                except Exception:
                    pass

        # Jira -- transition to Resolved + add comment + link PR
        if jira_ticket_id:
            try:
                self.bridge.jira_update_status(jira_ticket_id, "Resolved")
            except Exception as e:
                logger.warning(f"Jira status update failed: {e}")
            try:
                self.bridge.jira_add_comment(
                    jira_ticket_id,
                    state.get("formatted_markdown", success_msg)[:5000],
                )
            except Exception as e:
                logger.warning(f"Jira comment failed: {e}")
            if pr_url:
                try:
                    self.bridge.jira_link_pr(
                        jira_ticket_id,
                        pr_url,
                        f"Amaze on Work: {incident_id} automated fix",
                    )
                except Exception as e:
                    logger.warning(f"Jira PR link failed: {e}")

        # Always post the full structured report to the #reports Slack channel
        try:
            incident = state.get("incident", {}) or {}
            root_cause_data = state.get("root_cause", {}) or {}
            fix_plan_data = state.get("fix_plan", {}) or {}
            validation_data = state.get("validation_result", {}) or {}
            security_data = state.get("security_result", {}) or {}

            # Build reasoning chain text
            reasoning_chain = []
            if isinstance(state.get("resolution_report"), dict):
                reasoning_chain = state["resolution_report"].get("reasoning_chain", [])
            reasoning_text = "\n".join(f"{i+1}. {s}" for i, s in enumerate(reasoning_chain))

            # Build changes summary text
            changes_parts = []
            for ch in fix_plan_data.get("files_to_modify", []):
                f = ch.get("file_path", "")
                r = ch.get("rationale", "")
                orig = ch.get("original_code", "")[:400]
                fixed = ch.get("fixed_code", "")[:400]
                if f:
                    part = f"*{f}*\n{r}"
                    if orig and fixed:
                        part += f"\n```Before:\n{orig}\n```\n```After:\n{fixed}\n```"
                    changes_parts.append(part)
            changes_text = "\n\n".join(changes_parts) or "No files changed"

            # Build security summary
            sec_summary = state.get("security_result", {})
            if isinstance(sec_summary, dict) and sec_summary.get("summary"):
                summ = sec_summary["summary"]
                security_text = (
                    f"Total: {summ.get('total', 0)} | "
                    f"Critical: {summ.get('critical', 0)} | "
                    f"High: {summ.get('high', 0)} | "
                    f"Medium: {summ.get('medium', 0)} | "
                    f"Low: {summ.get('low', 0)}"
                )
            else:
                security_text = ""

            risk_data = state.get("risk_assessment", {}) or {}
            confidence = root_cause_data.get("confidence", 0)
            if isinstance(risk_data, dict) and risk_data.get("confidence"):
                confidence = risk_data["confidence"]

            self.bridge.post_full_report_to_reports_channel(
                incident_id=incident_id,
                title=incident.get("title", incident_id),
                root_cause=(
                    root_cause_data.get("hypothesis", "")
                    or root_cause_data.get("root_cause_summary", "")
                    or "Not determined"
                ),
                reasoning=reasoning_text,
                changes_made=changes_text,
                validation_verdict=(
                    validation_data.get("verdict", "SKIPPED")
                    if isinstance(validation_data, dict) else "SKIPPED"
                ),
                risk_level=risk_level or risk_data.get("level", "MEDIUM"),
                confidence=float(confidence),
                resolution_time=resolution_time,
                pr_url=pr_url,
                patch_diff=fix_plan_data.get("patch", ""),
                security_summary=security_text,
            )
            logger.info(f"[Reports] Full report posted to #reports channel for {incident_id}")
        except Exception as e:
            logger.warning(f"Failed to post full report to #reports channel: {e}")

    # ── Fix Options for User Selection ──────────────────────────────

    def _post_fix_options(
        self,
        incident_id: str,
        all_candidates: list[dict],
        risk_level: str,
        risk_assessment: dict,
        current_pr_url: str,
        slack_channel: str = "",
        slack_thread_ts: str = "",
    ) -> None:
        """Post all fix options to Slack for user selection.

        For MEDIUM risk: shows alternatives to the auto-created PR.
        For HIGH risk: shows all options for manual pick.
        """
        if not all_candidates:
            return

        breakdown = risk_assessment.get("breakdown", {})
        composite = risk_assessment.get("composite_score", 0)

        # Build header
        risk_emoji = {
            "LOW": "🟢", "MEDIUM": "🟡", "HIGH": "🔴",
        }.get(risk_level, "⚪")

        header = (
            f"{risk_emoji} *{incident_id} — Fix Options* "
            f"(Risk: {risk_level}, Score: {composite}/100)\n"
        )

        # Risk breakdown summary
        risk_parts = []
        for factor, data in breakdown.items():
            if isinstance(data, dict) and "score" in data:
                risk_parts.append(f"{factor}: {data.get('score', 0)}/{data.get('max', 0)}")
        if risk_parts:
            header += f"_Risk factors: {', '.join(risk_parts)}_\n\n"

        # Build candidate blocks
        fix_blocks = []
        for i, candidate in enumerate(all_candidates[:5], 1):
            fix_plan = candidate.get("fix_plan", {})
            score = candidate.get("score", 0)
            verdict = candidate.get("verdict", "UNKNOWN")
            cid = candidate.get("candidate_id", f"c{i}")

            verdict_icon = {
                "FIX_CONFIRMED": "✅", "NO_CHANGE": "⚠️",
                "REGRESSION_DETECTED": "❌", "UNRELATED_FAILURE": "🔶",
            }.get(verdict, "❓")

            desc = fix_plan.get("description", "No description")
            files = [c.get("file_path", "?") for c in fix_plan.get("files_to_modify", [])]

            block = f"*Fix #{i}* ({cid}) — {verdict_icon} {verdict} | Score: {score:.0f}\n"
            block += f"> {desc}\n"
            if files:
                block += f"> Files: `{'`, `'.join(files)}`\n"

            # Show code diff preview
            for change in fix_plan.get("files_to_modify", [])[:1]:
                original = change.get("original_code", "")[:150]
                fixed = change.get("fixed_code", "")[:150]
                if original and fixed:
                    block += f"```\n- {original}\n+ {fixed}\n```\n"

            if i == 1 and current_pr_url:
                block += f"🔗 _PR created: {current_pr_url}_\n"

            fix_blocks.append(block)

        # Action instructions
        if current_pr_url:
            instructions = (
                f"\n📋 *PR created with Fix #1.* "
                f"To switch to a different fix, reply in #fix-selection:\n"
                f"`!fix <number> {incident_id}`\n"
                f"_(e.g., `!fix 2 {incident_id}` to use Fix #2 instead)_"
            )
        else:
            instructions = (
                f"\n⚠️ *HIGH RISK — No auto-PR created.* "
                f"To create a PR with a fix, reply in #fix-selection:\n"
                f"`!fix <number> {incident_id}`\n"
                f"_(e.g., `!fix 1 {incident_id}` to create PR with Fix #1)_"
            )

        full_message = header + "\n".join(fix_blocks) + instructions

        try:
            self.bridge.post_slack_message(
                channel=slack_channel,
                text=full_message,
                thread_ts=slack_thread_ts,
            )
            logger.info(f"[Supervisor] Posted {len(fix_blocks)} fix options for {incident_id}")
        except Exception as e:
            logger.warning(f"Failed to post fix options: {e}")



    # ── State Application ──────────────────────────────────────────

    def _apply_response(
        self, state: PipelineState, agent_name: str, response: AgentResponse
    ) -> PipelineState:
        """Apply agent's response data to the shared pipeline state."""
        data = response.data
        if not data:
            return state

        if agent_name == AgentType.INCIDENT_PARSER.value:
            if isinstance(data, dict):
                state["incident"] = data

        elif agent_name == AgentType.CODEBASE_ANALYST.value:
            if isinstance(data, dict):
                state["root_cause"] = data

        elif agent_name == AgentType.CRITIC.value:
            if isinstance(data, dict):
                state["critic_verdict"] = data.get("verdict", "")
                state["critic_feedback"] = data.get("feedback", "")

        elif agent_name == AgentType.FIX_WRITER.value:
            if isinstance(data, dict):
                state["fix_plan"] = data

        elif agent_name == AgentType.VALIDATION.value:
            if isinstance(data, dict):
                state["validation_result"] = data

        elif agent_name == AgentType.SECURITY.value:
            if isinstance(data, dict):
                state["security_result"] = data

        elif agent_name == AgentType.SYNTHESIS.value:
            if isinstance(data, dict):
                state["resolution_report"] = data.get("report", {})
                state["formatted_markdown"] = data.get("formatted_markdown", "")

        elif agent_name == AgentType.RISK_SCORER.value:
            if isinstance(data, dict):
                state["risk_assessment"] = data

        elif agent_name == AgentType.WEB_RESEARCHER.value:
            if isinstance(data, dict):
                state["research_hints"] = data
                # Also append research hint context to validation_feedback
                hint = data.get("fix_hint", "")
                sources = ", ".join(r.get("url", "") for r in data.get("sources", [])[:3] if r.get("url"))
                if hint:
                    existing_feedback = state.get("validation_feedback", "") or ""
                    state["validation_feedback"] = (
                        f"{existing_feedback}\n\n[WebResearcher Hint]\n{hint}"
                        f"\nSources: {sources}"
                    ).strip()

        return state

    # ── Batch Resolution ───────────────────────────────────────────

    def resolve_all_incidents(
        self,
        owner: str,
        repo: str,
        repo_url: str = "https://github.com/Rezinix-AI/shopstack-platform",
        slack_channel: str = "",
    ) -> list[dict]:
        """Resolve all incidents fetched from the target repo."""
        parser = self.agents[AgentType.INCIDENT_PARSER.value]
        incident_ids = parser.list_incidents(owner, repo)

        if not incident_ids:
            logger.warning("No incidents found!")
            return []

        logger.info(f"Found {len(incident_ids)} incidents to resolve")
        results = []

        for incident_id in incident_ids:
            logger.info(f"\n{'=' * 60}\nProcessing {incident_id}\n{'=' * 60}")
            try:
                incident = parser.parse_from_github(owner, repo, incident_id)
                result = self.resolve_incident(
                    incident=incident,
                    repo_url=repo_url,
                    slack_channel=slack_channel,
                )
                results.append(result)
            except Exception as e:
                logger.error(f"Failed to process {incident_id}: {e}")
                results.append({"incident_id": incident_id, "error": str(e)})

        return results
