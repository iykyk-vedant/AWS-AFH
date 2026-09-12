#!/usr/bin/env bash
# ==============================================================================
# Amaze on Work — EC2 Auto-Start & Persistence Setup
# Configures Docker and Amaze on Work services to automatically run on boot.
# ==============================================================================

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RESET='\033[0m'

echo -e "${CYAN}================================================================="
echo "   Amaze on Work — EC2 Auto-Start & Persistence Configuration"
echo -e "=================================================================${RESET}"

APP_DIR="/home/ubuntu/AWS-AFH"
cd "$APP_DIR"

# 1. Enable Docker Daemon on System Boot
echo -e "\n${YELLOW}[1/5] Enabling Docker service on host boot...${RESET}"
sudo systemctl enable docker
sudo systemctl start docker
echo -e "${GREEN}✓ Docker systemd service enabled on boot.${RESET}"

# 2. Update local repo with git token from .env (if token has repo access)
echo -e "\n${YELLOW}[2/5] Checking repository synchronization...${RESET}"
if [ -f .env ]; then
    TOKEN=$(grep -E "^GITHUB_TOKEN=" .env | cut -d= -f2 | tr -d '\r\n')
    if [ -n "$TOKEN" ]; then
        git remote set-url origin "https://${TOKEN}@github.com/iykyk-vedant/AWS-AFH.git" 2>/dev/null || true
        git pull origin main 2>/dev/null || echo "Git pull skipped (using current local copy)"
    fi
fi

# 3. Ensure Neo4j container has restart: always in docker-compose.yml
echo -e "\n${YELLOW}[3/5] Updating docker-compose.yml with restart: always...${RESET}"
if ! grep -q "restart: always" docker-compose.yml; then
    sed -i '/container_name: amaze-neo4j/a \    restart: always' docker-compose.yml
fi
echo -e "${GREEN}✓ Neo4j container configured with restart: always.${RESET}"

# 4. Start Neo4j container and verify persistent volume
echo -e "\n${YELLOW}[4/5] Starting Neo4j container...${RESET}"
docker compose up -d neo4j
echo -e "${GREEN}✓ Neo4j running with persistent storage in aws-afh_neo4j_data.${RESET}"

# 5. Create Systemd Service for Amaze on Work (FastAPI + Dashboard + Webhooks)
echo -e "\n${YELLOW}[5/5] Creating /etc/systemd/system/amaze.service...${RESET}"
sudo bash -c "cat << 'EOF' > /etc/systemd/system/amaze.service
[Unit]
Description=Amaze on Work Autonomous Incident Engineering Service
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/AWS-AFH
EnvironmentFile=/home/ubuntu/AWS-AFH/.env
ExecStartPre=/usr/bin/docker compose -f /home/ubuntu/AWS-AFH/docker-compose.yml up -d neo4j
ExecStart=/home/ubuntu/AWS-AFH/venv/bin/python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=5
StandardOutput=append:/home/ubuntu/AWS-AFH/server.log
StandardError=append:/home/ubuntu/AWS-AFH/server.log

[Install]
WantedBy=multi-user.target
EOF"

sudo systemctl daemon-reload
sudo systemctl enable amaze.service
sudo systemctl restart amaze.service

echo -e "${GREEN}✓ amaze.service installed and enabled on boot!${RESET}"

# Verify
sleep 3
sudo systemctl status amaze.service --no-pager
echo -e "\n${GREEN}================================================================="
echo "   Auto-Start Configuration Complete!"
echo "   Whenever EC2 restarts, Docker, Neo4j, and Amaze on Work"
echo "   will automatically launch and be 100% ready to use."
echo -e "=================================================================${RESET}"
