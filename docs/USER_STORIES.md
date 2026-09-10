# User Stories & Implementation Order — Computer-Use Automation System

Derived from `TDD-ComputerUse-Automation-System.md` §6. Written for Claude Code to implement session-by-session, in dependency order. Each phase is a natural session/PR boundary — don't start a phase until its dependencies are checked off, but within a phase stories can interleave.

**How to use this with Claude Code:** work one phase at a time, in order. Paste (or point Claude Code at) the phase's stories plus the relevant TDD sections (component table §1.4, the matching sequence diagram §6.2, the data model in §6.4). Each story's Acceptance Criteria is your definition of done for that unit of work — treat it as the test to write first.

---

## Implementation order (phase → story IDs → why this order)

| # | Phase | Stories | Why here |
|---|---|---|---|
| 0 | Foundation & core contracts | ST-001 – ST-005 | Nothing else compiles without shared types, config, and a target to point at. |
| 1 | Surface Adapter & Perception | ST-006 – ST-009 | The one thing every other domain depends on: a way to see and touch the live app. |
| 2 | Policy/Guardrail Engine | ST-010 – ST-012 | Must exist *before* any action executes, discovery or replay — safety is not bolted on later. |
| 3 | Observability & Evidence | ST-013 – ST-015 | Wire logging/evidence early so every phase after this is debuggable and produces evidence for free. |
| 4 | Discovery Agent + LLM Provider Router | ST-016 – ST-021 | First point you get a real, live, model-driven run end to end — the "heart of the project" the brief requires. |
| 5 | Artifact Recorder & Store | ST-022 – ST-026 | Nothing to replay without a persisted artifact from a successful discovery run. |
| 6 | Locator Resolution Engine | ST-027 – ST-029 | Replay needs this; build it once artifacts exist to resolve against. |
| 7 | Replay Executor | ST-030 – ST-035 | The production execution path — deterministic replay plus the full error/outcome taxonomy. |
| 8 | Escalation & Handoff | ST-036 – ST-040 | Needs a real discovery or replay run in flight to interrupt — depends on Phases 4–7 existing. |
| 9 | Sandbox Isolation | ST-041 – ST-042 | Hardening pass on the session lifecycle Phases 1–8 already built; safe to do last since it wraps, not replaces, the adapter interface. |
| 10 | Evidence assembly & write-up | ST-043 – ST-045 | Final packaging once the vertical slice actually runs end to end. |

This order deliberately puts Discovery (Phase 4) before Replay (Phase 7) even though the TDD's narrative describes replay as "the production path" — you cannot record a real artifact to replay until a discovery run has actually produced one, and the brief is explicit that the discovery run has to be genuine, not stubbed.

---

## Phase 0 — Foundation & core contracts

### ST-001: Project scaffold & configuration loading
**Size:** S · **Depends on:** — · **Components:** all
**As a** developer, **I want** a runnable project skeleton with environment-based config (LLM provider keys, target app URL, tenant allowlist path) **so that** every later story has somewhere to live and a consistent way to get secrets without hardcoding them.
- Given a fresh checkout, when I run the setup command in README, then the project installs dependencies and starts without error using a `.env.example`-derived config.
- Given a required config value is missing, when the app starts, then it fails fast with a clear error naming the missing key (never a silent default for a secret).

### ST-002: Core data model types
**Size:** M · **Depends on:** ST-001 · **Components:** Artifact Store, Replay Executor, Orchestrator, Escalation Service
**As a** developer, **I want** typed definitions for `CapabilityArtifact`, `Step`, `RunRecord`, `ReplayResult`, and `InterventionRequest` (per TDD §6.4) **so that** every component agrees on shape before any of them are implemented.
- Given the type definitions, when any component constructs one of these objects, then it's validated against a schema (JSON Schema or equivalent) at construction time, not just at the API boundary.
- Given the `Step` type, when a mutating action is declared, then `idempotent: bool` is a required field, not optional (forces the idempotency decision at authoring time, per ADR discussion in the TDD).

