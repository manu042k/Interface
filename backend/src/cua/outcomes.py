"""Per-host exceptional-state library.

A hand-authored `BusinessOutcomeRule` / `RecoverableRule` needs someone to have
watched the exact page happen live. But a given host renders the SAME not-found /
permission / maintenance / timeout / app-error pages no matter which business
flow reached them — so one entry per host condition covers every capability
recorded against that host, instead of hand-fixing each capability the first time
someone happens to trigger the state.

The recorder merges the entry for `urlparse(entry_url).hostname` into every
artifact it compiles (`_seed_business_outcomes` / `_seed_recoverables`), on top of
the generic flow-shape seeds. Unknown host -> empty, same as before this existed.

Populate `_LIBRARY` with one entry per host you record against, keyed by
`urlparse(entry_url).hostname`. Each value is a `{"business_outcomes": fn,
"recoverables": fn}` pair returning freshly-built rule lists.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .models import (  # noqa: F401 - re-exported for host library authors
    BusinessOutcomeRule,
    Condition,
    LocatorStrategy,
    RecoverableRule,
)

_LIBRARY: dict[str, dict[str, object]] = {}


def _host_of(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower()


def business_outcomes_for(entry_url: str) -> list[BusinessOutcomeRule]:
    lib = _LIBRARY.get(_host_of(entry_url))
    return lib["business_outcomes"]() if lib else []  # type: ignore[operator]


def recoverables_for(entry_url: str) -> list[RecoverableRule]:
    lib = _LIBRARY.get(_host_of(entry_url))
    return lib["recoverables"]() if lib else []  # type: ignore[operator]
