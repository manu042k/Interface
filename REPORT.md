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
decision is synchronous and in-process: the guardrail check is a function call,
not a network hop that can be skipped under load. Run execution, notifications,
and evidence writes are async/fire-and-forget so a slow log never blocks an
action.

**Composition root** (`assembly.py`) wires everything from `Config`; swapping
SQLite→Postgres, `FileSink`→object storage, or Playwright→a desktop driver
happens there, not in callers. The `cua` CLI and the FastAPI gateway are two
façades over the same `System`.

**LLM provider router.** `discovery.decide()` goes through `LLMRouter`, an ordered
list of OpenAI-compatible providers (OpenRouter, Groq, NVIDIA NIM, OpenAI: any
subset that has a key; an `OPENAI_API_KEY` auto-prepends `openai`) with
per-provider health state and a client-side RPM pace (`min_interval` from a
configured `rpm`) so a free tier isn't driven into a 429 in the first place. A
429 / quota / sustained-5xx is a *rotate, don't retry-in-place* signal: the
provider is marked `cooling_down` (TTL from `Retry-After`) and the same request
goes to the next provider. A 402 / out-of-balance is different: that provider is
`DISABLED` for the run, not cooled. All providers cooled → `AllProvidersExhausted`
(the orchestrator pauses); all `DISABLED` → `AllProvidersOutOfBalance` (the run
stops FAILED, no point spinning). Replay never touches this.

## 2. Artifact schema

`CapabilityArtifact` (`models.py`, Pydantic: construction *is* validation):

- **`steps: [Step]`**: the executed, successful, *actionable* steps 1:1. `observe`,
  failed actions, guardrail-rejected actions, and interstitial-dismiss clicks
  never become steps.
- **`Step.locator_spec: [LocatorStrategy]`**: a **ranked** chain, not one selector
  (the legacy-markup reality has no test IDs). Rank order encodes robustness:
  `role_name` → `label` → `relative_to_landmark` (“the value cell in the *Savings*
  row”) → `text` → `dom_anchor`. Every strategy carries a `rationale` string: the
  "your reasoning about robustness" the brief asks for, in-tree.
- **`Step.idempotent: bool`**: *required*, forcing the decision at authoring time.
  Mutating clicks to `/create|submit|confirm/…` routes default to non-idempotent.
