#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
REPO_DIR="${PROJECT_DIR:h}"
RUNTIME_DIR="$REPO_DIR/work/runtime"
export PATH="$RUNTIME_DIR/bin:$PATH"

cd "$PROJECT_DIR"
docker context use colima-ai-legal >/dev/null 2>&1 || true
docker-compose --env-file .env.local down
print "AI 法务聊天产品已停止。数据和报告仍保留在本机。"
