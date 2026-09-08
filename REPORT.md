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
enforcement + artifact versioning) gets it for free in-process, and Discovery —
the only component with an LLM latency/cost profile — can later be pulled into
its own worker pool behind the same interfaces without a rewrite.

**Sync vs async.** Anything that touches the live surface or makes a safety
decision is synchronous and in-process — the guardrail check is a function call,
not a network hop that can be skipped under load. Run execution, notifications,
and evidence writes are async/fire-and-forget so a slow log never blocks an
action.

**Composition root** (`assembly.py`) wires everything from `Config`; swapping
SQLite→Postgres, `FileSink`→object storage, or Playwright→a desktop driver
happens there, not in callers. The `cua` CLI and the FastAPI gateway are two
façades over the same `System`.

**LLM provider router.** `discovery.decide()` goes through `LLMRouter`, an ordered
list of OpenAI-compatible providers (OpenRouter, NVIDIA NIM) with per-provider
health state. A 429 / quota / sustained-5xx is a *rotate, don't retry-in-place*
signal: the provider is marked `cooling_down` (TTL from `Retry-After`) and the
same request is retried against the next provider. All providers exhausted →
a distinct `AllProvidersExhausted` the orchestrator pauses on. Replay never
touches this.

## 2. Artifact schema

`CapabilityArtifact` (`models.py`, Pydantic — construction *is* validation):

- **`steps: [Step]`** — the executed, successful, *actionable* steps 1:1. `observe`,
  failed actions, guardrail-rejected actions, and interstitial-dismiss clicks
  never become steps.
- **`Step.locator_spec: [LocatorStrategy]`** — a **ranked** chain, not one selector
  (the legacy-markup reality has no test IDs). Rank order encodes robustness:
  `role_name` → `label` → `relative_to_landmark` (“the value cell in the *Savings*
  row”) → `text` → `dom_anchor`. Every strategy carries a `rationale` string — the
  "your reasoning about robustness" the brief asks for, in-tree.
- **`Step.idempotent: bool`** — *required*, forcing the decision at authoring time.
  Mutating clicks to `/create|submit|confirm/…` routes default to non-idempotent.
- **`ValueBinding`** — typed values are parameterised: a typed value equal to a
  supplied param becomes a `param` binding; a secret-looking literal is refused
  and forced to a param; URLs are canonicalised to `{param}` templates.
- **`input_schema` / `output_schema`** — JSON Schema (draft 2020-12). Params are
  validated *before any action touches the surface*; extracted outputs validated
  before return.
- **`checkpoint: Condition`** — the final success assertion, verified, never assumed.
- **`known_outcomes: [BusinessOutcomeRule]` and `recoverable_rules: [RecoverableRule]`**
  — the error taxonomy is *part of the contract*, not bolted onto the executor.
- **Versioned + reviewable**: `(artifact_id, version)` rows in SQLite, immutable
  except the `draft → approved|rejected` transition; replay always pins an
  explicit version, never "latest". The whole artifact is redacted before it is
  persisted.

## 3. Determinism & error handling

**Determinism.** Replay resolves each step's ranked locator chain against the
current `SurfaceState`, checks the guardrail, acts, then **verifies the step
checkpoint** before moving on — a click that didn't produce the expected state
halts rather than continuing blind. No LLM anywhere in the loop. Resolution
results are cached per `(artifact_version, surface_fingerprint, step_index)` with
singleflight coalescing. The `matched_rank` of the strategy that resolved is the
**drift signal**: a non-zero rank means the primary identifier degraded, logged
as `drift_signal: true` and queryable as a trend before it becomes a failure.
Every wait is bounded — no unbounded sleep anywhere.

**Exceptional states** (`replay/executor.py`). Before every step, and again
whenever a checkpoint fails, the screen is matched against the artifact's
declared rules:

| Class | Detection | Response |
|---|---|---|
| **Business outcome** | a `known_outcome.when` condition matches (e.g. "No members matched") | STOP, return `{outcome: business_outcome, code}` — a legitimate answer, **never** logged as an error, `failure_detail` null |
| **Recoverable** | a `recoverable_rule.when` matches (interstitial, transient slow load) | `dismiss` / `wait` / `reload`, log `recoverable_condition`, retry the step (bounded). Any recovery → final outcome `recoverable_then_success` |
| **Hard failure** | anything else that breaks a checkpoint or can't resolve | STOP, `{outcome: hard_failure, failure_detail: {step_index, expected, observed}}` + screenshot + DOM snapshot |

The result contract (`ReplayResult.outcome`) is an enum — a caller never
string-matches an error message to tell success, a known outcome, and a failure
apart. **Idempotency-aware retry (ST-035):** a non-idempotent step that fails
ambiguously is *not* resubmitted — the executor re-observes and re-checks the
checkpoint first; if it already holds, the step is treated as done.

**UI drift** (secondary, per the brief): the `SurfaceState.fingerprint` is a
structural signature (tag skeleton + form field names, no text values) — stable
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
are already ranked by *portability across surface kinds* — `role_name` works on a
desktop AX tree too; `dom_anchor` is the web-only last resort.

**Multi-tenant reuse.** An artifact is recorded once against a canonical **base**
instance (`vendor_app_id` + `app_version`). Per-tenant variance (branding, an
extra confirmation step, a renamed field) is a thin **override** keyed
`(vendor_app_id, tenant_id, overrides_base_version)` that only declares the steps
that differ — `store.resolve_for_tenant()` returns an approved override merged
onto the approved base, else the base. No per-tenant re-recording. Drift is
managed by the fingerprint + `drift_signal` trend per `(artifact, step)`: a
tenant whose resolutions start falling to lower-ranked strategies is flagged for
re-review before it breaks, without disturbing the other 199.

