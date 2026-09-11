# Evidence

End-to-end demonstration bundles from a **real LLM-driven run** — the discovery
loop was driven by `openai/gpt-4o-mini` via OpenRouter (not the offline `scripted`
pilot, which exists only for CI / the no-key demo and is exercised by the test
suite, not here).

Each bundle is self-contained: the structured per-step event log
(`events.jsonl`), per-step screenshots (`stepN-screenshot-*.png` + `.meta.json`),
a DOM snapshot on failure (`stepN-dom-*.html`), a `summary.json`, and — for the
discovery run — the recorded `artifact.json`.

Regenerate with the commands in [`../backend/REPRODUCE.md`](../backend/REPRODUCE.md).

## The through-line

`01` records a draft `CapabilityArtifact` from a genuine model run; a human
approves it; `02`–`05` replay that same approved artifact **deterministically —
no model in the loop** — one bundle per outcome class.

| Bundle | Mode | Input | Outcome | What it shows |
|---|---|---|---|---|
| `01-discovery-real-llm` | discovery (`openrouter` / `gpt-4o-mini`) | `member_id=12345` | `completed` | 13 `llm_call` events (`"provider":"openrouter","model":"openai/gpt-4o-mini"`), client-side RPM pacing visible as `provider_throttle`. Produces a 4-step artifact: typed `member_id` in / `savings_balance` out, 2 `known_outcomes` (`member_not_found`, `permission_denied`), 2 `recoverable_rules`. `artifact.json` is the approved capability. |
| `02-replay-success` | replay | `member_id=12345` | `recoverable_then_success` | Happy path. The MockBank record screen shows a one-time session notice; the artifact's `session_notice_interstitial` rule dismisses it and the run returns `savings_balance = $4,182.55`. |
| `03-replay-member-not-found` | replay | `member_id=00000` | `business_outcome` · `member_not_found` | A "no such member" result is a **legitimate answer**, not a crash — `failure_detail` is null, nothing logged as an error. Stops at step 1. |
| `04-replay-permission-denied` | replay | `member_id=99999` | `business_outcome` · `permission_denied` | The second declared business outcome — a permission wall the caller must be told about. |
| `05-replay-hard-failure` | replay | corrupted entry URL | `hard_failure` | The first control can't be resolved. Stops with a structured `failure_detail {step_index, expected, observed}` **plus** a screenshot and a DOM snapshot (`step0-dom-*.html`) — the richer signal for debugging. |

Every `ReplayOutcome` in the contract is represented:
`recoverable_then_success` (`02`), `business_outcome` with both codes (`03`, `04`),
`hard_failure` (`05`). A pure `success` with zero recoveries, and the
discovery/replay escalation → live-session handoff → resume path, are covered by
the test suite (`backend/tests/test_phase7_replay.py`,
`test_phase8_escalation.py`).
