"""
LangGraph state definitions for Amaze on Work pipeline.

Defines the IncidentContext (parsed from incident tickets) and the
PipelineState (shared state across all agents in the workflow).
"""

from typing import Any, Optional, Literal
from enum import Enum

from src.config import GITHUB_REPO_URL

class AgentType(str, Enum):
    """All agent types in Amaze on Work."""
    SUPERVISOR = "supervisor"
    INCIDENT_PARSER = "incident_parser"
    CODEBASE_ANALYST = "codebase_analyst"
    KNOWLEDGE_RETRIEVER = "knowledge_retriever"
    CRITIC = "critic"
    FIX_WRITER = "fix_writer"
    VALIDATION = "validation"
    KG_BUILDER = "kg_builder"
    SYNTHESIS = "synthesis"
    RISK_SCORER = "risk_scorer"
    SECURITY = "security"
    WEB_RESEARCHER = "web_researcher"


class FailureType(str, Enum):
    """Classification of incident failure types."""
    LOGICAL_ERROR = "logical_error"
    CONFIGURATION = "configuration"
    DEPENDENCY = "dependency"
    RUNTIME_CRASH = "runtime_crash"
    SECURITY = "security"
    PERFORMANCE = "performance"
    MISSING_IMPORT = "missing_import"
    MISSING_DEPENDENCY = "missing_dependency"
    TYPE_ERROR = "type_error"
    UNKNOWN = "unknown"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ValidationVerdict(str, Enum):
    FIX_CONFIRMED = "FIX_CONFIRMED"
    REGRESSION_DETECTED = "REGRESSION_DETECTED"
    UNRELATED_FAILURE = "UNRELATED_FAILURE"
    NO_CHANGE = "NO_CHANGE"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


class CriticVerdict(str, Enum):
    APPROVED = "APPROVED"
    NEEDS_REVISION = "NEEDS_REVISION"


# ─── TypedDict State Definitions ──────────────────────────────────────────

from typing import TypedDict


class StackFrame(TypedDict, total=False):
    file: str
    function: str
    line: int
    code: str


class IncidentContext(TypedDict, total=False):
    """Canonical incident object parsed from JSON tickets."""
    id: str
    title: str
    description: str
    stack_traces: list[StackFrame]
    affected_service: str
    environment: str
    severity: str
    failure_type: str
    error_log: str
    tags: list[str]
    steps_to_reproduce: list[str]
    expected_behavior: str
    actual_behavior: str
    recent_changes: str
    linked_commits: list[str]
    linked_issue_url: str
    reported_by: str
    timestamp: str


class RootCauseAnalysis(TypedDict, total=False):
    """Root cause hypothesis from the Codebase Analyst."""
    hypothesis: str
    confidence: float
    suspect_files: list[str]
    suspect_functions: list[str]
    suspect_lines: list[dict]
    code_snippets: dict[str, str]
    failure_type: str
    reasoning: str


class FixPlan(TypedDict, total=False):
    """Proposed fix from the Fix Writer."""
    description: str
    files_to_modify: list[dict]
    patch: str
    rationale: str
    characterization_test: str | None
    change_size: int


class TestResult(TypedDict, total=False):
    """Test execution result."""
    total: int
    passed: int
    failed: int
    errors: int
    skipped: int
    test_names: list[str]
    failed_tests: list[str]
    stdout: str
    stderr: str
    runtime_seconds: float


class ValidationResult(TypedDict, total=False):
    """Before/after validation result."""
    verdict: str
    before: TestResult
    after: TestResult
    regressions: list[str]
    fixes_confirmed: list[str]
    unrelated_failures: list[str]


class RiskAssessment(TypedDict, total=False):
    """Risk scoring result with principled multi-factor scoring."""
    level: str                       # LOW | MEDIUM | HIGH
    composite_score: int             # 0-100 composite risk score
    blast_radius: int                # Downstream callers affected (graph)
    coupling_score: int              # fan_in × fan_out for modified functions
    cyclomatic_delta: int            # Complexity added/removed by the fix
    file_churn: int                  # Bugfix commits in recent history
    has_tests: bool                  # Whether tests exist for the area
    change_size: int                 # Lines changed in the fix
    environment: str                 # prod / staging / dev
    severity: str                    # P0-P3 or LOW/MEDIUM/HIGH/CRITICAL
    policy: str                      # Human-readable policy description
    deployment_action: str           # "auto_pr" | "pr_with_options" | "options_only"
    all_candidates: list[dict]       # Ranked list of all scored fix candidates
    best_candidate_id: str           # ID of the top-ranked candidate
    breakdown: dict[str, Any]        # Per-factor score breakdown


class ResolutionReport(TypedDict, total=False):
    """Final resolution report."""
    incident_id: str
    title: str
    root_cause: str
    changes_made: list[dict]
    diff: str
    validation: ValidationResult
    risk: RiskAssessment
    confidence_score: float
    reasoning_chain: list[str]
    resolution_time_seconds: float


class Message(TypedDict):
    role: str
    content: str
    agent: str | None


class PipelineState(TypedDict, total=False):
    """Shared state across all agents in the Amaze on Work pipeline."""
    # Input
    incident: IncidentContext
    repo_url: str
    repo_owner: str
    repo_name: str

    # Pipeline tracking
    current_agent: str
    attempt: int
    max_retries: int
    start_time: float
    messages: list[Message]

    # Agent outputs
    root_cause: RootCauseAnalysis
    critic_verdict: str
    critic_feedback: str
    fix_plan: FixPlan
    validation_result: ValidationResult
    risk_assessment: RiskAssessment
    resolution_report: ResolutionReport
    knowledge_context: dict[str, Any]
    security_result: dict[str, Any]
    research_hints: dict[str, Any]  # From WebResearcherAgent

    # Validation retry tracking
    validation_attempt: int          # Current attempt number (same error)
    last_error_signature: str        # Fingerprint of last failed error
    validation_logs: list            # All per-attempt log dicts
    validation_feedback: str         # Feedback from validator to fix_writer
    validation_generated_tests: str  # Generated test file content

    # Flags
    is_complete: bool
    error: str | None
    needs_retry: bool
    needs_human: bool


def create_initial_state(
    incident: IncidentContext,
    repo_url: str = GITHUB_REPO_URL,
) -> PipelineState:
    """Create the initial pipeline state from an incident and repo URL."""
    import time

    # Parse owner/name from URL
    parts = repo_url.rstrip("/").split("/")
    repo_owner = parts[-2] if len(parts) >= 2 else ""
    repo_name = parts[-1] if len(parts) >= 1 else ""

    return PipelineState(
        incident=incident,
        repo_url=repo_url,
        repo_owner=repo_owner,
        repo_name=repo_name,
        current_agent=AgentType.INCIDENT_PARSER.value,
        attempt=1,
        max_retries=3,
        start_time=time.time(),
        messages=[],
        root_cause={},
        critic_verdict="",
        critic_feedback="",
        fix_plan={},
        validation_result={},
        risk_assessment={},
        resolution_report={},
        knowledge_context={},
        security_result={},
        research_hints={},
        validation_attempt=0,
        last_error_signature="",
        validation_logs=[],
        validation_feedback="",
        validation_generated_tests="",
        is_complete=False,
        error=None,
        needs_retry=False,
        needs_human=False,
    )


def add_message(state: PipelineState, role: str, content: str, agent: str | None = None) -> PipelineState:
    """Add a message to the pipeline state."""
    messages = list(state.get("messages", []))
    messages.append(Message(role=role, content=content, agent=agent))
    return {**state, "messages": messages}