## 5. Escalation & handoff

**Detect.** Discovery emits `stuck(reason)` when it can't safely proceed
(missing control, unexpected screen, guardrail block it can't route around, or
`all_providers_exhausted`); replay raises on an unrecoverable hard failure. The
run transitions to `stuck` and its session is **held, not torn down**.

**Route.** `EscalationService.open_intervention()` acquires the automation lock
via the `SessionBroker`, captures a context bundle (screenshot, DOM, transcript
tail, current URL, capability/goal/step) into evidence, and creates an
`InterventionRequest`.

**Take control of the *same* session.** The `SessionBroker` control lock is a
compare-and-swap + **TTL lease** (not an orphanable mutex — a crashed worker's
lease simply lapses and is reaped). The operator claims the intervention, then
`take_control` releases automation's lease and grants the human's on the *same*
`session_id`, returning a live handle + a CDP-style remote-display ref. Every
operator action is executed through the adapter against that session and recorded
to `human_actions_log`.

**Hand back.** `release_control` → `resume`: releases the human lease,
re-acquires automation's, and evaluates the goal checkpoint — if it already
holds, the run is resolved as *goal satisfied*; otherwise automation continues
its loop from the human-modified state. With `CUA_USE_SANDBOX=1` the operator
takes over by clicking directly in the live **noVNC** canvas of the same
container; without it, the console's scripted action buttons drive the shared
session. Either way the **mechanism** (pause / cede / resume on one session,
lock ownership model, action recording) is real and covered by tests.

## 6. Safety

**Allowlist, default deny.** `PolicyEngine.check()` runs synchronously, in-process,
before *every* action (discovery and replay). Per-tenant policy independently
expresses permitted domains, route regexes, and action types. Unknown tenant,
unparseable target, a broken allowlist file, or an exception inside the check all
resolve to **BLOCK** — fail closed, always.

**Risk class.** Actions are `safe_reversible` by default; `risky_irreversible` if
the step declares it, the action type is on the tenant's risky list, or the
target route matches a risky pattern (`/sub-account/create$`). The risky class is
dispositioned by tenant policy — default `require_confirmation` rather than silent
execution. During discovery, an unconfirmed risky action is fed back to the model
("needs human confirmation — pick a safe alternative or call stuck"). During
replay, the **human review that promoted the artifact to `approved` is the
confirmation** for its risky steps; unattended replay of a non-approved artifact
is refused at the gateway.

**Data handling.** A pure `redact()` on every write path (artifact store, log
sink, evidence metadata): full account numbers (12–17 digits, epoch-timestamp
excluded), Luhn-valid card numbers, SSNs, bearer/JWT/API-key patterns, and
sensitive keys are replaced with a marker that says *what* was removed. Free-typed
values are masked to `«typed:len=N»` by construction, because a raw password has
no delimiter for a pattern to catch. Partial PII (last-4, first name) is
deliberately kept for debuggability — the exact line is flagged as a compliance
decision, not guessed.

**Limits.** Redaction is pattern + key based, so a novel secret format in free
text can slip through until a rule is added. Screenshots are not pixel-redacted —
evidence capture is gated to failure points, and screenshots of real account
screens would need field-level masking before production. The allowlist is
coarse (domain/route/action) — it does not understand business semantics
("transfer under $100 is fine").

## 7. Cuts

Deliberately thin-but-real, or stubbed at a clean seam:

- **Operator console** — now a Next.js 16 + shadcn app: goal input, a live
  **noVNC** view of the run, a streaming event timeline, an in-browser terminal
  into the run's sandbox, the stuck-run handoff, and a printable report. The
  co-browsing takeover is real (operator clicks land in the same browser).
- **Container sandbox** — now real: `CUA_USE_SANDBOX=1` gives every run its own
  Docker container (`Xvfb → xfce → headed Chromium/CDP → x11vnc → websockify`),
  the worker attaches over CDP and drives the *same* browser the operator
  watches, and the container is held on `STUCK` for takeover. Still design-only:
  a microVM (Firecracker/gVisor) boundary, kernel CPU/memory ceilings, a warm
  pool for cold-start, and orchestration beyond a single Docker host (k8s).
- **Postgres** — SQLite behind the same store interface; the access pattern
  (transactional versioning, base/override lookup) is Postgres-shaped.
- **Desktop / legacy-web adapters** — one `SurfaceAdapter` seam, Playwright
  implementation. The DOM-outline perception path already degrades gracefully
  for no-accessibility-info markup.
- **Discovery model quality** — the offline `scripted` pilot recognises a
  handful of MockBank screens so CI and the no-key demo run the whole pipeline;
  a real run uses OpenRouter/NIM via the router with identical downstream
  behaviour.
- **Artifact governance** — a single `draft → approved` gate with a reviewer
  name; no multi-reviewer workflow, RBAC, or re-approval-on-drift policy.

**What I'd build next:** re-approval triggered automatically by a `drift_signal`
trend; the bounded single-step assisted-fallback on replay hard failure
(policy-checked, logged as distinct evidence, never chained); a real desktop
adapter to prove the surface seam; canonicalisation of routes/values into
parameterised patterns across two MockBank "tenant" variants.
