#!/usr/bin/env bash
# ops/demo/stop.sh -- stops everything ops/demo/start.sh started: the
# dashboard dev server, the widget test-page server, and the
# samoflow-demo Compose project (postgres/qdrant/api/worker). Containers
# are stopped and removed, but their data VOLUME is kept (same
# `down`, not `down -v`, semantics as `make down`) -- rerunning
# start.sh resumes with the same seeded tenant/content rather than
# starting from scratch. Never touches any other Compose project or
# container on this machine.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

PROJECT_NAME="samoflow-demo"
RUNTIME_DIR="/tmp/${PROJECT_NAME}"

COMPOSE=(docker compose --env-file .env -f deploy/docker-compose.yml -f ops/demo/docker-compose.override.yml)
export COMPOSE_PROJECT_NAME="$PROJECT_NAME"

stop_pid() {
  local name="$1"
  local pid_file="$RUNTIME_DIR/${name}.pid"
  if [ -f "$pid_file" ]; then
    local pid
    pid=$(cat "$pid_file")
    if kill -0 "$pid" 2>/dev/null; then
      echo "==> Stopping ${name} (pid ${pid})"
      kill "$pid" 2>/dev/null || true
      for _ in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
  fi
}

stop_pid "dashboard"
stop_pid "widget"

if ! docker info >/dev/null 2>&1; then
  echo "Docker is not running -- nothing to do for the ${PROJECT_NAME} containers." >&2
  exit 0
fi

echo "==> Stopping the ${PROJECT_NAME} Compose project (data volumes kept)"
"${COMPOSE[@]}" down

echo "Demo stopped. Re-run ops/demo/start.sh to bring it back up."
