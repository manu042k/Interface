"""Semantic duplicate detection (cua.dedup): a differently-worded re-recording
of the same function is flagged for review even when its fingerprint differs."""

from __future__ import annotations

from cua import dedup
from cua.artifact.store import ArtifactStore
from cua.models import CapabilityArtifact, Condition, LocatorStrategy, Step

_LOC = [LocatorStrategy(kind="text", params={"text": "x"}, rank=0, rationale="test")]


def _mk_step(i, action, param):
    kw = dict(step_index=i, action_type=action, description=action, idempotent=True)
    if action not in ("assert_state", "navigate"):
        kw["locator_spec"] = _LOC
    if action in ("type", "select"):
        kw["value_binding"] = {"param": param} if param else {"literal": "x"}
    if action == "assert_state":
        kw["step_checkpoint"] = Condition(kind="text_present", params={"any": ["ok"]})
    return Step(**kw)


def _art(name, *, entry, keys, risk="safe_reversible", steps, checkpoint):
    return CapabilityArtifact(
        name=name, vendor_app_id="acme", goal_description=f"do {name}",
        entry_url=entry,
        input_schema={"type": "object", "properties": {k: {"type": "string"} for k in keys}},
        steps=[_mk_step(i, a, p) for i, (a, p) in enumerate(steps)],
        checkpoint=checkpoint,
    )


_OPEN_SHARE_A = dict(
    entry="https://x.test/signon", keys=["operator", "password", "member", "share_type", "deposit"],
    risk="risky_irreversible",
    steps=[("type", "operator"), ("type", "password"), ("click", None), ("click", None),
           ("type", "member"), ("click", None), ("click", None), ("click", None),
           ("select", "share_type"), ("type", "deposit"), ("click", None), ("click", None),
           ("assert_state", None)],
    checkpoint=Condition(kind="text_present", params={"any": ["SHARE OPENED"]}),
)
# same function, one fewer nav click + different asserted text
_OPEN_SHARE_B = dict(
    entry="https://x.test/signon", keys=["operator", "password", "member", "share_type", "deposit"],
    risk="risky_irreversible",
    steps=[("type", "operator"), ("type", "password"), ("click", None),
           ("type", "member"), ("click", None), ("click", None), ("click", None),
           ("select", "share_type"), ("type", "deposit"), ("click", None), ("click", None),
           ("assert_state", None)],
    checkpoint=Condition(kind="text_present", params={"any": ["new share is active"]}),
)


def test_structural_overlap_strong_for_same_function_different_path():
    a = _art("open_share", **_OPEN_SHARE_A)
    b = _art("add_account", **_OPEN_SHARE_B)
    ov = dedup.structural_overlap(a, b)
    assert ov["same_entry"] and ov["same_keys"] and ov["same_risk"]
    assert ov["same_checkpoint"]  # both text_present, values ignored
    assert ov["action_jaccard"] >= 0.8
    assert ov["strong"] is True


def test_structural_overlap_not_strong_for_a_genuinely_different_capability():
    a = _art("open_share", **_OPEN_SHARE_A)
    login = _art(
        "sign_on", entry="https://x.test/signon", keys=["operator", "password"],
        steps=[("type", "operator"), ("type", "password"), ("click", None)],
        checkpoint=Condition(kind="url_matches", params={"pattern": "/menu"}),
    )
    ov = dedup.structural_overlap(a, login)
    assert ov["strong"] is False
    assert ov["same_keys"] is False


async def test_semantic_twin_flags_a_structural_match_without_a_model(tmp_path):
    store = ArtifactStore(tmp_path / "s.db")
    existing = store.save_draft(_art("open_share", **_OPEN_SHARE_A))
    store.promote(existing.artifact_id, existing.version, "approve", reviewer="x")
    new = _art("add_account", **_OPEN_SHARE_B)

    hit = await dedup.semantic_twin(new, store, router=None)  # no router needed
    assert hit is not None
    twin, basis, note = hit
    assert twin.name == "open_share"
    assert basis == "structural"
    assert "open_share@v1" in note


