"""ST-011/012: the pre-action guardrail check.

`check()` is called synchronously, in-process, immediately before every action
reaches the Surface Adapter — discovery and replay alike. It cannot be skipped
under load because it is a function call, not a network hop.

Fail-closed everywhere: unknown tenant, unparseable target, or an exception in
the check itself all resolve to BLOCK.

Risk model (§3.4): actions are `safe_reversible` by default. An action is
`risky_irreversible` if the step/tool says so, OR its target route matches the
tenant's `risky_route_patterns`, OR its action type is in `risky_action_types`.
The risky class is dispositioned by tenant policy — default `require_confirmation`
(a human/gate must approve) rather than silent execution. We prefer
require_confirmation over hard block as the default so a legitimate
"reach the confirmation screen" goal is still reachable under human oversight;
a tenant can set `block` for a stricter posture.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from ..models import ActionType, RiskClass
from .allowlist import Allowlist, TenantPolicy


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
            disp = tenant.risk_policy.risky_irreversible
            if disp == "block":
                return PolicyDecision(PolicyVerdict.BLOCK, f"risky/irreversible action blocked by tenant policy ({path_q})", risk)
            if disp == "require_confirmation":
                return PolicyDecision(
                    PolicyVerdict.REQUIRE_CONFIRMATION,
                    f"risky/irreversible action requires confirmation ({path_q})",
                    risk,
                )
            # "flag": allow but mark
            return PolicyDecision(PolicyVerdict.ALLOW, f"risky/irreversible action flagged, permitted ({path_q})", risk)

        return PolicyDecision(PolicyVerdict.ALLOW, "within allowlist; safe/reversible", risk)

    @staticmethod
    def _classify(ctx: ActionContext, tenant: TenantPolicy, path_q: str) -> RiskClass:
        if ctx.declared_risk == RiskClass.RISKY_IRREVERSIBLE:
            return RiskClass.RISKY_IRREVERSIBLE
        if str(ctx.action_type) in tenant.risk_policy.risky_action_types:
            return RiskClass.RISKY_IRREVERSIBLE
        if tenant.route_is_risky(path_q):
            return RiskClass.RISKY_IRREVERSIBLE
        return RiskClass.SAFE_REVERSIBLE
