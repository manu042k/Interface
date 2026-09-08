"""ST-022/025/026: Artifact Store.

SQLite here for zero-setup; the access pattern (strong consistency, transactional
versioning, base/override lookup) is exactly Postgres's and the swap is confined
to this file (ADR-03/04). Rows are immutable except for the review-state
transition draft -> approved|rejected.

Versioning: a new draft for an existing `name` (same vendor_app_id + tenant
scope) gets `max(version)+1`; prior versions stay readable. Replay always pins an
explicit `version` — never "latest".
"""

from __future__ import annotations

import sqlite3
import threading
import time
from enum import StrEnum
from pathlib import Path

from ..models import ArtifactStatus, CapabilityArtifact

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id     TEXT NOT NULL,
    version         INTEGER NOT NULL,
    name            TEXT NOT NULL,
    vendor_app_id   TEXT NOT NULL,
    app_version     TEXT NOT NULL,
    scope_kind      TEXT NOT NULL,          -- base | override
    tenant_id       TEXT,                   -- null for base
    overrides_base_version INTEGER,
    status          TEXT NOT NULL,
    body            TEXT NOT NULL,          -- full CapabilityArtifact JSON (redacted)
    created_at      REAL NOT NULL,
    reviewed_by     TEXT,
    reviewed_at     REAL,
    review_notes    TEXT,
    PRIMARY KEY (artifact_id, version)
);
CREATE INDEX IF NOT EXISTS ix_art_name ON artifacts(name, vendor_app_id, scope_kind, tenant_id);
CREATE INDEX IF NOT EXISTS ix_art_status ON artifacts(status);
"""


class PromotionDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


class ArtifactStore:
    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        return con

    # -- write ------------------------------------------------------
    def save_draft(self, artifact: CapabilityArtifact) -> CapabilityArtifact:
        """Persist as a new DRAFT version. Mutates artifact.version in place."""
        with self._lock, self._connect() as con:
            scope = artifact.tenant_scope
            row = con.execute(
                """SELECT COALESCE(MAX(version), 0) AS v FROM artifacts
                   WHERE name = ? AND vendor_app_id = ? AND scope_kind = ?
                   AND IFNULL(tenant_id,'') = IFNULL(?,'')""",
                (artifact.name, artifact.vendor_app_id, scope.kind, scope.tenant_id),
            ).fetchone()
            artifact.version = int(row["v"]) + 1
            artifact.status = ArtifactStatus.DRAFT
            con.execute(
                """INSERT INTO artifacts
                   (artifact_id, version, name, vendor_app_id, app_version, scope_kind,
                    tenant_id, overrides_base_version, status, body, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    artifact.artifact_id, artifact.version, artifact.name, artifact.vendor_app_id,
                    artifact.app_version, scope.kind, scope.tenant_id, scope.overrides_base_version,
                    artifact.status, artifact.model_dump_json(), time.time(),
                ),
            )
        return artifact

    def promote(
        self,
        artifact_id: str,
        version: int,
        decision: PromotionDecision | str,
        *,
        reviewer: str,
        notes: str | None = None,
    ) -> CapabilityArtifact:
        decision = PromotionDecision(decision)
        new_status = ArtifactStatus.APPROVED if decision == PromotionDecision.APPROVE else ArtifactStatus.REJECTED
        with self._lock, self._connect() as con:
            cur = con.execute(
                "SELECT body, status FROM artifacts WHERE artifact_id = ? AND version = ?",
                (artifact_id, version),
            ).fetchone()
            if cur is None:
                raise KeyError(f"no artifact {artifact_id} v{version}")
            if cur["status"] != ArtifactStatus.DRAFT:
                raise ValueError(f"artifact {artifact_id} v{version} is {cur['status']}, not draft")
            art = CapabilityArtifact.model_validate_json(cur["body"])
            art.status = new_status
            art.reviewed_by = reviewer
            art.reviewed_at = time.time()
            art.review_notes = notes
            con.execute(
                """UPDATE artifacts SET status = ?, reviewed_by = ?, reviewed_at = ?, review_notes = ?, body = ?
                   WHERE artifact_id = ? AND version = ?""",
                (new_status, reviewer, art.reviewed_at, notes, art.model_dump_json(), artifact_id, version),
            )
        return art

    def set_summary(self, artifact_id: str, version: int, summary: str) -> None:
        """Persist a record-time agent summary onto an existing row."""
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT body FROM artifacts WHERE artifact_id = ? AND version = ?",
                (artifact_id, version),
            ).fetchone()
            if row is None:
                return
            art = CapabilityArtifact.model_validate_json(row["body"])
            art.agent_summary = summary
            con.execute(
                "UPDATE artifacts SET body = ? WHERE artifact_id = ? AND version = ?",
                (art.model_dump_json(), artifact_id, version),
            )

    # -- read -------------------------------------------------------
    def get(self, artifact_id: str, version: int) -> CapabilityArtifact:
        with self._connect() as con:
            row = con.execute(
                "SELECT body FROM artifacts WHERE artifact_id = ? AND version = ?",
                (artifact_id, version),
            ).fetchone()
        if row is None:
            raise KeyError(f"no artifact {artifact_id} v{version}")
        return CapabilityArtifact.model_validate_json(row["body"])

    def list(
        self,
        *,
        status: str | None = None,
        name: str | None = None,
        vendor_app_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[CapabilityArtifact]:
        clauses = ["1=1"]
        args: list[object] = []
        for col, val in (("status", status), ("name", name), ("vendor_app_id", vendor_app_id)):
            if val:
                clauses.append(f"{col} = ?")
                args.append(val)
        if tenant_id is not None:
            clauses.append("IFNULL(tenant_id,'') = ?")
            args.append(tenant_id)
        q = f"SELECT body FROM artifacts WHERE {' AND '.join(clauses)} ORDER BY name, version"
        with self._connect() as con:
            rows = con.execute(q, args).fetchall()
        return [CapabilityArtifact.model_validate_json(r["body"]) for r in rows]

    def latest_approved(self, name: str, vendor_app_id: str = "generic") -> CapabilityArtifact | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT body FROM artifacts
                   WHERE name = ? AND vendor_app_id = ? AND status = ? AND scope_kind = 'base'
                   ORDER BY version DESC LIMIT 1""",
                (name, vendor_app_id, ArtifactStatus.APPROVED),
            ).fetchone()
        return CapabilityArtifact.model_validate_json(row["body"]) if row else None

    # -- de-dup on record ------------------------------------------
    def latest(self, name: str, vendor_app_id: str, *, scope_kind: str = "base") -> CapabilityArtifact | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT body FROM artifacts WHERE name = ? AND vendor_app_id = ? AND scope_kind = ?
                   ORDER BY version DESC LIMIT 1""",
                (name, vendor_app_id, scope_kind),
            ).fetchone()
        return CapabilityArtifact.model_validate_json(row["body"]) if row else None

    def find_by_fingerprint(
        self, name: str, vendor_app_id: str, fingerprint: str, *, scope_kind: str = "base"
    ) -> CapabilityArtifact | None:
        if not fingerprint:
            return None
        for a in self.list(name=name, vendor_app_id=vendor_app_id):
            if a.tenant_scope.kind == scope_kind and a.flow_fingerprint == fingerprint:
                return a
        return None

    def bump_confirmation(self, artifact_id: str, version: int, run_id: str | None = None) -> CapabilityArtifact:
        with self._lock, self._connect() as con:
            row = con.execute(
                "SELECT body FROM artifacts WHERE artifact_id = ? AND version = ?", (artifact_id, version)
            ).fetchone()
            if row is None:
                raise KeyError(f"no artifact {artifact_id} v{version}")
            a = CapabilityArtifact.model_validate_json(row["body"])
            a.confirmations += 1
            a.last_confirmed_at = time.time()
            con.execute(
                "UPDATE artifacts SET body = ? WHERE artifact_id = ? AND version = ?",
                (a.model_dump_json(), artifact_id, version),
            )
        return a

    def replace_draft(self, artifact: CapabilityArtifact, *, artifact_id: str, version: int) -> CapabilityArtifact:
        """Overwrite an un-reviewed draft's body in place (keeps its id + version)."""
        with self._lock, self._connect() as con:
            cur = con.execute(
                "SELECT status FROM artifacts WHERE artifact_id = ? AND version = ?", (artifact_id, version)
            ).fetchone()
            if cur is None:
                raise KeyError(f"no artifact {artifact_id} v{version}")
            if cur["status"] != ArtifactStatus.DRAFT:
                raise ValueError("replace_draft only overwrites an un-reviewed draft")
            artifact.artifact_id = artifact_id
            artifact.version = version
            artifact.status = ArtifactStatus.DRAFT
            con.execute(
                "UPDATE artifacts SET body = ?, name = ?, created_at = ? WHERE artifact_id = ? AND version = ?",
                (artifact.model_dump_json(), artifact.name, time.time(), artifact_id, version),
            )
        return artifact

    # -- ST-026: base/override resolution ------------------------------
    def resolve_for_tenant(
        self, name: str, tenant_id: str, *, vendor_app_id: str = "generic"
    ) -> CapabilityArtifact | None:
        """Tenant override (approved) first; fall back to approved base."""
        with self._connect() as con:
            ov = con.execute(
                """SELECT body FROM artifacts
                   WHERE name = ? AND vendor_app_id = ? AND status = ?
                   AND scope_kind = 'override' AND tenant_id = ?
                   ORDER BY version DESC LIMIT 1""",
                (name, vendor_app_id, ArtifactStatus.APPROVED, tenant_id),
            ).fetchone()
        if ov:
            override = CapabilityArtifact.model_validate_json(ov["body"])
            base = self.get(override.artifact_id, override.tenant_scope.overrides_base_version or 1) \
                if override.tenant_scope.overrides_base_version else None
            return _merge_override(base, override) if base else override
        return self.latest_approved(name, vendor_app_id)


def _merge_override(base: CapabilityArtifact, override: CapabilityArtifact) -> CapabilityArtifact:
    """Thin layering: an override only needs to declare steps that differ (by
    step_index) plus any schema/checkpoint changes. Unspecified steps inherit."""
    merged = base.model_copy(deep=True)
    by_index = {s.step_index: s for s in override.steps}
    merged.steps = [by_index.get(s.step_index, s) for s in merged.steps]
    if override.checkpoint and override.checkpoint.kind:
        merged.checkpoint = override.checkpoint
    merged.known_outcomes = override.known_outcomes or merged.known_outcomes
    merged.recoverable_rules = override.recoverable_rules or merged.recoverable_rules
    merged.tenant_scope = override.tenant_scope
    merged.artifact_id = override.artifact_id
    merged.version = override.version
    merged.status = override.status
    return merged
