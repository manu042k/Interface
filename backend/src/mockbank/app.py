"""MockBank Flask app. Deliberately non-semantic markup — see package docstring.

Run:  python -m mockbank.app  (or `mockbank` after `pip install -e .`)
Env:  MOCKBANK_HOST (default 127.0.0.1), MOCKBANK_PORT (default 8799)
"""

from __future__ import annotations

import os
import time
from html import escape

from flask import Flask, make_response, request

app = Flask(__name__)

# --- seed data -------------------------------------------------------------
# Full account numbers are here ON PURPOSE — the automation must never persist
# them into an artifact or log (redaction test surface).
MEMBERS: dict[str, dict[str, str]] = {
    "12345": {
        "name": "Dana Whitfield",
        "savings_balance": "$4,182.55",
        "checking_balance": "$1,204.10",
        "full_account_number": "4000123456789010",
        "status": "Active",
        "branch": "Elm Street",
    },
    "22222": {
        "name": "Marco Reyes",
        "savings_balance": "$19.00",
        "checking_balance": "$0.00",
        "full_account_number": "4000222222222226",
        "status": "Active",
        "branch": "Downtown",
    },
    "34567": {
        "name": "Priya Nadar",
        "savings_balance": "$102,930.42",
        "checking_balance": "$8,150.75",
        "full_account_number": "4000345678901231",
        "status": "Active",
        "branch": "Lakeview",
    },
}

SUB_ACCOUNT_TYPES = ["Regular Savings", "Holiday Club", "Money Market", "Youth Savings"]

_PAGE_HITS: dict[str, int] = {}

# --- adversarial edge-case state (per process) ----------------------------
# All opt-in via query params, so the default MockBank + existing tests are
# untouched. These reproduce the runtime conditions the brief calls out:
# validation errors, transient app errors, session expiry, unexpected
# confirmation steps, duplicate submission, and content/label drift.
_BOOMED: set[str] = set()          # {mid} that have already 500'd once
_CREATED: dict[tuple, str] = {}    # (mid, acct_type, amt) -> confirmation no.


def _sleep_from(req) -> None:
    """Honour ?slow=<seconds> (was a hardcoded 3s). Capped so a test can still
    kill it, but high enough that ?slow=20 blows a 15s action timeout."""
    try:
        s = float(req.args.get("slow") or 0)
    except ValueError:
        s = 0.0
    if s > 0:
        time.sleep(min(s, 30.0))


def _session_dead(req) -> bool:
    return req.cookies.get("coreserv_dead") == "1"


def _expired_page() -> str:
    return _p("Session Ended", _notice(
        "Your CoreServ session has ended (idle timeout). "
        "Return to Member Search and start again."
    ))


def _p(title: str, body: str) -> str:
    # Frameset-era doctype, table layout, no CSS classes, no test ids.
    return (
        "<!DOCTYPE html>\n<html><head><title>%s :: CoreServ 7.2</title></head>\n"
        "<body bgcolor=\"#f4f4ef\" text=\"#111111\">\n"
        "<table width=\"760\" border=\"1\" cellpadding=\"6\" cellspacing=\"0\" bgcolor=\"#dfe6ef\">\n"
        "<tr><td><font face=\"Verdana\" size=\"2\"><b>CoreServ Back-Office</b> &nbsp;|&nbsp; "
        "<a href=\"/\">Home</a> &nbsp; <a href=\"/search\">Member Search</a></font></td></tr>\n"
        "</table><br>\n%s\n</body></html>" % (escape(title), body)
    )


@app.after_request
def _no_cache(resp):  # keep replay observations honest
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def home() -> str:
    body = (
        "<table border=\"0\"><tr><td><font face=\"Verdana\" size=\"2\">"
        "Welcome to the CoreServ servicing console. Use "
        "<a href=\"/search\">Member Search</a> to locate an account."
        "</font></td></tr></table>"
    )
    return _p("Home", body)


