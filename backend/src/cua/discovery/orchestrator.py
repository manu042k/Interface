"""ST-019/020: the discovery Orchestrator.

State machine for one discovery run: observe -> decide -> guardrail-check -> act,
until done / stuck / max-steps / timeout. Guardrail rejections are fed back to
the agent as an observation (not silently dropped). `stuck` and
`all_providers_exhausted` both route to a pause-and-hold path — the session is
kept open for Phase 8 escalation, never torn down.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Config
from ..events import RunLogger
from ..llm.router import AllProvidersExhausted, AllProvidersOutOfBalance, LLMRouter
from ..models import (
    ActionType,
    RiskClass,
    RunMode,
    RunRecord,
    RunStatus,
    SurfaceState,
    ToolCall,
)
from ..policy.engine import ActionContext, PolicyEngine, PolicyVerdict
from ..surface.base import Action, SurfaceAdapter
from ..surface.perception import Perception
from .agent import DiscoveryAgent

_LOGIN_RE = re.compile(r"\b(log\s?in|log\s?on|sign\s?on|sign\s?in|authenticat)", re.I)
_DOWNSTREAM_RE = re.compile(
    r"\b(read|extract|check|view|get|fetch|look ?up|lookup|look ?at|find|search|"
    r"open|create|add|transfer|update|change|edit|place|submit|post|deposit|"
    r"withdraw|balance|amount|then|after (that|logging|signing)|navigate|go to|"
    r"pull ?up|pull|bring ?up|show|display|review|verify|confirm|inquir|"
    r"record|account|member|detail|history|status)\b",
    re.I,
)


_QUOTED_RE = re.compile(r"[\"'“‘]([^\"'”’]{3,80})[\"'”’]")
_CAPS_RUN_RE = re.compile(r"\b([A-Z][A-Z0-9 ]{4,40}[A-Z0-9])\b")


_CONFIRM_MARKERS = (
    "changes saved", "has been updated", "has been saved", "has been recorded",
    "has been applied", "has been placed", "has been posted", "has been created",
    "successfully updated", "successfully saved", "successfully created",
    "information updated", "record updated", "update saved",
    "transfer posted", "transaction posted", "posted successfully", "payment posted",
    "share opened", "account opened", "account created",
    "hold placed", "hold recorded", "hold applied", "hold removed",
    "confirmation number", "reference number", "confirmation:",
    "was successful", "completed successfully", "operation complete",
)
# past-tense outcome words that make an ALL-CAPS heading a confirmation
_DONE_WORDS = (
    "updated", "saved", "posted", "opened", "created", "complete", "completed",
    "confirm", "confirmed", "applied", "recorded", "placed", "removed", "accepted",
    "processed", "submitted", "approved", "rejected", "cancelled", "canceled", "denied",
)
_HEADING_RE = re.compile(r"<h[1-3]\b[^>]*>\s*\n?\s*([^\n<][^\n<]{2,60})", re.I)


def _salvage_from_state(state: SurfaceState | None) -> dict[str, Any] | None:
    """The model gave assert_state nothing usable and its args carry no phrase —
    but it just observed a screen. If that screen shows a recognised confirmation
    marker (or an outcome-y heading), assert THAT, exact-cased as it appears."""
    if state is None:
        return None
    hay = f"{state.title or ''}\n{state.dom_excerpt or ''}"
    low = hay.lower()
    hits: list[str] = []
    for m in _CONFIRM_MARKERS:
        i = low.find(m)
        if i != -1:
            hits.append(hay[i:i + len(m)])  # preserve original casing
    # a page heading that reads like a result ("ACCOUNT HOLD APPLIED")
    for h in _HEADING_RE.findall(hay):
        h = h.strip()
        if any(w in h.lower().split() for w in _DONE_WORDS):
            hits.append(h)
    # a prominent ALL-CAPS result banner, heading or not
    for cap in _CAPS_RUN_RE.findall(hay):
        c = cap.strip()
        if any(w in c.lower().split() for w in _DONE_WORDS):
            hits.append(c)
    seen = list(dict.fromkeys(hits))
    return {"kind": "text_present", "params": {"any": seen[:4]}} if seen else None


_SUBMIT_VAL_RE = re.compile(r'<input\b[^>]*\btype="(?:submit|button)"[^>]*\bvalue="([^"]+)"', re.I)
_SUBMIT_VAL_RE2 = re.compile(r'<input\b[^>]*\bvalue="([^"]+)"[^>]*\btype="(?:submit|button)"', re.I)
_TAG_THEN_TEXT_RE = re.compile(r"<(?:button|a)\b[^>]*>\s*\n\s*([^\n<][^\n]{0,40})", re.I)


def _visible_controls(state: SurfaceState | None) -> str:
    """The literal labels of the buttons / links on the screen right now, for
    feeding back when the model names a control that isn't there."""
    if state is None:
        return ""
    src = state.dom_excerpt or ""
    labels: list[str] = []
    for rx in (_SUBMIT_VAL_RE, _SUBMIT_VAL_RE2, _TAG_THEN_TEXT_RE):
        for m in rx.finditer(src):
            t = m.group(1).strip()
            if 1 < len(t) < 40 and t not in labels:
                labels.append(t)
    return ", ".join(f"[{x}]" for x in labels[:12])


def _condition_usable(cond: dict[str, Any]) -> bool:
    """A condition the evaluator can actually act on — `kind` set AND the
    params it needs are present (not `text_present` with empty params)."""
    kind = cond.get("kind")
    p = cond.get("params") or {}
    if kind in ("text_present", "text_absent"):
        return bool(p.get("any") or p.get("text"))
    if kind == "url_matches":
        return bool(p.get("pattern"))
    if kind in ("element_present", "element_absent"):
        return bool(p.get("target"))
    if kind in ("all_of", "any_of"):
        return bool(p.get("conditions"))
    return bool(kind)


def _salvage_condition(args: dict[str, Any], reasoning: str = "") -> dict[str, Any]:
    """Models (esp. Gemini) sometimes send `assert_state`/`wait_for` with
    `condition: {}` and put the phrase they meant to check in the reasoning
    text (which the provider layer has already split off into `reasoning`).
    Rebuild a usable `text_present` / `url_matches` condition from whatever they
    gave us; return the original if it's already well-formed, or `{}` if nothing
    is salvageable (the caller then fails once with a sharp schema hint)."""
    cond = args.get("condition")
    if isinstance(cond, dict) and _condition_usable(cond):
        return cond
    # a partly-formed condition (kind set, params empty) — keep its kind, try to
    # fill the missing bit from the reasoning below
    keep_kind = cond.get("kind") if isinstance(cond, dict) else None

    # 1) a phrase the model quoted — in reasoning (its stated intent) or
    #    anywhere in the args (handles a phrase parked in a stray key)
    phrases: list[str] = []
    for v in (reasoning, *(x for x in args.values() if isinstance(x, str))):
        if isinstance(v, str):
            phrases += _QUOTED_RE.findall(v)
            phrases += [m.strip() for m in _CAPS_RUN_RE.findall(v)]
    # 2) a phrase sitting at the top level under a plausible key
    for k in ("text", "expect", "expected", "value", "phrase", "contains"):
        if isinstance(args.get(k), str) and args[k].strip():
            phrases.append(args[k].strip())
    seen: list[str] = []
    for p in phrases:
        if p and p not in seen:
            seen.append(p)
    if seen:
        kind = keep_kind if keep_kind in ("text_present", "text_absent") else "text_present"
        return {"kind": kind, "params": {"any": seen[:4]}}

    # 3) a url/pattern hint
    for k in ("pattern", "url", "url_matches"):
        if isinstance(args.get(k), str) and args[k].strip():
            return {"kind": "url_matches", "params": {"pattern": args[k].strip()}}
    return {}


