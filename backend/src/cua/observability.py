"""ST-004 / ST-013 / ST-014 / ST-015: Observability Sink.

Local-disk implementation of a storage-agnostic interface:
  logEvent(run_id, step, event)   -> ordered, timestamped, redacted structured log
  putEvidence(run_id, step, blob) -> screenshot / DOM snapshot / trace blob

Two guarantees from the TDD's sync/async split (§1.3):
  * logging is fire-and-forget — a slow or broken sink never blocks or crashes
    the caller on the live-surface hot path;
  * everything is redacted (ST-015) on the way to disk.

Swap `FileSink` for an object-store + append-only-log-DB sink in production
(ADR-03/04) — callers only see the `Sink` protocol.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Protocol

from .redaction import redact


class Sink(Protocol):
    def log_event(self, run_id: str, step: int | None, event: dict[str, Any]) -> None: ...
    def put_evidence(
        self, run_id: str, step: int | None, kind: str, data: bytes, meta: dict[str, Any] | None = None
    ) -> str: ...
    def read_events(self, run_id: str) -> list[dict[str, Any]]: ...


class FileSink:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._locks: dict[str, threading.Lock] = {}
        self._global = threading.Lock()

    # -- internals ---------------------------------------------------------
    def _run_dir(self, run_id: str) -> Path:
        d = self.root / run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _lock_for(self, run_id: str) -> threading.Lock:
        with self._global:
            return self._locks.setdefault(run_id, threading.Lock())

    # -- ST-013 / ST-015: structured, ordered, redacted event log ---------
    def log_event(self, run_id: str, step: int | None, event: dict[str, Any]) -> None:
        try:
            record = {
                "ts": time.time(),
                "run_id": run_id,
                "step": step,
                **redact(event),
            }
            line = json.dumps(record, default=str, ensure_ascii=False)
            path = self._run_dir(run_id) / "events.jsonl"
            with self._lock_for(run_id):
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
        except Exception:  # noqa: BLE001 — logging must never take down the caller
            # Last-ditch: drop to stderr, keep going.
            traceback.print_exc()

    # -- ST-014: richer evidence, gated to failure/checkpoint by callers --
    def put_evidence(
        self, run_id: str, step: int | None, kind: str, data: bytes, meta: dict[str, Any] | None = None
    ) -> str:
        try:
            ext = {"screenshot": "png", "dom": "html", "trace": "zip"}.get(kind, "bin")
            name = f"step{step if step is not None else 'x'}-{kind}-{int(time.time()*1000)}.{ext}"
            path = self._run_dir(run_id) / name
            path.write_bytes(data)
            if meta is not None:
                (path.with_suffix(path.suffix + ".meta.json")).write_text(
                    json.dumps(redact(meta), default=str, indent=2), encoding="utf-8"
                )
            self.log_event(run_id, step, {"event": "evidence", "kind": kind, "ref": name})
            return name
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return ""

    def read_events(self, run_id: str) -> list[dict[str, Any]]:
        path = self.root / run_id / "events.jsonl"
        if not path.exists():
            return []
        out: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out


class NullSink:
    """No-op sink for unit tests that don't assert on logs."""

    def log_event(self, *_: Any, **__: Any) -> None:  # noqa: D401
        pass

    def put_evidence(self, *_: Any, **__: Any) -> str:
        return ""

    def read_events(self, run_id: str) -> list[dict[str, Any]]:
        return []
