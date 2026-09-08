# CUA Backend

Python implementation of the Computer-Use Automation System: LLM-driven discovery,
deterministic replay, safety guardrails, escalation/handoff, and the MockBank
legacy target app.

See the repo-root `README.md` for the full setup + demo path, and `REPORT.md` for
the design write-up.

## Layout

| Path | What |
|---|---|
| `src/cua/` | The automation system (config, models, redaction, observability, surface, policy, llm, discovery, artifact, replay, escalation, api, cli) |
| `src/mockbank/` | Intentionally hostile "legacy" Flask target app |
| `tests/` | pytest suite |
| `config/` | Example per-tenant allowlist |

## Quick start

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium
cp .env.example .env   # fill in an OpenRouter or NVIDIA NIM key for a real discovery run

# run the target app
python -m mockbank.app        # http://127.0.0.1:8799

# tests (offline, no model key needed)
pytest
```