def _derive_login_success_check(goal: str, target: str) -> dict | None:
    """A goal that is ONLY "log in" has no explicit finish line, so the model can
    sign on successfully and then thrash (re-click submit, sign off, retry...).
    Derive one: done once you've LEFT the auth path with no rejection text. But
    NOT when login is just the first step of a larger task ("log in AND read the
    balance", "sign on and pull up member 100234") — that would end the run
    early. A 3+ digit number in the goal is treated as a downstream target id."""
    from urllib.parse import urlparse

    if not _LOGIN_RE.search(goal):
        return None
    if _DOWNSTREAM_RE.search(goal) or re.search(r"\d{3,}", goal):
        return None
    auth_seg = (urlparse(target).path.rsplit("/", 1)[-1] or "signon").lower()
    return {
        "kind": "all_of",
        "params": {"conditions": [
            {"kind": "url_matches", "params": {
                "pattern": rf"^(?!.*/(?:{re.escape(auth_seg)}|signon|login|sign-?in|auth)\b).+"
            }},
            {"kind": "text_absent", "params": {"any": [
                "invalid operator", "invalid credentials", "incorrect password",
                "login failed", "sign-on failed", "not authorized", "try again",
                "access denied",
            ]}},
        ]},
    }


_TOOL_TO_ACTION = {
    "click": ActionType.CLICK,
    "type": ActionType.TYPE,
    "select": ActionType.SELECT,
    "navigate": ActionType.NAVIGATE,
    "wait_for": ActionType.WAIT_FOR,
    "extract": ActionType.EXTRACT,
    "assert_state": ActionType.ASSERT_STATE,
    "scroll": ActionType.SCROLL,
    "press_key": ActionType.PRESS_KEY,
}


@dataclass
class TranscriptEntry:
    step: int
    state_before: SurfaceState
    tool_call: ToolCall
    action: Action | None
    action_ok: bool
    action_result: dict[str, Any]
    guardrail_verdict: str
    extract_as: str | None = None


@dataclass
class DiscoveryTranscript:
    run_id: str
    goal: str
    target: str
    tenant: str
    params: dict[str, Any] = field(default_factory=dict)
    entries: list[TranscriptEntry] = field(default_factory=list)
    done_outputs: dict[str, Any] = field(default_factory=dict)
    final_state: SurfaceState | None = None
    stuck_reason: str | None = None
    # set when the run ended on a recognised business-outcome screen:
    # (code, message, the exact phrases that matched)
    business_outcome: tuple[str, str, list[str]] | None = None
    session_id: str | None = None
    intervention_id: str | None = None


