"""Durable record of runs.

Runs live in memory during a process (app.state.runs); this mirrors them to a
JSON file so `GET /runs` still lists past runs after a restart. Whole-map
rewrite on every change — fine at this scale, and an atomic replace keeps the
file consistent.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from ..models import RunRecord


class RunStore:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def load(self) -> dict[str, RunRecord]:
        if not self._path.exists():
            return {}
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a corrupt file must not crash startup
            return {}
        out: dict[str, RunRecord] = {}
        for rid, body in raw.items():
            try:
                out[rid] = RunRecord.model_validate(body)
            except Exception:  # noqa: BLE001 — skip rows an older schema can't load
                continue
        return out

    def save_all(self, runs: dict[str, RunRecord]) -> None:
        data = {rid: r.model_dump(mode="json") for rid, r in runs.items()}
        blob = json.dumps(data, separators=(",", ":"))
        with self._lock:
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(blob, encoding="utf-8")
            tmp.replace(self._path)
