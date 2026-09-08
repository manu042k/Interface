# Evidence

End-to-end demonstration of the vertical slice, against the local **MockBank**
legacy app (`backend/src/mockbank`).

Bundles **01–05** use the **offline deterministic pilot** (`CUA_LLM_PROVIDERS=scripted`)
so they reproduce in CI with no key. Bundles **06–08** are a **genuine
LLM-driven run**: discovery driven by `deepseek-ai/deepseek-v4-pro-0813` via the
**NVIDIA NIM** provider, inside a per-run Docker sandbox (`CUA_USE_SANDBOX=1`).
The pipeline, artifact schema, guardrails and evidence format are identical —
only the `decide()` calls differ. See `backend/REPRODUCE.md`.

| Bundle | What it shows |
|---|---|
| `01-discovery/` | Offline discovery completing *"look up member 12345 and read their current savings balance"*. `events.jsonl` = the structured per-step log (decision + model reasoning, guardrail verdict, action, checkpoint). `summary.json` = the run + the recorded draft `CapabilityArtifact` (ranked locator chains + rationale, typed I/O schema, checkpoint, `known_outcomes`, `recoverable_rules`). |
| `02-replay-success/` | Deterministic replay, `member_id=12345`. `recoverable_then_success`: the *Session Notice* interstitial fired and was auto-dismissed by a `recoverable_rule`, then `savings_balance = {raw: "$4,182.55", amount: 4182.55}` returned. |
| `03-replay-business-outcome/` | `member_id=00000` → `business_outcome`, `code: "member_not_found"` — a legitimate answer, not an error; `failure_detail` null. |
| `04-replay-permission-denied/` | `member_id=99999` → `business_outcome`, `code: "permission_denied"`. |
| `05-replay-hard-failure/` | Wrong entry point so step 0 can't resolve → `hard_failure` with `failure_detail: {step_index, expected, observed}` + screenshot + DOM snapshot. |
| `06-real-llm-discovery/` | **Genuine model-driven run.** `events.jsonl` shows every turn with `"event": "llm_call", "provider": "nvidia_nim"` — the model (deepseek-v4-pro) observing the sandboxed browser, deciding, and acting until `done`. `report.md` / `report.json` = the assembled run report. `recorded-artifact-v2.json` = the resulting `CapabilityArtifact` (the recorder collapsed 6 redundant `extract` turns → 1 step; multi-strategy locators e.g. `[name="q"]` → `role_name(textbox)`). `step*-screenshot-*.png` = per-step evidence. |
| `07-real-llm-replay-success/` | Deterministic replay of that real artifact (no model), `member_id=34567` → `success`, `savings_balance = {raw: "$102,930.42", amount: 102930.42}`. Proves the discover→record→replay round-trip for a real LLM run. |
| `08-real-llm-replay-not-found/` | Same real artifact, `member_id=00000` → `business_outcome`, `code: "member_not_found"`. |

## Reading `events.jsonl`

One JSON object per line, ordered, timestamped. Event kinds: `run_started`,
`decision` (with the model's `reasoning`), `guardrail` (verdict + reason),
`action` (result + matched locator strategy), `locator_resolution` (which ranked
strategy matched + `drift_signal`), `checkpoint`, `recoverable_condition`,
`business_outcome`, `hard_failure`, `run_finished`. All values pass through the
redaction layer on the way to disk — typed field values show as `«typed:len=N»`,
full account/card/SSN as `«REDACTED:…»`.
