# Computer-Use Automation System

Backend integration layer that gives AI agents **hands** for API-less legacy
banking apps. An LLM figures out a task once by driving the real UI (**discovery**);
the run is recorded as a typed, versioned, reviewable **`CapabilityArtifact`**;
after human approval an agent invokes it a thousand times with **no model in the
loop** (**deterministic replay**), and a human can **take over the live session**
when replay or discovery gets stuck.

> The model discovers. The artifact becomes a reusable capability. Deterministic
> replay is how the agent invokes it in production.

- **Design write-up:** [`REPORT.md`](./REPORT.md) (7 required headings)
- **End-to-end evidence:** [`evidence/`](./evidence/) (discovery run, 4 replays incl. business-outcome + hard-failure)
- **User stories / build log:** [`USER_STORIES.md`](./USER_STORIES.md) — 45 stories, 10 phases, each committed with tests
- **Original design doc:** [`TDD-ComputerUse-Automation-System.md`](./TDD-ComputerUse-Automation-System.md)

## Layout

```
backend/    Python monolith: discovery loop, artifact schema+store, replay
            executor, policy/guardrails, escalation/handoff, per-run Docker
            sandbox (noVNC + CDP), MockBank target app
frontend/   Next.js 16 + shadcn console: goal input, live noVNC + event stream +
            sandbox terminal, stuck-run handoff, printable run report
evidence/   Committed demo bundles
```

## Setup

Requires Python 3.11+ and Node 18+ (frontend only).

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env          # optional — only needed for a REAL LLM discovery run
```

### Config (`backend/.env`)

| Key | Purpose |
|---|---|
| `CUA_LLM_PROVIDERS` | `openrouter,nvidia_nim` for a real run; `scripted` (default) runs fully offline |
| `OPENROUTER_API_KEY` / `NVIDIA_NIM_API_KEY` | provider keys — a missing key fails fast at startup, never silently |
| `CUA_TARGET_BASE_URL` | MockBank base URL (default `http://127.0.0.1:8799`) |
| `CUA_ALLOWLIST_PATH` | per-tenant allowlist (`config/allowlist.example.json`) |
| `CUA_DB_PATH` / `CUA_EVIDENCE_ROOT` | SQLite + local evidence roots |

Discovery needs an LLM. **Everything else — replay, guardrails, escalation, the
whole test suite — runs with no external services** using `CUA_LLM_PROVIDERS=scripted`.

## Demo path (exact commands)

```bash
cd backend && source .venv/bin/activate

# 1. start the legacy target app (leave running)
cua serve-mock                                   # http://127.0.0.1:8799

# 2. DISCOVERY — LLM drives the UI, completes the goal, records a draft artifact
#    real run:  set CUA_LLM_PROVIDERS=openrouter (or nvidia_nim) + a key in .env
#    offline:   CUA_LLM_PROVIDERS=scripted  (deterministic, no key)
cua discover \
  --goal "look up member 12345 and read their current savings balance" \
  --target http://127.0.0.1:8799/search \
  --params member_id=12345 \
  --name read_savings_balance \
  --out ../evidence/01-discovery

# 3. REVIEW — a human promotes the draft (unattended replay is refused until approved)
cua artifacts                                    # copy the artifact_id
cua approve <artifact_id> 1 --reviewer you

# 4. REPLAY — deterministic, no LLM. Try the happy path and an exceptional state.
cua replay <artifact_id> --version 1 \
  --target http://127.0.0.1:8799/search --params member_id=12345 \
  --out ../evidence/02-replay-success

cua replay <artifact_id> --version 1 \
  --target http://127.0.0.1:8799/search --params member_id=00000 \
  --out ../evidence/03-replay-business-outcome        # -> business_outcome: member_not_found
```

### HTTP API (optional)

```bash
cua serve                                         # http://127.0.0.1:8080/docs
# POST /runs · GET /runs/{id} · GET /artifacts · POST /artifacts/{id}/versions/{v}/promote
# POST /replays/{id}/invoke · GET /capabilities · /interventions/*
```

### Live console with per-run sandbox (Next.js + Docker)

Watch a discovery run in a live noVNC canvas, shell into the run's sandbox from an
in-browser terminal, and take over a stuck run by clicking directly in the canvas.

```bash
bash backend/sandbox_image/build.sh              # once — builds cua-sandbox:latest (needs Docker)
cd frontend && npm install && cd ..
bash start.sh                                    # MockBank :8799 · gateway :8080 (CUA_USE_SANDBOX=1) · console :3000
# open http://localhost:3000 → New run → submit → watch it live → Generate report
```

Without Docker, set `CUA_USE_SANDBOX=0` (the default): the plain headless adapter
runs and the console still works minus the live view.

## Tests

```bash
cd backend && pytest        # 72 tests (~45s); 3 sandbox tests skip cleanly without Docker/image
ruff check src tests
cd ../frontend && npm run build
```

## What's real vs. mocked

**Real:** the discovery loop, the artifact schema + SQLite store + versioning +
review gate, the full replay executor with the business-outcome / recoverable /
hard-failure taxonomy, the multi-strategy locator engine with drift signal,
the fail-closed policy engine, the control-lock + session-handoff mechanism
(same live session, actions recorded, resume), cross-host egress blocking,
the LLM provider router with rotation.

**Mocked / design-only (documented in `REPORT.md` §Cuts):** the operator console
UI (mechanism is real, UI is a thin API + React panel), real container/microVM
sandboxing (per-context isolation + egress block + wall-clock kill are real),
Postgres (SQLite behind the same interface), desktop/legacy-web surface adapters
(one `SurfaceAdapter` seam, Playwright impl).
