#!/usr/bin/env bash
# ============================================================
# WebPlatformForMPTBG — one-click Ubuntu setup script
# Usage: bash setup.sh
# ============================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()      { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════╗"
echo "  ║   WebPlatformForMPTBG  —  One-Click Setup    ║"
echo "  ╚══════════════════════════════════════════════╝"
echo -e "${NC}"

# ── 1. Check OS ──────────────────────────────────────────────
if [[ "$(uname)" != "Linux" ]]; then
    warn "This script is designed for Ubuntu/Debian Linux."
fi

# ── 2. Install Docker if missing ─────────────────────────────
if ! command -v docker &>/dev/null; then
    info "Docker not found. Installing Docker Engine..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq ca-certificates curl gnupg lsb-release
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
      https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" \
      | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    sudo apt-get update -qq
    sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
    sudo usermod -aG docker "$USER"
    ok "Docker installed."
else
    ok "Docker $(docker --version | awk '{print $3}' | tr -d ',') already installed."
fi

# ── 3. Ensure current user can access the Docker socket ──────
# The docker group may not be active yet in the current shell session
# (happens right after installation OR if the user was never added).
# Solution: add the user to the group and use 'sudo' for this session.
DOCKER_CMD="docker"
if ! docker info &>/dev/null 2>&1; then
    warn "Cannot reach Docker daemon as '${USER}' (permission denied on socket)."
    info "Adding '${USER}' to the 'docker' group..."
    sudo usermod -aG docker "$USER"
    info "Using 'sudo docker' for this session."
    info "To run WITHOUT sudo in future sessions, log out and log back in, then re-run setup.sh."
    DOCKER_CMD="sudo docker"
fi

# ── 4. Detect compose command ─────────────────────────────────
if $DOCKER_CMD compose version &>/dev/null 2>&1; then
    COMPOSE="$DOCKER_CMD compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
else
    info "Installing docker-compose-plugin..."
    sudo apt-get install -y -qq docker-compose-plugin
    COMPOSE="$DOCKER_CMD compose"
fi
ok "Using: $COMPOSE"

# ── 5. Create .env from .env.example if missing ───────────────
if [[ ! -f .env ]]; then
    cp .env.example .env
    # Generate a random SECRET_KEY
    NEW_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null \
                 || openssl rand -hex 32 2>/dev/null \
                 || cat /proc/sys/kernel/random/uuid | tr -d '-')
    sed -i "s|change-this-to-a-random-secret-key-in-production|${NEW_SECRET}|g" .env
    ok ".env created from .env.example with a fresh SECRET_KEY."
else
    ok ".env already exists — keeping it as-is."
fi

# ── 6. Build & start all services ────────────────────────────
info "Building Docker images (this may take a few minutes on first run)..."
$COMPOSE build --parallel

info "Starting all services in the background..."
$COMPOSE up -d

# ── 7. Wait for backend to be healthy ────────────────────────
info "Waiting for backend to become ready..."
MAX_WAIT=90
ELAPSED=0
until curl -sf http://localhost:8000/health >/dev/null 2>&1; do
    if (( ELAPSED >= MAX_WAIT )); then
        warn "Backend did not respond within ${MAX_WAIT}s."
        warn "Check logs with:  $COMPOSE logs backend"
        break
    fi
    sleep 3
    (( ELAPSED += 3 ))
    echo -n "."
done
echo ""

# ── 8. Print status & URLs ────────────────────────────────────
echo ""
$COMPOSE ps
echo ""
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo -e "${GREEN}  Platform is UP!${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
echo -e "  Frontend  →  ${CYAN}http://localhost:3000${NC}"
echo -e "  API       →  ${CYAN}http://localhost:8000${NC}"
echo -e "  API docs  →  ${CYAN}http://localhost:8000/docs${NC}"
echo -e "  API redoc →  ${CYAN}http://localhost:8000/redoc${NC}"
echo ""
echo -e "  Stop:     ${YELLOW}make down${NC}  or  ${YELLOW}docker compose down${NC}"
echo -e "  Logs:     ${YELLOW}make logs${NC}  or  ${YELLOW}docker compose logs -f${NC}"
echo -e "  Restart:  ${YELLOW}make restart${NC}"
echo -e "${GREEN}═══════════════════════════════════════════════${NC}"
