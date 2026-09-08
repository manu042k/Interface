"""Phase 2 — ST-010..ST-012."""

from __future__ import annotations

import os

from cua.models import ActionType, RiskClass
from cua.policy.engine import ActionContext, PolicyEngine, PolicyVerdict

ALLOWLIST = os.path.join(os.path.dirname(__file__), "..", "config", "allowlist.example.json")


def _engine() -> PolicyEngine:
    return PolicyEngine.from_path(ALLOWLIST)


# --- ST-010: allowlist model, default deny ------------------------------
def test_unknown_tenant_is_denied():
    d = _engine().check(ActionContext("no-such-tenant", ActionType.CLICK, "http://127.0.0.1:8799/search"))
    assert d.verdict == PolicyVerdict.BLOCK


def test_offlist_domain_and_route_denied():
    eng = _engine()
    assert eng.check(ActionContext("default", ActionType.NAVIGATE, "http://evil.example/search")).verdict == PolicyVerdict.BLOCK
    assert eng.check(ActionContext("default", ActionType.NAVIGATE, "http://127.0.0.1:8799/admin/wipe")).verdict == PolicyVerdict.BLOCK


def test_broken_allowlist_fails_closed(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json", encoding="utf-8")
    eng = PolicyEngine.from_path(str(bad))
    assert eng.check(ActionContext("default", ActionType.CLICK, "http://127.0.0.1:8799/search")).verdict == PolicyVerdict.BLOCK


# --- ST-011: pre-action check ----------------------------------------
def test_in_allowlist_action_allowed():
    d = _engine().check(ActionContext("default", ActionType.TYPE, "http://127.0.0.1:8799/search"))
    assert d.verdict == PolicyVerdict.ALLOW
    assert d.risk_class == RiskClass.SAFE_REVERSIBLE


def test_disallowed_action_type_blocked():
    # allowlist permits click/type/select/navigate/wait_for/extract/assert_state — not "delete"
    d = _engine().check(ActionContext("default", "delete", "http://127.0.0.1:8799/search"))
    assert d.verdict == PolicyVerdict.BLOCK


# --- ST-012: risk classification ------------------------------------
def test_risky_route_requires_confirmation():
    d = _engine().check(
        ActionContext("default", ActionType.CLICK, "http://127.0.0.1:8799/member/12345/sub-account/create")
    )
    assert d.verdict == PolicyVerdict.REQUIRE_CONFIRMATION
    assert d.risk_class == RiskClass.RISKY_IRREVERSIBLE


def test_declared_risk_promotes_to_confirmation():
    d = _engine().check(
        ActionContext("default", ActionType.CLICK, "http://127.0.0.1:8799/member/12345",
                      declared_risk=RiskClass.RISKY_IRREVERSIBLE)
    )
    assert d.verdict == PolicyVerdict.REQUIRE_CONFIRMATION


def test_safe_action_stays_fast():
    d = _engine().check(ActionContext("default", ActionType.EXTRACT, "http://127.0.0.1:8799/member/12345"))
    assert d.verdict == PolicyVerdict.ALLOW