@app.route("/search")
def search() -> str:
    body = (
        "<form method=\"GET\" action=\"/members\">\n"
        "<table border=\"1\" cellpadding=\"4\" cellspacing=\"0\">\n"
        "<tr><td><font face=\"Verdana\" size=\"2\">Member ID or name</font></td>\n"
        "<td><input type=\"text\" name=\"q\" size=\"28\"></td>\n"
        "<td><input type=\"submit\" name=\"go\" value=\"Search\"></td></tr>\n"
        "</table></form>\n"
        "<font face=\"Verdana\" size=\"1\">Known test IDs: 12345, 22222, 34567. "
        "Others exercise alternate paths.</font>"
    )
    return _p("Member Search", body)


@app.get("/expired")
def expired() -> str:
    return _expired_page()


@app.route("/members")
def members():
    if _session_dead(request):
        return _expired_page(), 440
    q = (request.args.get("q") or "").strip()
    _sleep_from(request)

    # EDGE: a decoy "Open record" link (points at Home) rendered BEFORE the real
    # one, to try to make the agent/locator click the wrong link.
    decoy = request.args.get("decoylink") == "1"

    if not q:
        return _p("Search Results", _notice("Please enter a member ID or name."))

    hit_id = None
    if q in MEMBERS:
        hit_id = q
    else:
        for mid, m in MEMBERS.items():
            if q.lower() in m["name"].lower():
                hit_id = mid
                break

    if q == "99999":
        return _p("Search Results", _notice(
            "This member record is restricted. You do not have permission to view it."
        ))

    if hit_id is None:
        # legitimate business outcome, NOT an error page
        body = (
            "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" width=\"600\">\n"
            "<tr bgcolor=\"#eeeeee\"><td><font face=\"Verdana\" size=\"2\"><b>Search Results</b></font></td></tr>\n"
            "<tr><td><font face=\"Verdana\" size=\"2\">No members matched "
            f"&quot;{escape(q)}&quot;.</font></td></tr>\n</table>"
        )
        return _p("Search Results", body)

    m = MEMBERS[hit_id]
    decoy_row = (
        "<tr><td>&nbsp;</td><td>&nbsp;</td>"
        "<td><a href=\"/\">Open&nbsp;record</a></td></tr>\n" if decoy else ""
    )
    body = (
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" width=\"600\">\n"
        "<tr bgcolor=\"#eeeeee\"><td colspan=\"3\"><font face=\"Verdana\" size=\"2\"><b>1 match</b></font></td></tr>\n"
        "<tr><td><font face=\"Verdana\" size=\"2\">ID</font></td>"
        "<td><font face=\"Verdana\" size=\"2\">Name</font></td><td>&nbsp;</td></tr>\n"
        f"{decoy_row}"
        f"<tr><td><font face=\"Verdana\" size=\"2\">{hit_id}</font></td>"
        f"<td><font face=\"Verdana\" size=\"2\">{escape(m['name'])}</font></td>"
        f"<td><a href=\"/member/{hit_id}\">Open&nbsp;record</a></td></tr>\n</table>"
    )
    return _p("Search Results", body)


def _notice(msg: str) -> str:
    return (
        "<table border=\"1\" cellpadding=\"8\" cellspacing=\"0\" width=\"600\" bgcolor=\"#fff3cd\">"
        f"<tr><td><font face=\"Verdana\" size=\"2\">{escape(msg)}</font></td></tr></table>"
    )


