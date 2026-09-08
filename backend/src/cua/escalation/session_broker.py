"""ST-036: Session Broker — the control lock.

Exactly one of {automation, human} holds a session's input lock at a time.
Acquisition is a compare-and-swap against a session record carrying
`held_by` + `lease_expires_at` — a TTL lease, not a mutex that a crashed worker
can orphan. A stale lease is reaped and the session becomes acquirable again.

In-memory here (single-process monolith); the same interface over a Postgres row
or a Redis lease is the scale-out swap.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum


class Holder(StrEnum):
    NONE = "none"
    AUTOMATION = "automation"
    HUMAN = "human"


@dataclass
class Lease:
    session_id: str
    holder: Holder
    token: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


@dataclass
class ControlLock:
    session_id: str
    held_by: Holder = Holder.NONE
    token: str | None = None
    lease_expires_at: float = 0.0

    def is_free(self, now: float) -> bool:
        return self.held_by == Holder.NONE or now >= self.lease_expires_at


class LockError(RuntimeError):
    pass


class SessionBroker:
    def __init__(self, *, default_ttl: float = 120.0) -> None:
        self._locks: dict[str, ControlLock] = {}
        self._ttl = default_ttl
        self._mu = threading.Lock()
        # session_id -> opaque live-session handle (adapter handle / CDP endpoint)
        self._handles: dict[str, str] = {}

    def register_session(self, session_id: str, handle: str) -> None:
        with self._mu:
            self._handles[session_id] = handle
            self._locks.setdefault(session_id, ControlLock(session_id))

    def session_handle(self, session_id: str) -> str | None:
        return self._handles.get(session_id)

    def holder(self, session_id: str) -> Holder:
        lock = self._locks.get(session_id)
        if lock is None:
            return Holder.NONE
        if lock.held_by != Holder.NONE and time.time() >= lock.lease_expires_at:
            return Holder.NONE  # lease lapsed
        return lock.held_by

    def acquire(self, session_id: str, holder: Holder, *, ttl: float | None = None) -> Lease:
        now = time.time()
        with self._mu:
            lock = self._locks.setdefault(session_id, ControlLock(session_id))
            if not lock.is_free(now) and lock.held_by != holder:
                raise LockError(
                    f"session {session_id} is held by {lock.held_by} until {lock.lease_expires_at:.0f}"
                )
            token = uuid.uuid4().hex
            lock.held_by = holder
            lock.token = token
            lock.lease_expires_at = now + (ttl or self._ttl)
            return Lease(session_id, holder, token, lock.lease_expires_at)

    def renew(self, lease: Lease, *, ttl: float | None = None) -> Lease:
        with self._mu:
            lock = self._locks.get(lease.session_id)
            if lock is None or lock.token != lease.token:
                raise LockError("cannot renew: lease no longer valid")
            lock.lease_expires_at = time.time() + (ttl or self._ttl)
            return Lease(lease.session_id, lock.held_by, lock.token, lock.lease_expires_at)

    def release(self, lease: Lease) -> None:
        with self._mu:
            lock = self._locks.get(lease.session_id)
            if lock is None:
                return
            if lock.token != lease.token:
                raise LockError("cannot release: not the current lease holder")
            lock.held_by = Holder.NONE
            lock.token = None
            lock.lease_expires_at = 0.0

    def reap_expired(self) -> list[str]:
        now = time.time()
        reaped: list[str] = []
        with self._mu:
            for sid, lock in self._locks.items():
                if lock.held_by != Holder.NONE and now >= lock.lease_expires_at:
                    lock.held_by = Holder.NONE
                    lock.token = None
                    reaped.append(sid)
        return reaped
