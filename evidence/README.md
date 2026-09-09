# Evidence

This directory holds end-to-end demonstration bundles — the structured per-step
event log (`events.jsonl`), per-step screenshots, and a `summary.json` / report
for each run.

The previous bundles were cleared for a fresh end-to-end pass on the current
system. Regenerate them with the commands in `backend/REPRODUCE.md`:

- **Offline (no key, CI-safe):** `CUA_LLM_PROVIDERS=scripted python -m cua.cli discover … --out ../evidence/<name>`
  covers discovery + the four replay outcomes (`success` /
  `recoverable_then_success` / `business_outcome` / `hard_failure`).
- **Real model:** set `CUA_LLM_PROVIDERS` + a provider key and
  `CUA_USE_SANDBOX=1`, then run a discovery from the UI or the CLI; the run's
  evidence is written under `backend/.data/evidence/<run_id>/` and can be copied
  here.

The pipeline, artifact schema, guardrails and evidence format are identical
across offline and real-model runs — only the `decide()` calls differ.
