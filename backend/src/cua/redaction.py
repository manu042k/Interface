"""ST-003 / ST-015 / ST-024: Secrets & Redaction Layer.

A pure function. Every write path (artifact store, log sink, evidence metadata)
runs payloads through `redact()` before persistence, so "never persist secrets"
is structural, not a convention people remember.

Design notes / known limits (flagged for the compliance owner, per TDD §5):
  * We redact FULL identifiers (full account/card/SSN). Partial/masked values
    (last-4, first name) are deliberately kept for debuggability — the exact
    line is a compliance decision, marked here, not guessed.
  * Screenshots are binary; we do NOT pixel-redact them. Evidence capture is
    gated to failure points only (ST-014) and screenshots of real account
    screens would need field-level masking before production — see REPORT.md Cuts.
"""

from __future__ import annotations

import re
from typing import Any

REDACTION_MARKER = "«REDACTED:{kind}»"

# Ordered (label, compiled pattern). First match wins per span.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Common secret-bearing keys in JSON-ish text: "password": "hunter2"
    ("secret", re.compile(
        r'("?(?:pass(?:word)?|passwd|pwd|secret|token|api[_-]?key|authorization|auth|bearer|'
        r'client[_-]?secret|private[_-]?key|session[_-]?id|cookie)"?\s*[:=]\s*)'
        r'("[^"]*"|\'[^\']*\'|[^\s,;}{]+)',
        re.IGNORECASE,
    )),
    # Bearer tokens
    ("token", re.compile(r"\bBearer\s+[A-Za-z0-9\-._~+/]+=*\b")),
    # JWT
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    # AWS-style access key id
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    # SSN (full)
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    # Full payment-card number (13-19 digits, optional separators). Luhn-checked below.
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    # Full bank account number: a long bare digit run (12-17). Shorter runs are
    # too often plain IDs; epoch timestamps are excluded in the substitution.
    ("account", re.compile(r"\b\d{12,17}\b")),
]


def _looks_like_epoch(digits: str) -> bool:
    """A 10-digit (~2001-2033) or 13-digit (ms) Unix timestamp — not an account."""
    if len(digits) == 10 and digits[0] == "1":
        return 1_000_000_000 <= int(digits) <= 2_000_000_000
    if len(digits) == 13 and digits[0] == "1":
        return 1_000_000_000_000 <= int(digits) <= 2_000_000_000_000
    return False

# Keys whose values we always redact wholesale, regardless of content.
_SENSITIVE_KEYS = {
    "password", "passwd", "pwd", "secret", "token", "api_key", "apikey",
    "authorization", "auth", "bearer", "client_secret", "private_key",
    "session_id", "cookie", "cookies", "set-cookie", "ssn", "tax_id",
    "card_number", "cardnumber", "pan", "cvv", "cvc", "pin",
    "full_account_number", "account_number",
}


def _luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if len(d) < 13:
        return False
    checksum = 0
    parity = len(d) % 2
    for i, n in enumerate(d):
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        checksum += n
    return checksum % 10 == 0


def redact_text(text: str) -> tuple[str, list[str]]:
    """Return (sanitized_text, [kinds_redacted])."""
    if not text:
        return text, []
    hits: list[str] = []
    out = text
    for kind, pat in _PATTERNS:
        def _sub(m: re.Match[str], _kind: str = kind) -> str:
            span = m.group(0)
            if _kind == "card":
                if not _luhn_ok(span):
                    return span  # not actually a card number — leave it
            if _kind == "account":
                digits = re.sub(r"\D", "", span)
                if len(digits) > 17 or _looks_like_epoch(digits):
                    return span  # timestamp / oversized run — not an account
            if _kind == "secret":
                # keep the key, redact only the value (group 2)
                hits.append(_kind)
                return m.group(1) + REDACTION_MARKER.format(kind=_kind)
            hits.append(_kind)
            return REDACTION_MARKER.format(kind=_kind)

        out = pat.sub(_sub, out)
    return out, hits


def redact(payload: Any) -> Any:
    """Recursively redact a JSON-serializable structure. Pure — no mutation."""
    sanitized, _ = _redact_with_report(payload)
    return sanitized


def redact_report(payload: Any) -> tuple[Any, list[str]]:
    """Same as redact() but also returns the list of redaction kinds applied."""
    return _redact_with_report(payload)


def _redact_with_report(payload: Any, _key: str | None = None) -> tuple[Any, list[str]]:
    kinds: list[str] = []
    if isinstance(payload, dict):
        out: dict[Any, Any] = {}
        for k, v in payload.items():
            key_l = str(k).strip().lower().replace("-", "_")
            # A sensitive key holding a scalar is the secret itself -> mask it
            # wholesale. A sensitive key holding a dict/list is a structural node
            # (e.g. a JSON-Schema fragment at `properties.password`), NOT the
            # value - recurse so we redact any leaf secrets without destroying
            # the shape a downstream validator depends on.
            if key_l in _SENSITIVE_KEYS and not isinstance(v, (dict, list, tuple)):
                out[k] = REDACTION_MARKER.format(kind="key:" + key_l)
                kinds.append("key:" + key_l)
                continue
            sv, sk = _redact_with_report(v, key_l)
            out[k] = sv
            kinds.extend(sk)
        return out, kinds
    if isinstance(payload, (list, tuple)):
        items = [_redact_with_report(v, _key) for v in payload]
        return [i[0] for i in items], [k for i in items for k in i[1]]
    if isinstance(payload, str):
        sv, sk = redact_text(payload)
        return sv, sk
    return payload, kinds
