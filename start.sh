#!/usr/bin/env bash
# Boot the whole stack for the live demo:
#   MockBank (:8799)  ->  Gateway w/ Docker sandbox (:8080)  ->  Console (:3000)
#
# Prereqs: backend venv at backend/.venv (pip install -e ".[dev]" && playwright
# install chromium), frontend deps (npm install), Docker running.
set -euo pipefail
cd "$(dirname "$0")"

BACK=backend/.venv/bin
[ -x "$BACK/cua" ] || { echo "backend venv missing — see backend/README.md"; exit 1; }

echo "==> building sandbox image (cua-sandbox:latest) if needed"
docker image inspect cua-sandbox:latest >/dev/null 2>&1 || bash backend/sandbox_image/build.sh

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; docker rm -f $(docker ps -q --filter name=cua-sandbox-) 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "==> MockBank on :8799"
( cd backend && MOCKBANK_INTERSTITIAL=1 .venv/bin/cua serve-mock --port 8799 ) & pids+=($!)

echo "==> Gateway on :8080 (CUA_USE_SANDBOX=1, offline scripted pilot)"
( cd backend && CUA_USE_SANDBOX=1 CUA_LLM_PROVIDERS="${CUA_LLM_PROVIDERS:-scripted}" \
  .venv/bin/cua serve --port 8080 ) & pids+=($!)

echo "==> Console on :3000"
( cd frontend && npm run dev ) & pids+=($!)

echo
echo "  MockBank : http://localhost:8799"
echo "  Gateway  : http://localhost:8080/docs"
echo "  Console  : http://localhost:3000"
echo "  (Ctrl-C to stop everything)"
wait