class Orchestrator:
    def __init__(
        self,
        *,
        config: Config,
        adapter: SurfaceAdapter,
        perception: Perception,
        agent: DiscoveryAgent,
        policy: PolicyEngine,
        router: LLMRouter,
        logger_factory: Any,
        escalation: Any | None = None,
        broker: Any | None = None,
        sandbox_manager: Any | None = None,
    ) -> None:
        self.cfg = config
        self.adapter = adapter
        self.perception = perception
        self.agent = agent
        self.policy = policy
        self.router = router
        self.sandbox_manager = sandbox_manager
        self._logger_factory = logger_factory  # (run_id) -> RunLogger
        self.escalation = escalation
        self.broker = broker

    async def run_discovery(
        self,
        *,
        goal: str,
        target: str,
        tenant: str = "default",
        params: dict[str, Any] | None = None,
        run: RunRecord | None = None,
        confirm_risky: bool = False,
        exhausted_ceiling: int = 20,
        max_steps: int | None = None,
        success_check: dict[str, Any] | None = None,
        # >0 makes a stuck discovery BLOCK for a human hand-back and then resume.
        # 0 (default, used by tests) opens the intervention and returns STUCK.
        handoff_wait_s: float = 0.0,
    ) -> tuple[RunRecord, DiscoveryTranscript]:
        params = params or {}
        step_budget = max_steps if max_steps is not None else self.cfg.max_steps
        run = run or RunRecord(mode=RunMode.DISCOVERY, tenant_id=tenant, app_target=target, goal=goal)
        run.status = RunStatus.RUNNING
        log: RunLogger = self._logger_factory(run.run_id)
        log.run_started("discovery", target, goal)

        transcript = DiscoveryTranscript(run_id=run.run_id, goal=goal, target=target, tenant=tenant, params=params)
        self._run_target = target  # for _discovery_business_outcome's per-host library lookup

        if success_check is None:
            success_check = _derive_login_success_check(goal, target)
            if success_check is not None:
                log.event(0, "derived_success_check",
                          detail="login goal — done once off the auth page with no error")

        deadline = time.time() + self.cfg.run_timeout_seconds
        history: list[str] = []
        note: str | None = None
        last_call: ToolCall | None = None  # for "what the agent was attempting" on escalation
        last_ok_tool: str | None = None  # last action that actually ran, for the done gate
        last_ok_step = -99  # step index of that last successful action
        done_nudged = 0  # rejected `done` calls (no successful verify before them)
        stuck_nudged = 0  # `stuck` calls pushed back because progress was just made
        policy_blocks = 0  # consecutive guardrail rejections the model can't fix by retrying
        used_params: set[str] = set()  # params whose value was actually typed/selected
        unused_param_nudged = 0

        from ..sandbox.session import close_run_surface, open_run_surface

        surface = await open_run_surface(
            adapter=self.adapter, sandbox_manager=self.sandbox_manager,
            target=target, tenant=tenant, run=run, logger=log,
        )
        session = surface.session_handle
        # no-progress loop guard: (tool, target) signature + a hash of the screen
        last_sig: str | None = None
        repeats = 0
        # cyclic-thrash guard: how many times each (tool|target) ran this run,
        # even when broken up by other actions (type A, type B, type A, ...).
        sig_counts: dict[str, int] = {}
        cycle_nudged = False
        # click-stall guard: consecutive clicks on one URL that never navigate or
        # change anything — the model "retries the same button with a different
        # identifier" over and over (each a new target, so the (tool|target)
        # counter above never trips). Reset by any navigation or a real edit.
        click_stall = 0
        click_stall_url: str | None = None
        resolve_fails = 0  # consecutive "could not resolve target" errors
        fail_streak = 0  # consecutive failed actions of the SAME tool (any error)
        fail_streak_tool: str | None = None
        committed: list[dict[str, str]] = []  # banking mutations that succeeded — never reverse/re-submit
        try:
            step = 0
            while True:
                run.step_count = step  # keep the live view current
                if step >= step_budget:
                    run.status = RunStatus.DEAD_END
                    run.detail = f"max steps ({step_budget}) reached without done/stuck"
                    log.event(step, "dead_end", reason=run.detail)
                    break
                if time.time() > deadline:
                    run.status = RunStatus.DEAD_END
                    run.detail = "run timeout"
                    log.event(step, "dead_end", reason=run.detail)
                    break

                state = await self.perception.observe(
                    self.adapter, session, sink=log.sink, run_id=run.run_id, step=step
                )

                try:
                    call = await self._decide_with_backoff(
                        goal, state, history, step, step_budget, params, note, exhausted_ceiling, log
                    )
                except AllProvidersOutOfBalance as exc:
                    run.status = RunStatus.FAILED
                    run.detail = "llm providers out of credits — run stopped"
                    transcript.stuck_reason = run.detail
                    log.event(step, "run_stopped", reason="providers_out_of_balance", detail=str(exc))
                    log.run_finished("failed", detail=run.detail)
                    break
                note = None
                if call is None:
                    run.status = RunStatus.STUCK
                    run.detail = "all_providers_exhausted"
                    transcript.stuck_reason = run.detail
                    log.stuck(step, run.detail)
                    break

                log.decision(step, call.tool, call.args, call.reasoning)
                if call.tool not in ("observe", "stuck", "done"):
                    last_call = call

                if call.usage:
                    run.llm_calls += 1
                    run.tokens_in += int(call.usage.get("prompt_tokens", 0) or 0)
                    run.tokens_out += int(call.usage.get("completion_tokens", 0) or 0)
                    log.event(
                        step, "tokens", calls=run.llm_calls,
                        tokens_in=run.tokens_in, tokens_out=run.tokens_out,
                    )

                if call.tool == "observe":
                    history.append("observe -> re-read screen")
                    continue

                if call.tool == "done":
                    # A `done` must be earned by a preceding verification that
                    # SUCCEEDED - the assert_state/extract that ran just before is
                    # what the recorder keeps as the replay checkpoint. This is
                    # checked EVERY time, not once: a model that fabricates an
                    # answer, gets nudged, fails an assert, then re-issues `done`
                    # must not slip a bogus capability through.
                    if last_ok_tool not in ("assert_state", "extract"):
                        done_nudged += 1
                        history.append("done -> REJECTED: no successful verify precedes it")
                        if done_nudged >= 3:
                            reason = (
                                "the agent keeps calling done without a verification that "
                                "passes - the success condition cannot be confirmed on screen"
                            )
                            resumed_note = await self._escalate_and_wait(
                                run, transcript, session, step, reason, goal, history, log,
                                handoff_wait_s, last_call,
                            )
                            if resumed_note is None:
                                break
                            note, last_sig, repeats, done_nudged = resumed_note, None, 0, 0
                            deadline = time.time() + self.cfg.run_timeout_seconds
                            step += 1
                            continue
                        note = (
                            "Not done. Before done you MUST call assert_state with the goal's "
                            "success condition (text_present of the exact confirmation wording on "
                            "the CURRENT screen) and it must return ok. If that assert_state "
                            "FAILS, the goal is not achieved - do not call done, re-observe or "
                            "call stuck. Never report a value you cannot see on screen."
                        )
                        step += 1
                        continue
                    # A supplied input param that was NEVER typed/selected means
                    # the agent skipped part of the task. Push back ONCE (the
                    # other loop guards — no-progress, stuck-nudge, assert-twice-
                    # completes — keep this from wedging), then let `done` through.
                    _skip = {"branch", "tenant", "operator", "password"}
                    form_params = {
                        k for k, v in params.items()
                        if k not in _skip and len(str(v)) >= 4
                    }
                    missed = sorted(form_params - used_params)
                    if missed and unused_param_nudged < 1:
                        unused_param_nudged += 1
                        history.append(f"done -> REJECTED: params never entered: {missed}")
                        note = (
                            "You have not entered these supplied values into a field yet: "
                            f"{', '.join(missed)}. Each one belongs in a field the goal named. "
                            "Go back to the form, type/select each, submit, then done."
                        )
                        step += 1
                        continue
                    transcript.done_outputs = dict(call.args.get("outputs", {}))
                    transcript.final_state = state
                    run.status = RunStatus.COMPLETED
                    run.detail = "goal achieved"
                    log.checkpoint(step, True, "agent declared done")
                    log.run_finished("completed", outputs=list(transcript.done_outputs))
                    break

                if call.tool == "stuck":
                    reason = _stuck_reason(call)
                    # Don't escalate a run that is actually making progress: the
                    # model sometimes calls stuck right after a string of
                    # SUCCESSFUL actions (confused by an earlier failure it has
                    # since worked around). Push back once, telling it to
                    # re-verify — but only twice, then honour the stuck call.
                    if step - last_ok_step <= 2 and stuck_nudged < 2:
                        stuck_nudged += 1
                        note = (
                            f"You called stuck, but your last action ({last_ok_tool}) SUCCEEDED "
                            f"and you are on {state.url}. Re-observe the current screen. If the "
                            "goal is already achieved, call assert_state on its success condition "
                            "and then done. Only call stuck again if you genuinely cannot proceed."
                        )
                        log.event(step, "stuck_nudged", reason=reason, on_url=state.url)
                        step += 1
                        continue
                    resumed_note = await self._escalate_and_wait(
                        run, transcript, session, step, reason, goal, history, log,
                        handoff_wait_s, last_call,
                    )
                    if resumed_note is None:
                        break  # no operator came back - end as STUCK
                    note, last_sig, repeats = resumed_note, None, 0
                    deadline = time.time() + self.cfg.run_timeout_seconds  # fresh budget
                    continue

                # Guard a login task from undoing itself: a click aimed at a
                # sign-off / logout / cancel control means the model thinks it
                # still needs to log in when it probably already has.
                if (
                    call.tool == "click"
                    and success_check is not None
                    and re.search(r"\b(log\s?in|log\s?on|sign\s?on|sign\s?in)", goal, re.I)
                ):
                    tgt = call.args.get("target") or {}
                    tval = " ".join(str(v) for v in (tgt.values() if isinstance(tgt, dict) else [tgt])).lower()
                    if any(w in tval for w in ("sign off", "signoff", "log out", "logout", "log off", "cancel")):
                        note = (
                            f"That control ({tval.strip()}) would SIGN YOU OUT. The goal is to "
                            f"log IN, and you are on {state.url} — you have very likely already "
                            "signed on. Call assert_state on the success condition (you are off "
                            "the sign-on page, no error text) and then done. Do NOT click sign-off."
                        )
                        log.event(step, "counterproductive_click_blocked", target=tval.strip())
                        step += 1
                        continue

                # actionable tool -> build Action, guardrail, execute
                action, extract_as = self._to_action(call, params)
                # last-ditch: the model gave assert_state/wait_for a condition it
                # cannot act on (kind but empty params, or {}) AND nothing in the
                # args to rebuild from — lift a confirmation phrase straight off
                # the screen it just observed, so a "done" edit isn't wedged by
                # a formatting slip.
                if action.type in (ActionType.ASSERT_STATE, ActionType.WAIT_FOR) and not _condition_usable(
                    action.condition if isinstance(action.condition, dict) else {}
                ):
                    # what the model *guessed* it wanted (from its reasoning)
                    guessed = action.condition.get("params", {}).get("any", []) \
                        if isinstance(action.condition, dict) else []
                    # what the screen it just observed actually shows
                    from_screen = _salvage_from_state(state)
                    scr = from_screen["params"]["any"] if from_screen else []
                    # union, screen phrases first — a guessed phrase that isn't
                    # literally on the page must not be the sole assertion (it
                    # breaks replay). text_present/any passes if ANY match.
                    merged = list(dict.fromkeys([*scr, *guessed]))
                    if merged:
                        action.condition = {"kind": "text_present", "params": {"any": merged[:6]}}
                        log.event(step, "condition_salvaged",
                                  **{"from": "screen+reasoning" if scr and guessed else ("screen" if scr else "reasoning")},
                                  condition=action.condition)
                    elif state.url:
                        # no confirmation phrase anywhere (a "navigate to a page"
                        # goal has no SAVED/POSTED banner) — assert we are on the
                        # page the model reached. digits -> \d+ so it replays for
                        # other ids. Always valid, always replayable.
                        pat = re.sub(r"\d+", r"\\d+", re.escape(state.url.split("?")[0]))
                        action.condition = {"kind": "url_matches", "params": {"pattern": pat}}
                        log.event(step, "condition_salvaged", **{"from": "url"}, condition=action.condition)
                # Whatever condition we actually acted on (repaired or not) is
                # what must be RECORDED — otherwise the artifact keeps the
                # model's broken `params:{}` and every replay hard-fails on it.
                if action.type in (ActionType.ASSERT_STATE, ActionType.WAIT_FOR) and isinstance(action.condition, dict):
                    call.args["condition"] = action.condition
                target_url = call.args.get("url") if call.tool == "navigate" else state.url

                # Post-commit lock: once a banking transaction has succeeded this
                # run, the agent must NOT try to reverse / void / refund / undo it
                # or re-submit it. A committed transaction is permanent — only a
                # human decides on any correction.
                if committed:
                    intent = _call_intent_text(call)
                    if call.tool in ("click", "press_key", "navigate") and _REVERSAL_RE.search(intent):
                        log.event(step, "reversal_blocked", attempted=intent,
                                  committed=committed[-1], detail="a committed transaction must not be reversed by automation")
                        reason = (
                            f"A transaction was already committed this run "
                            f"({committed[-1]['what']}). The agent then tried to reverse/undo it "
                            f"('{intent}'). Automation must NEVER roll back a committed banking "
                            f"transaction — a human must decide on any correction. Do not resume "
                            f"the agent into a reversal."
                        )
                        transcript.entries.append(
                            TranscriptEntry(step, state, call, action, False, {"reversal_blocked": reason}, PolicyVerdict.BLOCK)
                        )
                        resumed_note = await self._escalate_and_wait(
                            run, transcript, session, step, reason, goal, history, log,
                            handoff_wait_s, last_call,
                        )
                        if resumed_note is None:
                            break
                        note, last_sig, repeats, committed = resumed_note, None, 0, []
                        deadline = time.time() + self.cfg.run_timeout_seconds
                        step += 1
                        continue
                    sig = _commit_sig(call)
                    if any(c["sig"] == sig for c in committed):
                        note = (
                            "You already completed that transaction this run — the page confirmed "
                            "it. Do NOT submit it again. Re-read the current screen to verify the "
                            "result, then finish, or call stuck if something is wrong."
                        )
                        log.event(step, "resubmit_blocked", sig=sig)
                        step += 1
                        continue

                decision = self.policy.check(
                    ActionContext(
                        tenant_id=tenant,
                        action_type=_TOOL_TO_ACTION[call.tool],
                        target_url=target_url,
                        declared_risk=None,
                        extra={
                            "target": _call_intent_text(call),
                            "value": call.args.get("value") or call.args.get("option") or "",
                        },
                    )
                )
                log.guardrail(step, decision.verdict, decision.reason)

                if decision.verdict == PolicyVerdict.BLOCK:
                    policy_blocks += 1
                    history.append(f"{call.tool} blocked by guardrail: {decision.reason}")
                    transcript.entries.append(
                        TranscriptEntry(step, state, call, action, False, {"blocked": decision.reason}, decision.verdict)
                    )
                    # Not something the model can fix by retrying — the route/action
                    # is off-policy. Nudge once, then escalate rather than burn steps.
                    if policy_blocks >= 2:
                        reason = f"guardrail keeps rejecting this action: {decision.reason}"
                        resumed_note = await self._escalate_and_wait(
                            run, transcript, session, step, reason, goal, history, log,
                            handoff_wait_s, last_call,
                        )
                        if resumed_note is None:
                            break
                        note, last_sig, repeats, policy_blocks = resumed_note, None, 0, 0
                        deadline = time.time() + self.cfg.run_timeout_seconds
                        step += 1
                        continue
                    note = (
                        f"That action was rejected by policy ({decision.reason}). Retrying "
                        f"the same action will NOT work. Either do something genuinely "
                        f"different that the goal allows, or call stuck now to bring in a human."
                    )
                    step += 1
                    continue

                if decision.verdict == PolicyVerdict.REQUIRE_CONFIRMATION:
                    if confirm_risky:
                        log.event(step, "risk_preauthorized", reason=decision.reason)
                    elif self.escalation is None:
                        # no approver wired (offline / tests) — refuse and nudge.
                        note = (
                            f"That action needs human approval ({decision.reason}) and no "
                            f"approver is available here. Do what the goal allows without it, "
                            f"or call stuck."
                        )
                        history.append(f"{call.tool} needs approval: {decision.reason}")
                        transcript.entries.append(TranscriptEntry(
                            step, state, call, action, False,
                            {"require_confirmation": decision.reason}, decision.verdict,
                        ))
                        step += 1
                        continue
                    else:
                        # Risk-approval gate: pause and ask a human to APPROVE or
                        # REJECT this exact action before it runs. No takeover.
                        phrase = _risk_action_phrase(call, goal)
                        granted = await self._await_risk_approval(
                            run, transcript, session, step, phrase, decision.reason,
                            goal, history, log, handoff_wait_s,
                        )
                        if granted is None:
                            run.status = RunStatus.DEAD_END
                            run.detail = f"risk approval not granted at step {step}: {phrase}"
                            log.run_finished("dead_end", reason=run.detail)
                            break
                        if granted is False:
                            note = (
                                f"A human REJECTED this action: {phrase}. Do NOT attempt it "
                                f"again. Either do something else the goal allows, or call stuck."
                            )
                            history.append(f"{call.tool} rejected by approver")
                            transcript.entries.append(TranscriptEntry(
                                step, state, call, action, False, {"rejected": phrase}, decision.verdict,
                            ))
                            step += 1
                            continue
                        log.event(step, "risk_approved", proposed_action=phrase)
                        deadline = time.time() + self.cfg.run_timeout_seconds  # fresh budget

                result = await self.adapter.execute(session, action)
                ok = result.ok
                log.action(
                    step, action.type, result.target_description, ok,
                    matched_strategy=result.matched_strategy, url_after=result.url_after,
                    error=result.error, timed_out=result.timed_out,
                )
                risk = RiskClass.RISKY_IRREVERSIBLE if decision.verdict == PolicyVerdict.REQUIRE_CONFIRMATION else RiskClass.SAFE_REVERSIBLE
                entry = TranscriptEntry(
                    step, state, call, action, ok,
                    {
                        "ok": ok, "error": result.error, "timed_out": result.timed_out,
                        "matched_strategy": result.matched_strategy, "url_after": result.url_after,
                        "extracted": result.extracted, "risk_class": str(risk),
                    },
                    decision.verdict, extract_as,
                )
                transcript.entries.append(entry)

                # A risky/irreversible action that SUCCEEDED is a committed
                # banking mutation. Record it so the post-commit lock can refuse
                # any later reversal or re-submit this run.
                if ok and risk == RiskClass.RISKY_IRREVERSIBLE and call.tool in ("click", "press_key", "navigate"):
                    committed.append({
                        "sig": _commit_sig(call),
                        "what": _call_intent_text(call) or _describe_call(call),
                        "url": str(result.url_after or ""),
                        "step": str(step),
                    })
                    log.event(step, "transaction_committed",
                              what=committed[-1]["what"], url=committed[-1]["url"],
                              detail="irreversible — reversal/re-submit is now locked out for this run")

                desc = _describe_call(call)

                # Landed on a recognised business-outcome screen (no such
                # member, permission wall)? End with it now — don't let the
                # model thrash on a page whose "answer" is already final. Only
                # after a navigation, so it's one extra observe at most per hop.
                if (
                    ok
                    and call.tool in ("click", "navigate", "press_key")
                    and str(result.url_after or "") != state.url
                ):
                    bo = await self._discovery_business_outcome(session, log=log, step=step)
                    if bo is not None:
                        code, msg, phrases = bo
                        run.status = RunStatus.BUSINESS_OUTCOME
                        run.detail = f"{code}: {msg}"
                        transcript.final_state = state
                        transcript.business_outcome = (code, msg, phrases)
                        log.event(step, "business_outcome", code=code, message=msg, matched=phrases)
                        log.run_finished("business_outcome", code=code)
                        break

                if ok:
                    last_ok_tool = call.tool
                    last_ok_step = step
                    policy_blocks = 0  # progress - forget earlier guardrail rejections
                    resolve_fails = 0
                    fail_streak, fail_streak_tool = 0, None
                    if call.tool in ("type", "select"):
                        entered = str(call.args.get("value") or call.args.get("option") or "")
                        for pk, pv in params.items():
                            if pv and str(pv) in entered:
                                used_params.add(pk)
                if not ok:
                    history.append(f"{desc} -> FAILED: {result.error}")
                    note = f"The last action failed: {result.error}. Re-observe and adapt, or call stuck."

                    # Same tool failing over and over (a malformed assert_state
                    # condition, a wait_for that never settles) sails past the
                    # no-progress guard, which only counts *successful* repeats.
                    if call.tool == fail_streak_tool:
                        fail_streak += 1
                    else:
                        fail_streak, fail_streak_tool = 1, call.tool
                    if fail_streak >= 4:
                        reason = (
                            f"repeated {call.tool} failed {fail_streak}x in a row "
                            f"(last error: {result.error})"
                        )
                        resumed_note = await self._escalate_and_wait(
                            run, transcript, session, step, reason, goal, history, log,
                            handoff_wait_s, last_call,
                        )
                        if resumed_note is None:
                            break
                        note, last_sig, repeats = resumed_note, None, 0
                        fail_streak, fail_streak_tool = 0, None
                        deadline = time.time() + self.cfg.run_timeout_seconds
                        continue
                    if call.tool in ("assert_state", "wait_for") and not (
                        isinstance(call.args.get("condition"), dict)
                        and _condition_usable(call.args["condition"])
                    ):
                        # the failure is a bad ARGUMENT, not a bad screen — say so
                        # on the FIRST miss, don't wait for a streak
                        note = (
                            f"Your {call.tool} `condition` was empty or malformed — that is why it "
                            'failed, not the page. Send a real object: '
                            '{"kind": "text_present", "params": {"any": ["<a phrase visible on the '
                            'screen right now>"]}} or {"kind": "url_matches", "params": {"pattern": '
                            '"/member/\\\\d+"}}. The phrase goes in params, not in your reasoning. '
                            "If you cannot find a phrase, call done with your outputs (the save "
                            "already went through) or call stuck."
                        )

                    if "could not resolve target" in (result.error or ""):
                        resolve_fails += 1
                        ctrls = _visible_controls(state)
                        ctrl_hint = (
                            f" The clickable controls actually on this screen are: {ctrls}. "
                            "Use one of those EXACT labels."
                            if ctrls else ""
                        )
                        if resolve_fails >= 2:
                            # Retrying the same control with new identifiers is
                            # the classic thrash — the control is very likely
                            # NOT on this page (the flow already moved past it,
                            # or the model has the wrong page in mind).
                            note = (
                                f"The last action failed: {result.error}. You have now failed "
                                f"to find this control {resolve_fails} times — it is most likely "
                                "NOT on this page. STOP trying new identifiers for it and STOP "
                                f"scrolling.{ctrl_hint} Act on one of THOSE toward the goal, or "
                                "call stuck. Do not name a control the observation does not show."
                            )
                        else:
                            note = (
                                f"The last action failed: {result.error}. The control could NOT be "
                                f"located.{ctrl_hint} Do NOT scroll. If none of those fit, try the "
                                "field's form name / placeholder, else re-read the screen and act "
                                "on what IS shown, or call stuck."
                            )
                    else:
                        resolve_fails = 0
                elif call.tool == "extract" and result.extracted is not None:
                    val = str(result.extracted)
                    history.append(f"{desc} -> got {val[:80]!r}")
                else:
                    history.append(desc)

                # A passing assert_state is the finish line. Nudge hard toward
                # `done` so the model doesn't wander off and redo work it has
                # already completed (observed on a real the legacy console edit run).
                if ok and call.tool == "assert_state":
                    note = (
                        "That check PASSED. If it is the goal's success condition, your "
                        "NEXT call must be done with the outputs - do not click, type, "
                        "navigate or scroll again."
                    )

                # --- no-progress loop guard -------------------------------
                # An action that "succeeds" but leaves the screen exactly as it
                # was, repeated, means the run is stuck in a loop the model
                # can't see its way out of. Nudge on the 2nd repeat; hard-stop
                # on the 4th (comp-use style: don't rely on the model to bail).
                sig = f"{call.tool}|{json.dumps(call.args.get('target') or call.args.get('url') or call.args.get('key') or '', sort_keys=True)}|{result.url_after}"
                if ok and sig == last_sig:
                    repeats += 1
                else:
                    repeats = 0
                last_sig = sig if ok else last_sig

                # --- cyclic-thrash guard ---------------------------------
                # The model can loop on a set of actions (type email, type
                # phone, type email, ...) so no single one repeats *consecutively*
                # but the run still goes nowhere. Count total repeats of this
                # (tool|target) and intervene once it's clearly stuck.
                tsig = f"{call.tool}|{json.dumps(call.args.get('target') or '', sort_keys=True)}"
                # `extract`/`scroll` loops (extract -> scroll -> extract -> ...)
                # dodge the consecutive guard the same way an A/B type cycle does.
                if ok and call.tool in ("type", "select", "click", "extract", "scroll", "wait_for"):
                    sig_counts[tsig] = sig_counts.get(tsig, 0) + 1
                    # scroll churns fast — cap it low regardless of target
                    scroll_n = sum(v for k, v in sig_counts.items() if k.startswith("scroll|"))
                    n = sig_counts[tsig]
                    if n >= 6 or scroll_n >= 6:
                        reason = f"cyclic thrash: repeated {call.tool} with no progress ({n}x this target, {scroll_n} scrolls)"
                        resumed_note = await self._escalate_and_wait(
                            run, transcript, session, step, reason, goal, history, log,
                            handoff_wait_s, last_call,
                        )
                        if resumed_note is None:
                            break
                        note, last_sig, repeats, sig_counts, cycle_nudged = resumed_note, None, 0, {}, False
                        deadline = time.time() + self.cfg.run_timeout_seconds
                        continue
                    if n >= 3 and not cycle_nudged:
                        cycle_nudged = True
                        if call.tool == "extract":
                            note = (
                                f"You have run extract on the same target {n} times and keep "
                                "re-reading it. If the value you got looks wrong (a decoy / a "
                                "different row), try a MORE SPECIFIC target for the cell you "
                                "want — e.g. a css/xpath, or a landmark that is unique to the "
                                "REAL row. Do not scroll-and-retry. If you truly cannot pick "
                                "the right cell, call stuck with that reason."
                            )
                        elif call.tool in ("type", "select"):
                            note = (
                                f"You have set this same field {n} times. It already holds your "
                                "value. Do the goal's NEXT sub-task (a different field, or submit), "
                                "then done."
                            )
                        else:
                            note = (
                                f"You have repeated {call.tool} {n} times with no progress. Try a "
                                "genuinely different action toward the goal, or call stuck."
                            )

                # --- click-stall guard ----------------------------------
                # A click is only progress if it opens/navigates/changes
                # something. When the model just keeps clicking (or trying and
                # failing to click) on one page — a broken submit under fault
                # injection, a dead control, or a wrong mental model ("still need
                # to sign on" on the menu page) — each attempt with a fresh
                # target or a fresh "could not resolve" error dodges every guard
                # above. Count consecutive no-progress click/press attempts on
                # one URL, successful-but-inert AND outright failed alike.
                _navigated = str(result.url_after or "") not in ("", state.url)
                _progress = _navigated or (ok and call.tool in ("type", "select", "extract", "assert_state"))
                if _progress:
                    click_stall, click_stall_url = 0, None
                elif call.tool in ("click", "press_key"):
                    if click_stall_url == state.url:
                        click_stall += 1
                    else:
                        click_stall, click_stall_url = 1, state.url
                    if click_stall >= 5:
                        reason = (
                            f"click-stall: {click_stall} click attempts on {state.url} that "
                            "changed nothing — the control is dead, the page is broken, or the "
                            "agent is looking for something that isn't there"
                        )
                        resumed_note = await self._escalate_and_wait(
                            run, transcript, session, step, reason, goal, history, log,
                            handoff_wait_s, last_call,
                        )
                        if resumed_note is None:
                            break
                        note, last_sig, repeats = resumed_note, None, 0
                        click_stall, click_stall_url = 0, None
                        deadline = time.time() + self.cfg.run_timeout_seconds
                        continue

                # The model sometimes re-asserts the same passing success check
                # instead of calling done. It has verified the goal twice —
                # that IS done. Record the assert's condition as the checkpoint.
                if ok and call.tool == "assert_state" and repeats >= 1:
                    transcript.done_outputs = {}
                    transcript.final_state = state
                    run.status = RunStatus.COMPLETED
                    run.detail = "goal achieved (success check verified)"
                    log.checkpoint(step, True, "assert_state verified twice — completing")
                    log.run_finished("completed", outputs=[])
                    break

                if repeats >= 1:
                    if call.tool == "scroll":
                        note = (
                            f"Scrolling is NOT helping — you have scrolled {repeats + 1} times "
                            f"with no change. You are on {result.url_after}. The control you want "
                            "is already loaded. Call observe, then act on a field or button BY "
                            "NAME (type into it / click it / select an option). Do not scroll again."
                        )
                    elif call.tool in ("type", "select"):
                        tgt = call.args.get("target")
                        who = tgt.get("name") or tgt.get("label") or tgt.get("near") if isinstance(tgt, dict) else tgt
                        note = (
                            f"The '{who}' field ALREADY holds the value you just entered — see "
                            "CURRENT FORM FIELD VALUES in the observation. Do NOT type into it "
                            "again. Move to the NEXT field the goal names, or if every field is "
                            "set, click the submit/save button."
                        )
                    else:
                        note = (
                            "You have already performed this exact action and the screen did not "
                            f"change — you are on {result.url_after}. Re-observe. If the goal's data "
                            "is on screen, verify with assert_state/extract and call done. Otherwise "
                            "take the NEXT step toward the goal (a different control), or call stuck."
                        )
                if repeats >= (2 if call.tool in ("type", "select") else 3):
                    reason = f"no progress: repeated {call.tool} {repeats + 1}x with no screen change"
                    resumed_note = await self._escalate_and_wait(
                        run, transcript, session, step, reason, goal, history, log,
                        handoff_wait_s, last_call,
                    )
                    if resumed_note is None:
                        break
                    note, last_sig, repeats = resumed_note, None, 0
                    deadline = time.time() + self.cfg.run_timeout_seconds
                    continue

                # --- goal checkpoint auto-complete -----------------------
                # If the caller gave a success condition, end the run the moment
                # it holds - don't wait for the model to call done.
                if ok and success_check:
                    chk = await self.adapter.execute(
                        session, Action(type=ActionType.ASSERT_STATE, condition=success_check)
                    )
                    if chk.ok:
                        transcript.done_outputs = {
                            e.extract_as: e.action_result.get("extracted")
                            for e in transcript.entries
                            if e.extract_as and e.action_result.get("extracted") is not None
                        }
                        transcript.final_state = state
                        run.status = RunStatus.COMPLETED
                        run.detail = "goal checkpoint reached"
                        log.checkpoint(step, True, "success_check satisfied")
                        log.run_finished("completed", outputs=list(transcript.done_outputs))
                        break

                step += 1

            transcript.final_state = transcript.final_state or await self.perception.observe(self.adapter, session)
            transcript.session_id = session
            run.step_count = step
            run.ended_at = time.time()

            # ST-037: a stuck run that was NOT already escalated in-loop raises an
            # intervention here and HOLDS its session for a human.
            if (
                run.status == RunStatus.STUCK
                and self.escalation is not None
                and not transcript.intervention_id
            ):
                if self.broker is not None:
                    self.broker.register_session(session, session)
                iv = await self.escalation.open_intervention(
                    run=run, session_id=session, step_index=step,
                    reason=run.detail or transcript.stuck_reason or "stuck",
                    attempting=_attempting_str(last_call, goal),
                    goal=goal, transcript_tail=history,
                )
                transcript.intervention_id = iv.intervention_id

            return run, transcript
        finally:
            if run.status == RunStatus.STUCK:
                log.event(None, "session_held", session=session, reason="stuck — awaiting escalation")
                if surface.sandbox is not None:
                    log.event(None, "sandbox_held", container=run.sandbox_container,
                              reason="stuck — awaiting operator")
            else:
                # A non-STUCK terminal state must not leave a handoff open — an
                # escalation aborted mid-wait (e.g. LLM out of credits) would
                # otherwise linger in the operator console forever.
                if self.escalation is not None:
                    from ..models import InterventionStatus

                    for _iv in self.escalation.list_interventions():
                        if _iv.run_id == run.run_id and _iv.status != InterventionStatus.RESOLVED:
                            try:
                                self.escalation.abandon(
                                    _iv.intervention_id, f"run ended ({run.status.value})"
                                )
                            except Exception:  # noqa: BLE001
                                pass
                await close_run_surface(
                    adapter=self.adapter, sandbox_manager=self.sandbox_manager,
                    surface=surface, run=run, logger=log,
                )

    async def _discovery_business_outcome(
        self, session: str, *, log: Any | None = None, step: int | None = None
    ) -> tuple[str, str, list[str]] | None:
        """The current screen against the same business-outcome patterns replay
        uses (per-host library + generic not-found / permission phrasings). A
        match means the goal has a legitimate non-happy answer — end the run
        with it, don't route a human. Returns (code, message, matched_phrases).

        When `log`/`step` are given, a screenshot of the outcome screen is
        attached to that step so the report shows *why* the run ended, not the
        pre-navigation form."""
        from ..conditions import evaluate as eval_condition
        from ..models import Condition
        from ..outcomes import business_outcomes_for

        rules = list(business_outcomes_for(getattr(self, "_run_target", "") or ""))
        rules += [
            ("member_not_found", ["No member records matched", "no members matched",
                                  "was not found", "no such member", "record not found"]),
            ("permission_denied", ["is not authorized to perform this function",
                                   "do not have permission", "authorization required",
                                   "access denied"]),
        ]  # generic fallbacks appended as (code, phrases) tuples
        try:
            obs_kw = (
                {"sink": log.sink, "run_id": log.run_id, "step": step}
                if log is not None
                else {}
            )
            state = await self.perception.observe(self.adapter, session, **obs_kw)
        except Exception:  # noqa: BLE001
            return None
        haystack = f"{state.title}\n{state.ax_summary}\n{state.dom_excerpt}".lower()
        for r in rules:
            if isinstance(r, tuple):
                code, phrases = r
                cond = Condition(kind="text_present", params={"any": phrases})
                msg = f"the app reported: {phrases[0]}"
            else:
                code, cond, msg = r.code, r.when, (r.message or r.code)
                phrases = list(cond.params.get("any") or ([cond.params["text"]] if cond.params.get("text") else []))
            try:
                if await eval_condition(cond, state):
                    hit = [p for p in phrases if p and p.lower() in haystack] or phrases[:1]
                    return code, msg, hit
            except Exception:  # noqa: BLE001
                continue
        return None

    async def _classify_stuck_as_outcome(
        self, session: str, goal: str, reason: str, attempting: str | None
    ) -> tuple[str, str, str, list[str]] | None:
        """The pattern library didn't recognise the stuck screen. Ask the model
        to read it and pick one of:
          - DONE      — the goal actually succeeded; the agent just looped
                        without calling done.
          - OUTCOME   — a DEFINITIVE non-happy business outcome the caller must
                        be told about (locked/suspended/closed account, record
                        not found, action not permitted, request declined) that
                        no human taking over the same session could change.
          - HUMAN / UNSURE — something a human operator could act on, or unclear.
        Returns ("done"|"outcome", code, phrase, [phrase]); None to escalate.
        Best-effort: offline / no router / an ungrounded answer all return None."""
        if self.router is None:
            return None
        try:
            state = await self.perception.observe(self.adapter, session)
        except Exception:  # noqa: BLE001
            return None
        haystack = f"{state.title}\n{state.ax_summary}\n{state.dom_excerpt}"
        system = (
            "You triage a stuck UI-automation run by reading the CURRENT screen. "
            "Reply with EXACTLY ONE line, nothing else:\n"
            "  DONE | <the exact on-screen sentence showing the goal succeeded>\n"
            "  OUTCOME <short_snake_case_code> | <the exact on-screen sentence that states it>\n"
            "  HUMAN\n"
            "  UNSURE\n"
            "DONE: the screen clearly shows the agent's goal was ACHIEVED — a "
            "success or confirmation the goal was asking for (e.g. 'Transfer "
            "Complete', 'Thank you for your order', 'Changes saved') — and the "
            "agent merely failed to recognise it. A success confirmation is "
            "NEVER an OUTCOME.\n"
            "OUTCOME: a DEFINITIVE non-happy result that is final for this goal "
            "and that NO human taking over the same browser session could change "
            "(account locked / suspended / closed, record not found, action not "
            "permitted, request rejected or declined).\n"
            "HUMAN: something a human operator could plausibly resolve by driving "
            "the page (a mis-filled form, an unexpected dialog, a control the "
            "agent could not find, a transient error)."
        )
        user = (
            f"GOAL: {goal}\n"
            f"AGENT WAS ATTEMPTING: {attempting}\n"
            f"AGENT'S STATED REASON FOR STOPPING: {reason}\n"
            f"PAGE TITLE: {state.title}\n"
            f"VISIBLE TEXT:\n{state.dom_excerpt[:1800]}"
        )
        try:
            out = (await self.router.call_text(system, user)).strip()
        except Exception:  # noqa: BLE001
            return None
        up = out.upper()
        if up.startswith("DONE"):
            kind, code = "done", "goal_confirmed"
            phrase = out[4:].strip().lstrip(":").lstrip("|").strip()
        elif up.startswith("OUTCOME"):
            kind = "outcome"
            body = out[len("OUTCOME"):].strip().lstrip(":").strip()
            code_raw, _, phrase = body.partition("|")
            code = re.sub(r"[^a-z0-9]+", "_", code_raw.strip().lower()).strip("_") or "business_outcome"
        else:
            return None
        phrase = phrase.strip().strip('"').strip()[:160]
        # the model must ground its answer in text actually on the screen
        toks = re.findall(r"[a-z0-9]{3,}", phrase.lower())
        hay = haystack.lower()
        if not phrase or len(toks) < 2 or sum(t in hay for t in toks) < max(2, len(toks) * 0.6):
            return None
        return kind, code, phrase, [phrase]

    async def _await_risk_approval(
        self, run, transcript, session, step, phrase, reason, goal, history, log, wait_s,
    ) -> bool | None:
        """Pause the run and ask a human to APPROVE / REJECT `phrase` before it
        runs. No takeover — just a yes/no. Returns True (approved) / False
        (rejected) / None (no approver / timed out)."""
        from ..models import InterventionStatus

        if self.escalation is None or not wait_s or wait_s <= 0:
            return None
        if self.broker is not None:
            self.broker.register_session(session, session)
        run.status = RunStatus.STUCK
        run.detail = f"awaiting approval — {phrase}"
        iv = await self.escalation.open_risk_approval(
            run=run, session_id=session, step_index=step,
            proposed_action=phrase, reason=reason, goal=goal, transcript_tail=history,
        )
        transcript.intervention_id = iv.intervention_id
        log.event(step, "awaiting_risk_approval", intervention_id=iv.intervention_id,
                  proposed_action=phrase, wait_s=wait_s)

        end = time.time() + wait_s
        while time.time() < end:
            await asyncio.sleep(1.5)
            try:
                cur = self.escalation.get(iv.intervention_id)
            except Exception:  # noqa: BLE001
                break
            if cur.status == InterventionStatus.RESOLVED:
                run.status = RunStatus.RUNNING
                run.detail = None
                log.event(step, "risk_approval_decided", decision=cur.decision,
                          by=cur.claimed_by, resolution=cur.resolution)
                return cur.decision == "approved"
        # nobody answered — treat as a reject-by-timeout and end the run
        try:
            self.escalation.decide(
                iv.intervention_id, approved=False, operator="system",
                note=f"no approver responded within {wait_s:.0f}s",
            )
        except Exception:  # noqa: BLE001
            pass
        log.event(step, "risk_approval_timeout", intervention_id=iv.intervention_id)
        return None

    async def _escalate_and_wait(
        self, run, transcript, session, step, reason, goal, history, log, wait_s,
        last_call: ToolCall | None = None,
    ) -> str | None:
        """Pause discovery: raise an intervention, hold the session, and BLOCK
        until an operator hands control back. Returns a resume `note` for the
        agent, or None if there is no escalation path / no operator ever came."""
        from ..models import InterventionStatus

        # Before treating this as "needs a human": is the screen a recognised
        # BUSINESS OUTCOME? "no member records matched" is a legitimate answer
        # ("member 12345 doesn't exist"), not something an operator can fix.
        bo = await self._discovery_business_outcome(session, log=log, step=step)
        _bo_src = "recognised at discovery"
        if bo is None:
            # The pattern library didn't recognise it. Discovery already has an
            # LLM in the loop — let it read the screen and decide: the goal
            # already succeeded (DONE), a definitive non-happy business outcome
            # (nothing a human on the same session could change), or a genuine
            # handoff. Best-effort: no router / offline / "" -> escalate.
            cls = await self._classify_stuck_as_outcome(
                session, goal, reason, _attempting_str(last_call, goal)
            )
            if cls is not None:
                kind, code, phrase, phrases = cls
                if kind == "done":
                    run.status = RunStatus.COMPLETED
                    run.detail = f"goal already satisfied — the screen shows: {phrase}"
                    transcript.stuck_reason = None
                    log.event(step, "goal_recognised_on_stuck", phrase=phrase,
                              detail="agent looped without calling done; screen shows success")
                    log.run_finished("completed")
                    return None
                bo = (code, phrase, phrases)
                _bo_src = "classified at discovery (llm)"
        if bo is not None:
            code, msg, phrases = bo
            run.status = RunStatus.BUSINESS_OUTCOME
            run.detail = f"{code}: {msg}"
            transcript.stuck_reason = None
            transcript.business_outcome = (code, msg, phrases)
            log.event(step, "business_outcome", code=code, message=msg, matched=phrases,
                      detail=f"{_bo_src} — ending run, no handoff")
            log.run_finished("business_outcome", code=code)
            return None

        run.status = RunStatus.STUCK
        run.detail = reason
        transcript.stuck_reason = reason
        log.stuck(step, reason)

        if self.escalation is None:
            return None

        attempting = _attempting_str(last_call, goal)
        r = reason.lower()
        terminal = any(m in r for m in (
            "already exists", "no new account was created", "cannot be undone",
            "was not found", "no such", "does not exist", "has timed out",
            "session has expired", "not authorized",
        ))

        if self.broker is not None:
            self.broker.register_session(session, session)
        iv = await self.escalation.open_intervention(
            run=run, session_id=session, step_index=step,
            reason=reason, attempting=attempting, goal=goal, transcript_tail=history,
        )
        transcript.intervention_id = iv.intervention_id

        if terminal:
            # the goal is unreachable from here and no operator action changes
            # that — end now and release the sandbox instead of holding it.
            try:
                self.escalation.abandon(iv.intervention_id, "unreachable goal — not an actionable handoff")
            except Exception:  # noqa: BLE001
                pass
            run.status = RunStatus.DEAD_END
            run.detail = f"dead end at step {step}: {reason}"
            log.event(step, "handoff_skipped", intervention_id=iv.intervention_id,
                      detail="stuck reason is not operator-actionable — ending run")
            log.run_finished("dead_end", reason=run.detail)
            return None

        if wait_s <= 0:
            return None  # no in-loop wait (tests / non-interactive callers)
        log.event(step, "awaiting_operator", intervention_id=iv.intervention_id, wait_s=wait_s)

        end = time.time() + wait_s
        while time.time() < end:
            await asyncio.sleep(1.5)
            try:
                cur = self.escalation.get(iv.intervention_id)
            except Exception:  # noqa: BLE001
                break
            if cur.status == InterventionStatus.RESOLVED:
                acts = getattr(cur, "human_actions_log", []) or []
                summary = "; ".join(
                    f"{a.get('type')}({a.get('target') or a.get('value') or a.get('url') or ''})".strip("()")
                    for a in acts
                ) or "no explicit actions recorded"
                run.status = RunStatus.RUNNING
                run.detail = None
                log.event(step, "operator_handed_back", intervention_id=iv.intervention_id, actions=summary)
                return (
                    f"An operator took control at step {step} and has now handed it back. "
                    f"What the operator did: {summary}. "
                    "Do NOT assume the goal is finished. Call observe first to re-read the "
                    "CURRENT screen, work out exactly what state the page is in now, then "
                    "continue toward the goal from here. Only call done after verifying the "
                    "success condition with assert_state / extract."
                )
        # The wait expired. If a human claimed it, give them ONE more window to
        # finish, then abandon regardless — a claimed-but-never-resolved
        # intervention must not hold a sandbox forever.
        try:
            final = self.escalation.get(iv.intervention_id)
        except Exception:  # noqa: BLE001
            final = None
        if final is not None and final.status == InterventionStatus.CLAIMED:
            log.event(step, "handoff_timeout", intervention_id=iv.intervention_id,
                      detail=f"operator in control — one more {wait_s:.0f}s grace window")
            grace_end = time.time() + wait_s
            while time.time() < grace_end:
                await asyncio.sleep(1.5)
                try:
                    cur = self.escalation.get(iv.intervention_id)
                except Exception:  # noqa: BLE001
                    break
                if cur.status == InterventionStatus.RESOLVED:
                    run.status = RunStatus.RUNNING
                    run.detail = None
                    return (
                        f"An operator handed control back at step {step}. Call observe "
                        "first, then continue toward the goal; verify with assert_state / "
                        "extract before done."
                    )
        try:
            self.escalation.abandon(iv.intervention_id, f"no resolution within {2 * wait_s:.0f}s")
        except Exception:  # noqa: BLE001
            pass
        run.status = RunStatus.DEAD_END
        run.detail = f"stuck at step {step} and no operator responded within {wait_s:.0f}s"
        log.event(step, "handoff_timeout", intervention_id=iv.intervention_id,
                  detail="unclaimed - ending run, releasing sandbox")
        log.run_finished("dead_end", reason=run.detail)
        return None

    # -- helpers ---------------------------------------------------
    async def _decide_with_backoff(
        self, goal, state, history, step, step_budget, params, note, ceiling, log
    ) -> ToolCall | None:
        """Rate limits are transient — keep rotating and retrying with a capped
        backoff (up to `ceiling` rounds). Out-of-credits is not: it propagates
        so the run stops rather than spinning."""
        backoff = 3.0
        for attempt in range(ceiling):
            try:
                return await self.agent.decide(
                    goal, state, history, steps_left=step_budget - step, params=params,
                    note=note, logger=log,
                )
            except AllProvidersOutOfBalance:
                raise
            except AllProvidersExhausted as exc:
                log.event(step, "all_providers_exhausted", attempt=attempt + 1, backoff_s=backoff, detail=str(exc))
                if attempt == ceiling - 1:
                    return None
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                # re-arm the cooled-down providers for a fresh attempt (a
                # rate-limited provider recovers; a disabled one stays out).
                self.router.reset()
        return None

    @staticmethod
    def _to_action(call: ToolCall, params: dict[str, Any]) -> tuple[Action, str | None]:
        a = call.args
        t = call.tool
        if t == "click":
            return Action(type=ActionType.CLICK, target_description=a["target"]), None
        if t == "type":
            return Action(type=ActionType.TYPE, target_description=a["target"], value=str(a.get("value", ""))), None
        if t == "select":
            return Action(type=ActionType.SELECT, target_description=a["target"], value=str(a.get("option", ""))), None
        if t == "navigate":
            return Action(type=ActionType.NAVIGATE, value=a["url"]), None
        if t == "wait_for":
            return Action(type=ActionType.WAIT_FOR, condition=_salvage_condition(a, call.reasoning), timeout_ms=int(a.get("timeout_ms", 15000))), None
        if t == "extract":
            return (
                Action(type=ActionType.EXTRACT, target_description=a["target"], expected_shape=a.get("expected_shape", "string")),
                a.get("as"),
            )
        if t == "assert_state":
            return Action(type=ActionType.ASSERT_STATE, condition=_salvage_condition(a, call.reasoning)), None
        if t == "scroll":
            tgt = {"text": a["to_text"]} if a.get("to_text") else None
            return Action(type=ActionType.SCROLL, target_description=tgt, value=str(a.get("direction", "down"))), None
        if t == "press_key":
            return Action(type=ActionType.PRESS_KEY, value=str(a.get("key", "Enter"))), None
        raise ValueError(f"non-actionable tool: {t}")


