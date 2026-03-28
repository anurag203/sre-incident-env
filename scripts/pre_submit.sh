#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PORT="${OPENENV_SMOKE_PORT:-8013}"
BASE_URL="http://127.0.0.1:${PORT}"
SERVER_PID=""

cleanup() {
  if [[ -n "${SERVER_PID}" ]]; then
    kill "${SERVER_PID}" >/dev/null 2>&1 || true
    wait "${SERVER_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT

echo "==> Running test suite"
./.venv/bin/pytest -q

echo "==> Validating local package"
./.venv/bin/openenv validate .

echo "==> Starting local server on ${BASE_URL}"
./.venv/bin/uvicorn server.app:app --host 127.0.0.1 --port "${PORT}" >/tmp/sre_incident_env_presubmit.log 2>&1 &
SERVER_PID=$!

for _ in {1..30}; do
  if curl -fsS "${BASE_URL}/health" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

curl -fsS "${BASE_URL}/health" >/dev/null

echo "==> Validating running server"
./.venv/bin/openenv validate --url "${BASE_URL}"

echo "==> Running deterministic inference smoke test"
BASELINE_MODE=policy ENV_BASE_URL="${BASE_URL}" ./.venv/bin/python inference.py

if [[ "${SKIP_DOCKER:-0}" != "1" ]]; then
  echo "==> Building Docker image"
  docker build -t sre-incident-env .
else
  echo "==> Skipping Docker build (SKIP_DOCKER=1)"
fi

echo "==> Pre-submit checks passed"
