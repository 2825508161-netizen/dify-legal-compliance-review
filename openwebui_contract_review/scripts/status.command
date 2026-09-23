#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
REPO_DIR="${PROJECT_DIR:h}"
RUNTIME_DIR="$REPO_DIR/work/runtime"
export PATH="$RUNTIME_DIR/bin:$PATH"

docker context use colima-ai-legal >/dev/null 2>&1 || true
cd "$PROJECT_DIR"
docker-compose --env-file .env.local ps
curl -fsS http://127.0.0.1:8787/health
print ""
