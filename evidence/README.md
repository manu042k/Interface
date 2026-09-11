# Evidence

End-to-end demonstration bundles from a **real LLM-driven run** — `01`-`05`'s
discovery loop was driven by `google/gemini-2.5-flash` via OpenRouter (not the
offline `scripted` pilot, which exists only for CI / the no-key demo and is
exercised by the test suite, not here).

Each bundle is self-contained: the structured per-step event log
(`events.jsonl`), per-step screenshots (`stepN-screenshot-*.png` + `.meta.json`),
a DOM snapshot on failure (`stepN-dom-*.html`), a `summary.json`, and — for the
discovery run — the recorded `artifact.json`. `01`-`06` are against the local
synthetic MockBank sandbox; `07` is the same handoff mechanism against
ParaBank, a real external site (see the brief's own "public proxy target"
example) — both are fabricated test data, no real PII either way.

Regenerate with the commands in [`../backend/REPRODUCE.md`](../backend/REPRODUCE.md).

## The through-line

`01` records a draft `CapabilityArtifact` from a genuine model run; a human
approves it; `02`–`05` replay that same approved artifact **deterministically —
no model in the loop** — one bundle per outcome class.

| Bundle | Mode | Input | Outcome | What it shows |
|---|---|---|---|---|
| `01-discovery-real-llm` | discovery (`openrouter` / `gemini-2.5-flash`) | `member_id=12345` | `completed` | 7 `llm_call` events (`"provider":"openrouter","model":"google/gemini-2.5-flash"`). Produces a 5-step artifact: typed `member_id` in / `savings_balance` out, 2 `known_outcomes` (`member_not_found`, `permission_denied`), 3 `recoverable_rules`. The checkpoint is `all_of(url_matches, extract_matches)` — it genuinely verifies the re-read `savings_balance` is present and currency-shaped, not just that the URL looks right. The search field's locator chain is ranked two deep (`dom_anchor` + `relative_to_landmark`, the field's row-label text), not a single selector with no fallback. `artifact.json` is the approved capability. |
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
against a goal that states a business rule, not a tool name — *"the account
type and deposit amount are decisions only a supervisor is authorized to
make; you must not choose them yourself"* — and reaches `stuck()` on its own,
demonstrating brief §3.6 end to end, not a mock:

1. **Detect & route** (`step 7`) — the model is nudged back twice (steps 5-6:
   it tries `stuck` before it has even confirmed there is a decision to make,
   and the orchestrator's own "you called stuck, but your last action
   succeeded" guard pushes it to re-verify), then calls `stuck` for real with
   its own reasoning: *"The current screen shows the new sub-account form
   with default values for account type and deposit amount. I need a
   supervisor to make these decisions."* The orchestrator opens a real
   `InterventionRequest` carrying that reason, the current step, and a
   context bundle.
2. **Take control of the SAME live session** — `EscalationService.take_control`
   transfers the `SessionBroker`'s CAS+TTL lease from automation to the
   operator on the identical `session_id` (not a fresh one). `take_control`
   now refuses (`ValueError`) if the intervention isn't a `handoff` — a real
   fix this bundle's regeneration surfaced: a `risk_approval` gate ("no
   session lock transfer at all", per `REPORT.md` §5) had no code enforcing
   that, so it could accidentally be driven through the same takeover API
   instead of its own `decide(approved=…)`. Covered by
   `test_take_control_refuses_a_risk_approval_gate`.
3. **A human acts** — four real actions through `OperatorConsole.perform`
   (select the account type, type the deposit, click Review, click Confirm
   creation) against the live MockBank session, each recorded to
   `human_actions_log`. A fifth entry (`by: "detected"`) shows the *passive*
   detection path also firing — even a raw DOM navigation the operator makes
   without going through the recorded-actions API gets logged.
4. **Hand back** — `release_control` → `resume()` re-acquires automation's
   lease on the same session.
5. **Resume & complete** — the agent re-observes rather than assuming the
   goal is done: it `extract`s the confirmation panel four times across the
   remaining steps before concluding, finds the real confirmation the
   human's actions produced, and finishes: `completed` — *"goal already
   satisfied — the screen shows: Sub-account created."* (`SA-320193` is the
   confirmation number in `summary.json`'s extracted text.)

`summary.json.human_actions` is the direct record of what the human did;
`events.jsonl` carries the full `intervention_opened → intervention_claimed →
control_transferred → human_action ×6 → intervention_resolved →
operator_handed_back` sequence in order. The stuck-replay (as opposed to
stuck-discovery) escalation path is structurally identical — same
`EscalationService`/`SessionBroker`, same lease/resume mechanics — and is
covered by `backend/tests/test_phase8_escalation.py`.

**What's real here vs. simulated, plainly:** a script stands in for the human
operator (`scripts/gen_handoff_evidence.py`), so `by: "demo-reviewer"` in
`human_actions_log` is that script, not a person at a keyboard — but it waits
a human-scale 2-3.7s between each of the four actions (measured from
`events.jsonl`), not the ~130ms back-to-back firing an earlier version of
this bundle used. Everything else is unscripted: the goal names a business
rule, never a tool, and the model reaches `stuck()` through its own
reasoning after two nudges back; `SessionBroker` is a real CAS + TTL-lease
class (`threading.Lock`, a genuine expiry check, no stub); `console.perform()`
submits real requests against the live MockBank session; the sub-account
really gets created server-side and `SA-320193` is MockBank's own
`hash(member, type, amount)` confirmation number, not fabricated; and the
agent's resume step genuinely re-observes and re-extracts that real value off
the real resulting page rather than being told the outcome. The lock-transfer
and resume mechanics are the thing being
demonstrated, and they run unmodified from what a live noVNC takeover through
the gateway would exercise (§5's "mechanism... is real and covered by tests
for both discovery and replay").

## `07-discovery-handoff-parabank` — the same mechanism against a real external site

The assignment brief names *"a public proxy target"* as an accepted goal
example. This bundle is `06`'s exact mechanism (`scripts/gen_handoff_evidence_parabank.py`,
a near-identical copy of `scripts/gen_handoff_evidence.py`) run against
[ParaBank](https://parabank.parasoft.com/) — Parasoft's own public demo
banking site, real and external, not something this repo controls — to show
the handoff isn't a MockBank-specific trick.

Real login (`john` / `demo`, ParaBank's own published QA-training credentials,
not a real customer), then **"Open a new account... the account type and
funding account are decisions only a supervisor is authorized to make."**
Same as `06`, the goal states a business rule, never a tool name:

1. **Sign in, navigate** (steps 0-3) — real `type`/`type`/`click`/`click`
   against the live external site: username, password, "Log In," "Open New
   Account."
2. **Detect & route** (`step 6`) — ParaBank's new-account form arrives with
   both fields already showing a default (`type` defaults to CHECKING,
   `fromAccountId` defaults to john's first account), so the model is nudged
   back twice reasoning about the business rule directly: *"Account type and
   funding account are not pre-selected, and I am not authorized to choose
   them"* (step 4), then *"The account type and funding account are business
   decisions that I cannot make... I need human intervention to proceed"*
   (step 5), before calling `stuck()` for real at step 6. The orchestrator
   opens a real `InterventionRequest` on ParaBank's live session, same as `06`.
3. **Take control of the SAME live session** on the external site.
4. **A human acts** — the operator reads the *live* `fromAccountId` dropdown's
   actual options off the real page (its account numbers aren't known ahead
   of time; the script does not hardcode one) and picks the first one,
   selects `type = SAVINGS`, and clicks "Open New Account" — three real
   actions against the live ParaBank session, 1.7-2.6s apart.
5. **Hand back & resume** — the agent re-observes, extracts the new account
   number ParaBank's server actually generated (`29328`), `assert_state`s the
   confirmation, and finishes: `completed` — *"goal achieved."*

**What's real here vs. simulated:** identical disclosure to `06` — the script
stands in for a person, paced at human-scale gaps, not fired instantly. The
site, the login, the account creation, the server-generated account number,
and the model's own `stuck()` reasoning are all real and unscripted; this run
was not rehearsed or edited after the fact.