@app.route("/member/<mid>")
def member_detail(mid: str):
    if _session_dead(request):
        return _expired_page(), 440
    _sleep_from(request)

    # EDGE: ?expire=1 - this request "logs you out". Everything member-related
    # returns the session-ended page from here on (until cookie cleared).
    if request.args.get("expire") == "1":
        resp = make_response("", 302)
        resp.headers["Location"] = "/expired"
        resp.set_cookie("coreserv_dead", "1")
        return resp

    # EDGE: ?boom=1 (or MOCKBANK_BOOM=1) - a transient 500 the FIRST time this
    # member is opened; a retry succeeds. ("App just errored", retry fixes it.)
    if (request.args.get("boom") == "1" or os.environ.get("MOCKBANK_BOOM") == "1") and mid not in _BOOMED:
        _BOOMED.add(mid)
        return _p("Error", _notice(
            "CoreServ encountered an unexpected error (ref 500-CORE-7742). "
            "Please retry."
        )), 500

    if mid == "99999":
        return _p("Restricted", _notice(
            "This member record is restricted. You do not have permission to view it."
        )), 403

    if mid not in MEMBERS:
        return _p("Not Found", (
            "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" width=\"600\">"
            "<tr><td><font face=\"Verdana\" size=\"2\">"
            f"Member record {escape(mid)} was not found.</font></td></tr></table>"
        )), 404

    # Unexpected interstitial: shown once per browser session before detail.
    # MOCKBANK_INTERSTITIAL=0 disables it (clean happy-path demos/tests).
    interstitial_on = os.environ.get("MOCKBANK_INTERSTITIAL", "1") != "0"
    acked = request.cookies.get("sessnotice") == "1"
    if interstitial_on and not acked and request.args.get("ack") != "1":
        body = (
            "<table border=\"1\" cellpadding=\"10\" cellspacing=\"0\" width=\"560\" bgcolor=\"#ffe0e0\">\n"
            "<tr><td><font face=\"Verdana\" size=\"2\"><b>Session Notice</b><br><br>"
            "Your CoreServ session will expire in 15 minutes. Save your work.<br><br>"
            f"<a href=\"/member/{escape(mid)}?ack=1\">[ Acknowledge and continue ]</a>"
            "</font></td></tr></table>"
        )
        resp = make_response(_p("Session Notice", body))
        resp.set_cookie("sessnotice", "1")
        return resp

    m = MEMBERS[mid]
    # EDGE: ?decoy=1 (or MOCKBANK_DECOY=1) - a fake "Savings" cell with a junk
    # value, ABOVE the real balances table, to trip landmark extraction.
    decoy_on = request.args.get("decoy") == "1" or os.environ.get("MOCKBANK_DECOY") == "1"
    decoy_row = (
        "  <tr><td><font face=\"Verdana\" size=\"2\">Savings</font></td>"
        "<td><font face=\"Verdana\" size=\"2\">$0.01</font></td></tr>\n"
        if decoy_on else ""
    )
    # EDGE: ?relabel=1 (or MOCKBANK_RELABEL=1) - the labels are swapped: the
    # landmark text is present but on the wrong row.
    if request.args.get("relabel") == "1" or os.environ.get("MOCKBANK_RELABEL") == "1":
        chk_label, sav_label = "Savings", "Checking"
    else:
        chk_label, sav_label = "Checking", "Savings"
    # EDGE: ?drift=1 (or MOCKBANK_DRIFT=1) - the savings balance ticks up a cent
    # on every load. Live data that is never the same twice: a value recorded in
    # discovery will not match on replay.
    savings = m["savings_balance"]
    if request.args.get("drift") == "1" or os.environ.get("MOCKBANK_DRIFT") == "1":
        _PAGE_HITS[mid] = _PAGE_HITS.get(mid, 0) + 1
        cents = int(round(float(savings.replace("$", "").replace(",", "")) * 100)) + _PAGE_HITS[mid]
        savings = f"${cents // 100:,}.{cents % 100:02d}"

    # Balance is buried in a nested table with no id/class — hostile on purpose.
    body = (
        "<table border=\"1\" cellpadding=\"0\" cellspacing=\"0\" width=\"620\"><tr><td>\n"
        "  <table border=\"0\" cellpadding=\"6\" cellspacing=\"0\" width=\"100%\">\n"
        f"  <tr bgcolor=\"#eeeeee\"><td colspan=\"2\"><font face=\"Verdana\" size=\"2\"><b>Member {mid} &mdash; {escape(m['name'])}</b></font></td></tr>\n"
        f"  <tr><td width=\"180\"><font face=\"Verdana\" size=\"2\">Status</font></td><td><font face=\"Verdana\" size=\"2\">{escape(m['status'])}</font></td></tr>\n"
        f"  <tr><td><font face=\"Verdana\" size=\"2\">Home Branch</font></td><td><font face=\"Verdana\" size=\"2\">{escape(m['branch'])}</font></td></tr>\n"
        f"{decoy_row}"
        "  <tr><td valign=\"top\"><font face=\"Verdana\" size=\"2\">Balances</font></td><td>\n"
        "     <table border=\"1\" cellpadding=\"4\" cellspacing=\"0\">\n"
        f"       <tr><td><font face=\"Verdana\" size=\"1\">{chk_label}</font></td>"
        f"<td align=\"right\"><font face=\"Verdana\" size=\"2\">{escape(m['checking_balance'])}</font></td></tr>\n"
        f"       <tr><td><font face=\"Verdana\" size=\"1\">{sav_label}</font></td>"
        f"<td align=\"right\"><font face=\"Verdana\" size=\"2\">{escape(savings)}</font></td></tr>\n"
        "     </table>\n"
        "  </td></tr>\n"
        f"  <tr><td colspan=\"2\"><font face=\"Verdana\" size=\"2\">"
        f"<a href=\"/member/{mid}/sub-account/new\">Open a new sub-account</a></font></td></tr>\n"
        "  </table>\n</td></tr></table>"
    )
    return _p(f"Member {mid}", body)


