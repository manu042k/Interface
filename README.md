# Computer-Use Automation System

Gives AI agents a way to operate legacy banking apps that have no API. An LLM
drives the real UI once to figure out how to do something (discovery). That
run gets recorded as a typed, versioned `CapabilityArtifact`. Once a human
approves it, the same flow runs again and again with no model in the loop
(deterministic replay). If a run gets stuck, a human can take over the same
live session and hand it back when done.

> The model discovers. The artifact becomes a reusable capability.
> Deterministic replay is how the agent invokes it in production.

You can drive the whole thing two ways: the `cua` CLI (`discover` / `replay` /
`approve` / `artifacts`, scriptable, no browser needed) or the Next.js
console (`./start.sh`: goal in, watch it run live, review, report). Both sit
on the same backend.

**Demo video**

[![Demo video](https://img.youtube.com/vi/JTa-ZaWoFZ0/hqdefault.jpg)](https://youtu.be/JTa-ZaWoFZ0)

**[▶ Watch the full video](https://youtu.be/JTa-ZaWoFZ0)**

- **Design write-up:** [`REPORT.md`](./REPORT.md), covers the 7 required sections
- **Evidence:** [`evidence/`](./evidence/), a real LLM discovery run with every
  deterministic-replay outcome class, plus two real stuck → handoff → resume
  runs (one against MockBank, one against a live external site, ParaBank).
  Details and exact numbers in [`evidence/README.md`](./evidence/README.md)
- **User stories / build log:** [`docs/USER_STORIES.md`](./docs/USER_STORIES.md), 45 stories across 10 phases
- **Original design doc:** [`docs/TDD-ComputerUse-Automation-System.md`](./docs/TDD-ComputerUse-Automation-System.md)

## Layout

```
backend/         Python monolith: discovery loop, artifact schema+store, replay
                  executor, policy/guardrails, escalation/handoff, per-run Docker
                  sandbox (noVNC + CDP), MockBank target app
backend/.data/    Committed dev DB + run history (tracked in git, see below)
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
cp .env.example .env          # only needed for a real LLM discovery run
```

### Config (`backend/.env`)

| Key | Purpose |
|---|---|
| `CUA_LLM_PROVIDERS` | ordered list for a real run, e.g. `openrouter,groq,nvidia_nim` (the router rotates on 429, disables a provider on 402/out-of-balance); `scripted` (default) runs fully offline |
| `OPENROUTER_API_KEY` / `GROQ_API_KEY` / `NVIDIA_NIM_API_KEY` / `OPENAI_API_KEY` | provider keys, a missing key for a listed provider fails fast at startup; `OPENAI_API_KEY`, if set, auto-prepends `openai` |
| `<PROVIDER>_MODEL` | model per provider, defaults are cost-right for the agent loop (`openai/gpt-4o-mini`, `gpt-4.1-mini`, `openai/gpt-oss-20b`, `deepseek-ai/deepseek-v4-flash-0731`) |
| `CUA_PROVIDER_RPM` / `<PROVIDER>_RPM` | client-side request pacing so free tiers don't 429 |
| `CUA_TARGET_BASE_URL` | MockBank base URL (default `http://127.0.0.1:8799`) |
| `CUA_ALLOWLIST_PATH` | per-tenant allowlist (`config/allowlist.example.json`) |
| `CUA_DB_PATH` / `CUA_EVIDENCE_ROOT` | SQLite + local evidence roots |

Discovery needs an LLM. Everything else, replay, guardrails, escalation, the
whole test suite, runs with no external services using `CUA_LLM_PROVIDERS=scripted`.

This repo's dev database is committed (`backend/.data/cua.db` and
`backend/.data/evidence/`): 19 capabilities across 31 approved artifact
versions, recorded live against MockBank, ParaBank, and SauceDemo over the
course of building this. Point `cua serve` and the frontend at it and the
Capabilities / Runs / Review pages are already populated, no need to run
discovery yourself first. Delete `backend/.data/` (or point `CUA_DB_PATH`
elsewhere) if you want a clean slate instead.

## Demo path (exact commands)

```bash
cd backend && source .venv/bin/activate

# 1. start the legacy target app (leave running)
cua serve-mock                                   # http://127.0.0.1:8799

# 2. DISCOVERY: LLM drives the UI, completes the goal, records a draft artifact
#    real run:  set CUA_LLM_PROVIDERS=openrouter (or groq / nvidia_nim) + a key in .env
#    offline:   CUA_LLM_PROVIDERS=scripted  (deterministic, no key, CI / demo only)
cua discover \
  --goal "look up member 12345 and read their current savings balance" \
  --target http://127.0.0.1:8799/search \
  --params member_id=12345 \
  --name read_savings_balance \
  --out /tmp/demo/01-discovery

# 3. REVIEW: a human promotes the draft (unattended replay is refused until approved)
cua artifacts                                    # copy the artifact_id
cua approve <artifact_id> 1 --reviewer you

# 4. REPLAY: deterministic, no LLM. Try the happy path and an exceptional state.
cua replay <artifact_id> --version 1 \
  --target http://127.0.0.1:8799/search --params member_id=12345 \
  --out /tmp/demo/02-replay-success

cua replay <artifact_id> --version 1 \
  --target http://127.0.0.1:8799/search --params member_id=00000 \
  --out /tmp/demo/03-replay-business-outcome          # -> business_outcome: member_not_found
```

The committed bundles under [`evidence/`](./evidence/) come from a real
`gemini-2.5-flash` discovery run. See [`backend/REPRODUCE.md`](./backend/REPRODUCE.md)
to regenerate them yourself.

### HTTP API (optional)

```bash
cua serve                                         # http://127.0.0.1:8080/docs
# POST /runs · GET /runs/{id} · GET /artifacts · POST /artifacts/{id}/versions/{v}/promote
# POST /replays/{id}/invoke · GET /capabilities · /interventions/*
```

### Frontend console (Next.js)

`start.sh` boots MockBank, the gateway, and the console together, one Ctrl-C
stops all three. It reads `CUA_USE_SANDBOX` the same way the backend does: an
explicit value on the command wins, otherwise it falls back to `backend/.env`,
otherwise it's off.

```bash
./start.sh                                       # no Docker: plain headless adapter
                                                  #   (runs npm install itself, first time only)
CUA_USE_SANDBOX=1 ./start.sh                     # live noVNC sandbox (needs Docker running;
                                                  #   builds cua-sandbox:latest on first use)
```

Open `http://localhost:3000` → New run → submit → watch it live → Generate report.
Without the sandbox the console still works fully: goal input, runs,
capabilities, review, reports, just without the live noVNC canvas or the
in-browser sandbox terminal.

If you'd rather run each piece yourself instead of the script:

```bash
cd backend && .venv/bin/cua serve-mock            # terminal 1, MockBank :8799
cd backend && .venv/bin/cua serve                 # terminal 2, gateway :8080
cd frontend && npm run dev                        # terminal 3, console :3000
```

## Tests

```bash
cd backend && pytest        # 156 tests (~80s); sandbox tests skip cleanly without Docker/image
ruff check src tests
cd ../frontend && npm run build
```

## Author

**Manoj Manjunatha** · [manu042k.tech](https://manu042k.tech/)
