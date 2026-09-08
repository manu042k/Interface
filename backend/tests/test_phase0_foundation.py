"""Phase 0 — ST-001..ST-005."""

from __future__ import annotations

import httpx
import pytest


# --- ST-001: config loading --------------------------------------------------
def test_config_fails_fast_on_missing_secret(monkeypatch):
    from cua.config import ConfigError, load_config

    monkeypatch.setenv("CUA_LLM_PROVIDERS", "openrouter")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(ConfigError) as exc:
        load_config(strict=True)
    assert "OPENROUTER_API_KEY" in str(exc.value)


def test_config_offline_scripted_needs_no_key(monkeypatch):
    from cua.config import load_config

    monkeypatch.setenv("CUA_LLM_PROVIDERS", "scripted")
    cfg = load_config(strict=True)
    assert cfg.offline is True
    assert cfg.max_steps > 0


# --- ST-002: core models validate at construction --------------------------
def test_step_requires_idempotent_flag():
    from cua.models import ActionType, Step

    with pytest.raises(Exception):
        Step(step_index=0, action_type=ActionType.CLICK, description="x", locator_spec=[])  # missing idempotent


def test_step_shape_matches_action():
    from cua.models import ActionType, LocatorStrategy, Step

    loc = [LocatorStrategy(kind="text", params={"text": "Search"}, rank=0, rationale="visible button label")]
    # click with locator ok
    Step(step_index=0, action_type=ActionType.CLICK, description="click search", locator_spec=loc, idempotent=True)
    # type without value_binding -> error
    with pytest.raises(Exception):
        Step(step_index=0, action_type=ActionType.TYPE, description="type", locator_spec=loc, idempotent=False)


def test_artifact_rejects_noncontiguous_steps():
    from cua.models import (
        ActionType,
        CapabilityArtifact,
        Condition,
        LocatorStrategy,
        Step,
    )

    loc = [LocatorStrategy(kind="text", params={"text": "x"}, rank=0, rationale="r")]
    s0 = Step(step_index=0, action_type=ActionType.CLICK, description="a", locator_spec=loc, idempotent=True)
    s2 = Step(step_index=2, action_type=ActionType.CLICK, description="b", locator_spec=loc, idempotent=True)
    with pytest.raises(Exception):
        CapabilityArtifact(
            name="x", goal_description="g", steps=[s0, s2],
            checkpoint=Condition(kind="url_matches", params={"pattern": ".*"}),
        )


# --- ST-003: redaction ----------------------------------------------------
def test_redact_strips_secrets_with_marker():
    from cua.redaction import redact_report

    payload = {
        "username": "dana",
        "password": "hunter2super",
        "note": "call about account 4000123456789010",
        "balance": "$4,182.55",
    }
    out, kinds = redact_report(payload)
    assert out["username"] == "dana"
    assert "hunter2super" not in str(out)
    assert "REDACTED" in str(out["password"])
    assert "4000123456789010" not in str(out)
    assert out["balance"] == "$4,182.55"  # ordinary business data untouched
    assert kinds  # something was redacted


def test_redact_is_noop_on_clean_data():
    from cua.redaction import redact

    payload = {"member": "Dana Whitfield", "savings": "$19.00", "branch": "Elm Street"}
    assert redact(payload) == payload


# --- ST-004: observability sink -----------------------------------------
def test_file_sink_orders_events_and_redacts(tmp_path):
    from cua.observability import FileSink

    sink = FileSink(tmp_path)
    sink.log_event("run1", 0, {"event": "step", "action": "type", "value": "password: sekritvalue1"})
    sink.log_event("run1", 1, {"event": "step", "action": "click"})
    events = sink.read_events("run1")
    assert [e["step"] for e in events] == [0, 1]
    assert "sekritvalue1" not in str(events)


def test_file_sink_never_raises_on_bad_input(tmp_path):
    from cua.observability import FileSink

    sink = FileSink(tmp_path)

    class Weird:
        pass

    # non-serializable value must not crash the caller
    sink.log_event("run2", 0, {"event": "x", "obj": Weird()})
    assert sink.read_events("run2")  # still wrote a line


# --- ST-005: target app reachable + flows exist ------------------------
def test_mockbank_happy_flow(mockbank):
    r = httpx.get(f"{mockbank}/members", params={"q": "12345"})
    assert r.status_code == 200 and "Open&nbsp;record" in r.text

    # interstitial appears first, then detail after ack
    with httpx.Client() as c:
        d0 = c.get(f"{mockbank}/member/12345")
        assert "Session Notice" in d0.text
        d1 = c.get(f"{mockbank}/member/12345", params={"ack": "1"})
        assert "Savings" in d1.text and "$4,182.55" in d1.text


def test_mockbank_not_found_and_restricted(mockbank):
    r = httpx.get(f"{mockbank}/members", params={"q": "00000"})
    assert r.status_code == 200 and "No members matched" in r.text

    r2 = httpx.get(f"{mockbank}/member/99999")
    assert r2.status_code == 403 and "restricted" in r2.text.lower()


def test_mockbank_sub_account_confirmation(mockbank):
    with httpx.Client() as c:
        c.get(f"{mockbank}/member/12345", params={"ack": "1"})
        r = c.post(
            f"{mockbank}/member/12345/sub-account/create",
            data={"acct_type": "Holiday Club", "amt": "25.00"},
        )
        assert r.status_code == 200
        assert "Sub-account created" in r.text and "Confirmation number" in r.text


def test_mockbank_validation_error(mockbank):
    r = httpx.post(f"{mockbank}/member/12345/sub-account/create", data={"acct_type": "", "amt": "0"})
    assert r.status_code == 400 and "choose an account type" in r.text.lower()