@app.route("/member/<mid>/sub-account/new")
def sub_account_new(mid: str):
    if mid not in MEMBERS:
        return _p("Not Found", _notice(f"Member {escape(mid)} not found.")), 404
    err = request.args.get("err")
    warn = ""
    if err == "type":
        warn = _notice("Please choose an account type.")
    elif err == "amt":
        warn = _notice("Initial deposit must be a positive number under 1,000,000.")
    opts = "".join(f"<option value=\"{escape(t)}\">{escape(t)}</option>" for t in SUB_ACCOUNT_TYPES)
    body = (
        f"{warn}"
        f"<form method=\"POST\" action=\"/member/{mid}/sub-account/create\">\n"
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" width=\"560\">\n"
        f"<tr bgcolor=\"#eeeeee\"><td colspan=\"2\"><font face=\"Verdana\" size=\"2\"><b>New Sub-Account &mdash; Member {mid}</b></font></td></tr>\n"
        "<tr><td><font face=\"Verdana\" size=\"2\">Account type</font></td>\n"
        f"<td><select name=\"acct_type\"><option value=\"\">-- select --</option>{opts}</select></td></tr>\n"
        "<tr><td><font face=\"Verdana\" size=\"2\">Initial deposit</font></td>\n"
        "<td><input type=\"text\" name=\"amt\" size=\"12\" value=\"0.00\"></td></tr>\n"
        "<tr><td colspan=\"2\" align=\"right\"><input type=\"submit\" name=\"submit\" value=\"Review\"></td></tr>\n"
        "</table></form>"
    )
    return _p(f"New Sub-Account {mid}", body)


def _valid_amount(raw: str) -> float | None:
    try:
        v = float(raw.replace("$", "").replace(",", "").strip() or "0")
    except ValueError:
        return None
    if v < 0 or v > 1_000_000:
        return None
    return v


