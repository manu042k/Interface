#!/usr/bin/env bash
# Boot the whole stack for the live demo: MockBank (:8799) -> Gateway (:8080)
# -> Console (:3000), backend and frontend together, one Ctrl-C stops all three.
#
# Prereqs: backend venv at backend/.venv (pip install -e ".[dev]" && playwright
# install chromium), frontend deps (npm install).
#
# CUA_USE_SANDBOX controls the live noVNC/Docker sandbox:
#   0 - plain headless adapter, no Docker needed, console works fully minus
#       the live canvas. (default in backend/.env.example.)
#   1 - per-run Docker sandbox (noVNC + CDP terminal + click-to-take-over);
#       needs Docker running, builds cua-sandbox:latest on first use.
#
# Precedence, same as the backend's own config loading: an env var set on
# this invocation wins; otherwise falls back to backend/.env's value; else 0.
#
#   ./start.sh                    # whatever backend/.env says (0 if unset)
#   CUA_USE_SANDBOX=1 ./start.sh  # force the live sandbox on, regardless of .env
#   CUA_USE_SANDBOX=0 ./start.sh  # force it off, regardless of .env
set -euo pipefail
cd "$(dirname "$0")"

BACK=backend/.venv/bin
[ -x "$BACK/cua" ] || { echo "backend venv missing — see backend/README.md"; exit 1; }

if [ -n "${CUA_USE_SANDBOX+x}" ]; then
    USE_SANDBOX="$CUA_USE_SANDBOX"
else
    USE_SANDBOX="$(grep -m1 '^CUA_USE_SANDBOX=' backend/.env 2>/dev/null | cut -d= -f2 | tr -d '[:space:]')"
    USE_SANDBOX="${USE_SANDBOX:-0}"
fi

if [ "$USE_SANDBOX" = "1" ]; then
    command -v docker >/dev/null 2>&1 || { echo "CUA_USE_SANDBOX=1 needs Docker, but 'docker' isn't on PATH"; exit 1; }
    echo "==> building sandbox image (cua-sandbox:latest) if needed"
    docker image inspect cua-sandbox:latest >/dev/null 2>&1 || bash backend/sandbox_image/build.sh
fi

pids=()
cleanup() {
    kill "${pids[@]}" 2>/dev/null || true
    if [ "$USE_SANDBOX" = "1" ]; then
        docker rm -f $(docker ps -q --filter name=cua-sandbox-) 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM

echo "==> MockBank on :8799"
( cd backend && MOCKBANK_INTERSTITIAL=1 .venv/bin/cua serve-mock --port 8799 ) & pids+=($!)

echo "==> Gateway on :8080 (CUA_USE_SANDBOX=$USE_SANDBOX)"
# CUA_LLM_PROVIDERS is deliberately NOT forced here (that was the same bug
# CUA_USE_SANDBOX had): if it's set on this invocation it passes through as
# normal shell inheritance; otherwise the backend's own config loading picks
# it up from backend/.env (defaults to the offline scripted pilot if .env
# doesn't set it either). Forcing scripted here would silently override a
# real .env provider key with no way to tell without checking `ps`.
( cd backend && CUA_USE_SANDBOX="$USE_SANDBOX" .venv/bin/cua serve --port 8080 ) & pids+=($!)

echo "==> Console on :3000"
( cd frontend && npm run dev ) & pids+=($!)

echo
echo "  MockBank : http://localhost:8799"
echo "  Gateway  : http://localhost:8080/docs"
echo "  Console  : http://localhost:3000"
echo "  (Ctrl-C to stop everything)"
wait
