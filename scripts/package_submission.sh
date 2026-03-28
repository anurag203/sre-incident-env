#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="${1:-${ROOT_DIR}/../sre_incident_env_submission}"

rm -rf "${TARGET_DIR}"
mkdir -p "${TARGET_DIR}"

rsync -a \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.pytest_cache/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '*.pyo' \
  --exclude '*.egg-info/' \
  --exclude 'outputs/' \
  --exclude '.env' \
  --exclude '.DS_Store' \
  "${ROOT_DIR}/" "${TARGET_DIR}/"

echo "Created clean submission bundle at: ${TARGET_DIR}"
