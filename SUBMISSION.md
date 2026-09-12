# Devpost Submission Package — Amaze on Work

> **Hackathon**: [Agents for Humans Hackathon](https://agentsforhumans.devpost.com/)  
> **Track**: **Professional Agents** (*an agent that makes someone dramatically better at the work they already do*)  
> **Core Framework**: **Strands Agents SDK** (`strands-agents`)  
> **Cloud Architecture**: **Amazon Bedrock AgentCore**  

---

## 1. Project Overview

* **Project Title**: Amaze on Work — Autonomous Incident-to-Fix Engineering Agent
* **Tagline**: An autonomous DevOps agent built with Strands Agents SDK and Amazon Bedrock AgentCore that ingests production alerts, diagnoses root causes with GraphRAG, and validates bug fixes end-to-end in Docker sandboxes.
* **License**: MIT License (Open Source)
* **Target Audience**: Professional Software Engineers, Site Reliability Engineers (SREs), and DevOps Teams.

---

## 2. The Pitch (Mandatory Video & Text Questions)

### (1) The Problem You're Solving
Production incidents and regressions are the single largest source of repetitive, judgment-heavy toil for software engineers. When an alert fires in Slack or Jira, engineers lose hours manually context-switching:
- Parsing messy stack traces and ambiguous error reports.
- Tracing complex dependency call-graphs across microservices.
- Guessing root causes and risking secondary regressions with rushed code fixes.
- Manually running test suites, submitting pull requests, and updating stakeholders across fragmented tools.

This manual process costs tech companies over $500,000 per hour of downtime and drains engineering productivity by 40%.

### (2) Who It's For
Amaze on Work is purpose-built for **Professional Developers, DevOps Engineers, and SRE Teams** who are on-call and responsible for production system reliability. It operates autonomously in the background, triaging incidents from Slack, GitHub, and Jira, and only requests human intervention when an important architectural or high-risk decision must be made.

### (3) Why It Matters
Amaze on Work reduces Mean Time to Resolution (MTTR) from **hours to under 3 minutes** while virtually eliminating secondary regressions. By combining **Strands Agents SDK** model-driven tool execution, **Amazon Bedrock AgentCore** serverless runtime, and **ephemeral Docker container validation**, Amaze on Work guarantees that every proposed patch passes tests before opening a verified pull request.

---

## 3. How It Works (End-to-End Workflow)

```
[Production Alert] (Slack / Jira / GitHub Webhook)
         │
         ▼
┌─────────────────────────────────────────────────────────┐
│              Strands Agents SDK Coordinator             │
│                 (Bedrock AgentCore Runtime)             │
└────────┬─────────────────────────┬──────────────────────┘
         │                         │
         ▼                         ▼
┌──────────────────┐      ┌───────────────────────────────┐
│ Incident Parser  │      │ Neo4j Code Property Graph     │
│ (Symptoms, Logs) │      │ (Blast Radius & GraphRAG)     │
└────────┬─────────┘      └──────────────┬────────────────┘
         │                               │
         └──────────────┬────────────────┘
                        ▼
         ┌──────────────────────────────┐
         │ Adversarial Critic & Planner │
         │   (Review Strategy & Diffs)  │
         └──────────────┬───────────────┘
                        ▼
         ┌──────────────────────────────┐
         │ Fix Writer (Targeted Patches)│
         └──────────────┬───────────────┘
                        ▼
         ┌──────────────────────────────┐
         │ Ephemeral Docker Sandbox     │
         │ (Pytest / Jest Validation)   │
         │ - 0 Regressions Verified     │
         └──────────────┬───────────────┘
                        ▼
         ┌──────────────────────────────┐
         │ Ops Delivery & Handoff       │
         │ • GitHub Pull Request Created│
         │ • Slack Interactive Approval │
         │ • Jira Ticket Resolved       │
         └──────────────────────────────┘
```

---

## 4. How We Built It (Technology Stack)

* **Agent Orchestration**: **Strands Agents SDK** (`strands-agents`) providing model-driven tool execution, streaming, and modular agent primitives (`strands_agent.py`).
* **Deployment & Cloud Infrastructure**: **Amazon Bedrock AgentCore** architecture utilizing the AgentCore Runtime (`agentcore_app.py`), FastMCP Gateways (`agentcore.json`), and long-term memory.
* **Knowledge Representation**: Neo4j Code Property Graph (GraphRAG) mapping functions, calls, and imports to calculate the blast-radius of every proposed code change.
* **Sandbox Validation**: Isolated Docker containers (`amaze-python:base` and `amaze-node:base`) running automated test deltas (pytest / Jest) to prevent regressions.
* **Platform Integrations**: FastMCP bridges connecting Slack Block Kit, Jira REST APIs, and GitHub Pull Request automations.
* **LLM Intelligence**: Powered by Cerebras Llama 3.3 70B and Amazon Bedrock foundation models for lightning-fast reasoning and patch synthesis.

---

## 5. 5-Minute Demo Video Script Outline

* **[0:00 - 1:00] The Problem**: 
  - Show a realistic production error alert posted to `#incidents` on Slack.
  - Explain the engineer's frustration: waking up at 2 AM, digging through repos, fear of breaking other services.
* **[1:00 - 1:45] Who It's For & Why It Matters**:
  - Introduce Amaze on Work for the **Professional Agents** track.
  - Highlight how it autonomously runs in the background and only surfaces when critical decisions are needed.
* **[1:45 - 3:45] Live System Demonstration**:
  - Run `python strands_agent.py --incident INC-001` or `python demo.py`.
  - Show the live console output:
    1. Incident Parser extracts stack traces and symptoms.
    2. Neo4j queries blast radius of suspect functions.
    3. Critic evaluates root cause hypotheses.
    4. Fix Writer writes minimal diff.
    5. Docker sandbox executes pytest before/after (verifying `0 regressions`).
    6. Pull Request created automatically on GitHub.
* **[3:45 - 4:45] Architecture & AWS Integration**:
  - Present the architecture slide: Strands Agents SDK + Amazon Bedrock AgentCore Runtime + FastMCP Gateways.
  - Mention `agentcore_app.py` and `agentcore.json`.
* **[4:45 - 5:00] Conclusion**:
  - Wrap up: Safe, autonomous incident resolution for every professional engineering team.

---

## 6. AWS Builder Post Template (⭐ Bonus Points)

**Title**: Amaze on Work: Autonomous Incident-to-Fix Engineering Agent with Strands and Bedrock AgentCore (Agents for Humans)

**Body Outline**:
1. Introduction to on-call developer burnout.
2. Why we chose **Strands Agents SDK** for tool-driven agent autonomy.
3. How **Amazon Bedrock AgentCore** provides enterprise-grade runtime scaling, IAM-authenticated MCP gateways, and sandboxing.
4. Lessons learned integrating GraphRAG with containerized regression testing.
5. Links to our open-source GitHub repository and demo video.