async def test_semantic_twin_asks_the_model_only_for_a_partial_match(tmp_path):
    store = ArtifactStore(tmp_path / "s.db")
    # same entry + same keys but a very different step shape -> "worth asking"
    member_lookup = _art(
        "member_lookup", entry="https://x.test/signon",
        keys=["operator", "password", "member", "share_type", "deposit"],
        steps=[("type", "operator"), ("type", "password"), ("click", None), ("type", "member"),
               ("click", None), ("click", None)],
        checkpoint=Condition(kind="text_present", params={"any": ["MEMBER RECORD"]}),
    )
    store.save_draft(member_lookup)
    new = _art("add_account", **_OPEN_SHARE_B)

    calls = {"n": 0}

    class FakeRouter:
        async def call_text(self, system, user):
            calls["n"] += 1
            return "DIFFERENT\nopening a share is not the same as looking a member up"

    hit = await dedup.semantic_twin(new, store, router=FakeRouter())
    assert calls["n"] == 1  # it asked
    assert hit is None      # model said different -> not flagged


async def test_semantic_twin_llm_says_same(tmp_path):
    store = ArtifactStore(tmp_path / "s.db")
    lookup = _art(
        "share_wizard", entry="https://x.test/signon",
        keys=["operator", "password", "member", "share_type", "deposit"],
        steps=[("type", "operator"), ("type", "password"), ("click", None), ("type", "member"),
               ("click", None), ("click", None), ("select", "share_type")],
        checkpoint=Condition(kind="text_present", params={"any": ["WIZARD"]}),
    )
    store.save_draft(lookup)
    new = _art("add_account", **_OPEN_SHARE_B)

    class FakeRouter:
        async def call_text(self, system, user):
            return "SAME\nboth open a new share for a member with an initial deposit"

    hit = await dedup.semantic_twin(new, store, router=FakeRouter())
    assert hit is not None
    twin, basis, note = hit
    assert basis == "llm" and twin.name == "share_wizard"
    assert "same function" in note


def test_store_set_duplicate_of_flags_and_annotates(tmp_path):
    store = ArtifactStore(tmp_path / "s.db")
    a = store.save_draft(_art("add_account", **_OPEN_SHARE_B))
    out = store.set_duplicate_of(a.artifact_id, a.version, "open_share@v1", note="looks identical")
    assert out.duplicate_of == "open_share@v1"
    assert out.record_outcome == "duplicate"
    assert "looks identical" in (out.review_notes or "")
    # persisted
    assert store.get(a.artifact_id, a.version).duplicate_of == "open_share@v1"


async def test_semantic_twin_ranks_candidates_by_signal_before_the_model_budget(tmp_path):
    """With more 'worth asking' candidates than the model-call budget (3), the
    real twin (same input keys) must be asked — not dropped by name order."""
    store = ArtifactStore(tmp_path / "s.db")

    # four decoys sharing only the entry URL + partial step overlap (no keys)
    for nm in ("aaa_first", "bbb_second", "ccc_third", "ddd_fourth"):
        store.save_draft(_art(
            nm, entry="https://x.test/signon", keys=["operator", "password"],
            steps=[("type", "operator"), ("type", "password"), ("click", None), ("type", "member"),
                   ("click", None), ("select", "share_type"), ("type", "deposit")],
            checkpoint=Condition(kind="url_matches", params={"pattern": "/x"}),
        ))
    # the true twin: same entry AND same full key set, but a different-KIND
    # checkpoint from `new` (text_present) so it's "worth asking", not "strong"
    store.save_draft(_art("zzz_real_twin", **{**_OPEN_SHARE_A,
                                              "checkpoint": Condition(kind="url_matches",
                                                                      params={"pattern": "/ok"})}))

    new = _art("add_account", **_OPEN_SHARE_B)
    asked: list[str] = []

    class FakeRouter:
        async def call_text(self, system, user):
            # capture which existing capability each call is about
            asked.append(user.split("existing: ")[1].split(")")[0])
            return "SAME\nsame function" if "zzz_real_twin" in user else "DIFFERENT\nno"

    hit = await dedup.semantic_twin(new, store, router=FakeRouter())
    assert hit is not None and hit[0].name == "zzz_real_twin"
    assert asked[0] == "zzz_real_twin"  # strongest signal asked first
    assert len(asked) <= 3
