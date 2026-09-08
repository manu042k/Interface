# Evidence

End-to-end demonstration of the vertical slice. All five bundles were produced by
the `cua` CLI against the local **MockBank** legacy app (see `backend/src/mockbank`).

The discovery run here used the **offline deterministic pilot** (`CUA_LLM_PROVIDERS=scripted`)
so the bundle is reproducible in CI without a key. A genuine LLM-driven run is
produced by the same command with `CUA_LLM_PROVIDERS=openrouter` (or `nvidia_nim`)
and a key in `backend/.env` — the pipeline, artifact schema, guardrails and
evidence format are identical; only the `decide()` calls differ. See
`backend/REPRODUCE.md` for the exact command.

| Bundle | What it shows |
|---|---|
| `01-discovery/` | A discovery run completing the goal *"look up member 12345 and read their current savings balance"*. `events.jsonl` = the structured per-step log (decision + model reasoning, guardrail verdict, action, checkpoint). `step*-screenshot-*.png` = per-step evidence. `summary.json` = the run + the **recorded draft `CapabilityArtifact`** (steps, ranked locator chains with robustness rationale, typed input/output schema, checkpoint, `known_outcomes`, `recoverable_rules`). |
| `02-replay-success/` | Deterministic replay of the approved artifact with `member_id=12345`. Outcome `recoverable_then_success`: the unexpected *Session Notice* interstitial fired mid-replay and was auto-dismissed by a `recoverable_rule` (`recovered_conditions: ["session_notice_interstitial"]`), then the typed output `savings_balance = {raw: "$4,182.55", amount: 4182.55}` was returned. |
| `03-replay-business-outcome/` | Same artifact, `member_id=00000`. Outcome `business_outcome`, `code: "member_not_found"` — a legitimate answer the caller branches on, **not** an error. `failure_detail` is null; nothing is logged as a failure. |
| `04-replay-permission-denied/` | `member_id=99999`. Outcome `business_outcome`, `code: "permission_denied"`. |
| `05-replay-hard-failure/` | The artifact invoked against a wrong entry point (`/member/34567`) so step 0 cannot resolve its control. Outcome `hard_failure` with `failure_detail: {step_index: 0, expected, observed}` and a screenshot + DOM snapshot captured at the failure point. |

## Reading `events.jsonl`

One JSON object per line, ordered, timestamped. Event kinds: `run_started`,
`decision` (with the model's `reasoning`), `guardrail` (verdict + reason),
`action` (result + matched locator strategy), `locator_resolution` (which ranked
strategy matched + `drift_signal`), `checkpoint`, `recoverable_condition`,
`business_outcome`, `hard_failure`, `run_finished`. All values pass through the
redaction layer on the way to disk — typed field values show as `«typed:len=N»`,
full account/card/SSN as `«REDACTED:…»`.
