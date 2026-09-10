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

the legacy console also exposes deliberate fault injection (see /settings): a random
error rate on posting actions, a force-mode toggle, and per-request
`?inject=validation|notfound|permission|timeout|maintenance|server`. Every
pattern below is confirmed against both the natural state and its injected twin.
"""

from __future__ import annotations

from urllib.parse import urlparse

from .models import (
    BusinessOutcomeRule,
    Condition,
    LocatorStrategy,
    RecoverableRule,
)


def _legacy_core_business_outcomes() -> list[BusinessOutcomeRule]:
    return [
        BusinessOutcomeRule(
            code="member_not_found",
            when=Condition(kind="text_present", params={"any": [
                "RECORD NOT FOUND", "No member records matched your search",
                "no member records matched",
            ]}),
            message="The requested member record does not exist on this host.",
        ),
        BusinessOutcomeRule(
            # Anchor on the denial SENTENCE, never on the "SUPERVISOR OVERRIDE
            # REQUIRED" banner — that banner is also a "RESTRICTED FUNCTION"
            # label on the legitimate Place Account Hold form, so anchoring
            # there flags an authorized super1 as denied before the flow runs.
            code="permission_denied",
            when=Condition(kind="text_present", params={"any": [
                "is not authorized to perform this function",
                "not authorized to perform this function",
                "Authorization Required", "a supervisor must sign on",
            ]}),
            message="The signed-on operator is not authorized; a supervisor override is required.",
        ),
        BusinessOutcomeRule(
            code="signon_rejected",
            when=Condition(kind="text_present", params={"any": [
                "Invalid operator ID or password", "Sign-on failed",
            ]}),
            message="Sign-on was rejected: invalid operator ID or password.",
            from_step=0,
        ),
        BusinessOutcomeRule(
            # transaction-level validation (Funds Transfer, Place Hold)
            code="validation_error",
            when=Condition(kind="text_present", params={"any": [
                "transaction could not be validated", "TRANSACTION REJECTED",
                "cannot be debited", "is on hold", "share is HOLD",
                "insufficient", "invalid amount",
            ]}),
            message="the legacy console rejected the transaction by server-side validation.",
            detail_selector="font.err + ul, .err + ul, ul.errors",
        ),
        BusinessOutcomeRule(
            # field-level validation (Update Member Information)
            code="field_validation_error",
            when=Condition(kind="text_present", params={"any": [
                "Please correct the following", "is not in a valid format",
                "is required",
            ]}),
            message="the legacy console rejected the submitted field values.",
            detail_selector="font.err + ul, .err + ul, ul.errors",
        ),
    ]


def _legacy_core_recoverables() -> list[RecoverableRule]:
    return [
        RecoverableRule(
            name="session_notice_interstitial",
            when=Condition(kind="text_present", params={"any": [
                "Session Notice", "please acknowledge",
            ]}),
            action="dismiss",
            target=[LocatorStrategy(
                kind="text", params={"text": "Acknowledge and continue"}, rank=0,
                rationale="the interstitial's only continue affordance; stable literal label",
            )],
            settle=Condition(kind="text_absent", params={"text": "Session Notice"}),
        ),
        RecoverableRule(
            name="scheduled_maintenance_interstitial",
            when=Condition(kind="text_present", params={"any": [
                "SCHEDULED MAINTENANCE IN PROGRESS", "maintenance window",
            ]}),
            action="dismiss",
            target=[LocatorStrategy(
                kind="role_name", params={"role": "link", "name": "Continue"}, rank=0,
                rationale="the maintenance interstitial's Continue link — dismissible, stable label",
            )],
            settle=Condition(kind="text_absent", params={"text": "SCHEDULED MAINTENANCE"}),
            max_attempts=3,
        ),
        RecoverableRule(
            name="application_error_500",
            when=Condition(kind="text_present", params={"any": [
                "APPLICATION ERROR", "HTTP 500", "could not process the request",
            ]}),
            action="reload",
            settle=Condition(kind="text_absent", params={"any": [
                "APPLICATION ERROR", "could not process the request",
            ]}),
            max_attempts=2,
        ),
        # NOTE: "YOUR SESSION HAS TIMED OUT" is deliberately NOT a recoverable
        # here — recovering means re-authenticating and resuming the *remaining*
        # steps, which a single-Step recovery_action can't express. It surfaces
        # as a hard failure with a clear reason instead of a misclassified one.
    ]


_LIBRARY: dict[str, dict[str, object]] = {
    "legacy-core.example.com": {
        "business_outcomes": _legacy_core_business_outcomes,
        "recoverables": _legacy_core_recoverables,
    },
}


def _host_of(url: str) -> str:
    return (urlparse(url or "").hostname or "").lower()


def business_outcomes_for(entry_url: str) -> list[BusinessOutcomeRule]:
    lib = _LIBRARY.get(_host_of(entry_url))
    return lib["business_outcomes"]() if lib else []  # type: ignore[operator]


def recoverables_for(entry_url: str) -> list[RecoverableRule]:
    lib = _LIBRARY.get(_host_of(entry_url))
    return lib["recoverables"]() if lib else []  # type: ignore[operator]