### ST-003: Secrets & Redaction Layer (baseline rules)
**Size:** S · **Depends on:** ST-001 · **Components:** Secrets & Redaction Layer
**As the** system, **I want** a pure `redact(payload) -> sanitizedPayload` function with baseline rules (strip anything matching credential/token/full-account-number patterns) **so that** artifacts and logs never persist secrets from day one, before any writer exists to call it.
- Given a payload containing a value that looks like a password, API key, or full account/card number, when `redact()` runs, then that value is replaced with a redaction marker, not omitted silently (marker must indicate *that* something was redacted).
- Given a payload with no sensitive content, when `redact()` runs, then the payload is returned unchanged (no false-positive stripping of ordinary business data).

### ST-004: Observability Sink interface (structured logging skeleton)
**Size:** S · **Depends on:** ST-002 · **Components:** Observability Sink
**As a** developer, **I want** a `logEvent(run_id, step, event)` and `putEvidence(run_id, step, blob)` interface wired to local disk (swappable for object storage later) **so that** every component built from here on can log without knowing the final storage backend.
- Given any component calls `logEvent`, when the call completes, then the event is retrievable by `run_id` in order, with a timestamp and step index.
- Given the sink is unavailable, when a component calls `logEvent`, then the call fails without blocking or crashing the caller (logging is fire-and-forget per TDD §1.3 async/sync split).

### ST-005: Target application selection & environment setup
**Size:** S · **Depends on:** ST-001 · **Components:** Surface Adapter
**As a** developer, **I want** a concrete proxy target chosen and reachable (a public demo/sandbox site or local app with a non-trivial search → detail → action → confirmation flow) **so that** every subsequent story has a real surface to build and test against.
- Given the chosen target, when I navigate to it manually, then it exposes at least one multi-step flow with a confirmation/success state and at least one legitimate "not found"/error path — both needed later for the outcome taxonomy (Phase 7).
- Given the target's terms of use, when automation is built against it, then rate limits are respected and no real credentials/PII are used (per assignment ground rules).

---

## Phase 1 — Surface Adapter & Perception

### ST-006: Surface Adapter — session lifecycle
**Size:** M · **Depends on:** ST-005 · **Components:** Surface Adapter
**As the** Orchestrator, **I want** `openSession(target, tenant) -> session_handle` and `closeSession(session_handle)` **so that** discovery and replay both get an isolated, addressable live session to act on.
- Given a valid target, when `openSession` is called, then a browser context/session is created and a handle is returned that later calls can reference.
- Given a session handle, when `closeSession` is called, then the underlying browser context is torn down and the handle becomes invalid for further actions.

### ST-007: Surface Adapter — primitive actions
**Size:** M · **Depends on:** ST-006 · **Components:** Surface Adapter
**As the** Orchestrator/Replay Executor, **I want** `execute(action) -> ActionResult` supporting click/type/select/navigate/wait_for against a resolved target **so that** both discovery and replay share one execution primitive.
- Given a resolved target and a `click` action, when executed, then the click occurs and `ActionResult` reports success/failure plus the resulting URL/state hash.
- Given a `wait_for` action with a condition and timeout, when the condition isn't met before timeout, then `ActionResult` reports a timeout outcome, not an exception that crashes the caller.

### ST-008: Perception — SurfaceState snapshot
**Size:** M · **Depends on:** ST-006 · **Components:** Perception Module
**As the** Discovery Agent/Locator Resolution Engine, **I want** `observe(session_handle) -> SurfaceState` returning a normalized AX tree summary + relevant DOM excerpt + screenshot reference + URL/title **so that** every decision and every locator resolution works from one consistent state representation.
- Given a live session, when `observe()` is called, then the returned `SurfaceState` includes at minimum: URL, a screenshot reference, and a structured accessibility-tree summary (not raw HTML dumped wholesale).
- Given a page with no accessible-name/role information (hostile legacy markup), when `observe()` is called, then it degrades to DOM structure + screenshot rather than returning an empty state.

### ST-009: Perception — extract()
**Size:** S · **Depends on:** ST-008 · **Components:** Perception Module
**As the** Discovery Agent/Replay Executor, **I want** `extract(target_description, expected_shape) -> value` **so that** reading data back out of the surface (a balance, a confirmation number) is a typed, first-class operation rather than ad hoc scraping.
- Given a target that exists and matches the expected shape (e.g., a currency string), when `extract` runs, then a typed value matching that shape is returned.
- Given a target that doesn't match the expected shape, when `extract` runs, then a structured error is returned naming what was expected vs. observed (feeds the hard-failure detail format in Phase 7).

---

## Phase 2 — Policy/Guardrail Engine

