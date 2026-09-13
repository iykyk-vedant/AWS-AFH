# Amaze on Work — Complete Implementation Plan

## System Vision

A multi-agent system where:
1. **Trigger Agent** listens on Slack and Jira — auto-triggered when an incident is posted
2. **Incident Parser** reads natural language ticket, extracts error type, affected service, environment, stack trace, recent commits
3. **KnowledgeRetriever** queries Neo4j for past resolved similar incidents
4. **Codebase Analyst** fetches code via GitHub MCP + queries Code Property Graph (GraphRAG) for call chains and blast radius + smart code chunking + LLM root cause diagnosis
5. **Reasoning Layer** — a structured chain-of-thought that each agent appends to, building a full causal narrative: *why* the error occurred → *root source* (not just symptom file) → *what fix* was chosen and alternatives considered → *why this fix* is minimal and safe → *verified by* test delta
6. **Critic** reviews root cause + reasoning chain before fix
7. **Fix Planner & Patch Writer** generates minimal code fix + characterization test
8. **Validation Agent** runs tests in Docker sandbox: before/after comparison, regression detection
9. If fix works → **Synthesis Agent** assembles the full reasoning chain into structured report → notify Slack + create GitHub PR + update Jira ticket
10. If fix fails (3 retries) → **Web Research Agent** searches StackOverflow for community fixes → retry
11. **Risk Scorer** gates deployment (auto-PR / PR+notify / report-only)

---

## Competitive Analysis

### State of the Art (2025)

| System | Approach | SWE-bench Score | Key Differentiator |
|--------|----------|----------------|-------------------|
| Claude 4 Opus | Single LLM + tools | 76.8% Verified | Best model, large context |
| Live-SWE-agent | Self-evolving tool refinement | 75.4% Verified | On-the-fly tool improvement |
| Refact.ai | Multi-agent (debug + plan sub-agents) | 70.4% Verified | Strategic planning tool |
| Devin | Autonomous SWE agent | 67% PR merge rate | High autonomy |
| Globant GCFA | Multi-agent collaborative | 48.33% Lite | Fault localization specialization |
| **Amaze on Work** | Multi-agent + GraphRAG + Jira/Slack | TBD | **End-to-end incident lifecycle** |

### Where Amaze on Work Competes Differently

SWE-bench systems fix one-off GitHub issues. Amaze on Work solves a **different and harder problem**: *production incident lifecycle from alert to merged PR*, which none of those systems do. Key differentiators:

1. **GraphRAG Code Property Graph** — blast radius analysis before any fix. No SWE-bench winner uses a structural graph; they rely on raw code context. This means our Analyst understands *which other functions break if we change this one* — critical for preventing regressions in production.

2. **End-to-End Lifecycle** — trigger (Slack/Jira) → parse → analyze → fix → validate → PR → report → close ticket. The closest competitor (Devin) still requires a human to file the issue. We automate from alert to resolution.

3. **Community Fallback** — StackOverflow search after LLM exhaustion. This is a unique safety net no current system has: when the LLM is stuck in a retry loop, we escalate to human-validated real-world fixes.

4. **Structured Ops Integration** — native Jira + Slack + GitHub MCP chain. Production engineering requires incident tracking across all three; building that is an architectural moat.

### Plan Upgrades from Research

Research reveals 3 improvements we should add to beat Globant GCFA (48%) benchmark level:

**1. Fault Localization Before Fix Generation (inspired by Globant GCFA)**
- Add a dedicated localization pass: before fix_writer runs, explicitly confirm suspect file + line via a targeted code search
- This is a proven pattern that separates good systems from great ones

**2. Strategic Planning Tool (inspired by Refact.ai 70.4%)**
- Before fix_writer, have a `strategic_planning()` step: outline the fix strategy in pseudocode first, then generate actual code
- Prevents LLM from jumping to wrong fix approach