@app.route("/member/<mid>/sub-account/create", methods=["POST"])
def sub_account_create(mid: str):
    if _session_dead(request):
        return _expired_page(), 440
    if mid not in MEMBERS:
        return _p("Not Found", _notice(f"Member {escape(mid)} not found.")), 404
    acct_type = (request.form.get("acct_type") or "").strip()
    amt = (request.form.get("amt") or "0.00").strip()

    if not acct_type:
        # Re-render the form with a validation error (recoverable-ish / business).
        return _p("New Sub-Account", _notice("Please choose an account type.") + (
            f"<br><font face=\"Verdana\" size=\"2\"><a href=\"/member/{mid}/sub-account/new?err=type\">Back to form</a></font>"
        )), 400

    # EDGE: deposit validation only fires here, on submit.
    if _valid_amount(amt) is None:
        return _p("New Sub-Account", _notice(
            "Initial deposit must be a positive number under 1,000,000."
        ) + (
            f"<br><font face=\"Verdana\" size=\"2\"><a href=\"/member/{mid}/sub-account/new?err=amt\">Back to form</a></font>"
        )), 400

    key = (mid, acct_type, amt)

    # EDGE: an unexpected confirmation step. The first POST does NOT create -
    # it returns an "are you sure" page that must be re-submitted with
    # confirmed=yes. (?skipconfirm=1 disables it for the happy path.)
    if request.form.get("confirmed") != "yes" and request.args.get("skipconfirm") != "1":
        body = (
            "<table border=\"1\" cellpadding=\"10\" cellspacing=\"0\" width=\"560\" bgcolor=\"#ffe0e0\">\n"
            "<tr><td><font face=\"Verdana\" size=\"2\"><b>Confirm sub-account creation</b><br><br>"
            f"You are about to open a <b>{escape(acct_type)}</b> sub-account for member "
            f"{mid} with an initial deposit of {escape(amt)}.<br>"
            "This action cannot be undone.</font></td></tr></table>\n"
            f"<form method=\"POST\" action=\"/member/{mid}/sub-account/create\">\n"
            f"<input type=\"hidden\" name=\"acct_type\" value=\"{escape(acct_type)}\">\n"
            f"<input type=\"hidden\" name=\"amt\" value=\"{escape(amt)}\">\n"
            "<input type=\"hidden\" name=\"confirmed\" value=\"yes\">\n"
            "<input type=\"submit\" name=\"go\" value=\"Confirm creation\">\n"
            "</form>"
        )
        return _p("Confirm", body)

    # EDGE: duplicate submission. An identical create that already succeeded
    # returns a WARNING (a legitimate business outcome), not a second account.
    if key in _CREATED:
        prev = _CREATED[key]
        return _p("Duplicate", _notice(
            f"A {acct_type} sub-account for member {mid} already exists "
            f"(ref {prev}). No new account was created."
        )), 409

    conf = "SA-" + str(abs(hash(key)) % 900000 + 100000)
    _CREATED[key] = conf
    # EDGE: ?noisyok=1 (or MOCKBANK_NOISY_OK=1) - the SUCCESS screen also carries
    # the words "error" and "not found" (in a reassuring sentence). A naive
    # substring check for error/business-outcome phrases would false-positive.
    noisy = request.args.get("noisyok") == "1" or os.environ.get("MOCKBANK_NOISY_OK") == "1"
    extra = (
        "<br>Validation: 0 errors. No conflicting records were found."
        if noisy else ""
    )
    body = (
        "<table border=\"1\" cellpadding=\"8\" cellspacing=\"0\" width=\"560\" bgcolor=\"#e3f4e3\">\n"
        "<tr><td><font face=\"Verdana\" size=\"2\"><b>Sub-account created</b></font></td></tr>\n"
        f"<tr><td><font face=\"Verdana\" size=\"2\">Member: {mid}<br>Type: {escape(acct_type)}<br>"
        f"Initial deposit: {escape(amt)}<br>Confirmation number: <b>{conf}</b>{extra}</font></td></tr>\n"
        "</table>"
    )
    return _p("Confirmation", body)


@app.get("/healthz")
def healthz() -> str:
    return "ok"


@app.post("/_reset")
def _reset() -> str:
    """Clear per-process edge-case state (transient-500 and duplicate-submit
    memory). For tests / repeatable demo runs."""
    _BOOMED.clear()
    _CREATED.clear()
    return "reset"


def main() -> None:
    host = os.environ.get("MOCKBANK_HOST", "127.0.0.1")
    port = int(os.environ.get("MOCKBANK_PORT", "8799"))
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
