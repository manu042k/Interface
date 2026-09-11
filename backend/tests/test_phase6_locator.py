"""Phase 6 — ST-027..ST-029."""

from __future__ import annotations

import pytest_asyncio

from cua.models import ActionType, LocatorStrategy
from cua.replay.locator import LocatorResolutionEngine
from cua.surface.base import Action
from cua.surface.playwright_adapter import PlaywrightAdapter


@pytest_asyncio.fixture
async def adapter():
    a = PlaywrightAdapter(headed=False)
    yield a
    await a.shutdown()


async def test_first_strategy_matches_no_drift(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/search")
    eng = LocatorResolutionEngine()
    spec = [
        LocatorStrategy(kind="role_name", params={"role": "button", "name": "Search"}, rank=0, rationale="r"),
        LocatorStrategy(kind="text", params={"text": "Search"}, rank=1, rationale="r"),
    ]
    res = await eng.resolve(spec, adapter, h, artifact_version=1, surface_fingerprint="fp1", step_index=0)
    assert res.ok and res.matched_rank == 0 and res.drift_signal is False


async def test_falls_back_and_flags_drift(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/member/12345?ack=1")
    eng = LocatorResolutionEngine()
    spec = [
        LocatorStrategy(kind="role_name", params={"role": "button", "name": "does-not-exist"}, rank=0, rationale="r"),
        LocatorStrategy(kind="relative_to_landmark", params={"near": "Savings"}, rank=1, rationale="r"),
    ]
    res = await eng.resolve(spec, adapter, h, artifact_version=1, surface_fingerprint="fp2", step_index=3)
    assert res.ok and res.matched_rank == 1 and res.drift_signal is True
    assert res.tried[0]["matched"] is False


async def test_unresolvable_returns_structured_error(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/search")
    eng = LocatorResolutionEngine()
    spec = [
        LocatorStrategy(kind="role_name", params={"role": "button", "name": "nope"}, rank=0, rationale="r"),
        LocatorStrategy(kind="text", params={"text": "also nope zzz"}, rank=1, rationale="r"),
    ]
    res = await eng.resolve(spec, adapter, h, artifact_version=1, surface_fingerprint="fp3", step_index=0)
    assert res.ok is False
    assert "unresolvable target" in res.error
    assert len(res.tried) == 2 and all(not t["matched"] for t in res.tried)


async def test_sole_visible_match_disambiguates_a_hidden_validation_placeholder(adapter, mockbank):
    """A `[name="x"], [id="x"]` dom_anchor can also match a hidden
    validation-placeholder span/div sharing the real control's id/name
    (ParaBank's billpay.htm: <input name="amount"> plus a hidden
    <span id="amount">, a client-side-JS error message invisible until a
    failed submit). Two matches used to fail closed as 'ambiguous' even
    though only one of them is actually visible/actionable. Found live-
    testing parabank_bill_pay after the earlier name-or-id css fix widened
    the selector enough to also catch the hidden placeholder."""
    h = await adapter.open_session(f"{mockbank}/search")
    page = adapter._sess(h).page
    await page.set_content(
        """
        <form>
          <input type="text" name="amount" value="">
          <span id="amount" style="display:none" class="error">Amount is required.</span>
        </form>
        """
    )
    outcome = await adapter.try_strategy(h, "dom_anchor", {"css": '[name="amount"], [id="amount"]'})
    assert outcome["matched"] is True and outcome["count"] == 1
    assert "only visible match" in outcome["describe"]

    eng = LocatorResolutionEngine()
    spec = [LocatorStrategy(kind="dom_anchor", params={"css": '[name="amount"], [id="amount"]'}, rank=0, rationale="r")]
    res = await eng.resolve(spec, adapter, h, artifact_version=1, surface_fingerprint="fp-amount", step_index=0)
    assert res.ok, res.error


async def test_two_visible_matches_stay_genuinely_ambiguous(adapter, mockbank):
    """The hidden-placeholder heuristic must not paper over a REAL duplicate —
    two equally visible, unrelated controls sharing a name/id still fail
    closed rather than guessing."""
    h = await adapter.open_session(f"{mockbank}/search")
    page = adapter._sess(h).page
    await page.set_content(
        '<input type="text" name="amount" value="one">'
        '<input type="text" id="amount" value="two">'
    )
    outcome = await adapter.try_strategy(h, "dom_anchor", {"css": '[name="amount"], [id="amount"]'})
    assert outcome["matched"] is False
    assert "ambiguous" in outcome["reason"]


async def test_relative_to_landmark_falls_back_to_a_div_card_when_no_table_row_exists(adapter, mockbank):
    """relative_to_landmark's <tr> ancestor lookup only covers legacy table
    layouts. A modern e-commerce product grid (SauceDemo: <div
    class="inventory_item"><h4>Sauce Labs Backpack</h4>...<button>Add to
    cart</button></div>) has no <tr> ancestor at all, so it used to resolve
    to nothing ('no element matched') even though there's an obvious
    per-card control to fall back to, the div/card equivalent of a table row.
    Found live-testing saucedemo_purchase_item: the recorded 'near=Sauce
    Labs Backpack' step hard-failed on replay."""
    h = await adapter.open_session(f"{mockbank}/search")
    page = adapter._sess(h).page
    await page.set_content(
        """
        <div class="inventory_list">
          <div class="inventory_item">
            <div class="inventory_item_name">Sauce Labs Bike Light</div>
            <button>Add to cart</button>
          </div>
          <div class="inventory_item">
            <div class="inventory_item_name">Sauce Labs Backpack</div>
            <button>Add to cart</button>
          </div>
        </div>
        """
    )
    outcome = await adapter.try_strategy(h, "relative_to_landmark", {"near": "Sauce Labs Backpack"})
    assert outcome["matched"] is True, outcome
    assert outcome["describe"] == "near='Sauce Labs Backpack'"

    eng = LocatorResolutionEngine()
    spec = [LocatorStrategy(kind="relative_to_landmark", params={"near": "Sauce Labs Backpack"}, rank=0, rationale="r")]
    res = await eng.resolve(spec, adapter, h, artifact_version=1, surface_fingerprint="fp-card", step_index=0)
    assert res.ok, res.error

    r = await adapter.execute(
        h, Action(type=ActionType.CLICK, target_description={"near": "Sauce Labs Backpack"})
    )
    assert r.ok, r.error


async def test_cache_singleflight(adapter, mockbank):
    h = await adapter.open_session(f"{mockbank}/search")
    calls = {"n": 0}
    real = adapter.try_strategy

    async def counting(*a, **k):
        calls["n"] += 1
        return await real(*a, **k)

    adapter.try_strategy = counting  # type: ignore[assignment]
    eng = LocatorResolutionEngine()
    spec = [LocatorStrategy(kind="role_name", params={"role": "button", "name": "Search"}, rank=0, rationale="r")]

    import asyncio

    results = await asyncio.gather(*[
        eng.resolve(spec, adapter, h, artifact_version=2, surface_fingerprint="fpX", step_index=0)
        for _ in range(5)
    ])
    assert all(r.ok for r in results)
    assert calls["n"] == 1  # coalesced: one real resolution for five concurrent callers

    dropped = eng.invalidate(fingerprint="fpX")
    assert dropped == 1


async def test_cache_key_isolates_artifacts_and_slots(adapter, mockbank):
    """Two v1 capabilities on the same-fingerprint page at the same step_index
    must not share a cached resolution (real regression: mb_open_subaccount
    replaying mb_read_savings's step-3 `near=Savings` locator)."""
    h = await adapter.open_session(f"{mockbank}/member/12345?ack=1")
    eng = LocatorResolutionEngine()
    a_spec = [LocatorStrategy(kind="relative_to_landmark", params={"near": "Savings"}, rank=0, rationale="r")]
    b_spec = [LocatorStrategy(kind="role_name", params={"role": "link", "name": "Open a new sub-account"}, rank=0, rationale="r")]

    ra = await eng.resolve(a_spec, adapter, h, artifact_id="A", artifact_version=1,
                           surface_fingerprint="fpM", step_index=3)
    rb = await eng.resolve(b_spec, adapter, h, artifact_id="B", artifact_version=1,
                           surface_fingerprint="fpM", step_index=3)
    assert ra.ok and rb.ok
    assert ra.matched_strategy != rb.matched_strategy  # not the cached A result

    # same artifact+fp+step but a recover slot is also distinct
    rc = await eng.resolve(b_spec, adapter, h, artifact_id="B", artifact_version=1,
                           surface_fingerprint="fpM", step_index=3, slot="recover:x")
    assert rc.ok
