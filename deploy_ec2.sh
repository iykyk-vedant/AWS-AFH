#!/usr/bin/env bash
# ==============================================================================
# Amaze on Work — One-Click AWS EC2 Deployment Script
# Supports: Ubuntu 22.04 / 24.04 LTS (x86_64 and ARM64 / AWS Graviton t4g)
# ==============================================================================

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}"
echo "================================================================="
echo "   Amaze on Work — Autonomous DevOps & Incident Engineering Agent"
echo "   AWS Agents for Humans Hackathon (Professional Agents Track)"
echo "   EC2 Automated Host Setup & Service Deployment"
echo "================================================================="
echo -e "${NC}"

# Step 1: Install System Dependencies
echo -e "${YELLOW}[1/6] Updating system & installing host packages...${NC}"
sudo apt-get update -y
sudo apt-get install -y python3-pip python3-venv docker.io docker-compose git curl jq

# Step 2: Docker Daemon & Permissions
echo -e "${YELLOW}[2/6] Configuring Docker service...${NC}"
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" || true

# Step 3: Python Virtual Environment
echo -e "${YELLOW}[3/6] Setting up Python virtual environment...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "${GREEN}Created virtual environment in ./venv${NC}"
fi

source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Step 4: Environment Variables (.env)
echo -e "${YELLOW}[4/6] Verifying environment configuration...${NC}"
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${GREEN}Created .env from .env.example.${NC}"
        echo -e "${RED}[ACTION REQUIRED] Please edit .env with your CEREBRAS_API_KEY and GITHUB_TOKEN:${NC}"
        echo -e "    nano .env"
    fi
else
    echo -e "${GREEN}.env file is present.${NC}"
fi

# Step 5: Build Docker Sandboxes (Non-blocking)
echo -e "${YELLOW}[5/6] Building Docker sandbox images for isolated testing...${NC}"
if docker info > /dev/null 2>&1; then
    docker build -f docker/python.Dockerfile -t amaze-python:base . || echo -e "${YELLOW}Docker build skipped or will run with sudo.${NC}"
else
    echo -e "${YELLOW}Docker socket not accessible yet in current shell (run 'newgrp docker' or restart session).${NC}"
fi

# Step 6: Ready Summary & Launch Options
echo -e "${CYAN}"
echo "================================================================="
echo "   Amaze on Work EC2 Setup Complete & Ready!"
echo "================================================================="
echo -e "${NC}"
echo -e "${GREEN}Available Commands to Run Whenever You Are Ready:${NC}"
echo ""
echo "1. Run Interactive 11-Incident Demo Runner:"
echo "   source venv/bin/activate && python demo.py"
echo ""
echo "2. Run Specific Incident (e.g. INC-001 or INC-004):"
echo "   source venv/bin/activate && python demo.py --incident INC-004"
echo ""
echo "3. Run Strands Agents SDK Coordinator:"
echo "   source venv/bin/activate && python strands_agent.py --incident INC-001"
echo ""
echo "4. Run Amazon Bedrock AgentCore Emulator:"
echo "   source venv/bin/activate && python agentcore_app.py"
echo ""
echo "5. Start Background FastAPI Server (Port 8000):"
echo "   nohup venv/bin/python -m src.api.main server --host 0.0.0.0 --port 8000 > server.log 2>&1 &"
echo "   (View logs: tail -f server.log)"
echo ""
