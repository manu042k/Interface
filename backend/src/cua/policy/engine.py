"""ST-011/012: the pre-action guardrail check.

`check()` is called synchronously, in-process, immediately before every action
reaches the Surface Adapter — discovery and replay alike. It cannot be skipped
under load because it is a function call, not a network hop.

Fail-closed everywhere: unknown tenant, unparseable target, or an exception in
the check itself all resolve to BLOCK.

Risk model (§3.4): actions are `safe_reversible` by default. An action is
`risky_irreversible` if the step/tool says so, OR its target route matches the
tenant's `risky_route_patterns`, OR its action type is in `risky_action_types`,
OR it carries a built-in banking-mutation signal (a money-movement /
account-lifecycle verb on the control it commits, or a mutation route) — this
last one applies on ANY tenant, including a bare auto-admitted target, so a
funds transfer on a site with no hand-written policy still gates.
The risky class is dispositioned by tenant policy — default `require_confirmation`
(a human/gate must approve) rather than silent execution. We prefer
require_confirmation over hard block as the default so a legitimate
"reach the confirmation screen" goal is still reachable under human oversight;
a tenant can set `block` for a stricter posture.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from ..models import ActionType, RiskClass
from .allowlist import Allowlist, TenantPolicy

# Built-in, tenant-independent banking-mutation signals. Conservative: they fire
# on the COMMIT gesture (a click / Enter on the control that posts, or a
# navigate to a mutation route), never on typing into a field or reading.
_MONEY_MOVE_RE = re.compile(
    r"\b(transfer|wire|remit|remittance|disburse\w*|withdraw\w*|deposit|"
    r"bill\s*pay|bill-?pay|payment|pay\s+(bill|payee|now|from)|send\s+money|"
    r"issue\s+(check|cheque|payment)|stop\s+payment|charge\s*back|charge|refund|"
    r"revers\w*|void)\b",
    re.I,
)
_LIFECYCLE_RE = re.compile(
    r"\b(open\s+(a\s+|an\s+)?(new\s+)?(account|share|sub-?account|certificate)|"
    r"close\s+(the\s+|this\s+)?account|place\s+(a\s+)?hold|release\s+(the\s+)?hold|"
    r"freeze\s+account|unfreeze|delete\s+(account|customer|member|user|operator)|"
    r"reset\s+(password|pin)|change\s+(beneficiary|ownership))\b",
    re.I,
)
# Generic commit verbs — risky ONLY when the route is also a mutation route,
# so a plain "Submit" on a search form does not trip.
_COMMIT_VERB_RE = re.compile(
    r"\b(submit|confirm|post|apply|authoriz\w*|approve|finaliz\w*|"
    r"complete\s+(order|transfer|payment|purchase)|place\s+order)\b",
    re.I,
)
_MUTATION_ROUTE_RE = re.compile(
    r"/(transfer|billpay|bill-?pay|payment|wire|withdraw\w*|deposit|disburse\w*|"
    r"post|confirm|authoriz\w*|approve|open-?account|close|hold|stop-?payment|"
    r"sub-?account/(new|create)|create|update-?profile)(/|$|\?|\.htm|\.do|\.aspx)",
    re.I,
)


def _mutation_signal(action_type: str, path_q: str, signal: str) -> str | None:
    """A built-in banking-mutation reason, or None. `signal` is the control's
    visible text / name / value flattened to one lowercase string."""
    if action_type not in ("click", "press_key", "navigate"):
        return None
    if _MONEY_MOVE_RE.search(signal):
        return "money-movement action (transfer / payment / withdrawal)"
    if _LIFECYCLE_RE.search(signal):
        return "account-lifecycle action (open / close / hold / reset)"
    if _MUTATION_ROUTE_RE.search(path_q):
        return f"mutation route ({path_q})"
    if _COMMIT_VERB_RE.search(signal) and _MUTATION_ROUTE_RE.search(path_q):
        return "commit action on a mutation route"
    return None


class PolicyVerdict(StrEnum):
    ALLOW = "allow"
    BLOCK = "block"
    REQUIRE_CONFIRMATION = "require_confirmation"


@dataclass(frozen=True)
class PolicyDecision:
    verdict: PolicyVerdict
    reason: str
    risk_class: RiskClass = RiskClass.SAFE_REVERSIBLE

    @property
    def allowed(self) -> bool:
        return self.verdict == PolicyVerdict.ALLOW


@dataclass
class ActionContext:
    tenant_id: str
    action_type: ActionType | str
    # The URL the action targets or the current page URL for non-navigation actions.
    target_url: str
    declared_risk: RiskClass | None = None
    extra: dict[str, Any] | None = None


_DEFAULT_ACTIONS = ["click", "type", "select", "navigate", "wait_for", "extract", "assert_state", "scroll", "press_key"]


class PolicyEngine:
    def __init__(self, allowlist: Allowlist) -> None:
        self._allowlist = allowlist

    def allow_target(self, tenant_id: str, target_url: str) -> None:
        """Register a run's entry-point host as permitted for `tenant_id`, so a
        typed Target URL just works without an allowlist file. Egress to any
        OTHER host stays blocked and the risky-route confirmation gate still
        applies — this only opens the door to the site the operator chose.
        """
        host = (urlparse(target_url).hostname or "").lower()
        if not host:
            return
        tenant = self._allowlist.for_tenant(tenant_id)
        if tenant is None:
            tenant = TenantPolicy(action_types=list(_DEFAULT_ACTIONS))
            self._allowlist.tenants[tenant_id] = tenant
        if not tenant.allows_domain(host):
            tenant.domains.append(host)
        if "^/" not in tenant.routes:
            tenant.routes.append("^/")

    @classmethod
    def from_path(cls, path: str) -> PolicyEngine:
        try:
            return cls(Allowlist.load(path))
        except Exception as exc:  # noqa: BLE001
            # A broken allowlist must not fail *open*. Load an empty (deny-all) one.
            eng = cls(Allowlist())
            eng._load_error = str(exc)  # type: ignore[attr-defined]
            return eng

    def check(self, ctx: ActionContext) -> PolicyDecision:
        try:
            return self._check(ctx)
        except Exception as exc:  # noqa: BLE001 — fail closed
            return PolicyDecision(PolicyVerdict.BLOCK, f"guardrail check errored, failing closed: {exc}")

    # -- internals ----------------------------------------------------
    def _check(self, ctx: ActionContext) -> PolicyDecision:
        tenant = self._allowlist.for_tenant(ctx.tenant_id)
        if tenant is None:
            return PolicyDecision(PolicyVerdict.BLOCK, f"no allowlist for tenant {ctx.tenant_id!r} (default deny)")

        action_type = str(ctx.action_type)
        if not tenant.allows_action(action_type):
            return PolicyDecision(PolicyVerdict.BLOCK, f"action type {action_type!r} not permitted for tenant")

        parsed = urlparse(ctx.target_url)
        host = parsed.hostname or ""
        path_q = parsed.path + (("?" + parsed.query) if parsed.query else "")

        if not host:
            return PolicyDecision(PolicyVerdict.BLOCK, f"cannot parse a host from target {ctx.target_url!r}")
        if not tenant.allows_domain(host):
            return PolicyDecision(PolicyVerdict.BLOCK, f"domain {host!r} not on tenant allowlist")
        if not tenant.allows_route(path_q):
            return PolicyDecision(PolicyVerdict.BLOCK, f"route {path_q!r} not on tenant allowlist")

        risk = self._classify(ctx, tenant, path_q)
        if risk == RiskClass.RISKY_IRREVERSIBLE:
            why = _mutation_signal(
                str(ctx.action_type), path_q,
                " ".join(str((ctx.extra or {}).get(k, "")) for k in ("target", "value", "label", "option")).lower(),
            ) or f"risky/irreversible action ({path_q})"
            disp = tenant.risk_policy.risky_irreversible
            if disp == "block":
                return PolicyDecision(PolicyVerdict.BLOCK, f"blocked by tenant policy — {why}", risk)
            if disp == "require_confirmation":
                return PolicyDecision(PolicyVerdict.REQUIRE_CONFIRMATION, f"needs human approval — {why}", risk)
            # "flag": allow but mark
            return PolicyDecision(PolicyVerdict.ALLOW, f"flagged, permitted — {why}", risk)

        return PolicyDecision(PolicyVerdict.ALLOW, "within allowlist; safe/reversible", risk)

    @staticmethod
    def _classify(ctx: ActionContext, tenant: TenantPolicy, path_q: str) -> RiskClass:
        if ctx.declared_risk == RiskClass.RISKY_IRREVERSIBLE:
            return RiskClass.RISKY_IRREVERSIBLE
        if str(ctx.action_type) in tenant.risk_policy.risky_action_types:
            return RiskClass.RISKY_IRREVERSIBLE
        if tenant.route_is_risky(path_q):
            return RiskClass.RISKY_IRREVERSIBLE
        # built-in banking-mutation signal — tenant-independent
        extra = ctx.extra or {}
        signal = " ".join(
            str(extra.get(k, "")) for k in ("target", "value", "label", "option")
        ).lower()
        if _mutation_signal(str(ctx.action_type), path_q, signal):
            return RiskClass.RISKY_IRREVERSIBLE
        return RiskClass.SAFE_REVERSIBLE

    def mutation_reason(self, ctx: ActionContext) -> str | None:
        """Public: the built-in banking-mutation reason for this action, if any
        (used by callers that want to log *why* a step is risky)."""
        parsed = urlparse(ctx.target_url)
        path_q = parsed.path + (("?" + parsed.query) if parsed.query else "")
        extra = ctx.extra or {}
        signal = " ".join(
            str(extra.get(k, "")) for k in ("target", "value", "label", "option")
        ).lower()
        return _mutation_signal(str(ctx.action_type), path_q, signal)
