# Reproducing the evidence

## Offline (no key, deterministic) — bundles `evidence/01-05`

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

## Genuine LLM-driven run — bundles `evidence/06-08`

`backend/.env` (gitignored):

```
CUA_LLM_PROVIDERS=nvidia_nim,openrouter          # router tries NIM first, rotates to OpenRouter
NVIDIA_NIM_API_KEY=nvapi-...
NVIDIA_NIM_MODEL=deepseek-ai/deepseek-v4-pro-0813   # returns proper OpenAI tool_calls; strong enough for the agent loop
OPENROUTER_API_KEY=sk-or-...
OPENROUTER_MODEL=anthropic/claude-sonnet-4
CUA_USE_SANDBOX=1                                # each run in its own Docker container, live noVNC
```

Model notes from the test keys used for `evidence/06-08`:
- **NVIDIA NIM** — `deepseek-ai/deepseek-v4-pro-0813` works well. `openai/gpt-oss-20b`
  emits valid `tool_calls` but is too weak to finish the flow reliably;
  `meta/llama-3.3-70b-instruct` and `openai/gpt-oss-120b` are EOL; several
  `nemotron` models dump JSON in `content` instead of `tool_calls`.
- **OpenRouter** — the supplied test key is out of credits (402, "can only
  afford 186 tokens"), so runs go through NIM; a funded OpenRouter key with
  `anthropic/claude-sonnet-4` also completes the goal.

```bash
cd backend && source .venv/bin/activate
bash sandbox_image/build.sh                       # once
MOCKBANK_INTERSTITIAL=0 cua serve-mock &          # clean happy path for the real run
cua serve &                                       # gateway :8080, reads .env

RID=$(curl -s -XPOST localhost:8080/runs -H 'content-type: application/json' -d '{
  "goal":"look up member 34567 and read their current savings balance",
  "target":"http://host.docker.internal:8799/search",
  "params":{"member_id":"34567"},"capability_name":"read_savings_balance_real"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['run_id'])")

# watch it live at http://localhost:3000/runs/$RID  (npm run dev in frontend/)
# poll: curl -s localhost:8080/runs/$RID
# then: approve the resulting artifact + replay it (deterministic, no model)
```

Every discovery turn is logged in `events.jsonl` as
`{"event":"llm_call","provider":"nvidia_nim", ...}` so a run can be correlated
back to the provider/model that served each decision.