**3. Multi-hop Reasoning via Graph (inspired by IBM Instana's topology-based AI)**
- When querying Neo4j, don't just look at direct callers — traverse 2-3 hops to find indirect dependencies
- This maps to real blast radius, not just immediate callers

These are added to Phase 4 (GraphRAG wiring).

---



- Repo: `https://github.com/Rezinix-AI/shopstack-platform`
- Agent reads: `Readme_Agent.md` for setup + test commands
- 16 intentional bugs (8 Python, 8 Node.js) — tests are designed to fail, after fix all pass with 0 failures
- **Python tests**: `cd python-service && python -m pytest tests/ -v`
- **Node tests**: `cd node-service && npm test`
- Docker NOT needed for tests — they use in-memory SQLite

---

## Gap Analysis (Verified Against Codebase)

### 1. Trigger Agent — MISSING ENTIRELY
- **Required:** Agent listening on Slack and Jira to auto-trigger the pipeline when an incident is posted
- **Actual:** No such file exists. Pipeline only starts via manual CLI command or API call.
- **Files affected:** Need new `src/agents/trigger_agent.py`, wire into [src/api/routes/slack_webhook.py](file:///d:/LocalClaude-AWS-AFH/src/api/routes/slack_webhook.py) (exists but not wired), new `src/api/routes/jira_webhook.py`

### 2. Slack Thread Workflow — BROKEN
- **Required:** Thread-based progress updates (received → analyzing → fix found → validating → PR created → report)
- **Actual:** [slack_server.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/slack_server.py) has [post_message](file:///d:/LocalClaude-AWS-AFH/src/mcp/slack_server.py#43-59), [post_resolution](file:///d:/LocalClaude-AWS-AFH/src/mcp/slack_server.py#105-166), [post_incident_started](file:///d:/LocalClaude-AWS-AFH/src/mcp/slack_tools.py#152-162) tools but supervisor never calls them during pipeline execution — only called at end in demo scripts
- **Files affected:** [src/agents/supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py), [src/mcp/slack_server.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/slack_server.py), [src/mcp/client_bridge.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/client_bridge.py)

### 3. Report Channel — MISSING
- **Required:** Minimalist notification to incident channel + detailed report to a separate Slack report channel
- **Actual:** Single [post_resolution](file:///d:/LocalClaude-AWS-AFH/src/mcp/slack_server.py#105-166) call exists but no concept of a separate report channel. `SLACK_REPORT_CHANNEL` not in [.env.example](file:///d:/LocalClaude-AWS-AFH/.env.example)
- **Files affected:** [src/mcp/slack_server.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/slack_server.py), [.env.example](file:///d:/LocalClaude-AWS-AFH/.env.example), [src/agents/supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py)

### 4. GitHub Auto-PR — EXISTS BUT NEVER CALLED
- **Required:** After successful fix + validation, create branch → commit fix → open PR on target repo
- **Actual:** [client_bridge.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/client_bridge.py) has a fully implemented [create_fix_pr()](file:///d:/LocalClaude-AWS-AFH/src/mcp/client_bridge.py#132-180) method (lines 132–179) — but [supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py) never calls it. PR flow is completely disconnected from the pipeline.
- **Files affected:** [src/agents/supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py) (add call after validation passes)

### 5. GraphRAG Not Wired Into Analysis
- **Required:** CodebaseAnalyst uses Neo4j Code Property Graph (call chains, blast radius, imports) for root cause diagnosis
- **Actual:** [kg_builder.py](file:///d:/LocalClaude-AWS-AFH/src/agents/kg_builder.py) builds the graph (File, Function, Class, Import nodes + CALLS/IMPORTS/CONTAINS edges) and [knowledge_retriever.py](file:///d:/LocalClaude-AWS-AFH/src/agents/knowledge_retriever.py) has [get_blast_radius()](file:///d:/LocalClaude-AWS-AFH/src/graph/query_interface.py#137-171), [find_similar_incidents()](file:///d:/LocalClaude-AWS-AFH/src/graph/query_interface.py#172-207) — but [codebase_analyst.py](file:///d:/LocalClaude-AWS-AFH/src/agents/codebase_analyst.py) never imports or queries either. It only does raw GitHub file fetch + LLM call.
- **Files affected:** [src/agents/codebase_analyst.py](file:///d:/LocalClaude-AWS-AFH/src/agents/codebase_analyst.py)

### 6. KnowledgeRetriever — EXISTS BUT NOT IN PIPELINE
- **Required:** Query Neo4j for past resolved incidents before analysis to provide historical context
- **Actual:** [knowledge_retriever.py](file:///d:/LocalClaude-AWS-AFH/src/agents/knowledge_retriever.py) is fully implemented with [find_similar_incidents()](file:///d:/LocalClaude-AWS-AFH/src/graph/query_interface.py#172-207), [get_blast_radius()](file:///d:/LocalClaude-AWS-AFH/src/graph/query_interface.py#137-171), [format_for_prompt()](file:///d:/LocalClaude-AWS-AFH/src/agents/knowledge_retriever.py#104-135) — but [supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py) only initializes 7 agents (no [KnowledgeRetrieverAgent](file:///d:/LocalClaude-AWS-AFH/src/agents/knowledge_retriever.py#22-141)). [state.py](file:///d:/LocalClaude-AWS-AFH/src/agents/state.py) even has a `knowledge_context` field reserved for it.
- **Files affected:** [src/agents/supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py) (add as 2nd step after parser)

### 7. Docker Sandbox Test Discovery — BROKEN
- **Required:** Run `python-service` tests with `pytest tests/ -v` and `node-service` tests with `npm test`
- **Actual:** [docker_runner.py](file:///d:/LocalClaude-AWS-AFH/src/sandbox/docker_runner.py) uses wrong paths and generic discovery. Always returns 0 tests found (NO_CHANGE verdict). The correct commands per `Readme_Agent.md` are: `cd python-service && python -m pytest tests/ -v` and `cd node-service && npm test`
- **Files affected:** [src/sandbox/docker_runner.py](file:///d:/LocalClaude-AWS-AFH/src/sandbox/docker_runner.py), [src/agents/validation.py](file:///d:/LocalClaude-AWS-AFH/src/agents/validation.py)

### 8. Smart Code Chunking — MISSING
- **Required:** Split files by AST function/class boundaries before sending to LLM (from tata-lcr project)
- **Actual:** [codebase_analyst.py](file:///d:/LocalClaude-AWS-AFH/src/agents/codebase_analyst.py) sends raw file content truncated at 3000 chars. No AST-based chunking. Relevant functions may get cut off mid-body.
- **Files affected:** [src/agents/codebase_analyst.py](file:///d:/LocalClaude-AWS-AFH/src/agents/codebase_analyst.py), new `src/utils/code_chunker.py`

### 9. FixWriter Characterization Test — FIELD EXISTS, ALWAYS None
- **Required:** Generate a test case that reproduces the bug (fails before fix, passes after)
- **Actual:** [state.py](file:///d:/LocalClaude-AWS-AFH/src/agents/state.py) line 111 has `characterization_test: str | None` in [FixPlan](file:///d:/LocalClaude-AWS-AFH/src/agents/state.py#105-113). [fix_writer.py](file:///d:/LocalClaude-AWS-AFH/src/agents/fix_writer.py) line 172 sets it to `None` — always.
- **Files affected:** [src/agents/fix_writer.py](file:///d:/LocalClaude-AWS-AFH/src/agents/fix_writer.py)

### 10. StackOverflow Fallback — MISSING ENTIRELY
- **Required:** After 3 failed retries, search StackOverflow for community fixes instead of giving up
- **Actual:** No such file exists. Supervisor just logs "pipeline failed" after 3 attempts. [AgentType](file:///d:/LocalClaude-AWS-AFH/src/agents/state.py#12-24) enum does not include a `WEB_RESEARCH` type.
- **Files affected:** New `src/agents/web_research_agent.py`, [src/agents/state.py](file:///d:/LocalClaude-AWS-AFH/src/agents/state.py) (add enum), [src/agents/supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py) (route after retries exhausted)

### 11. Jira Integration — MISSING ENTIRELY
- **Required:** Fetch Jira tickets as incident sources, update status (Open → In Progress → Resolved), add resolution comment, link GitHub PR
- **Actual:** No Jira files anywhere in the project. `JIRA_URL`, `JIRA_API_TOKEN` not in [.env.example](file:///d:/LocalClaude-AWS-AFH/.env.example).
- **Files affected:** New `src/mcp/jira_server.py`, new `src/api/routes/jira_webhook.py`, [src/mcp/client_bridge.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/client_bridge.py), [.env.example](file:///d:/LocalClaude-AWS-AFH/.env.example), [requirements.txt](file:///d:/LocalClaude-AWS-AFH/requirements.txt)

### 12. Supervisor Is Plain While-Loop — NOT LangGraph
- **Required:** LangGraph `StateGraph` with conditional routing edges
- **Actual:** [supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py) is a `while current_step < len(pipeline)` loop with manual index tracking. [PipelineState](file:///d:/LocalClaude-AWS-AFH/src/agents/state.py#172-202) is defined as a `TypedDict` (correct) but no actual LangGraph graph is constructed.
- **Files affected:** [src/agents/supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py)

### 13. Duplicate / Dead Files — TO CLEAN
- [src/mcp/github_tools.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/github_tools.py) — same functionality as [github_server.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/github_server.py), imported by [codebase_analyst.py](file:///d:/LocalClaude-AWS-AFH/src/agents/codebase_analyst.py) and [fix_writer.py](file:///d:/LocalClaude-AWS-AFH/src/agents/fix_writer.py)
- [src/mcp/slack_tools.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/slack_tools.py) — same functionality as [slack_server.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/slack_server.py)
- [tests/list_slack_channels.py](file:///d:/LocalClaude-AWS-AFH/tests/list_slack_channels.py), [tests/test_slack.py](file:///d:/LocalClaude-AWS-AFH/tests/test_slack.py) — temp debug scripts
- All agent files — emoji/unicode characters in log strings (cause Windows cp1252 crash)
- [Previous_PROJECT_DOCUMENTATION.md](file:///d:/LocalClaude-AWS-AFH/Previous_PROJECT_DOCUMENTATION.md), [IMPLEMENTATION.md](file:///d:/LocalClaude-AWS-AFH/IMPLEMENTATION.md) — stale

---

## Proposed Changes

### Phase 1: Cleanup

#### Delete these files:
- [src/mcp/github_tools.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/github_tools.py) — duplicate, replaced by [github_server.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/github_server.py)
- [src/mcp/slack_tools.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/slack_tools.py) — duplicate, replaced by [slack_server.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/slack_server.py)
- [tests/list_slack_channels.py](file:///d:/LocalClaude-AWS-AFH/tests/list_slack_channels.py) — temp debug script
- [tests/test_slack.py](file:///d:/LocalClaude-AWS-AFH/tests/test_slack.py) — temp debug script
- [Previous_PROJECT_DOCUMENTATION.md](file:///d:/LocalClaude-AWS-AFH/Previous_PROJECT_DOCUMENTATION.md) — stale
- [IMPLEMENTATION.md](file:///d:/LocalClaude-AWS-AFH/IMPLEMENTATION.md) — stale

#### Modify all agent files:
- Remove all emoji and Unicode characters from log/output strings
- Remove all `# TODO`, `# Future`, placeholder comments
- Clean unused imports

---

### Phase 2: Fix Sandbox Test Validation (P0 — 30% of score)

#### [MODIFY] [docker_runner.py](file:///d:/LocalClaude-AWS-AFH/src/sandbox/docker_runner.py)
- Python service: `cd python-service && pip install -r requirements.txt && python -m pytest tests/ -v --tb=short`
- Node service: `cd node-service && npm install && npm test -- --verbose`
- Parse individual test names and pass/fail status from output
- Track: which tests newly pass, which newly fail (regressions)

#### [MODIFY] [validation.py](file:///d:/LocalClaude-AWS-AFH/src/agents/validation.py)
- Wire correct test commands per service type
- Track individual test names before/after for regression detection

---

### Phase 3: LangGraph Supervisor Rewrite (P0 — 15% of score)

#### [MODIFY] [supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py)
Rewrite as proper LangGraph `StateGraph`:
```
[START]
  --> incident_parser
  --> knowledge_retriever       # NEW: query past incidents
  --> codebase_analyst
  --> critic
  --> fix_writer
  --> validation
       |-- PASS --> synthesis --> risk_scorer --> reporter --> [END]
       |-- FAIL (retry < 3) --> fix_writer (loop)
       |-- FAIL (retry >= 3) --> web_research_agent --> fix_writer --> validation --> ...
```
- Slack progress callbacks at each node transition
- After validation passes: call [create_fix_pr()](file:///d:/LocalClaude-AWS-AFH/src/mcp/client_bridge.py#132-180) and update Jira

---

### Phase 4: Wire GraphRAG + KnowledgeRetriever (P0 — 20% of score)

#### [MODIFY] [codebase_analyst.py](file:///d:/LocalClaude-AWS-AFH/src/agents/codebase_analyst.py)
- Auto-ingest repo into Neo4j via KGBuilder on first run (if not in graph)
- Before LLM call: query Neo4j for:
  - Call chains: what functions call the suspect function
  - Blast radius: all downstream dependents
  - Import graph: what modules import the suspect file
- Add smart code chunking: split files by AST function/class boundaries using the `CodeChunker` ported from the tata-lcr project (see Phase 10), send only relevant chunks to LLM

#### [MODIFY] [supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py)
- Add `knowledge_retriever` as second node in the pipeline (after parser, before analyst)
- Pass historical context from KR into analyst's state

---

### Phase 5: Trigger Agent — Slack Incident Channel + Jira (P1 — 10% bonus)

#### Slack Incident Channel & Jira Setup
- **Slack**: Create a dedicated `#incidents` channel. Any message posted here (in plain English) auto-triggers the pipeline.
- **Jira**: Any new ticket created with `bug` or [incident](file:///d:/LocalClaude-AWS-AFH/src/mcp/github_tools.py#242-251) label auto-triggers the pipeline.
- **Immediate Acknowledgment**: The system instantly replies in the Slack thread or Jira ticket:
  ```
  INC-0042 assigned | Analyzing your incident now...
  Service: python-service | Severity: HIGH
  ```

#### Live Agent Visibility (Presentation Ready)
**Crucial for the demo:** The system will post live updates to the Slack thread and the Jira ticket comments *as each agent starts*. This means you hold the entire presentation in the Slack/Jira UI, without ever showing the terminal.
*Progress update examples posted live:*
- `[Status] 🕵️ Incident Parser extracting context...`
- `[Status] 📚 Knowledge Retriever checking past incidents...`
- `[Status] 🔬 Codebase Analyst querying GraphRAG for root cause...`
- `[Status] 🛠️ Fix Planner & Patch Writer generating fix...`
- `[Status] 🧪 Validation Agent running sandbox tests...`

#### Incident Number Assignment
- Auto-incrementing counter: `src/utils/incident_counter.py` (reads/writes to `incidents.json`)
- Each new incident gets `INC-{counter:04d}` (e.g., `INC-0042`)
- This ID threads through the entire pipeline: Slack threads, Jira tickets, Branch names, PR titles, Report names.

#### [NEW] [trigger_agent.py](file:///d:/LocalClaude-AWS-AFH/src/agents/trigger_agent.py)
Triggered by:
1. **Slack**: message in `#incidents` channel via `message.channels` event (filters out bot loops).
2. **Jira**: `issue_created` webhook.
For each trigger:
- Assign incident number via `incident_counter.next_id()`
- Post immediate acknowledgement (`INC-XXXX assigned...`) to Slack/Jira.
- Kick off [supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py) pipeline.

#### Webhooks
- **[MODIFY] [slack_webhook.py](file:///d:/LocalClaude-AWS-AFH/src/api/routes/slack_webhook.py)**: Wire `message.channels` event → `trigger_agent.py`.
- **[NEW] [jira_webhook.py](file:///d:/LocalClaude-AWS-AFH/src/api/routes/jira_webhook.py)**: Wire `issue_created` event → `trigger_agent.py`.

---

### Phase 6: Live GitHub Auto-PR + Slack/Jira Channel Workflow (P1 — 10% bonus)

#### [MODIFY] [supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py)
**Live Callbacks:** At each stage transition, the supervisor fires a callback to [client_bridge.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/client_bridge.py) to post the "Live Agent Visibility" string (listed above) to the active Slack thread AND Jira ticket comments.

**Validation & PR Generation:**
- The **Docker Sandbox** ([docker_runner.py](file:///d:/LocalClaude-AWS-AFH/src/sandbox/docker_runner.py)) MUST execute the fix against the tests. If they pass (0 regressions):
- Call `bridge.create_fix_pr()` → creates branch `fix/INC-XXXX`, commits the fix, and opens the PR on the `shopstack-platform` repo.
- Post the final successful PR link back to the Slack thread and Jira ticket to close out the demo flow.

#### [MODIFY] [client_bridge.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/client_bridge.py)
- Implement `post_progress_update(slack_raw, jira_raw, status_msg)` to push the live agent status updates to both platforms concurrently.

---

### Phase 6: GitHub Auto-PR + Slack Channel Workflow (P1 — 10% bonus)

#### [MODIFY] [supervisor.py](file:///d:/LocalClaude-AWS-AFH/src/agents/supervisor.py)
After validation passes:
1. Call `bridge.create_fix_pr(owner, repo, incident_id, fix_plan, report_body)` — creates branch, commits fix, opens PR on shopstack-platform
2. Post PR URL to Slack incident thread

Slack thread workflow (all in same thread as original incident message):
- "Incident received — starting analysis..."
- "Root cause identified: [brief]"
- "Fix generated — running validation in Docker sandbox..."
- "Validation: [PASS/FAIL] — [test summary]"
- "PR created: [url]"
- "Report ready" → link to detailed report in report channel

#### [MODIFY] [client_bridge.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/client_bridge.py)
- Fix [create_fix_pr()](file:///d:/LocalClaude-AWS-AFH/src/mcp/client_bridge.py#132-180): get full modified file content (original + applied patch) before committing
- Add `post_progress_update(channel, thread_ts, step, detail)` for Slack thread updates
- Add `post_detailed_report(channel, report)` for report channel

#### [MODIFY] [slack_server.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/slack_server.py)
- Add `post_progress_update` tool
- Add `post_detailed_report` tool with Block Kit formatting

---

### Phase 7: Jira MCP Integration (P1 — 10% bonus)

#### [NEW] [jira_server.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/jira_server.py)
FastMCP server with tools:
- `get_incident_tickets(project_key, status)` — fetch open incidents
- `get_ticket(ticket_id)` — fetch single ticket
- `create_ticket(project, title, description, priority)` — create new
- `update_ticket_status(ticket_id, status)` — Open → In Progress → Resolved
- `add_comment(ticket_id, comment)` — attach resolution report
- `link_pr(ticket_id, pr_url)` — link GitHub PR to ticket

#### [MODIFY] [client_bridge.py](file:///d:/LocalClaude-AWS-AFH/src/mcp/client_bridge.py)
- Add Jira tool wrappers
- After resolution: `update_ticket_status()` + `add_comment(resolution_report)`

#### [MODIFY] [.env.example](file:///d:/LocalClaude-AWS-AFH/.env.example) + [.env](file:///d:/LocalClaude-AWS-AFH/.env)
- Add `JIRA_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`, `JIRA_PROJECT_KEY`

#### [MODIFY] [requirements.txt](file:///d:/LocalClaude-AWS-AFH/requirements.txt)
- Add `jira>=3.5.0`

---

### Phase 8: StackOverflow Fallback + Characterization Tests (P1 — 20% intelligence)

#### [NEW] [web_research_agent.py](file:///d:/LocalClaude-AWS-AFH/src/agents/web_research_agent.py)
Triggered after 3 failed validation retries:
- Searches StackOverflow API (`api.stackexchange.com`) for error message + root cause keywords
- Extracts top-voted answers with code snippets and accepted answers
- Structures findings into: `potential_fixes[]` with code snippets + explanation
- Returns to supervisor which feeds these to FixWriter as new context for retry

#### [MODIFY] [fix_writer.py](file:///d:/LocalClaude-AWS-AFH/src/agents/fix_writer.py)
Generate characterization test alongside fix:
- If incident has no test covering the bug, generate a minimal pytest / jest test that:
  - Reproduces the bug on unfixed code (should fail)
  - Passes on fixed code
- Write the test to `python-service/tests/test_{incident_id}.py` or `node-service/tests/{incident_id}.test.js`
- Include in the patch alongside the fix

---

## New Files Summary

| File | Type | Purpose |
|------|------|---------|
| `src/agents/trigger_agent.py` | NEW | Slack/Jira listener → triggers pipeline |
| `src/agents/web_research_agent.py` | NEW | StackOverflow search after 3 retries |
| `src/mcp/jira_server.py` | NEW | Jira MCP server (6 tools) |
| `src/api/routes/jira_webhook.py` | NEW | Jira webhook event handler |
| `src/utils/incident_counter.py` | NEW | Auto-incrementing INC-XXXX number generator |
| `src/utils/code_chunker.py` | NEW | AST-based code chunker (ported from tata-lcr) |
| `src/utils/smart_links.py` | NEW | VS Code deep-links + code snippet renderer (ported from tata-lcr) |

---

### Phase 10: Smart Linking System (from tata-lcr)

The tata-lcr project has two utilities in `src/utils/` that we port directly:

#### [NEW] [code_chunker.py](file:///d:/LocalClaude-AWS-AFH/src/utils/code_chunker.py)
Port `CodeChunker` + `CodeChunk` dataclass from tata-lcr verbatim:
```python
@dataclass
class CodeChunk:
    content: str
    start_line: int
    end_line: int
    chunk_type: str   # 'function', 'class', 'module', 'block', 'full'
    name: str | None = None

class CodeChunker:
    DEFAULT_MAX_LINES = 500
    DEFAULT_MAX_CHARS = 50_000

    def chunk_by_structure(content, language) -> list[CodeChunk]
    # Python: splits at 'def ', 'class ', 'async def ' boundaries
    # Other: splits by line count blocks
```
**Where it's used:** `codebase_analyst.py` — before sending file content to LLM, apply chunker. Only the chunk containing the suspect function/line is sent, not the whole file. This prevents LLM context overflow on large files.

#### [NEW] [smart_links.py](file:///d:/LocalClaude-AWS-AFH/src/utils/smart_links.py)
Port `SmartLinks` from tata-lcr — generates clickable VS Code links and HTML code snippets for every finding:
```python
class SmartLinks:
    def vscode_link(file_path, line, end_line=None) -> str
    # Returns: vscode://file/{path}:{line}:1:{end_line}:999

    def format_snippet(lines, line_nums, context=3) -> str
    # Returns HTML-formatted code block with line numbers
    # Highlights the exact line(s) of the fix
```
**Where it's used:**
- `synthesis.py` — for each file modified in the fix, generate a `vscode://` link + code snippet (before/after diff) in the resolution report
- Slack report — include `file.py:L47` with clickable link in the Block Kit message
- Jira comment — inline code blocks with exact line references so engineers can click directly into their editor

**Concrete output example:**
```
Fix applied to: src/auth/user.py:L47
[Open in VS Code](vscode://file/c:/Projects/shopstack/python-service/src/auth/user.py:47:1:47:999)

Before:
47 │ return bcrypt.check_password_hash(user.password_hash, password)

After:
47 │ if not user.password_hash:
48 │     return False  
49 │ return bcrypt.check_password_hash(user.password_hash, password)
```

---

### Phase 9: Reasoning Layer

The reasoning layer is NOT a separate agent. It is a **structured chain-of-thought object** (`ReasoningChain`) threaded through `PipelineState`. Each agent appends one reasoning step, and Synthesis assembles the full chain into the report.

#### [MODIFY] [state.py](file:///d:/LocalClaude-AWS-AFH/src/agents/state.py)
Add to `PipelineState`:
```python
class ReasoningStep(TypedDict):
    agent: str
    question: str   # What was this agent trying to answer?
    finding: str    # What did it conclude?
    evidence: str   # What code / data supports this?
    confidence: float

reasoning_chain: list[ReasoningStep]
```

#### Per-agent reasoning appends:

| Agent | Question Answered | What It Appends |
|-------|-------------------|-----------------|
| `incident_parser` | What type of failure is this? | Error class, affected service, failure type classification |
| `codebase_analyst` | Why did this error occur? What is the root source? | Causal chain from graph traversal + LLM reasoning |
| `critic` | Is the root cause analysis correct? | Confidence verdict + doubts |
| `fix_writer` | What fix was applied and why? | Fix description, alternatives considered, why this is minimal |
| `validation` | Did the fix work? Any new regressions? | Test delta: before vs after counts + names |
| `risk_scorer` | Is it safe to ship this fix? | Blast radius scope, risk level, policy |

#### [MODIFY] [synthesis.py](file:///d:/LocalClaude-AWS-AFH/src/agents/synthesis.py)
Assemble `reasoning_chain` into two formats:
- **Slack minimalist format**: 4-line summary — error, root cause, fix, verdict
- **Detailed report section**: Full Q&A reasoning chain with evidence for each step

---

## Verification Plan

### Per-Incident
1. Run tests BEFORE fix → confirm target tests fail
2. Apply fix + characterization test
3. Run tests AFTER → all pass, 0 regressions
4. Verify PR created on GitHub with correct branch + diff
5. Verify Slack thread shows all 6 progress steps + report in report channel
6. Verify Jira ticket status = Resolved + report attached as comment

### Integration
- Post incident in Slack → trigger agent → full pipeline → PR + reports + Jira updated
- Create Jira ticket → trigger agent → full pipeline → Jira updated
- After 3 retries → StackOverflow agent activates + logs found answers
- `python demo_batch.py --slack-channel C0AL8NG5J79` → all 16 incidents resolved
