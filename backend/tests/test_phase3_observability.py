"""Phase 3 — ST-013..ST-015."""

from __future__ import annotations

from cua.events import RunLogger
from cua.observability import FileSink


def test_run_log_is_ordered_and_carries_reasoning(tmp_path):
    sink = FileSink(tmp_path)
    log = RunLogger(sink, "runA")
    log.run_started("discovery", "http://127.0.0.1:8799/search", goal="look up member 12345")
    log.decision(0, "type", {"target": {"role": "textbox"}, "value": "12345"}, "the search box is the only text input")
    log.action(0, "type", "textbox", True, matched_strategy="role=textbox")
    log.checkpoint(0, True, "search field populated")
    events = sink.read_events("runA")
    kinds = [e["event"] for e in events]
    assert kinds == ["run_started", "decision", "action", "checkpoint"]
    assert events[1]["reasoning"].startswith("the search box")
    assert [e["step"] for e in events if e["step"] is not None] == [0, 0, 0]


def test_evidence_only_on_failure_points(tmp_path):
    sink = FileSink(tmp_path)
    log = RunLogger(sink, "runB")
    # happy steps: no evidence
    for i in range(3):
        log.action(i, "click", "x", True)
    # failure: capture a screenshot + dom
    ref = log.evidence_screenshot(3, b"\x89PNGfake", {"url": "http://x/y"})
    log.evidence_dom(3, "<html>state</html>", {"url": "http://x/y"})
    blobs = list(tmp_path.glob("runB/*"))
    names = sorted(p.name for p in blobs)
    assert ref in names
    # exactly the events log + 1 png + 1 png.meta + 1 html + 1 html.meta
    assert any(n.endswith(".png") for n in names)
    assert any(n.endswith(".html") for n in names)
    assert sum(1 for n in names if n.endswith(".png") or n.endswith(".html")) == 2


def test_write_path_redacts(tmp_path):
    sink = FileSink(tmp_path)
    log = RunLogger(sink, "runC")
    log.decision(0, "type", {"target": {"label": "Password"}, "value": "s3cr3tPassw0rd"}, "login form")
    log.action(0, "type", "Password", True, sent_value="password=s3cr3tPassw0rd")
    blob = (tmp_path / "runC" / "events.jsonl").read_text()
    assert "s3cr3tPassw0rd" not in blob
    assert "REDACTED" in blob
