# Amaze on Work — Complete Setup & Deployment Guide

> **Project**: Amaze on Work — Autonomous Incident-to-Fix Engineering Agent  
> **Track**: Professional Agents (AWS Agents for Humans Hackathon)  
> **Core Frameworks**: Strands Agents SDK (`strands-agents`) & Amazon Bedrock AgentCore  

This comprehensive guide covers everything needed to configure, test, run, and host **Amaze on Work** locally and on AWS.

---

## Table of Contents
1. [Prerequisites & Requirements](#1-prerequisites--requirements)
2. [Environment Configuration (.env)](#2-environment-configuration-env)
3. [Local Installation & Setup](#3-local-installation--setup)
4. [System Health & Integration Verification](#4-system-health--integration-verification)
5. [Running Amaze on Work (4 Execution Modes)](#5-running-amaze-on-work-4-execution-modes)
6. [AWS Deployment Guide & $50 Credits Strategy](#6-aws-deployment-guide--50-credits-strategy)
   - [Option A: Amazon EC2 (Recommended for Demos)](#option-a-amazon-ec2-t4gmedium-recommended-for-demos)
   - [Option B: AWS Lambda (Serverless & Free Tier)](#option-b-aws-lambda-serverless--free-tier)
   - [Option C: Amazon Bedrock AgentCore Deployment](#option-c-amazon-bedrock-agentcore-deployment)
7. [Troubleshooting & FAQs](#7-troubleshooting--faqs)

---

## 1. Prerequisites & Requirements

* **Python**: `3.11` or `3.12`
* **Git**: Installed and available in PATH
* **Docker & Docker Compose**: Optional for local testing, recommended for containerized test sandboxes and Neo4j
* **API Keys**:
  * **Cerebras Cloud API Key** (Free tier from [cloud.cerebras.ai](https://cloud.cerebras.ai))
  * **GitHub Personal Access Token** (classic token with `repo` scope or fine-grained with read/write to repository contents & pull requests)
  * *(Optional)* Slack Bot Token & Signing Secret (for Slack alerts & interactive `#fix-selection`)
  * *(Optional)* Atlassian Jira API Token (for Jira ticket status synchronization)

---

## 2. Environment Configuration (`.env`)

Create your `.env` file in the project root by copying the template:

```powershell
Copy-Item .env.example .env
```
*(On macOS / Linux / WSL: `cp .env.example .env`)*

### Environment Variables Reference

| Variable | Required? | Default / Example | Description |
| :--- | :---: | :--- | :--- |
| **`CEREBRAS_API_KEY`** | **Yes** | `csk-...` | Fast Llama 3.3 70B inference engine for multi-agent reasoning. |
| `CEREBRAS_BASE_URL` | No | `https://api.cerebras.ai/v1` | Cerebras OpenAI-compatible API base URL. |
| `CEREBRAS_MODEL` | No | `llama-3.3-70b` | Model family for agent reasoning. |
| **`GITHUB_TOKEN`** | **Yes** | `ghp_...` | GitHub token for reading code, analyzing branches, and opening PRs. |
| **`GITHUB_OWNER`** | **Yes** | `iykyk-vedant` | Target organization or user where the target repo lives. |
| **`GITHUB_REPO`** | **Yes** | `AFH-DEMO` | Target microservice repository analyzed and repaired by the agent. |
| `SLACK_BOT_TOKEN` | No | `xoxb-...` | Slack Bot User OAuth Token (`chat:write`, `channels:read`). |
| `SLACK_SIGNING_SECRET` | No | `...` | Secret from Slack App Settings to authenticate webhooks. |
| `SLACK_INCIDENTS_CHANNEL_ID` | No | `C0AL8NG5J79` | Slack channel ID for `#incidents` triage alerts. |
| `SLACK_REPORT_CHANNEL` | No | `C0AL8NG5J79` | Slack channel ID for detailed resolution reports. |
| `JIRA_URL` | No | `https://yourorg.atlassian.net` | Atlassian instance URL. |
| `JIRA_EMAIL` | No | `dev@yourorg.com` | Atlassian account email. |
| `JIRA_API_TOKEN` | No | `...` | Atlassian API security token. |
| `JIRA_PROJECT_KEY` | No | `FIX` | Jira project key for incident tracking. |
| `NEO4J_URI` | No | `bolt://localhost:7687` | Neo4j connection URI *(auto-falls back to NetworkX if offline)*. |
| `NEO4J_USER` | No | `neo4j` | Neo4j database user. |
| `NEO4J_PASSWORD` | No | `amaze_2026` | Neo4j database password. |
| `PORT` | No | `8000` | REST API / Webhook server port. |
| `RISK_AUTO_MERGE_THRESHOLD` | No | `LOW` | Auto-merge threshold (`LOW`, `MEDIUM`, `HIGH`). |

> [!NOTE]
> **Minimal Setup to Run**: You only need **`CEREBRAS_API_KEY`** and **`GITHUB_TOKEN`** to run the agents, diagnose code, run tests, and create automated pull requests!

---

## 3. Local Installation & Setup

### Step 1: Clone Repository & Create Virtual Environment
```bash
git clone https://github.com/iykyk-vedant/AWS-AFH.git
cd AWS-AFH

# Create virtual environment
python -m venv venv

# Activate on Windows:
.\venv\Scripts\Activate.ps1

# Activate on Linux / macOS:
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### Step 2 (Optional): Start Neo4j Database
If you want persistent graph indexing with the web browser interface at `http://localhost:7474`:
```bash
docker-compose up -d neo4j
```
> [!TIP]
> If Docker or Neo4j is not running, Amaze on Work **automatically falls back to its built-in in-memory NetworkX graph backend** with zero configuration required.

### Step 3 (Optional): Build Isolated Sandbox Containers
To run isolated test executions in Docker for Node and Python:
```bash
docker build -f docker/node.Dockerfile -t amaze-node:base .
docker build -f docker/python.Dockerfile -t amaze-python:base .
```

---

## 4. System Health & Integration Verification

Verify that all subsystems and environment variables are operational:

```bash
# Full 6-subsystem integration diagnostic
python tests/integration_check.py
```
This tests:
1. **LLM (Cerebras)**: Connectivity and response latency.
2. **GitHub API**: Rate limits and token permissions.
3. **Slack API**: Channel access and bot authentication.
4. **Docker Sandbox**: Container image availability.
5. **Knowledge Graph**: Neo4j / NetworkX graph engine.
6. **Pipeline**: Dry-run incident triage.

You can also run the quick API health check:
```bash
python -m src.api.main health
```

---

## 5. Running Amaze on Work (4 Execution Modes)

### Mode 1: Interactive Terminal Demo Runner (Recommended for Demos)
Runs the full 7-step autonomous healing pipeline on an incident ticket with a rich terminal UI:
```bash
# Run default incident (INC-001)
python demo.py

# Run a specific incident ticket
python demo.py --incident INC-003

# Run with Slack notifications posted to your channel
python demo.py --incident INC-001 --slack-channel #incidents
```

### Mode 2: FastAPI REST & Webhook Server
Starts the REST API and webhook receiver:
```bash
python -m src.api.main server --port 8000
```
* **Interactive API Docs**: `http://localhost:8000/docs`
* **Resolve Incident Endpoint**: `POST /api/incidents/resolve`
* **Slack Events Webhook**: `POST /api/webhooks/slack`
* **Human-in-the-Loop Fix Selection Webhook**: `POST /api/webhooks/fix-selection` (accepts Slack commands like `!fix 2 INC-0034`)

### Mode 3: Strands Agents SDK CLI Runner
Executes via the AWS Strands Agents tool coordinator:
```bash
python strands_agent.py --incident INC-001
python strands_agent.py --prompt "Fix production 500 error in auth login"
```

### Mode 4: Amazon Bedrock AgentCore Runtime
Runs the local AgentCore emulator:
```bash
python agentcore_app.py
```

---

## 6. AWS Deployment Guide & $50 Credits Strategy

### How to Maximize $50 in AWS Credits

| Hosting Method | Monthly Cost | Lifespan of $50 Credits | Recommendation |
| :--- | :--- | :--- | :--- |
| **AWS Lambda (Serverless)** | **$0.00 – $1.00** | **Indefinite (Free Tier)** | Best for $0 idle cost |
| **EC2 `t4g.small` (Stopped when idle)** | **~$5.00 – $10.00** | **5+ Months** | Great for active testing |
| **EC2 `t4g.medium` (24/7 during judging)** | **~$28.00 – $32.00** | **~1.5 to 2 Months** | Best for seamless live demos |

> [!IMPORTANT]
> **Testing Phase Spend**: Testing consumes very little compute. Running 100 incident simulations uses less than **$3.00** in LLM tokens and **~$2.00** in on-demand compute. Your $50 credits will comfortably cover the entire development and judging period.

---

### Option A: Amazon EC2 (`t4g.medium`) — Recommended for Demos

Runs FastAPI, Strands Agents, Docker sandbox, and Neo4j on an ARM64 Graviton instance:

1. **Launch EC2 Instance**:
   * **Name**: `amaze-on-work-server`
   * **AMI**: Ubuntu Server 24.04 LTS
   * **Architecture**: **`64-bit (Arm)`**
   * **Instance Type**: `t4g.medium` (2 vCPU, 4GB RAM — ~$0.0336/hr)
   * **Storage**: 30 GiB `gp3` SSD
   * **Security Group Rules**:
     * Port `22` (SSH)
     * Port `8000` (FastAPI Server & Webhooks)
     * *(Optional)* Port `7474` (Neo4j Browser)
2. **Setup on Server**:
   ```bash
   sudo apt update && sudo apt install -y docker.io docker-compose python3-pip python3-venv git
   sudo usermod -aG docker ubuntu
   newgrp docker

   git clone https://github.com/iykyk-vedant/AWS-AFH.git
   cd AWS-AFH
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt

   cp .env.example .env
   nano .env  # Add your CEREBRAS_API_KEY and GITHUB_TOKEN

   docker-compose up -d neo4j
   docker build -f docker/node.Dockerfile -t amaze-node:base .
   docker build -f docker/python.Dockerfile -t amaze-python:base .

   # Run as a background service
   nohup python3 -m src.api.main server --host 0.0.0.0 --port 8000 > server.log 2>&1 &
   ```

---

### Option B: AWS Lambda (Serverless & Free Tier)

For near-zero cost, host the API and Strands coordinator in AWS Lambda:
1. **Packaging**: Package the application as a **Lambda Container Image** with Python and Node pre-installed.
2. **Web Adapter**: Use `mangum` to wrap `src.api.main:app` as a Lambda handler:
   ```python
   from mangum import Mangum
   from src.api.main import app
   handler = Mangum(app)
   ```
3. **Function URL**: Enable **AWS Lambda Function URL** for a free HTTPS webhook endpoint without paying for API Gateway.
4. **Graph & Storage**: Use the in-memory **NetworkX** graph and store incident reports in **Amazon S3**.

---

### Option C: Amazon Bedrock AgentCore Deployment

Amaze on Work includes native integration files for Bedrock AgentCore:
* **Adapter**: [`agentcore_app.py`](file:///d:/LocalClaude-AWS-AFH/agentcore_app.py)
* **Manifest**: [`agentcore.json`](file:///d:/LocalClaude-AWS-AFH/agentcore.json)

#### Method C1: CLI Deployment
```bash
# 1. Configure AWS CLI
aws configure

# 2. Test locally in the AgentCore emulator
python agentcore_app.py

# 3. Deploy via AgentCore CLI
agentcore deploy --config agentcore.json --region us-east-1
```

#### Method C2: AWS Management Console (Bedrock UI)
1. In the **AWS Console**, navigate to **Amazon Bedrock** (Region: `us-east-1` or `us-west-2`).
2. Go to **Model Access** and ensure **Anthropic Claude 3.5 Haiku/Sonnet** or **Meta Llama 3.3 70B** is enabled.
3. Go to **Builder Tools** → **Agents** → **Create Agent**:
   * **Agent Name**: `amaze-on-work-agent`
   * **Model**: Choose `Claude 3.5 Haiku` or `Llama 3.3 70B`.
   * **Instructions**: Copy the system prompt from [`strands_agent.py`](file:///d:/LocalClaude-AWS-AFH/strands_agent.py).
4. **Add Action Groups**: Connect GitHub and Slack tool handlers defined in `src/mcp/`.
5. **Test in the Bedrock Test Panel**:
   ```text
   Resolve incident INC-001 for repo iykyk-vedant/AFH-DEMO
   ```

---

## 7. Troubleshooting & FAQs

### Q1: Can I run this without Docker?
**Yes.** The system uses the in-memory **NetworkX** graph backend when Neo4j is offline, and validation falls back to local execution if Docker is not installed.

### Q2: What if I run out of Cerebras LLM rate limits?
You can adjust `CEREBRAS_MODEL` or configure alternative providers in `src/llm/` (e.g., Bedrock Claude 3.5 Haiku).

### Q3: How do I test the Human-in-the-Loop fix selection?
1. Start the server: `python -m src.api.main server --port 8000`
2. Trigger an incident triage run. Fix candidates are saved to [`data/fix_candidates/`](file:///d:/LocalClaude-AWS-AFH/data/fix_candidates).
3. Post `!fix 2 INC-0034` in your configured Slack channel or send a POST request to `/api/webhooks/fix-selection`. The agent will automatically switch to candidate #2 and update the PR!

### Q4: How do I prevent unexpected AWS charges?
Create an **AWS Cost Budget** in the AWS Console set to **`$45.00`** with email alerts. Stop your EC2 instance when not in active use.
