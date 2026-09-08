# Reproducing the evidence

## Offline (no key, deterministic — this is what's committed in `evidence/`)

```bash
cd backend && source .venv/bin/activate
export CUA_LLM_PROVIDERS=scripted
cua serve-mock &                      # http://127.0.0.1:8799

cua discover --goal "look up member 12345 and read their current savings balance" \
  --target http://127.0.0.1:8799/search --params member_id=12345 \
  --name read_savings_balance --out ../evidence/01-discovery

AID=$(python -c "from cua.assembly import build_system; from cua.config import load_config; \
  print(build_system(load_config(strict=False)).store.list(name='read_savings_balance')[0].artifact_id)")

cua approve $AID 1 --reviewer demo-reviewer
cua replay $AID --version 1 --target http://127.0.0.1:8799/search --params member_id=12345 --out ../evidence/02-replay-success
cua replay $AID --version 1 --target http://127.0.0.1:8799/search --params member_id=00000 --out ../evidence/03-replay-business-outcome
cua replay $AID --version 1 --target http://127.0.0.1:8799/search --params member_id=99999 --out ../evidence/04-replay-permission-denied
cua replay $AID --version 1 --target "http://127.0.0.1:8799/member/34567?ack=1" --params member_id=12345 --out ../evidence/05-replay-hard-failure
```

## Real LLM-driven discovery run

Put a key in `backend/.env`:

```
CUA_LLM_PROVIDERS=openrouter,nvidia_nim
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=anthropic/claude-sonnet-4
NVIDIA_NIM_API_KEY=nvapi-...
```

Then run the **same** `cua discover` command with `CUA_LLM_PROVIDERS` unset (so it
reads `.env`). The agent loop, tool vocabulary, guardrails, artifact schema, and
evidence format are identical; only the `decide()` calls hit a real model, and
each `events.jsonl` `llm_call` line records which provider served that turn.
A single successful run is inexpensive (~10–20 model turns).
