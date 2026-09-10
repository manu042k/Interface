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


def test_allow_target_opens_the_typed_host_only():
    eng = _engine()
    tgt = "https://legacy-core.example.com/signon"
    # not on the file allowlist -> blocked
    assert eng.check(ActionContext("default", ActionType.NAVIGATE, tgt)).verdict == PolicyVerdict.BLOCK
    eng.allow_target("default", tgt)
    # now the typed host + its routes are permitted
    assert eng.check(ActionContext("default", ActionType.NAVIGATE, tgt)).allowed
    assert eng.check(
        ActionContext("default", ActionType.CLICK, "https://legacy-core.example.com/members/100234")
    ).allowed
    # a different host is still blocked
    assert eng.check(
        ActionContext("default", ActionType.NAVIGATE, "https://evil.example/x")
    ).verdict == PolicyVerdict.BLOCK


def test_allow_target_creates_a_missing_tenant():
    eng = _engine()
    eng.allow_target("brand-new", "https://acme.test/app")
    assert eng.check(ActionContext("brand-new", ActionType.CLICK, "https://acme.test/app/page")).allowed


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


# -- built-in banking-mutation risk (tenant-independent) ---------------------

def test_money_movement_control_needs_approval_even_without_tenant_config():
    """A 'Transfer' button trips require_confirmation on a bare auto-admitted
    host that has no hand-written risky_route_patterns."""
    eng = _engine()
    tgt = "https://parabank.parasoft.com/parabank/transfer.htm"
    eng.allow_target("default", tgt)
    d = eng.check(ActionContext(
        "default", ActionType.CLICK, tgt,
        extra={"target": "Transfer", "value": "999999"},
    ))
    assert d.verdict == PolicyVerdict.REQUIRE_CONFIRMATION
    assert "money-movement" in d.reason


def test_mutation_route_is_risky_by_route_alone():
    eng = _engine()
    tgt = "https://parabank.parasoft.com/parabank/billpay.htm"
    eng.allow_target("default", tgt)
    d = eng.check(ActionContext("default", ActionType.CLICK, tgt, extra={"target": "Send Payment"}))
    assert d.verdict == PolicyVerdict.REQUIRE_CONFIRMATION


def test_plain_submit_on_a_search_form_is_not_risky():
    eng = _engine()
    tgt = "https://shop.example/search"
    eng.allow_target("default", tgt)
    d = eng.check(ActionContext("default", ActionType.CLICK, tgt, extra={"target": "Search"}))
    assert d.verdict == PolicyVerdict.ALLOW
    d2 = eng.check(ActionContext("default", ActionType.CLICK, tgt, extra={"target": "Submit"}))
    assert d2.verdict == PolicyVerdict.ALLOW  # generic verb, non-mutation route


def test_typing_into_an_amount_field_is_not_a_commit():
    eng = _engine()
    tgt = "https://parabank.parasoft.com/parabank/transfer.htm"
    eng.allow_target("default", tgt)
    d = eng.check(ActionContext("default", ActionType.TYPE, tgt, extra={"target": "amount", "value": "999999"}))
    assert d.verdict == PolicyVerdict.ALLOW  # only click/press_key/navigate commit
