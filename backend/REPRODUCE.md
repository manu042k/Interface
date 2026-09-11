# Reproducing the evidence

The committed bundles in `../evidence/` are from a **real LLM-driven run**. All
commands run from `backend/` with the venv active and MockBank up:

```bash
cd backend && source .venv/bin/activate
cua serve-mock &                       # http://127.0.0.1:8799
```

## `evidence/01-05` — genuine LLM discovery + deterministic replays

`backend/.env` (gitignored) needs a working provider key. Config used for the
currently committed `01-05` bundles:

```
CUA_LLM_PROVIDERS=openrouter,groq,nvidia_nim   # router rotates on 429, disables on 402
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=google/gemini-2.5-flash       # what 01-05 were actually recorded with
GROQ_API_KEY=gsk_...                           # GROQ_MODEL=openai/gpt-oss-20b (fallback)
CUA_PROVIDER_RPM=20                            # client-side pacing so free tiers don't 429
```

Model notes:
- **OpenRouter `google/gemini-2.5-flash`** — what the committed `01-05` bundles
  were recorded with. Weaker on raw tool-call formatting than `gpt-4o-mini`
  (the `assert_state`/`wait_for` malformed-output repair in `REPORT.md` §3 was
  written against this model's failure modes) but completes the flow reliably.
- **OpenRouter `openai/gpt-4o-mini`** — also completes the MockBank flow
  reliably with clean `tool_calls`; a fine alternative, just not what's
  currently committed.
- **Groq `openai/gpt-oss-20b`** — valid `tool_calls`, but too weak to finish the
  flow on its own; useful only as a rotation fallback.
- **NVIDIA NIM `deepseek-ai/deepseek-v4-flash-0731`** — works; several `nemotron`
  models put the tool call in `content` instead of `tool_calls` and are unusable
  under `tool_choice:"required"`; `meta/llama-3.3-70b-instruct` is EOL (410).

```bash
export CUA_LLM_PROVIDERS=openrouter,groq CUA_USE_SANDBOX=0 CUA_DB_PATH=.data/evidence-real.db
rm -f .data/evidence-real.db

# 1. discovery — a real model drives the UI and records a draft artifact
MOCKBANK_INTERSTITIAL=1 cua discover \
  --goal "look up member 12345 and read their current savings balance" \
  --target http://127.0.0.1:8799/search --params member_id=12345 \
  --name read_savings_balance --out ../evidence/01-discovery-real-llm

AID=$(python -c "from cua.artifact.store import ArtifactStore; \
  print(ArtifactStore('.data/evidence-real.db').list(name='read_savings_balance')[0].artifact_id)")
CUA_LLM_PROVIDERS=scripted cua approve $AID 1 --reviewer demo-reviewer
python -c "from cua.artifact.store import ArtifactStore; a=ArtifactStore('.data/evidence-real.db').get('$AID',1); \
  open('../evidence/01-discovery-real-llm/artifact.json','w').write(a.model_dump_json(indent=2))"

# 2-5. deterministic replay of that artifact — no model — one per outcome class
MOCKBANK_INTERSTITIAL=1 cua replay $AID --version 1 --target http://127.0.0.1:8799/search \
  -p member_id=12345 --out ../evidence/02-replay-success              # recoverable_then_success
MOCKBANK_INTERSTITIAL=1 cua replay $AID --version 1 --target http://127.0.0.1:8799/search \
  -p member_id=00000 --out ../evidence/03-replay-member-not-found     # business_outcome: member_not_found
MOCKBANK_INTERSTITIAL=1 cua replay $AID --version 1 --target http://127.0.0.1:8799/search \
  -p member_id=99999 --out ../evidence/04-replay-permission-denied    # business_outcome: permission_denied
MOCKBANK_INTERSTITIAL=0 cua replay $AID --version 1 \
  --target "http://127.0.0.1:8799/member/34567?ack=1" \
  -p member_id=12345 --out ../evidence/05-replay-hard-failure         # hard_failure + DOM snapshot
```

Every discovery turn is logged in `events.jsonl` as
`{"event":"llm_call","provider":"openrouter","model":"google/gemini-2.5-flash",...}`
so a run correlates back to the provider/model that served each decision;
`provider_throttle` events show the client-side RPM pacing.

## `evidence/06-discovery-handoff` — genuine stuck → handoff → resume

This one isn't reproducible with the `cua` CLI alone — the CLI has no
subcommand to claim/take-control/resume an intervention (only `cua serve` +
the gateway's REST API, or the console, can do that), so the bundle was
generated with a small script that drives the *real* orchestrator +
`EscalationService`/`OperatorConsole` directly, the same code path `cua
serve` uses:

```bash
cd backend && source .venv/bin/activate
cua serve-mock &                                  # fresh process — MockBank's
                                                    # duplicate-submission guard
                                                    # is in-memory per process
```

```bash
CUA_USE_SANDBOX=0 python3 scripts/gen_handoff_evidence.py
```

[`scripts/gen_handoff_evidence.py`](./scripts/gen_handoff_evidence.py) does,
in order: `build_system(load_config())` (the same composition root the
CLI/gateway use) → `asyncio.create_task(orchestrator.run_discovery(...,
handoff_wait_s=120))` (a real block-and-wait, like a live operator session) →
polls `escalation.list_interventions(status="open")` until one appears →
`console.claim` / `console.take_control` (real CAS+TTL lease transfer onto
the SAME `session_id`) → four `console.perform(...)` calls, each preceded by
a 1-4s pause (a human-scale gap, not four calls fired back-to-back) against
the live session (select the account type, type the deposit, click Review,
click Confirm creation) → `console.release_control` (hands back; automation
resumes on the same session and, per §5, re-verifies rather than assuming
success) → dumps evidence with `cua.cli._dump_run_evidence`, the same helper
the CLI itself uses.

The script's `GOAL` states a business rule ("the account type and deposit
amount are decisions only a supervisor is authorized to make"), never a tool
name — it does not tell the model to call `stuck`. The model reaches
`stuck()` on its own once it's actually on the new-sub-account form; the
script checks `iv.kind == "handoff"` before taking control and aborts with a
clear message if it isn't (a `risk_approval` gate refuses `take_control`
outright — see §5's "no session lock transfer at all"). If a model or a
reworded goal produces a `risk_approval` gate instead, that is not a bug in
the demo; it means the goal needs to push the model to recognise the
business-rule conflict before it attempts the risky click, not after.

MockBank's sub-account creation has a real duplicate-submission guard keyed
on `(member_id, account_type, amount)` — restart `cua serve-mock` fresh
before regenerating, or pick a combination that hasn't been created yet in
the running process.

## `evidence/07-discovery-handoff-parabank` — the same mechanism, a real external site

Same idea as `06`, against ParaBank instead of MockBank — no local target
process needed, just network access to `parabank.parasoft.com`:

```bash
cd backend && source .venv/bin/activate
CUA_USE_SANDBOX=0 python3 scripts/gen_handoff_evidence_parabank.py
```

[`scripts/gen_handoff_evidence_parabank.py`](./scripts/gen_handoff_evidence_parabank.py)
is `gen_handoff_evidence.py` retargeted: it logs in as ParaBank's own public
demo user (`john`/`demo`), opens a new account, and states the same kind of
business-rule goal ("the account type and funding account are decisions only
a supervisor is authorized to make"). One difference from the MockBank
script: since it drives the discovery loop directly rather than through the
gateway, it must call `system.policy.allow_target(tenant, target)` itself
first — mirroring what `api/gateway.py::_validate_target_or_400` does for a
typed Target URL in the console — or the policy engine's default-deny
allowlist blocks the live site. The operator's `fromAccountId` pick is also
read off the *live* page (john's real account numbers aren't known ahead of
time), not hardcoded.

## No-key demo path (offline, not committed as evidence)

The same pipeline runs fully offline with `CUA_LLM_PROVIDERS=scripted` — a
deterministic pilot that recognises a handful of MockBank screens. This is what
CI and the test suite exercise; it is **not** the evidence (the discovery run has
to be a real model). Swap `CUA_LLM_PROVIDERS=scripted` into the sequence above to
try it without a key.

## Live console + per-run Docker sandbox

```bash
bash sandbox_image/build.sh                       # once — builds cua-sandbox:latest
# backend/.env: CUA_USE_SANDBOX=1
cua serve &                                       # gateway :8080, reads .env
cd ../frontend && npm run dev                     # console :3000
```

Submit a run from the console, watch it live in the noVNC canvas; a stuck
discovery *or* a stuck replay raises an intervention you can Claim → take control
(click directly in the canvas) → Hand back, and automation resumes on the same
container.
