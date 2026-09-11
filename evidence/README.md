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
`hard_failure` (`05`). A pure `success` with zero recoveries is covered by
the test suite, not a separate bundle here.

## `06-discovery-handoff` — genuine stuck → live-session handoff → resume

A **real** LLM-driven discovery run (`openrouter` / `google/gemini-2.5-flash`)
against a goal it cannot finish alone — *"open a new sub-account; you cannot
decide the account type and deposit amount yourself"* — demonstrating brief
§3.6 end to end, not a mock:

1. **Detect & route** (`step 7`) — after three `stuck` calls (it correctly
   never guesses the type/amount itself, exactly as instructed), the
   orchestrator opens a real `InterventionRequest` carrying the reason, the
   current step, and a context bundle.
2. **Take control of the SAME live session** — `EscalationService.take_control`
   transfers the `SessionBroker`'s CAS+TTL lease from automation to the
   operator on the identical `session_id` (not a fresh one).
3. **A human acts** — four real actions through `OperatorConsole.perform`
   (select the account type, type the deposit, click Review, click Confirm
   creation) against the live MockBank session, each recorded to
   `human_actions_log`. A fifth entry (`by: "detected"`) shows the *passive*
   detection path also firing — even a raw DOM navigation the operator makes
   without going through the recorded-actions API gets logged.
4. **Hand back** — `release_control` → `resume()` re-acquires automation's
   lease on the same session.
5. **Resume & complete** — the agent re-observes rather than assuming the
   goal is done (`assert_state`, then several `extract` calls), finds the
   real confirmation screen the human's actions produced, and finishes:
   `completed` — *"goal already satisfied — the screen shows: Sub-account
   created."*

`summary.json.human_actions` is the direct record of what the human did;
`events.jsonl` carries the full `intervention_opened → intervention_claimed →
control_transferred → human_action ×6 → intervention_resolved →
operator_handed_back` sequence in order. The stuck-replay (as opposed to
stuck-discovery) escalation path is structurally identical — same
`EscalationService`/`SessionBroker`, same lease/resume mechanics — and is
covered by `backend/tests/test_phase8_escalation.py`.
