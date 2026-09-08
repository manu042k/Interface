"""A tiny screen-driven rule engine used as `ScriptedProvider(fallback=...)` so
the system runs end-to-end with NO model key — for tests and the offline demo.

It is deliberately not an "agent": it recognizes a handful of MockBank screens for
two goals (read a savings balance; open a sub-account to the confirmation screen)
and returns the obvious next tool call. A real discovery run uses a real model
via the LLM router; this keeps CI hermetic and lets reviewers see the full
pipeline without credentials.
"""

from __future__ import annotations

import re
from typing import Any

_MEMBER_ID_RE = re.compile(r"member\s+#?(\d{3,6})", re.I)
_ACCT_TYPE_RE = re.compile(r"(?:type|as a)\s+['\"]?([A-Z][A-Za-z ]+?)['\"]?(?:\s+sub-?account|\s*$|,)", re.I)


def _url_line(user: str) -> str:
    m = re.search(r"^\s*url:\s*(.+)$", user, re.M)
    return (m.group(1).strip() if m else "")


def offline_fallback(system: str, user: str) -> tuple[str, dict[str, Any], str]:
    url = _url_line(user)
    goal_m = re.search(r"^GOAL:\s*(.+)$", user, re.M)
    goal = goal_m.group(1) if goal_m else ""
    mid_m = _MEMBER_ID_RE.search(goal)
    member_id = mid_m.group(1) if mid_m else "12345"

    typed_already = "type into" in user
    opened_record = bool(re.search(r"/member/\d+", url))
    on_search = url.endswith("/search")
    on_members_list = "/members" in url
    notice = "Session Notice" in user
    balance_extracted = "extract savings_balance" in user
    wants_balance = "balance" in goal.lower()
    wants_sub_account = "sub-account" in goal.lower() or "sub account" in goal.lower()

    # 0. dismiss the unexpected interstitial
    if notice:
        return "click", {"target": {"text": "Acknowledge and continue"}}, "dismiss the session-notice interstitial"

    # 1. search
    if on_search and not typed_already:
        return "type", {"target": {"role": "textbox"}, "value": member_id}, "enter the member id in the search box"
    if on_search and typed_already:
        return "click", {"target": {"role": "button", "name": "Search"}}, "submit the member search"
    if on_members_list and "Open" in user and not opened_record:
        return "click", {"target": {"text": "Open"}}, "open the matching member record"

    # 2a. read a balance
    asserted = "assert_state {" in user
    if wants_balance and opened_record and not balance_extracted:
        return ("extract",
                {"target": {"near": "Savings"}, "expected_shape": "currency", "as": "savings_balance"},
                "read the savings balance from the balances table")
    if wants_balance and balance_extracted and not asserted:
        return ("assert_state",
                {"condition": {"kind": "text_present", "params": {"text": "Savings"}}},
                "confirm we are on the member record showing the balance")
    if wants_balance and balance_extracted and asserted:
        return "done", {"outputs": {"savings_balance": "<from extract step>"}}, "goal reached: balance read"

    # 2b. open a sub-account
    if wants_sub_account and opened_record and "New Sub-Account" not in user and "Open a new sub-account" in user:
        return "click", {"target": {"text": "Open a new sub-account"}}, "start the new sub-account flow"
    if "New Sub-Account" in user and "select" not in user.lower().split("action history")[-1]:
        acct = _ACCT_TYPE_RE.search(goal)
        acct_type = acct.group(1).strip() if acct else "Holiday Club"
        return "select", {"target": {"label": "Account type"}, "option": acct_type}, "choose the requested account type"
    if "New Sub-Account" in user and "Review" not in user.split("ACTION HISTORY")[-1]:
        return "click", {"target": {"role": "button", "name": "Review"}}, "submit the sub-account form"
    if "Sub-account created" in user:
        return ("assert_state",
                {"condition": {"kind": "text_present", "params": {"text": "Confirmation number"}}},
                "confirm we reached the confirmation screen")

    # 3. done
    if wants_balance:
        return "done", {"outputs": {"savings_balance": "<from extract step>"}}, "goal reached: balance read"
    if wants_sub_account and "Sub-account created" in user:
        return "done", {"outputs": {"confirmation_visible": True}}, "goal reached: confirmation screen"

    return "stuck", {"reason": f"offline pilot did not recognize screen: {url}", "context": {}}, "unrecognized screen"