def _describe_call(call: ToolCall) -> str:
    a = call.args
    if call.tool == "type":
        return f"type into {a.get('target')} (value hidden)"
    if call.tool == "navigate":
        return f"navigate {a.get('url')}"
    if call.tool == "extract":
        return f"extract {a.get('as')} <- {a.get('target')} as {a.get('expected_shape')}"
    return f"{call.tool} {a.get('target') or a.get('condition') or ''}".strip()


_REVERSAL_RE = re.compile(
    r"\b(revers\w*|refund\w*|charge\s*back|undo|roll\s*back|void\w*|"
    r"cancel\s+(the\s+)?(transfer|payment|transaction|order|deposit|withdrawal)|"
    r"delete\s+(the\s+)?(transfer|payment|transaction)|"
    r"(transfer|send|move|pay)\s+(it|the\s+(money|funds|amount))?\s*back)\b",
    re.I,
)


def _call_intent_text(call: ToolCall) -> str:
    """The control's visible text / name / label plus any value, flattened to
    one lowercase string — the semantic signal for risk + reversal matching."""
    a = call.args
    t = a.get("target")
    parts: list[str] = []
    if isinstance(t, dict):
        parts += [str(t.get(k, "")) for k in ("text", "name", "label", "near", "role")]
    elif t:
        parts.append(str(t))
    parts += [str(a.get(k, "")) for k in ("url", "value", "option")]
    parts.append((call.reasoning or "")[:120])
    return " ".join(p for p in parts if p).lower().strip()


