#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h}"
REPO_DIR="${PROJECT_DIR:h}"
RUNTIME_DIR="$REPO_DIR/work/runtime"

export PATH="$RUNTIME_DIR/bin:$PATH"

if [[ ! -x "$RUNTIME_DIR/bin/colima" || ! -x "$RUNTIME_DIR/bin/docker" ]]; then
  print "缺少本地容器运行环境，请先执行项目安装。"
  exit 1
fi

if ! colima status --profile ai-legal >/dev/null 2>&1; then
  colima start --profile ai-legal --cpu 4 --memory 6 --disk 40 --runtime docker
fi

docker context use colima-ai-legal >/dev/null
cd "$PROJECT_DIR"

if [[ ! -f .env.local ]]; then
  cp .env.example .env.local
  secret_value="$(openssl rand -hex 32)"
  /usr/bin/sed -i '' "s/replace-with-a-local-random-value/$secret_value/" .env.local
fi

docker-compose --env-file .env.local up -d --build
set -a
source .env.local
set +a
python3 scripts/install_function.py

print ""
print "AI 法务聊天产品已启动： http://127.0.0.1:3000"
open "http://127.0.0.1:3000"