- **`ValueBinding`**: typed values are parameterised: a typed value equal to a
  supplied param becomes a `param` binding; a secret-looking literal is refused
  and forced to a param; URLs are canonicalised to `{param}` templates. A third
  binding, `from_output`, chains a step's input to an **earlier step's own
  extracted output** within the same run: "select the account you just
  opened" as a later step's target, a value that doesn't exist until this run
  creates it, so it can be neither a literal (breaks every future run) nor a
  caller-supplied param (the caller can't know it in advance). The recorder
  detects this automatically: a typed/selected value that exactly matches an
  earlier extract's raw result gets `from_output` instead of falling through
  to a param or a stale literal; replay resolves it from the run's live
  `outputs` dict, not the caller's `params`.
- **`input_schema` / `output_schema`**: JSON Schema (draft 2020-12). Params are
  validated *before any action touches the surface*; extracted outputs validated
  before return.
- **`checkpoint: Condition`**: the final success assertion, verified, never assumed.
- **`known_outcomes: [BusinessOutcomeRule]` and `recoverable_rules: [RecoverableRule]`**:
  the error taxonomy is *part of the contract*, not bolted onto the executor.
- **Versioned + reviewable**: `(artifact_id, version)` rows in SQLite, immutable
  except the `draft → approved|rejected` transition; replay always pins an
  explicit version, never "latest". The whole artifact is redacted before it is
  persisted.

## 3. Determinism & error handling

**Determinism.** Replay resolves each step's ranked locator chain against the
current `SurfaceState`, checks the guardrail, acts, then **verifies the step
checkpoint** before moving on: a click that didn't produce the expected state
halts rather than continuing blind. No LLM anywhere in the loop. Resolution
results are cached per `(artifact_version, surface_fingerprint, step_index)` with
singleflight coalescing. The `matched_rank` of the strategy that resolved is the
**drift signal**: a non-zero rank means the primary identifier degraded, emitted
per resolution as `drift_signal: true` in the run's event log so a degrading
locator is visible *before* it becomes a failure. Aggregating those events into a
per-`(artifact, step)` trend (and auto-triggering re-review off it) is the
obvious next step (see §7). Every wait is bounded; no unbounded sleep anywhere.

**Exceptional states** (`replay/executor.py`). Before every step, and again
whenever a checkpoint fails, the screen is matched against the artifact's
declared rules:

| Class | Detection | Response |
|---|---|---|
| **Business outcome** | a `known_outcome.when` condition matches (e.g. "No members matched") | STOP, return `{outcome: business_outcome, code}`: a legitimate answer, **never** logged as an error, `failure_detail` null |
| **Recoverable** | a `recoverable_rule.when` matches (interstitial, transient slow load) | `dismiss` / `wait` / `reload`, log `recoverable_condition`, retry the step (bounded). Any recovery → final outcome `recoverable_then_success` |
| **Hard failure** | anything else that breaks a checkpoint or can't resolve | STOP, `{outcome: hard_failure, failure_detail: {step_index, expected, observed}}` + screenshot + DOM snapshot |

**Duplicate detection (`cua/dedup.py`).** `record()` first catches a re-run that
reproduced an existing flow byte-for-byte (`flow_fingerprint` match →
`duplicate_of`). A differently-worded re-recording of the *same function* takes
a structurally different path, so a looser post-record pass runs (never in a
loop): cheap structural signals (same entry URL, same input-schema keys, same
risk class, same checkpoint *shape*, high overlap of `(action, bound-param)`
pairs) flag it directly; when those are only partially there and a router is
configured, **one** `router.call_text` asks "same function? SAME / DIFFERENT /
UNSURE". Either way it's a `duplicate_of` review signal, never an auto-delete.

**Drift self-healing (unrecognised states).** A hard failure that broke a
checkpoint on a screen matching *no* declared rule also emits a `drift_signal`
event and a `DriftCandidate` (the failing step, the page's own error text, the
evidence refs). Out of the replay loop (still no LLM), `cua/drift.py`
`propose_patch` turns that into a **v+1 DRAFT** capability with a candidate
`text_present` `known_outcome` (`record_outcome="drift_patch"`, `supersedes`
set), filed into the normal `/review` queue. Never auto-applied: a reviewer sets
the real code, decides business-outcome vs recoverable, or rejects it. Repeated
failures patch the same open draft rather than stacking new versions.

The result contract (`ReplayResult.outcome`) is an enum: a caller never
string-matches an error message to tell success, a known outcome, and a failure
apart. **Idempotency-aware retry (ST-035):** a non-idempotent step that fails
ambiguously is *not* resubmitted; the executor re-observes and re-checks the
checkpoint first; if it already holds, the step is treated as done.

**Malformed model output on `assert_state` / `wait_for`.** Weaker models
(observed on Gemini 2.5 Flash) send `condition: {}` or `{"kind": "text_present",
"params": {}}` and put the phrase they meant to check in the (often mis-keyed,
`reas1oning`) rationale field. Discovery repairs this without a model round-trip:
a stricter tool sub-schema, a fuzzy rationale-key match, `_salvage_condition`
(rebuild from any quoted / ALL-CAPS phrase in the args), and finally
`_salvage_from_state` (lift a recognised confirmation marker, "CHANGES SAVED",
"TRANSFER POSTED" …, straight off the screen just observed). A run that used to
loop `assert_state` to the step ceiling now finishes on the first try; if
nothing is salvageable it fails once with the exact required shape, and a
same-tool failure streak escalates rather than grinding.

**UI drift** (secondary, per the brief): the `SurfaceState.fingerprint` is a
structural signature (tag skeleton + form field names, no text values): stable
across content changes, sensitive to markup drift; it keys the resolution cache
and, together with `drift_signal`, is how per-tenant/version drift surfaces.

## 4. Heterogeneity & multi-tenant

**Surface abstraction.** The seam is `SurfaceAdapter`
(`open_session` / `execute(Action)` / `snapshot` / `cdp_endpoint`) plus
`Perception` normalising a `RawSnapshot` into a surface-agnostic `SurfaceState`
(AX outline + DOM outline + screenshot + fingerprint). The recorded flow is
expressed entirely in that abstract vocabulary, so a legacy-web adapter (same
Playwright driver, DOM-outline path already degrades when there's no
accessibility info) or a desktop adapter (UI Automation / MSAA, screenshot +
AX tree, a VNC/RDP endpoint standing in for CDP) drops in without touching the
artifact schema, the locator engine, or the replay executor. Locator strategies
are already ranked by *portability across surface kinds*: `role_name` works on a
desktop AX tree too; `dom_anchor` is the web-only last resort.

**Multi-tenant reuse.** An artifact is recorded once against a canonical **base**
instance (`vendor_app_id` + `app_version`). Per-tenant variance (branding, an
extra confirmation step, a renamed field) is a thin **override** keyed
`(vendor_app_id, tenant_id, overrides_base_version)` that only declares the steps
that differ; `store.resolve_for_tenant()` returns an approved override merged
onto the approved base, else the base (`_merge_override`, unit-tested). No
per-tenant re-recording. Drift is managed by the fingerprint + the per-resolution
`drift_signal`: a tenant whose resolutions start falling to lower-ranked
strategies would be flagged for re-review before it breaks, without disturbing
the other 199; the aggregation/alerting on top of the signal is the design's
next step, not yet built.

## 5. Escalation & handoff

**Detect.** Discovery emits `stuck(reason)` when it can't safely proceed
(missing control, unexpected screen, a no-progress loop, guardrail block it can't
route around, or `all_providers_exhausted`) and blocks up to
`CUA_HANDOFF_WAIT_SECONDS` (default 240s) for an operator, since discovery is a
live, attended run by nature. Replay is the opposite by design: it is the
unattended, deterministic path an AI agent invokes in production, so it must
never block an invoke request on a human just because one step couldn't
recover: it reports `hard_failure` immediately (with `failure_detail` +
evidence + a `drift_candidate`), full stop. The escalate-and-resume mechanism
for replay (`ReplayExecutor._escalate_replay`, brief §3.6 "a replay hits a
condition it can't recover from") is still real and exercised directly by the
test suite: it opens an intervention exactly like discovery's, on the SAME
session, when wired with a `handoff_wait_s`, but it is **not** the gateway's
default invoke path (`CUA_REPLAY_HANDOFF_WAIT_SECONDS` defaults to `0`, a
separate knob from discovery's; a deployment opts a replay path INTO waiting
on a human explicitly). Either way that it fires, the run transitions to
`stuck` and its session is **held, not torn down**.

**A second, lighter gate: risk approval.** Not every escalation is "the agent
is stuck." When policy returns `REQUIRE_CONFIRMATION` for a genuinely
risky/irreversible action the agent otherwise knows how to perform (§6),
full session takeover is overkill: the human doesn't need to drive the
browser, they need to see *what's about to happen* and say yes or no.
`EscalationService.open_risk_approval()` raises a second `InterventionRequest`
kind (`risk_approval`, distinct from `handoff`) carrying the proposed action
in plain language, with no session lock transfer at all; `decide(approved=…)`
resolves it. `take_control()` enforces that separation directly: it raises if
the intervention isn't a `handoff`, so a `risk_approval` gate cannot be driven
through the takeover API by mistake, only through its own `decide()`. Two
rejections of the same proposed action `dead_end` the run rather than looping
the agent back to try the identical rejected step again. This is the
mechanism that actually implements §3.4's "require confirmation" disposition
day to day; full handoff is reserved for when a human must *act*, not just
*decide*.

`evidence/06-discovery-handoff` demonstrates the full handoff end to end with
nothing scripted into the prompt: the goal states a business rule ("the
account type and deposit amount are decisions only a supervisor is
authorized to make"), never a tool name, and the model reaches `stuck()` on
its own after being nudged back twice for calling it too early. A human
(a script standing in for one, paced at 1.9-2.9s per action rather than fired
back-to-back) takes control of the live session, decides the two fields,
submits, and hands back; the agent resumes, re-observes, retries its own
truncated first extract, and reports the real confirmation number MockBank
computed. `evidence/07-discovery-handoff-parabank` runs the identical
mechanism, unmodified, against a second, real external site: ParaBank's
public demo bank (parasoft.com), the assignment brief's own example of "a
public proxy target." Same organic `stuck()`, same live take-control, same
resume-and-verify; the only difference is the app underneath, which is the
point: this isn't a MockBank-specific trick. See `evidence/README.md` for
both bundles' exact transcripts and what remains simulated (the operator
script) versus real (everything it drives).

**Route.** `EscalationService.open_intervention()` acquires the automation lock
via the `SessionBroker`, captures a context bundle (screenshot, DOM, transcript
tail, current URL, capability/goal/step) into evidence, and creates an
`InterventionRequest`.

**Take control of the *same* session.** The `SessionBroker` control lock is a
compare-and-swap + **TTL lease** (not an orphanable mutex: a crashed worker's
lease simply lapses and is reaped). The operator claims the intervention, then
`take_control` releases automation's lease and grants the human's on the *same*
`session_id`, returning a live handle + a CDP-style remote-display ref. Every
operator action is executed through the adapter against that session and recorded
to `human_actions_log`.

**Hand back.** `release_control` → `resume` releases the human lease and
re-acquires automation's on the same `session_id`. `resume` can take a
`goal_checkpoint` and, if it already holds, resolve the run as *goal satisfied*;
in the current wiring the caller passes none, so instead:

- **Discovery** resumes as `RUNNING` with an explicit instruction to `observe`
  the current screen first and *not* assume the goal is done; it must re-verify
  with `assert_state` / `extract` before it may call `done`. (The earlier
  hard-coded MockBank checkpoint was removed; it could never hold on another
  site and wedged the run.)
- **Replay** re-observes and re-checks the failed step's own checkpoint; if the
  human's actions satisfied it the run continues from the next step, otherwise it
  retries the recorded step once, and if that still fails returns the
  `hard_failure` (now tagged with a `human_intervention` recovered-condition).

With `CUA_USE_SANDBOX=1` the operator takes over by clicking directly in the live
**noVNC** canvas of the same container; without it, the console's scripted action
buttons drive the shared session. Either way the **mechanism** (pause / cede /
resume on one session, lock ownership model, action recording) is real and
covered by tests for both discovery and replay.

## 6. Safety

**Allowlist, default deny.** `PolicyEngine.check()` runs synchronously, in-process,
before *every* action (discovery and replay). Per-tenant policy independently
expresses permitted domains, route regexes, and action types. Unknown tenant,
unparseable target, a broken allowlist file, or an exception inside the check all
resolve to **BLOCK**: fail closed, always.

**Risk class.** Actions are `safe_reversible` by default; `risky_irreversible` if
the step declares it, the action type is on the tenant's risky list, or a
*mutating* action (click/type/select/press_key, never a passive
extract/assert_state/wait_for/navigate/scroll, which can't be irreversible no
matter which route it's on) lands on a route matching a risky pattern
(`/sub-account/create$`). The risky class is dispositioned by tenant policy:
default `require_confirmation` rather than silent execution. During
discovery, with no escalation service wired the risky action is fed back to
the model to route around ("pick a safe alternative or call stuck"); with one
wired, it opens the lightweight **risk-approval gate** (§5) instead: the
human sees the proposed action and approves or rejects it, no session
takeover. During replay, the **human review that promoted the artifact to
`approved` is the confirmation** for its risky steps; unattended replay of a
non-approved artifact is refused at the gateway.

**Data handling.** A pure `redact()` on every write path (artifact store, log
sink, evidence metadata): full account numbers (12–17 digits, epoch-timestamp
excluded), Luhn-valid card numbers, SSNs, bearer/JWT/API-key patterns, and
sensitive keys are replaced with a marker that says *what* was removed. Free-typed
values are masked to `«typed:len=N»` by construction, because a raw password has
no delimiter for a pattern to catch. Partial PII (last-4, first name) is
deliberately kept for debuggability; the exact line is flagged as a compliance
decision, not guessed.

**Limits.** Redaction is pattern + key based, so a novel secret format in free
text can slip through until a rule is added. Screenshots are not pixel-redacted:
evidence capture is gated to failure points, and screenshots of real account
screens would need field-level masking before production; every screenshot in
`evidence/` is of either the synthetic MockBank sandbox or ParaBank's public
QA-training demo site (parasoft.com's own fabricated test bank, `john`/`demo`,
not a real customer), no real PII either way. The allowlist is coarse
(domain/route/action); it does not understand business semantics ("transfer
under $100 is fine").

## 7. Cuts

Deliberately thin-but-real, or stubbed at a clean seam:

- **Operator console**, now a Next.js 16 + shadcn app: goal input, a live
  **noVNC** view of the run, a streaming event timeline, an in-browser terminal
  into the run's sandbox, the stuck-run handoff, and a printable report. The
  co-browsing takeover is real (operator clicks land in the same browser).
- **Container sandbox**, now real: `CUA_USE_SANDBOX=1` gives every run its own
  Docker container (`Xvfb → xfce → headed Chromium/CDP → x11vnc → websockify`),
  the worker attaches over CDP and drives the *same* browser the operator
  watches, and the container is held on `STUCK` for takeover. Still design-only:
  a microVM (Firecracker/gVisor) boundary, kernel CPU/memory ceilings, a warm
  pool for cold-start, and orchestration beyond a single Docker host (k8s).
- **Postgres**: SQLite behind the same store interface; the access pattern
  (transactional versioning, base/override lookup) is Postgres-shaped.
- **Desktop / legacy-web adapters**: one `SurfaceAdapter` seam, Playwright
  implementation. The DOM-outline perception path already degrades gracefully
  for no-accessibility-info markup.
- **Discovery model quality**: the offline `scripted` pilot recognises a
  handful of MockBank screens so CI and the no-key demo run the whole pipeline;
  a real run uses OpenRouter / Groq / NIM / OpenAI via the router (code + example
  default `openai/gpt-4o-mini`) with identical downstream behaviour. Weaker
  tool-callers (Gemini 2.5 Flash was the stress case for the `assert_state`
  repair in §3) are handled without a model round-trip.
  `evidence/01-discovery-real-llm` is a real `gpt-4o-mini` discovery run;
  `evidence/02-05` are its deterministic replays, one per outcome class.
- **Artifact governance**: a single `draft → approved` gate with a reviewer
  name; no multi-reviewer workflow or RBAC. Re-review *is* auto-triggered for a
  replay that lands on an unrecognised state (drift self-healing files a v+1
  draft, §3); the locator `drift_signal` trend does not yet do the same.

**What I'd build next:**

A **navigation graph beneath the artifact layer**, so a shared prefix across
capabilities on the same app (login → search → open record shows up in
nearly every capability recorded against a given target) stops being
re-discovered by the LLM every single time. It reuses infrastructure that
already exists rather than adding a new primitive: nodes are
`(vendor_app_id, tenant_scope, surface_fingerprint)` (`SurfaceState.fingerprint`
is already a stable structural key per screen), and edges are
`(from_fingerprint, locator_signature) → (to_fingerprint, action_type,
risk_class, success_count, sample_reasoning)`, populated for free from every
discovery run's real steps (the same `flow_fingerprint`/`cua/dedup.py`
mechanism already does the equivalent one level up, at the whole-flow
granularity). Before each LLM decision, check whether the current fingerprint
has an outgoing edge worth taking toward the goal (a cheap keyword/embedding
match against accumulated `reasoning` text); if so, take it directly (same
policy/guardrail check as any step, just skip the LLM call for that hop)
and fall back to full reasoning the moment the trail runs out of known
edges, with the existing post-action checkpoint catching (and recovering
from) a wrong turn. The one non-negotiable: a "known" edge can never
silently skip approval; only `safe_reversible` edges fast-path; a
`risky_irreversible` edge still goes through risk-approval every time,
known or not, so this can never erode the "a human approved this specific
capability" guarantee the review gate rests on. Payoff compounds: discovery
gets cheaper and faster the more capabilities are recorded against a given
app, converging toward a real site map, while staying genuinely LLM-driven
at every actually-new edge, the requirement the brief is strictest about.

Also: aggregate the per-resolution `drift_signal` into a
per-`(artifact, step)` trend that files a re-review the same way unrecognised
states already do; post-run LLM classification of a `DriftCandidate`
(business-outcome vs recoverable vs genuine defect) to pre-fill the draft;
the bounded single-step assisted-fallback on replay hard failure
(policy-checked, logged as distinct evidence, never chained); a real desktop
adapter to prove the surface seam; canonicalisation of routes/values into
parameterised patterns across two MockBank "tenant" variants.