### ST-010: Allowlist configuration model
**Size:** S · **Depends on:** ST-002 · **Components:** Policy/Guardrail Engine
**As a** Tenant/Platform Admin, **I want** a configurable allowlist (permitted domains/routes, permitted action types) loaded per tenant **so that** guardrails are enforced against explicit configuration, not hardcoded assumptions.
- Given an allowlist config file/record for a tenant, when the engine loads it, then domains, routes, and permitted action types are all independently expressible.
- Given a target/action not covered by any tenant's allowlist, when checked, then the default is **deny**, never implicit allow.

### ST-011: Pre-action guardrail check (blocking hook)
**Size:** M · **Depends on:** ST-010, ST-007 · **Components:** Policy/Guardrail Engine, Orchestrator, Replay Executor
**As the** Orchestrator/Replay Executor, **I want** `checkAction(action, context) -> Allow | Block | RequireConfirmation` called synchronously and in-process before every action reaches the Surface Adapter **so that** no action — discovery or replay — can execute outside policy, ever.
- Given an action outside the tenant's allowlist, when checked, then it returns `Block` and the action is never sent to the Surface Adapter.
- Given the guardrail check itself throws or times out, when that happens, then the default behavior is `Block`, not `Allow` (fail closed).

### ST-012: Risk classification (safe/reversible vs. risky/irreversible)
**Size:** M · **Depends on:** ST-011 · **Components:** Policy/Guardrail Engine
**As the** Policy Engine, **I want** every action type tagged with a risk class and risky/irreversible actions handled conservatively (block, require confirmation, or flag) **so that** the system doesn't treat "click a link" and "submit a funds transfer" identically.
- Given an action classified `risky_irreversible`, when checked during discovery, then it returns `RequireConfirmation` (or `Block`, per configured policy) rather than silently proceeding — document which of the two is the default and why.
- Given an action classified `safe_reversible`, when checked, then it proceeds without additional confirmation, keeping the common path fast.

---

## Phase 3 — Observability & Evidence

### ST-013: Structured per-step event log
**Size:** S · **Depends on:** ST-004, ST-007 · **Components:** Observability Sink, Orchestrator
**As an** engineer debugging a run, **I want** every step logged with what was attempted, why (goal/decision context for discovery; artifact step for replay), and the outcome **so that** I can reconstruct a run's full history without re-running it.
- Given a completed run, when I query its logs by `run_id`, then I see an ordered sequence of step events, each including timestamp, action type, target description, and outcome.
- Given a discovery run, when a step is logged, then the model's stated reasoning/decision for that step is captured alongside the action (not just the action itself).

### ST-014: Evidence capture on failure
**Size:** S · **Depends on:** ST-013, ST-008 · **Components:** Observability Sink, Perception Module
**As an** engineer debugging a failed run, **I want** a screenshot (and DOM snapshot where applicable) captured at the moment of failure **so that** I have a richer signal than the log line alone.
- Given a step fails (hard failure), when the failure is recorded, then a screenshot reference and current `SurfaceState` are attached to that log event.
- Given a successful run, when it completes, then evidence capture does not fire on every step (only failure points, or configured checkpoints) — avoid drowning storage in unnecessary blobs.

