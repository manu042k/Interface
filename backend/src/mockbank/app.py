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
        "<font face=\"Verdana\" size=\"1\">Try 12345, 22222, 34567. "
        "00000 = not found, 99999 = restricted.</font>"
    )
    return _p("Member Search", body)


@app.route("/members")
def members() -> str:
    q = (request.args.get("q") or "").strip()
    if request.args.get("slow") == "1":
        time.sleep(3.0)

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
    body = (
        "<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\" width=\"600\">\n"
        "<tr bgcolor=\"#eeeeee\"><td colspan=\"3\"><font face=\"Verdana\" size=\"2\"><b>1 match</b></font></td></tr>\n"
        "<tr><td><font face=\"Verdana\" size=\"2\">ID</font></td>"
        "<td><font face=\"Verdana\" size=\"2\">Name</font></td><td>&nbsp;</td></tr>\n"
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
    if request.args.get("slow") == "1":
        time.sleep(3.0)

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
    acked = request.cookies.get("sessnotice") == "1"
    if not acked and request.args.get("ack") != "1":
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
    # Balance is buried in a nested table with no id/class — hostile on purpose.
    body = (
        "<table border=\"1\" cellpadding=\"0\" cellspacing=\"0\" width=\"620\"><tr><td>\n"
        "  <table border=\"0\" cellpadding=\"6\" cellspacing=\"0\" width=\"100%\">\n"
        f"  <tr bgcolor=\"#eeeeee\"><td colspan=\"2\"><font face=\"Verdana\" size=\"2\"><b>Member {mid} &mdash; {escape(m['name'])}</b></font></td></tr>\n"
        f"  <tr><td width=\"180\"><font face=\"Verdana\" size=\"2\">Status</font></td><td><font face=\"Verdana\" size=\"2\">{escape(m['status'])}</font></td></tr>\n"
        f"  <tr><td><font face=\"Verdana\" size=\"2\">Home Branch</font></td><td><font face=\"Verdana\" size=\"2\">{escape(m['branch'])}</font></td></tr>\n"
        "  <tr><td valign=\"top\"><font face=\"Verdana\" size=\"2\">Balances</font></td><td>\n"
        "     <table border=\"1\" cellpadding=\"4\" cellspacing=\"0\">\n"
        "       <tr><td><font face=\"Verdana\" size=\"1\">Checking</font></td>"
        f"<td align=\"right\"><font face=\"Verdana\" size=\"2\">{escape(m['checking_balance'])}</font></td></tr>\n"
        "       <tr><td><font face=\"Verdana\" size=\"1\">Savings</font></td>"
        f"<td align=\"right\"><font face=\"Verdana\" size=\"2\">{escape(m['savings_balance'])}</font></td></tr>\n"
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
    warn = _notice("Please choose an account type.") if err == "type" else ""
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


@app.route("/member/<mid>/sub-account/create", methods=["POST"])
def sub_account_create(mid: str):
    if mid not in MEMBERS:
        return _p("Not Found", _notice(f"Member {escape(mid)} not found.")), 404
    acct_type = (request.form.get("acct_type") or "").strip()
    amt = (request.form.get("amt") or "0.00").strip()
    if not acct_type:
        # Re-render the form with a validation error (recoverable-ish / business).
        return _p("New Sub-Account", _notice("Please choose an account type.") + (
            f"<br><font face=\"Verdana\" size=\"2\"><a href=\"/member/{mid}/sub-account/new?err=type\">Back to form</a></font>"
        )), 400

    # Deterministic confirmation number from inputs.
    conf = "SA-" + str(abs(hash((mid, acct_type, amt))) % 900000 + 100000)
    body = (
        "<table border=\"1\" cellpadding=\"8\" cellspacing=\"0\" width=\"560\" bgcolor=\"#e3f4e3\">\n"
        "<tr><td><font face=\"Verdana\" size=\"2\"><b>Sub-account created</b></font></td></tr>\n"
        f"<tr><td><font face=\"Verdana\" size=\"2\">Member: {mid}<br>Type: {escape(acct_type)}<br>"
        f"Initial deposit: {escape(amt)}<br>Confirmation number: <b>{conf}</b></font></td></tr>\n"
        "</table>"
    )
    return _p("Confirmation", body)


@app.get("/healthz")
def healthz() -> str:
    return "ok"


def main() -> None:
    host = os.environ.get("MOCKBANK_HOST", "127.0.0.1")
    port = int(os.environ.get("MOCKBANK_PORT", "8799"))
    app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
