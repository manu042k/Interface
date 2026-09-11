# Design Write-up

## 1. Architecture

**One process, five module boundaries** (`backend/src/cua/`): `discovery`
(the only code that calls a model for a decision), `artifact` (the contract
layer), `replay` (deterministic execution, never imports `discovery`/`llm`),
`escalation` (the only code that changes *who* holds a session's input lock),
and `surface` + `policy` + `redaction` + `observability` as shared libraries
every domain consumes. The boundaries are about *what may not reach across*
(replay must never call the LLM client), not deployment topology. A modular
monolith is the right call here: the brief penalises premature scaling
infrastructure, the one place that needs strong consistency (guardrail
enforcement + artifact versioning) gets it for free in-process, and Discovery,
the only component with an LLM latency/cost profile, can later be pulled into
its own worker pool behind the same interfaces without a rewrite.

**Sync vs async.** Anything that touches the live surface or makes a safety
decision is synchronous and in-process: the guardrail check is a function
call, not a network hop that can be skipped under load. Run execution,
notifications, and evidence writes are async/fire-and-forget so a slow log
never blocks an action.

**Composition root** (`assembly.py`) wires everything from `Config`; swapping
SQLite→Postgres, `FileSink`→object storage, or Playwright→a desktop driver
happens there, not in callers. The `cua` CLI and the FastAPI gateway are two
façades over the same `System`.

**LLM provider router.** `discovery.decide()` goes through an ordered list of
OpenAI-compatible providers with client-side RPM pacing so a free tier isn't
driven into a 429 in the first place; a 429/quota signal rotates to the next
provider, a 402/out-of-balance disables it for the run, and all providers
cooled/disabled ends the run cleanly rather than spinning. Replay never
touches this.

## 2. Artifact schema

`CapabilityArtifact` (`models.py`, Pydantic: construction *is* validation):

- **`steps: [Step]`**: the executed, successful, *actionable* steps 1:1. `observe`,
  failed actions, guardrail-rejected actions, and interstitial-dismiss clicks
  never become steps.
- **`Step.locator_spec: [LocatorStrategy]`**: a **ranked** chain, not one selector
  (the legacy-markup reality has no test IDs). Rank order encodes robustness:
  `role_name` → `label` → `relative_to_landmark` ("the value cell in the *Savings*
  row") → `text` → `dom_anchor`. Every strategy carries a `rationale` string: the
  "your reasoning about robustness" the brief asks for, in-tree.
- **`Step.idempotent: bool`**: *required*, forcing the decision at authoring time.
  Mutating clicks to `/create|submit|confirm/…` routes default to non-idempotent.
- **`ValueBinding`**: typed values are parameterised: a typed value equal to a
  supplied param becomes a `param` binding; a secret-looking literal is
  refused and forced to a param; URLs are canonicalised to `{param}`
  templates. A third binding, `from_output`, chains a step's input to an
  **earlier step's own extracted output** within the same run ("select the
  account you just opened"): a value that doesn't exist until this run
  creates it, so it can be neither a literal nor a caller-supplied param. The
  recorder detects this automatically: a typed/selected value matching an
  earlier extract's raw result gets `from_output`; replay resolves it from the
  run's live `outputs`, not the caller's `params`.
- **`input_schema` / `output_schema`**: JSON Schema (draft 2020-12). Params are
  validated *before any action touches the surface*; extracted outputs validated
  before return.
- **`checkpoint: Condition`**: the final success assertion, verified, never assumed.
- **`known_outcomes` / `recoverable_rules`**: the error taxonomy is *part of
  the contract*, not bolted onto the executor.
- **Versioned + reviewable**: `(artifact_id, version)` rows in SQLite, immutable
  except the `draft → approved|rejected` transition; replay always pins an
  explicit version, never "latest." The whole artifact is redacted before it
  is persisted.

## 3. Determinism & error handling

**Determinism.** Replay resolves each step's ranked locator chain against the
current `SurfaceState`, checks the guardrail, acts, then **verifies the step
checkpoint** before moving on: a click that didn't produce the expected state
halts rather than continuing blind. No LLM anywhere in the loop. Resolution
results are cached per `(artifact_version, surface_fingerprint, step_index)`.
The `matched_rank` of the strategy that resolved is the **drift signal**: a
non-zero rank means the primary identifier degraded, visible in the run's
event log *before* it becomes a failure. Every wait is bounded.

**Exceptional states** (`replay/executor.py`). Before every step, and again
whenever a checkpoint fails, the screen is matched against the artifact's
declared rules:

| Class | Detection | Response |
|---|---|---|
| **Business outcome** | a `known_outcome.when` condition matches ("No members matched") | STOP, `{outcome: business_outcome, code}`: a legitimate answer, **never** logged as an error |
| **Recoverable** | a `recoverable_rule.when` matches (interstitial, transient slow load) | `dismiss` / `wait` / `reload`, retry the step (bounded). Any recovery → `recoverable_then_success` |
| **Hard failure** | anything else that breaks a checkpoint or can't resolve | STOP, `{outcome: hard_failure, failure_detail: {step_index, expected, observed}}` + screenshot + DOM snapshot |

**Duplicate detection (`cua/dedup.py`).** An exact re-run is caught by
`flow_fingerprint`. A differently-worded re-recording of the same function
takes a structurally different path, so a post-record pass compares entry
URL, input-schema keys, risk class, checkpoint shape, and `(action,
bound-param)` overlap; when that's ambiguous and a router is configured, one
`router.call_text` asks SAME/DIFFERENT/UNSURE. Either way it's a
`duplicate_of` review signal, never an auto-delete.

**Drift self-healing (unrecognised states).** A hard failure on a screen
matching *no* declared rule emits a `DriftCandidate` (failing step, the
page's own error text, evidence refs). `cua/drift.py` turns that into a
**v+1 DRAFT** with a candidate `known_outcome`, filed into the normal review
queue; never auto-applied, a reviewer decides.

The result contract (`ReplayResult.outcome`) is an enum: a caller never
string-matches an error message to tell success, a known outcome, and a
failure apart. A non-idempotent step that fails ambiguously is not
resubmitted: the executor re-checks the checkpoint first; if it already
holds, the step is treated as done.

**Malformed model output.** Weaker models (observed on Gemini 2.5 Flash) send
an empty `assert_state` condition and put the phrase they meant to check in
the rationale field instead. Discovery repairs this without a model
round-trip: a fuzzy rationale-key match, then a salvage pass that rebuilds
the condition from a quoted/ALL-CAPS phrase in the args or lifts a
recognised confirmation marker straight off the screen just observed. A run
that used to loop to the step ceiling now finishes on the first try.

**UI drift** (secondary, per the brief): `SurfaceState.fingerprint` is a
structural signature (tag skeleton + form field names, no text values),
stable across content changes, sensitive to markup drift. It keys the
resolution cache and, with `drift_signal`, is how per-tenant/version drift
surfaces.

## 4. Heterogeneity & multi-tenant

**Surface abstraction.** The seam is `SurfaceAdapter`
(`open_session` / `execute(Action)` / `snapshot` / `cdp_endpoint`) plus
`Perception` normalising a `RawSnapshot` into a surface-agnostic `SurfaceState`
(AX outline + DOM outline + screenshot + fingerprint). The recorded flow is
expressed entirely in that abstract vocabulary, so a legacy-web adapter (same
Playwright driver, DOM-outline path already degrades when there's no
accessibility info) or a desktop adapter (UI Automation / MSAA, screenshot +
AX tree, a VNC/RDP endpoint standing in for CDP) drops in without touching
the artifact schema, the locator engine, or the replay executor. Locator
strategies are already ranked by *portability across surface kinds*:
`role_name` works on a desktop AX tree too; `dom_anchor` is the web-only last
resort.

**Multi-tenant reuse.** An artifact is recorded once against a canonical
**base** instance (`vendor_app_id` + `app_version`). Per-tenant variance
(branding, an extra confirmation step, a renamed field) is a thin
**override** keyed `(vendor_app_id, tenant_id, overrides_base_version)` that
only declares the steps that differ; `store.resolve_for_tenant()` returns an
approved override merged onto the approved base, else the base. No
per-tenant re-recording. A tenant whose resolutions start falling to
lower-ranked strategies (via `drift_signal`) would be flagged for re-review
before it breaks, without disturbing the others; aggregating that signal
into an alert is the design's next step, not yet built.

Beyond the curated `evidence/` bundles, `backend/.data/` (committed) is this
project's real dev database: 19 capabilities built live across MockBank,
ParaBank, and SauceDemo: evidence the abstraction above held up outside the
one demo app.

## 5. Escalation & handoff

**Detect.** Discovery emits `stuck(reason)` when it can't safely proceed
(missing control, unexpected screen, a no-progress loop, an unroutable
guardrail block) and blocks up to `CUA_HANDOFF_WAIT_SECONDS` for an operator,
since discovery is a live, attended run by nature. Replay is the opposite by
design: unattended and deterministic, so it never blocks an invoke on a
human: it reports `hard_failure` immediately. An escalate-and-resume path
for replay exists too (`ReplayExecutor._escalate_replay`, opt-in via its own
`handoff_wait_s`, not the gateway's default), for a deployment that wants a
replay to wait on a human explicitly. Either way it fires, the run
transitions to `stuck` and its session is **held, not torn down**.

**A second, lighter gate: risk approval.** Not every escalation is "the agent
is stuck." When policy flags a genuinely risky/irreversible action the agent
otherwise knows how to perform, full takeover is overkill; the human just
needs to see what's about to happen and say yes or no.
`EscalationService.open_risk_approval()` raises a second `InterventionRequest`
kind (`risk_approval`, distinct from `handoff`), no session lock transfer at
all; `decide(approved=…)` resolves it. `take_control()` enforces that
separation in code: it raises if the intervention isn't a `handoff`, so a
`risk_approval` gate can't be driven through the takeover API by accident.
Two rejections of the same action `dead_end` the run rather than looping.
This is the mechanism that implements §3.4's "require confirmation"
day to day; full handoff is reserved for when a human must *act*.

`evidence/06-discovery-handoff` and `07-discovery-handoff-parabank` (a real
external site, ParaBank, proving it isn't MockBank-specific) demonstrate the
full handoff with nothing scripted into the prompt: the goal states a
business rule, never a tool name, and the model reaches `stuck()` on its
own; a human takes control of the live session, decides, hands back, and the
agent resumes and re-verifies rather than assuming success. See
`evidence/README.md` for both transcripts and what's simulated versus real.

**Route → take control → hand back.** `open_intervention()` acquires the
automation lock via the `SessionBroker`, a compare-and-swap + **TTL lease**
(a crashed worker's lease lapses and is reaped, never orphans the session).
`take_control` releases automation's lease and grants the human's on the
*same* `session_id`; every operator action runs through the adapter and is
recorded to `human_actions_log`. `release_control` → `resume` reverses the
lease and, on discovery, re-verifies with `assert_state`/`extract` before
calling `done` (an earlier hard-coded checkpoint that wedged on other sites
was removed); on replay, re-checks the failed step's checkpoint and either
continues or retries once before surfacing `hard_failure`. With
`CUA_USE_SANDBOX=1` the operator takes over by clicking directly in the live
noVNC canvas of the same Docker container; without it, the console's
scripted action buttons drive the shared session. Either way the
**mechanism** (pause/cede/resume on one session, lock ownership, action
recording) is real and covered by tests for both discovery and replay.

## 6. Safety

**Allowlist, default deny.** `PolicyEngine.check()` runs synchronously,
in-process, before *every* action. Per-tenant policy expresses permitted
domains, route regexes, and action types independently. Unknown tenant,
unparseable target, a broken allowlist file, or an exception inside the
check all resolve to **BLOCK**: fail closed, always.

**Risk class.** Actions are `safe_reversible` by default; `risky_irreversible`
if the step declares it, or a *mutating* action (click/type/select/press_key,
never a passive read, which can't be irreversible regardless of route) lands
on a risky route pattern. Default disposition is `require_confirmation`, not
silent execution. During discovery with no escalation service wired, the
risky action is fed back to the model to route around; with one wired, it
opens the risk-approval gate (§5) instead. During replay, the **human review
that promoted the artifact is the confirmation** for its risky steps;
unattended replay of a non-approved artifact is refused at the gateway.

**Data handling.** A pure `redact()` on every write path: full account
numbers, Luhn-valid card numbers, SSNs, bearer/JWT/API-key patterns, and
sensitive keys are replaced with a marker naming what was removed. Free-typed
values are masked to `«typed:len=N»` by construction. Partial PII (last-4,
first name) is deliberately kept for debuggability, a flagged compliance
decision, not an oversight.

**Limits.** Redaction is pattern/key based, so a novel secret format can slip
through until a rule is added. Screenshots are not pixel-redacted; every one
in `evidence/` is of the synthetic MockBank sandbox or ParaBank's public
QA-training demo (fabricated test data either way), but a real account
screen would need field-level masking before production. The allowlist is
coarse (domain/route/action) and doesn't understand business semantics
("transfer under $100 is fine").

## 7. Cuts

Two things the brief explicitly said could be mocked ended up built for
real instead of stubbed, worth naming since they read like cuts otherwise:
the **operator console** is a full Next.js 16 app with a live noVNC canvas,
event timeline, and in-browser sandbox terminal (not a bare/mock surface),
and the **container sandbox** (`CUA_USE_SANDBOX=1`) gives every run a real
per-run Docker container the worker drives over CDP, held on `STUCK` for
takeover. What's still genuinely cut:

- **Sandbox isolation depth**: a single Docker host, not a microVM
  (Firecracker/gVisor) boundary, kernel resource ceilings, a warm pool for
  cold-start, or orchestration beyond one host (k8s).
- **Postgres**: SQLite behind the same store interface; the access pattern
  (transactional versioning, base/override lookup) is Postgres-shaped.
- **Desktop / legacy-web adapters**: one `SurfaceAdapter` seam, Playwright
  implementation only. The DOM-outline perception path already degrades
  gracefully for no-accessibility-info markup.
- **Discovery model quality**: the offline `scripted` pilot recognises a
  handful of MockBank screens for CI/no-key demos; a real run uses
  OpenRouter/Groq/NIM/OpenAI via the router with identical downstream
  behaviour. `evidence/01-05` is a real `gpt-4o-mini` discovery run and its
  deterministic replays.
- **Artifact governance**: a single `draft → approved` gate with a reviewer
  name, no multi-reviewer workflow or RBAC. Re-review *is* auto-triggered for
  drift self-healing (§3); the locator `drift_signal` trend does not yet do
  the same.

**What I'd build next:** a **navigation graph** beneath the artifact layer,
so a shared prefix across capabilities on the same app (login → search →
open record) stops being re-discovered every run. Nodes are already-stable
`(vendor_app_id, tenant_scope, surface_fingerprint)` keys; edges are
`(locator_signature) → (next_fingerprint, action_type, risk_class)`,
populated for free from real discovery steps. Before each decision, check
for a known edge toward the goal and take it directly, skipping the LLM call
for that hop, falling back to full reasoning once the trail runs out; a
known edge never skips approval, so a `risky_irreversible` one still goes
through risk-approval every time. Also: aggregate `drift_signal` into a
per-step trend that triggers re-review the same way unrecognised states
already do, and a real desktop adapter to prove the surface seam.
