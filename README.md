# Computer-Use Automation System

Backend integration layer that gives AI agents **hands** for API-less legacy
banking apps. An LLM figures out a task once by driving the real UI (**discovery**);
the run is recorded as a typed, versioned, reviewable **`CapabilityArtifact`**;
after human approval an agent invokes it a thousand times with **no model in the
loop** (**deterministic replay**), and a human can **take over the live session**
when replay or discovery gets stuck.

> The model discovers. The artifact becomes a reusable capability. Deterministic
> replay is how the agent invokes it in production.

The whole system is testable two ways: the `cua` **CLI** (`discover` /
`replay` / `approve` / `artifacts`, scriptable, no browser needed) and the
Next.js **console** (`./start.sh`, goal input → live run → capabilities →
review → report). Same backend, same `System` underneath either front door.

**Demo video:**

[![Demo video](https://img.youtube.com/vi/JTa-ZaWoFZ0/hqdefault.jpg)](https://youtu.be/JTa-ZaWoFZ0)

- **Design write-up:** [`REPORT.md`](./REPORT.md) (7 required headings)
- **End-to-end evidence:** [`evidence/`](./evidence/) — a **real** `gpt-4o-mini` discovery run (via OpenRouter) with its recorded `artifact.json`, deterministic replays of that artifact covering every outcome class (`recoverable_then_success`, `business_outcome` ×2 codes, `hard_failure`), and a **real** discovery → stuck → live-session handoff → resume bundle (`06-discovery-handoff`, `google/gemini-2.5-flash`) — genuine `stuck()` reached through the model's own reasoning (never told which tool to call), a real operator taking control of the *same* session, and a clean resume-to-completion. `07-discovery-handoff-parabank` runs the identical mechanism against a real external site (ParaBank, the brief's own "public proxy target" example), proving it isn't MockBank-specific. The offline `scripted` pilot is a CI/no-key demo path only, not evidence.
- **User stories / build log:** [`docs/USER_STORIES.md`](./docs/USER_STORIES.md) — 45 stories, 10 phases, each committed with tests
- **Original design doc:** [`docs/TDD-ComputerUse-Automation-System.md`](./docs/TDD-ComputerUse-Automation-System.md)

Every command shown in this file, `backend/README.md`, `frontend/README.md`,
and `backend/REPRODUCE.md` has actually been run against this repo, not just
written down — including both `start.sh` modes (with and without Docker) and
the full `evidence/01-05` reproduce sequence.

## Layout

```
backend/         Python monolith: discovery loop, artifact schema+store, replay
                  executor, policy/guardrails, escalation/handoff, per-run Docker
                  sandbox (noVNC + CDP), MockBank target app
backend/.data/    Committed dev DB + run history (see note below) — NOT gitignored
frontend/         Next.js 16 + shadcn console: goal input, live noVNC + event stream +
                  sandbox terminal, stuck-run handoff, printable run report
evidence/         Committed demo bundles
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
| `CUA_LLM_PROVIDERS` | ordered list for a real run, e.g. `openrouter,groq,nvidia_nim` (the router rotates on 429, disables a provider on 402/out-of-balance); `scripted` (default) runs fully offline |
| `OPENROUTER_API_KEY` / `GROQ_API_KEY` / `NVIDIA_NIM_API_KEY` / `OPENAI_API_KEY` | provider keys — a missing key for a listed provider fails fast at startup; `OPENAI_API_KEY`, if set, auto-prepends `openai` |
| `<PROVIDER>_MODEL` | model per provider — defaults are cost-right for the agent loop (`openai/gpt-4o-mini`, `gpt-4.1-mini`, `openai/gpt-oss-20b`, `deepseek-ai/deepseek-v4-flash-0731`) |
| `CUA_PROVIDER_RPM` / `<PROVIDER>_RPM` | client-side request pacing so free tiers don't 429 |
| `CUA_TARGET_BASE_URL` | MockBank base URL (default `http://127.0.0.1:8799`) |
| `CUA_ALLOWLIST_PATH` | per-tenant allowlist (`config/allowlist.example.json`) |
| `CUA_DB_PATH` / `CUA_EVIDENCE_ROOT` | SQLite + local evidence roots |

Discovery needs an LLM. **Everything else — replay, guardrails, escalation, the
whole test suite — runs with no external services** using `CUA_LLM_PROVIDERS=scripted`.

**This repo ships with its dev database committed** (`backend/.data/cua.db` +
`backend/.data/evidence/`, 19 distinct capabilities / 31 approved artifact
versions across MockBank, ParaBank, and SauceDemo, built up over this
project's development). Point `cua serve` +
the frontend at it with no setup and the Capabilities / Runs / Review pages are
already populated — you don't have to run discovery yourself first to see a
working system. Delete `backend/.data/` (or point `CUA_DB_PATH` elsewhere) for
a clean slate.

## Demo path (exact commands)

```bash
cd backend && source .venv/bin/activate

# 1. start the legacy target app (leave running)
cua serve-mock                                   # http://127.0.0.1:8799

# 2. DISCOVERY — LLM drives the UI, completes the goal, records a draft artifact
#    real run:  set CUA_LLM_PROVIDERS=openrouter (or groq / nvidia_nim) + a key in .env
#    offline:   CUA_LLM_PROVIDERS=scripted  (deterministic, no key — CI / demo only)
cua discover \
  --goal "look up member 12345 and read their current savings balance" \
  --target http://127.0.0.1:8799/search \
  --params member_id=12345 \
  --name read_savings_balance \
  --out /tmp/demo/01-discovery

# 3. REVIEW — a human promotes the draft (unattended replay is refused until approved)
cua artifacts                                    # copy the artifact_id
cua approve <artifact_id> 1 --reviewer you

# 4. REPLAY — deterministic, no LLM. Try the happy path and an exceptional state.
cua replay <artifact_id> --version 1 \
  --target http://127.0.0.1:8799/search --params member_id=12345 \
  --out /tmp/demo/02-replay-success

cua replay <artifact_id> --version 1 \
  --target http://127.0.0.1:8799/search --params member_id=00000 \
  --out /tmp/demo/03-replay-business-outcome          # -> business_outcome: member_not_found
```

The committed bundles under [`evidence/`](./evidence/) come from a real
`gpt-4o-mini` discovery run — see [`backend/REPRODUCE.md`](./backend/REPRODUCE.md)
to regenerate them.

### HTTP API (optional)

```bash
cua serve                                         # http://127.0.0.1:8080/docs
# POST /runs · GET /runs/{id} · GET /artifacts · POST /artifacts/{id}/versions/{v}/promote
# POST /replays/{id}/invoke · GET /capabilities · /interventions/*
```

### Frontend console (Next.js)

`start.sh` boots MockBank + the gateway + the console together (one Ctrl-C
stops all three), reading `CUA_USE_SANDBOX` the same way the backend itself
does — an explicit value on the invocation wins, else it falls back to
`backend/.env`'s value, else `0`:

```bash
cd frontend && npm install && cd ..              # once
./start.sh                                       # no Docker: plain headless adapter
CUA_USE_SANDBOX=1 ./start.sh                     # live noVNC sandbox (needs Docker running;
                                                  #   builds cua-sandbox:latest on first use)
```

Open `http://localhost:3000` → New run → submit → watch it live → Generate report.
Without the sandbox the console still works fully — goal input, runs,
capabilities, review, reports — just without the live noVNC canvas or the
in-browser sandbox terminal.

Prefer three separate terminals instead of the script:

```bash
cd backend && .venv/bin/cua serve-mock            # terminal 1 — MockBank :8799
cd backend && .venv/bin/cua serve                 # terminal 2 — gateway :8080
cd frontend && npm run dev                        # terminal 3 — console :3000
```

## Tests

```bash
cd backend && pytest        # 156 tests (~80s); sandbox tests skip cleanly without Docker/image
ruff check src tests
cd ../frontend && npm run build
```

## What's real vs. mocked

**Real:** the discovery loop, the artifact schema + SQLite store + versioning +
review gate, the full replay executor with the business-outcome / recoverable /
hard-failure taxonomy **and the stuck-replay → human-handoff → resume path**,
the multi-strategy locator engine with drift signal, the fail-closed policy
engine, the control-lock + session-handoff mechanism (same live session, actions
recorded, resume), cross-host egress blocking, the LLM provider router with
rotation + client-side RPM pacing, and — with `CUA_USE_SANDBOX=1` — a real
per-run **Docker** container (`Xvfb → xfce → headed Chromium/CDP → x11vnc →
websockify`) that the worker drives over CDP and an operator watches/​takes over
via noVNC, held on `STUCK` for handoff.

**Mocked / design-only (documented in `REPORT.md` §Cuts):** the operator console
is a thin Next.js app over the real handoff API; a microVM (Firecracker/gVisor)
boundary, kernel CPU/memory ceilings, a warm pool, and orchestration beyond a
single Docker host are design-only; Postgres (SQLite behind the same interface);
desktop/legacy-web surface adapters (one `SurfaceAdapter` seam, Playwright impl);
the offline `scripted` discovery pilot for CI / the no-key demo.

## Author

**Manoj Manjunatha** — [manu042k.tech](https://manu042k.tech/)