### ST-015: Redaction wired into the write path
**Size:** S · **Depends on:** ST-003, ST-013 · **Components:** Observability Sink, Secrets & Redaction Layer
**As the** system, **I want** every log event and evidence blob passed through `redact()` before persistence **so that** the "never persist secrets" guardrail is enforced structurally, not by convention.
- Given a step event containing a typed value (e.g., a password field's input), when logged, then the persisted event shows a redaction marker in place of the raw value.
- Given a screenshot evidence blob, when captured on a page showing a masked/partial account number, when persisted, then the same field-level redaction expectations documented in the TDD's open question are at minimum flagged as unresolved in a code comment — do not silently assume screenshots need no redaction.

---

## Phase 4 — Discovery Agent + LLM Provider Router

### ST-016: LLM Provider Router — client abstraction
**Size:** M · **Depends on:** ST-001 · **Components:** LLM Provider Router
**As the** Discovery Agent, **I want** one `call(prompt, tools) -> ModelResponse` interface backed by configurable providers (OpenRouter, NVIDIA NIM) **so that** I never talk to a provider SDK directly.
- Given both providers configured, when `call()` is invoked, then it routes to the highest-preference `healthy` provider first.
- Given a provider's response, when returned, then the response is normalized to one internal `ModelResponse` shape regardless of which provider served it.

### ST-017: LLM Provider Router — rotation on rate limit
**Size:** M · **Depends on:** ST-016 · **Components:** LLM Provider Router
**As the** system, **I want** the router to rotate to the next configured provider on a 429/rate-limit/quota error, mark the failed provider `cooling_down`, and retry the same request **so that** one provider's limit never stalls a discovery run.
- Given the active provider returns 429 with a `Retry-After` header, when that happens, then the provider is marked `cooling_down` until that header's duration elapses, and the request is retried against the next provider in the same call.
- Given all configured providers are `cooling_down` simultaneously, when a call is attempted, then the router returns a distinct `all_providers_exhausted` error (not a generic timeout) for the Orchestrator to handle per ST-020.
- Given a provider successfully serves a request, when logged, then which provider served it is recorded alongside the turn (per TDD §1.5).

### ST-018: Discovery Agent — tool vocabulary & decision call
**Size:** L · **Depends on:** ST-017, ST-008 · **Components:** Discovery Agent
**As the** Orchestrator, **I want** `decide(goal, SurfaceState, history) -> ToolCall` implemented against the fixed 10-tool vocabulary (observe/click/type/select/navigate/wait_for/extract/assert_state/done/stuck, per TDD §1.3) **so that** the model's output is always a structured, executable action, never free text or arbitrary code.
- Given a goal and current `SurfaceState`, when `decide()` is called, then the response is a single, schema-valid tool call from the fixed vocabulary — reject and retry once on a malformed/out-of-vocabulary response before treating it as a hard failure.
- Given the model calls `done(outputs)`, when that happens, then the outputs are validated as a structured object, ready to hand to the Artifact Recorder.
- Given the model calls `stuck(reason, context)`, when that happens, then the reason and context are captured verbatim for the Escalation Service (Phase 8).

### ST-019: Orchestrator — observe/decide/act loop with budgets
**Size:** L · **Depends on:** ST-018, ST-011 · **Components:** Orchestrator
**As the** system, **I want** the loop to run observe → decide → guardrail-check → act repeatedly until `done`, `stuck`, max-steps, or timeout **so that** a discovery run can't run forever or act outside policy.
- Given a run exceeds the configured max-step count without reaching `done`/`stuck`, when that happens, then the run is stopped with a `dead_end` status, not left running indefinitely.
- Given the guardrail check blocks a proposed action, when that happens, then the rejection is fed back to the Discovery Agent as an observation on the next turn (per the sequence diagram in TDD §6.2 Flow A), not silently dropped.

### ST-020: Orchestrator — stuck/exhausted handling
**Size:** M · **Depends on:** ST-019, ST-017 · **Components:** Orchestrator
**As the** system, **I want** `stuck()` calls and `all_providers_exhausted` errors both routed to a pause-and-escalate path (stub acceptable until Phase 8 is built) **so that** neither condition crashes the run or loses its session.
- Given the model calls `stuck()`, when that happens, then the run transitions to a `stuck` status with the session held open (not torn down), ready for Phase 8's escalation to pick up.
- Given `all_providers_exhausted` occurs, when that happens, then the run pauses and retries the LLM call on a longer backoff rather than failing immediately; only escalates if this persists past a configured ceiling.

### ST-021: Goal Gateway — POST /runs
**Size:** M · **Depends on:** ST-019 · **Components:** Goal Gateway, Orchestrator
**As the** Calling Agent, **I want** `POST /runs {goal, target, mode: discovery}` to validate the target against the allowlist and return a `run_id` immediately while the run executes async **so that** I never block on a long-running discovery loop.
- Given a valid goal and allowlisted target, when I POST, then I receive a 202 with `{run_id, status: pending}` before the run has finished.
- Given a target outside the allowlist, when I POST, then I receive a validation error and no run is created.
- Given a run in progress, when I `GET /runs/{id}`, then I see current status (`pending|running|stuck|completed|failed`) and, once complete, the resulting `artifact_id` if applicable.

---

## Phase 5 — Artifact Recorder & Store

### ST-022: Artifact Store — schema & persistence
**Size:** M · **Depends on:** ST-002, ST-001 · **Components:** Artifact Store
**As a** developer, **I want** `CapabilityArtifact` persisted in Postgres with versioning and status (`draft|approved|deprecated`) **so that** artifacts are durable, queryable, and never silently overwritten.
- Given a new artifact is saved, when persisted, then it gets version 1 and status `draft`.
- Given an existing artifact is re-recorded (new discovery run for the same capability), when saved, then it creates a new version rather than mutating the prior one — prior versions remain readable.

### ST-023: Artifact Recorder — transcript to draft artifact
**Size:** L · **Depends on:** ST-022, ST-019 · **Components:** Artifact Recorder
**As the** system, **I want** a successful discovery transcript converted into an ordered `CapabilityArtifact` (steps, locator candidates per step, declared inputs/outputs, checkpoint) **so that** the model's exploratory run becomes a reusable, replayable capability.
- Given a discovery run that ended in `done`, when the Recorder runs, then it produces a draft artifact whose steps match the executed action sequence 1:1 (no LLM-only steps like intermediate `observe()` calls leak into the artifact).
- Given the discovery run's `done(outputs)` payload, when recorded, then it becomes the artifact's `output_schema`, and any values the model was given as goal parameters become the `input_schema`.
- Given the final state the model asserted before calling `done`, when recorded, then it becomes the artifact's `checkpoint` condition.

### ST-024: Redaction applied to artifact persistence
**Size:** S · **Depends on:** ST-023, ST-003 · **Components:** Artifact Recorder, Secrets & Redaction Layer
**As the** system, **I want** every artifact passed through `redact()` before it's written to the Artifact Store **so that** no captured `type` step value containing sensitive input becomes permanently stored in a reusable capability.
- Given a discovered `type` step whose value looks like a credential, when the artifact is persisted, then that value is redacted/parameterized rather than stored literally.

### ST-025: Artifact review & promotion API
**Size:** M · **Depends on:** ST-022 · **Components:** Artifact Store
**As a** Capability Reviewer, **I want** `GET /artifacts?status=draft`, `GET /artifacts/{id}/versions/{v}`, and `POST /artifacts/{id}/versions/{v}/promote {decision, notes}` **so that** I can review a draft artifact's steps/locators/risk classification and gate it before any agent can invoke it.
- Given a draft artifact, when I promote it with `decision: approve`, then its status becomes `approved` and it becomes eligible for replay invocation.
- Given a draft artifact, when I promote it with `decision: reject`, then its status becomes `rejected` with my notes attached, and it remains replay-ineligible.
- Given an artifact not yet `approved`, when a replay is attempted against it, then the invocation is refused with a clear "not approved" error (this is the gate that makes review meaningful, not decorative).

### ST-026: Base/override artifact model (minimal multi-tenant schema)
**Size:** M · **Depends on:** ST-022 · **Components:** Artifact Store
**As a** Tenant Admin, **I want** an artifact optionally scoped as `base` or as a `{tenant_id, overrides_base_version}` override **so that** tenant-specific variance doesn't require a full re-recording (per ADR-08).
- Given a base artifact, when a tenant-specific override is created referencing it, then the override only needs to declare the steps/locators that differ — unspecified steps inherit from base.
- Given a replay invocation for a specific tenant, when resolving which artifact to run, then the resolver checks for a tenant override first and falls back to the base artifact if none exists.

---

## Phase 6 — Locator Resolution Engine

### ST-027: Multi-strategy locator resolution
**Size:** L · **Depends on:** ST-008, ST-023 · **Components:** Locator Resolution Engine
**As the** Replay Executor, **I want** `resolve(locator_spec, SurfaceState) -> {target, matched_strategy}` to try a ranked list of strategies (role+accessible-name, test-id if present, semantic DOM anchor path, relative-to-landmark, screenshot-region fallback) in order **so that** replay survives markup that has no stable selectors (per ADR-02).
- Given a locator spec with multiple ranked strategies, when the first strategy fails to resolve, then the next is tried automatically, and the response reports which one ultimately matched.
- Given no strategy resolves, when that happens, then a structured "unresolvable target" error is returned, naming which strategies were tried and why each failed — never a raw exception.

### ST-028: Drift signal exposure
**Size:** S · **Depends on:** ST-027 · **Components:** Locator Resolution Engine, Observability Sink
**As an** engineer monitoring capability health, **I want** "which strategy matched" logged per resolution, with an alert-worthy signal when a lower-ranked (less robust) strategy had to be used **so that** UI drift shows up as a trend before it becomes an outright failure.
- Given a resolution that matched via the primary strategy, when logged, then it's recorded as a normal-health event.
- Given a resolution that had to fall back to a lower-ranked strategy, when logged, then it's flagged distinctly (e.g., `drift_signal: true`) so it's queryable across runs of the same artifact/step.

### ST-029: Resolution caching with request coalescing
**Size:** M · **Depends on:** ST-027 · **Components:** Locator Resolution Engine
**As the** system, **I want** resolved-locator results cached per `(artifact_version, surface_fingerprint)` with singleflight coalescing on cache miss **so that** concurrent replays of the same hot capability don't stampede recomputation (per TDD §3.2).
- Given two concurrent replay steps resolving the same locator spec against the same surface fingerprint, when the cache is cold, then only one resolution computation runs and both callers receive its result.
- Given the surface fingerprint changes (drift detected), when that happens, then the cache entry for that key is invalidated, not silently served stale.

---

## Phase 7 — Replay Executor

### ST-030: Replay invocation — input validation at the boundary
**Size:** S · **Depends on:** ST-025, ST-002 · **Components:** Capability Gateway, Replay Executor
**As the** Calling Agent, **I want** `POST /replays/{artifact_id}/invoke {version, params, idempotency_key}` to validate `params` against the artifact's declared `input_schema` before any action touches the live surface **so that** a poison-pill payload is rejected at the boundary, not mid-flow (per §3.3).
- Given `params` missing a required field or with a wrong type, when invoked, then a 400-equivalent structured validation error is returned and no session is opened.
- Given valid `params`, when invoked, then the Replay Executor is dispatched with them.

### ST-031: Replay Executor — deterministic step execution with checkpoints
**Size:** L · **Depends on:** ST-030, ST-027, ST-011 · **Components:** Replay Executor
**As the** Calling Agent, **I want** each artifact step executed via locator resolution → guardrail check → action → step-checkpoint verification, with no LLM in the loop **so that** replay is deterministic and self-verifying, not "click and hope" (per ADR-07).
- Given an artifact with N steps, when replayed, then each step's checkpoint is verified before proceeding to the next — a click that didn't produce the expected state halts the run rather than continuing blind.
- Given all steps succeed and the final checkpoint holds, when the run completes, then declared outputs are extracted and returned per the `output_schema`.

### ST-032: Outcome taxonomy — business outcomes
**Size:** M · **Depends on:** ST-031 · **Components:** Replay Executor
**As the** Calling Agent, **I want** a legitimate result like "no such member" classified as `outcome: business_outcome` with a `business_outcome_code`, not as a failure **so that** I can branch on it as a normal answer (per the TDD's "business outcome vs. failure" glossary entry).
- Given a replay hits a state the artifact declares as a known business-outcome pattern (e.g., a "no results" message), when detected, then the result is `{outcome: business_outcome, code: "..."}`, and the run is not logged or surfaced as an error.
- Given the target app's "not found" flow is exercised, when replayed with a deliberately invalid input, then this is the concrete demonstration required for `/evidence/` (per assignment §6.3).

### ST-033: Outcome taxonomy — recoverable conditions
**Size:** M · **Depends on:** ST-031 · **Components:** Replay Executor
**As the** Replay Executor, **I want** known recoverable conditions (an expected interstitial dialog, a transient slow load) detected and handled deliberately (dismiss/wait-retry) **so that** the run continues rather than treating every runtime hiccup as a hard stop.
- Given a step's SurfaceState shows a known dismissible interstitial, when detected, then it's dismissed automatically and the original step is retried, logged as a recoverable-condition event (distinct from both success and failure).
- Given a transient load exceeds the expected wait but resolves within a bounded retry window, when that happens, then the run proceeds without surfacing an error.

### ST-034: Outcome taxonomy — hard failures
**Size:** M · **Depends on:** ST-031, ST-014 · **Components:** Replay Executor, Observability Sink
**As an** engineer debugging a failed replay, **I want** any unrecognized state to produce `{outcome: hard_failure, failure_detail: {step_index, expected, observed}}` with evidence attached **so that** I can debug without re-running the capability live.
- Given a step's checkpoint fails and the resulting state matches no known business-outcome or recoverable pattern, when that happens, then the run halts, evidence is captured (per ST-014), and the structured failure detail names exactly what was expected vs. what was observed.
- Given a hard failure, when returned to the caller, then it is clearly distinguishable in the response shape from both `success` and `business_outcome` — a caller must never have to string-match an error message to tell them apart.

### ST-035: Idempotency-aware retry for mutating steps
**Size:** M · **Depends on:** ST-031, ST-002 · **Components:** Replay Executor
**As the** system, **I want** a mutating step's `idempotent: false` flag to change retry behavior — re-check the checkpoint before retrying rather than blindly resubmitting **so that** an ambiguous failure after a submit click never causes a duplicate transaction (per §3.1).
- Given a non-idempotent step times out after execution with an ambiguous outcome, when the executor considers retrying, then it first re-observes the surface and checks whether the step's checkpoint already holds before deciding to retry or escalate.
- Given an idempotent step (e.g., a read/navigate), when it fails, then it retries per the standard backoff policy without this extra check.

---

## Phase 8 — Escalation & Handoff

### ST-036: Session Broker — control lock with TTL lease
**Size:** M · **Depends on:** ST-006 · **Components:** Session Broker
**As the** system, **I want** `acquireLock(session_id, holder) -> lease` / `releaseLock(session_id, holder)` implementing a compare-and-swap lock with a TTL **so that** exactly one of {automation, human} controls a session at a time, and a crashed process can't orphan the lock forever (per §3.2).
- Given no one holds the lock, when automation calls `acquireLock`, then it succeeds and the session is marked `held_by: automation`.
- Given automation holds the lock and its lease expires without renewal (e.g., the worker crashed), when a reaper checks, then the lock is released and becomes acquirable again.
- Given a human has the lock, when automation attempts to act on that session, then the action is refused — enforced by the broker, not just by convention in the Orchestrator.

### ST-037: Escalation Service — intervention request with context
**Size:** M · **Depends on:** ST-020, ST-036, ST-014 · **Components:** Escalation Service
**As a** Human Operator, **I want** a `stuck` run to produce an Intervention Request carrying which capability/goal, the current step, a screenshot, and why it stopped **so that** I have enough context to act without re-deriving what happened.
- Given a run transitions to `stuck`, when the Escalation Service processes it, then an `InterventionRequest` is created with `{run_id, step_index, reason, context: {screenshot_ref, transcript_tail, current_url}}`.
- Given the intervention is created, when that happens, then the automation's session lock is held (via ST-036), not released, so the session state is exactly as the automation left it.

### ST-038: Operator Console (mocked) — claim and view
**Size:** M · **Depends on:** ST-037 · **Components:** Operator Console
**As a** Human Operator, **I want** to see open Intervention Requests, claim one, and view its context (screenshot, reason, current step) **so that** I can decide whether and how to intervene.
- Given open intervention requests exist, when I `GET /interventions?status=open`, then I see them listed with enough summary info to prioritize (tenant, capability, reason).
- Given I claim one, when I do, then its status becomes `claimed` and it's no longer available for another operator to claim simultaneously.

### ST-039: Live session control transfer
**Size:** L · **Depends on:** ST-038, ST-036 · **Components:** Operator Console, Session Broker
**As a** Human Operator, **I want** to take control of the *same* live session the automation was using — not a fresh one — perform manual steps, and have those actions recorded **so that** my intervention preserves the automation's progress instead of starting over (per §3.6, the mock-operator-UI-but-real-mechanism requirement).
- Given I claim an intervention, when I request control, then `acquireLock(session_id, human)` succeeds, I get a live remote-display handle to the exact session (CDP for the browser surface), and I can see the same page state the automation was on.
- Given I perform manual actions on that live session, when I do, then each action is captured (per §6.3's `human_actions_log`) — this is a real, minimal recording, not a stub.

### ST-040: Resume after handoff
**Size:** M · **Depends on:** ST-039 · **Components:** Escalation Service, Orchestrator, Session Broker
**As the** system, **I want** a resume signal, once the operator releases control, to hand the session back to automation and let the run continue from its last checkpoint (or be marked resolved if the human completed the goal) **so that** the pause/cede/resume seam described in §3.6 is real, not one-directional.
- Given the operator releases control, when that happens, then `releaseLock(session_id, human)` succeeds and the Escalation Service signals the Orchestrator to resume.
- Given the operator's manual actions already achieved the goal, when resuming, then the Orchestrator recognizes the checkpoint already holds and marks the run `completed` rather than re-attempting already-done steps.
- Given the operator's actions did not fully resolve the block, when resuming, then automation continues its observe → decide → act loop from the current (human-modified) state.

---

## Phase 9 — Sandbox isolation

### ST-041: Per-session ephemeral sandbox with network restriction
**Size:** L · **Depends on:** ST-006 · **Components:** Surface Adapter / sandbox orchestration
**As the** Platform, **I want** every session's Surface Adapter to run inside its own container (or microVM), provisioned per run and destroyed after, with egress restricted to the allowlisted target domain(s) **so that** no tenant's session data can leak into another's (per ADR-09).
- Given a session is opened, when the sandbox is provisioned, then its outbound network is restricted to only the target app's allowlisted domain(s) — a request to any other host from inside the sandbox fails.
- Given a session closes, when the sandbox tears down, then no session artifacts (cookies, cache, downloads) persist beyond that container's lifetime.

### ST-042: Resource ceilings and watchdog
**Size:** M · **Depends on:** ST-041 · **Components:** Surface Adapter / sandbox orchestration
**As the** Platform, **I want** hard CPU/memory/wall-clock ceilings per sandbox with a watchdog that kills a runaway session **so that** one misbehaving run can't degrade the host or other tenants' runs (per §3.3).
- Given a session exceeds its configured resource ceiling, when the watchdog detects it, then the session is force-terminated and the run is marked `failed` with a `resource_exceeded` reason, with evidence captured up to that point.
- Given normal sessions well within budget, when running, then the ceiling never triggers false positives under expected load.

---

## Phase 10 — Evidence assembly & write-up

### ST-043: `/evidence/` — real discovery run
**Size:** S · **Depends on:** Phase 4, Phase 5 · **Components:** all discovery-path components
**As the** submission, **I want** at least one genuine LLM-driven discovery run's logs, transcript, and resulting artifact captured under `/evidence/` **so that** the "discovery run has to be real" requirement is unambiguously demonstrated (per assignment §4).
- Given a completed discovery run, when evidence is assembled, then `/evidence/` contains the structured log, at least one screenshot, and the resulting draft artifact JSON.

### ST-044: `/evidence/` — replay run including an exceptional state
**Size:** S · **Depends on:** Phase 7 · **Components:** Replay Executor
**As the** submission, **I want** two replay evidence sets — one happy path, one that deliberately hits a business outcome or hard failure — under `/evidence/` **so that** the outcome taxonomy is demonstrated, not just described (per assignment §6.3).
- Given a successful replay and a deliberately-failing replay (bad input or simulated failure), when evidence is assembled, then both result payloads and logs are captured, clearly showing the difference between `success`, `business_outcome`, and `hard_failure`.

### ST-045: README.md and REPORT.md
**Size:** M · **Depends on:** all prior phases · **Components:** —
**As a** reviewer, **I want** `/README.md` (setup, run instructions, exact demo command sequence) and `/REPORT.md` (the seven required headings) **so that** the submission is assessable without reverse-engineering the code.
- Given a fresh clone, when I follow README.md, then I can run the exact demo path: start a discovery run on a goal, then replay the resulting artifact, using only documented commands and config.
- Given REPORT.md, when read, then it covers Architecture, Artifact schema, Determinism & error handling, Heterogeneity & multi-tenant, Escalation & handoff, Safety, and Cuts — matching the assignment's required structure exactly.

---

## Coverage check against the assignment's core requirements (§3)

| Requirement | Covered by |
|---|---|
| 3.1 Goal-driven agent loop | Phase 4 (ST-016 – ST-021) |
| 3.2 Structured artifact | Phase 5 (ST-022 – ST-026) |
| 3.3 Deterministic replay | Phase 6 – 7 (ST-027 – ST-035) |
| 3.4 Safety & policy guardrails | Phase 2 (ST-010 – ST-012), reinforced by Phase 9 (ST-041 – ST-042) |
| 3.5 Evidence / observability | Phase 3 (ST-013 – ST-015), Phase 10 (ST-043 – ST-044) |
| 3.6 Human-in-the-loop escalation & handoff | Phase 8 (ST-036 – ST-040) |
| 3.7 Design for heterogeneity & scale | ST-026 (base/override schema) + TDD §5 write-up — deliberately design-only, not built, per assignment scope note |