def _commit_sig(call: ToolCall) -> str:
    """Identity of a commit action, to catch a verbatim re-submit."""
    a = call.args
    return f"{call.tool}|{json.dumps(a.get('target'), sort_keys=True)}|{a.get('value') or a.get('option') or ''}|{a.get('url') or ''}"


def _risk_action_phrase(call: ToolCall, goal: str | None) -> str:
    """Plain-language description of the risky action awaiting sign-off, e.g.
    "click 'Transfer' (value: 500) — goal: transfer 500 from the first ...""."""
    a = call.args
    t = a.get("target")
    label = ""
    if isinstance(t, dict):
        label = t.get("text") or t.get("name") or t.get("label") or t.get("near") or ""
    elif t:
        label = str(t)
    val = a.get("value") or a.get("option") or ""
    piece = f"{call.tool} '{label or _call_intent_text(call)[:60]}'"
    if val:
        piece += f" (value: {val})"
    g = (goal or "").strip()
    if g:
        piece += f" — goal: {g[:160]}"
    return piece


def _stuck_reason(call: ToolCall) -> str:
    """Why the model called `stuck`. It puts this under `reason`, but the
    provider layer sweeps any reason/reasoning-shaped key — including mangled
    ones like Gemini's `reas1on` — into `call.reasoning` before the loop sees
    the args, so fall back through that and a nested `context.reason`."""
    ctx = call.args.get("context")
    return (
        (call.args.get("reason") or "").strip()
        or (str(ctx.get("reason")).strip() if isinstance(ctx, dict) and ctx.get("reason") else "")
        or (call.reasoning or "").strip()
        or "unspecified"
    )


def _attempting_str(call: ToolCall | None, goal: str | None) -> str | None:
    """A plain-language line for the operator: what the agent was trying to do
    when it gave up. Its stated intent + the concrete control/value it wanted."""
    if call is None:
        return f"work toward the goal: {goal}" if goal else None
    a = call.args
    target = a.get("target") or a.get("url") or a.get("condition") or a.get("key")
    verb = {
        "click": "click", "type": "type into", "select": "pick an option in",
        "navigate": "navigate to", "wait_for": "wait for", "extract": "read",
        "assert_state": "verify", "scroll": "scroll to", "press_key": "press key on",
    }.get(call.tool, call.tool)
    val = ""
    if call.tool == "type":
        val = " (a value it was given; hidden here)"
    elif call.tool in ("select", "press_key", "scroll") and a.get("value"):
        val = f" = {a['value']!r}"
    piece = f"{verb} {target}{val}".strip()
    intent = (call.reasoning or "").strip().rstrip(".")
    if intent:
        return f"{intent} — by trying to {piece}"
    return f"trying to {piece}"
