# Evidence

End-to-end demonstration bundles. Each is a self-contained run: the structured
per-step event log (`events.jsonl`), per-step screenshots (`stepN-screenshot-*.png`
plus `.meta.json`), a DOM snapshot on failure (`stepN-dom-*.html`), a
`summary.json`, and — for discovery runs — the recorded `artifact.json`.

Regenerate with the commands in [`../backend/REPRODUCE.md`](../backend/REPRODUCE.md).

## The through-line

`01` (discovery) records a draft `CapabilityArtifact`; a human approves it; `02`–`06`
replay that same approved artifact deterministically (no model in the loop),
one bundle per outcome class. `07`–`09` repeat the slice with a **real LLM**
(`openai/gpt-4o-mini` via OpenRouter) driving discovery.

| Bundle | Mode | Target input | Outcome | What it shows |
|---|---|---|---|---|
| `01-discovery` | discovery (offline `scripted`) | `member_id=12345` | `completed` | 7 observe/act steps → a 5-step draft artifact with typed `member_id` in / `savings_balance` out, 2 `known_outcomes`, 2 `recoverable_rules`. `artifact.json` is the approved capability. |
| `02-replay-success` | replay | `member_id=12345` | `recoverable_then_success` | Happy path. The MockBank record screen shows a one-time session notice; the artifact's `session_notice_interstitial` rule dismisses it and the run returns `savings_balance = $4,182.55`. |
| `03-replay-business-outcome` | replay | `member_id=00000` | `business_outcome` · `member_not_found` | A "no such member" result is a **legitimate answer**, not a crash — `failure_detail` is null, nothing is logged as an error. Stops at step 1. |
| `04-replay-permission-denied` | replay | `member_id=99999` | `business_outcome` · `permission_denied` | Second declared business outcome — a permission wall the caller must be told about. |
| `05-replay-recoverable` | replay | `member_id=12345`, `MOCKBANK_INTERSTITIAL=1` | `recoverable_then_success` | The genuine session interstitial fires before the detail screen; the recoverable rule dismisses it (see `step*` screenshots) and the run completes. |
| `06-replay-hard-failure` | replay | corrupted entry URL | `hard_failure` | The first control can't be resolved. Stops with a structured `failure_detail {step_index, expected, observed}` **plus** a screenshot and a DOM snapshot (`step0-dom-*.html`) — the richer signal for debugging. |
| `07-discovery-real-llm` | discovery (**real** `openrouter` / `gpt-4o-mini`) | `member_id=12345` | `completed` | 13 `llm_call` events (`"provider":"openrouter","model":"openai/gpt-4o-mini"`), client-side RPM pacing visible as `provider_throttle`. Produces a 4-step artifact with the same typed contract. `artifact.json` included. |
| `08-replay-real` | replay | `member_id=12345` | `recoverable_then_success` | Deterministic replay of the **real-LLM** artifact from `07` — no model in the loop — returns `savings_balance = $4,182.55`. |
| `09-replay-real-not-found` | replay | `member_id=00000` | `business_outcome` · `member_not_found` | The real-LLM artifact also handles the exceptional state correctly. |

Every `ReplayOutcome` in the contract is represented: `success` /
`recoverable_then_success` (`02`, `05`, `08`), `business_outcome` (`03`, `04`, `09`),
`hard_failure` (`06`). A pure `success` with zero recoveries is asserted by the
unit suite (`backend/tests/test_phase7_replay.py::test_replay_success_returns_outputs`);
on the live MockBank target the record screen's one-time notice means the happy
path exercises the recoverable branch too.

The replay-stuck → human-handoff → resume path (brief §3.6) is covered by
`test_phase7_replay.py::test_stuck_replay_escalates_and_resumes_after_handback`
and is exercisable in the console (a stuck replay raises an intervention and holds
the live session for an operator).
