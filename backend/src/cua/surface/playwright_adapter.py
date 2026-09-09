"""ST-006/007 + ST-041 seam: Playwright web surface adapter.

One browser process, one **context per session** (isolation boundary — cookies,
storage, cache die with the session). Egress is filtered at the context's route
handler to the session's allowlisted hosts; a true per-session container/microVM
is the production hardening of this same seam (ADR-09, design-only here).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    async_playwright,
)

from ..conditions import SHAPE_PATTERNS, shape_pattern
from ..models import ActionResult, ActionType
from .base import Action, RawSnapshot, SurfaceAdapter, SurfaceError

_WS_RE = re.compile(r"\s+")
_SHAPED = set(SHAPE_PATTERNS)  # shapes with a recognisable text pattern


def _match_option(want: str, opts: list[dict[str, str]]) -> dict[str, str] | None:
    """Best <option> for the model's requested value. Legacy selects have
    values like 'MAIN-001' and labels like 'MAIN-001 - Main Office'; the model
    may pass either, a substring, or a bare index. Never hang on a non-match."""
    if not opts:
        return None
    w = want.strip().lower()
    if not w:
        return None
    for o in opts:  # exact value or label
        if want == o["value"] or w == o["label"].lower():
            return {"value": o["value"]} if o["value"] else {"label": o["label"]}
    if len(w) >= 2:
        for o in opts:  # substring either way
            lab = o["label"].lower()
            if w in lab or lab in w or (o["value"] and w in o["value"].lower()):
                return {"value": o["value"]} if o["value"] else {"label": o["label"]}
    if w.isdigit():
        # "branch 1" almost always means the FIRST branch, not option value "1"
        # or 0-based index 1 — try 1-based first, then 0-based, skipping a
        # leading blank/prompt option.
        i = int(w)
        # skip a leading blank/prompt option (value="" by convention)
        real = [k for k, o in enumerate(opts) if o["value"]] or list(range(len(opts)))
        for cand in (i - 1, i):
            if 0 <= cand < len(real):
                o = opts[real[cand]]
                return {"value": o["value"]} if o["value"] else {"label": o["label"]}
    return None


@dataclass
class _Session:
    handle: str
    context: BrowserContext
    page: Page
    tenant: str
    allowed_hosts: set[str] = field(default_factory=set)
    blocked_egress: list[str] = field(default_factory=list)
    # set for sandbox sessions: the CDP-attached Browser to disconnect (not kill)
    cdp_browser: Browser | None = None


class PlaywrightAdapter(SurfaceAdapter):
    surface_family = "web"

    def __init__(
        self, *, headed: bool = False, default_timeout_ms: int = 15000,
        session_wall_clock_s: float = 300.0,
    ) -> None:
        self._headed = headed
        self._default_timeout = default_timeout_ms
        self._pw = None
        self._browser: Browser | None = None
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()
        from .sandbox import SessionBudget, SessionWatchdog

        self.watchdog = SessionWatchdog(SessionBudget(wall_clock_s=session_wall_clock_s))

    # -- lifecycle ------------------------------------------------------
    async def _ensure_pw(self):
        async with self._lock:
            if self._pw is None:
                self._pw = await async_playwright().start()
            return self._pw

    async def _ensure_browser(self) -> Browser:
        await self._ensure_pw()
        async with self._lock:
            if self._browser is None:
                self._browser = await self._pw.chromium.launch(headless=not self._headed)
            return self._browser

    async def open_session(
        self,
        target: str,
        tenant: str = "default",
        *,
        extra_allowed_hosts: set[str] | None = None,
        cdp_url: str | None = None,
    ) -> str:
        handle = "sess_" + uuid.uuid4().hex[:12]
        cdp_browser: Browser | None = None

        if cdp_url:
            # Attach to the headed browser running inside the run's sandbox
            # container and drive the SAME page the user watches over noVNC.
            # The CDP HTTP endpoint answers a beat before the browser is really
            # ready (Chromium-in-Docker race -> WS "socket hang up code=1006",
            # then "Target.createTarget Protocol error"), so retry the whole
            # attach - connect, grab a context, grab a page.
            pw = await self._ensure_pw()
            last_exc: Exception | None = None
            context = page = None
            for attempt in range(8):
                try:
                    cdp_browser = await pw.chromium.connect_over_cdp(cdp_url)
                    context = (
                        cdp_browser.contexts[0]
                        if cdp_browser.contexts
                        else await cdp_browser.new_context()
                    )
                    page = context.pages[0] if context.pages else await context.new_page()
                    await page.evaluate("1")  # prove the target really answers
                    break
                except Exception as exc:  # noqa: BLE001 - transient CDP handshake failure
                    last_exc = exc
                    if cdp_browser is not None:
                        with contextlib.suppress(Exception):
                            await cdp_browser.close()
                        cdp_browser = None
                    await asyncio.sleep(1.5 + attempt)
            if cdp_browser is None or page is None or context is None:
                raise SurfaceError(f"could not attach to sandbox CDP at {cdp_url}: {last_exc}")
        else:
            browser = await self._ensure_browser()
            context = await browser.new_context()
            page = await context.new_page()

        context.set_default_timeout(self._default_timeout)

        allowed = {urlparse(target).hostname or ""}
        allowed |= extra_allowed_hosts or set()
        allowed.discard("")
        sess = _Session(
            handle=handle, context=context, page=page, tenant=tenant,
            allowed_hosts=allowed, cdp_browser=cdp_browser,
        )
        self._sessions[handle] = sess

        # ST-041 seam: block cross-host egress at the browser boundary.
        async def _route(route, request):  # noqa: ANN001
            host = urlparse(request.url).hostname or ""
            if host and sess.allowed_hosts and host not in sess.allowed_hosts:
                sess.blocked_egress.append(request.url)
                await route.abort()
            else:
                await route.continue_()

        await context.route("**/*", _route)
        self.watchdog.register(handle)

        if target:
            await page.goto(target, wait_until="domcontentloaded")
        return handle

    async def sweep_watchdog(self) -> list[str]:
        """ST-042: force-close sessions past their wall-clock budget. Returns the
        killed session handles (callers mark the run resource_exceeded)."""
        killed: list[str] = []
        for meter in self.watchdog.sweep():
            await self.close_session(meter.session_id)
            killed.append(meter.session_id)
        return killed

    def egress_report(self, session_handle: str) -> list[str]:
        sess = self._sessions.get(session_handle)
        return list(sess.blocked_egress) if sess else []

    async def close_session(self, session_handle: str) -> None:
        self.watchdog.unregister(session_handle)
        sess = self._sessions.pop(session_handle, None)
        if sess is None:
            return
        try:
            if sess.cdp_browser is not None:
                # Disconnect only — the sandbox container owns the browser and is
                # torn down by the SandboxManager.
                await sess.cdp_browser.close()
            else:
                await sess.context.close()
        except Exception:  # noqa: BLE001
            pass

    async def shutdown(self) -> None:
        for h in list(self._sessions):
            await self.close_session(h)
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None

    # -- helpers ------------------------------------------------------
    def _sess(self, handle: str) -> _Session:
        sess = self._sessions.get(handle)
        if sess is None:
            raise SurfaceError(f"unknown or closed session: {handle}")
        return sess

    async def _state_hash(self, page: Page) -> str:
        try:
            sig = await page.evaluate(
                "() => document.body ? document.body.innerText.slice(0, 4000) : ''"
            )
        except Exception:  # noqa: BLE001
            sig = ""
        return hashlib.sha1(f"{page.url}\n{sig}".encode()).hexdigest()[:16]

    async def _resolve(self, page: Page, desc: Any) -> tuple[Locator, str]:
        """Best-effort description -> Locator for the discovery path.

        Replay uses the dedicated multi-strategy Locator Resolution Engine; this
        is the lighter resolver the model's structured target_description feeds.
        """
        if desc is None:
            raise SurfaceError("action has no target_description and no locator")

        if isinstance(desc, str):
            desc = {"text": desc}
        if not isinstance(desc, dict):
            raise SurfaceError(f"unsupported target_description: {desc!r}")

        # 1. explicit css / xpath
        if desc.get("css"):
            return page.locator(desc["css"]).first, f"css={desc['css']}"
        if desc.get("xpath"):
            return page.locator(f"xpath={desc['xpath']}").first, f"xpath={desc['xpath']}"

        # 1b. explicit id
        if desc.get("id"):
            loc = page.locator(f'#{desc["id"]}')
            if await loc.count():
                return loc.first, f'id={desc["id"]}'

        role, name = desc.get("role"), desc.get("name")
        near = desc.get("near")
        text = desc.get("text")
        label = desc.get("label")
        placeholder = desc.get("placeholder")

        # 2. role + accessible name (most portable). The model often puts the
        # name in `text` instead of `name`; treat either as the a11y name so we
        # don't fall through to "the first element of this role" on the page.
        acc_name = name or text
        if role and acc_name:
            loc = page.get_by_role(role, name=acc_name, exact=False)
            if await loc.count():
                return loc.first, f"role={role} name={acc_name!r}"
            # exact, in case a shorter name (e.g. a nav link) also matched loosely
            loc = page.get_by_role(role, name=acc_name, exact=True)
            if await loc.count():
                return loc.first, f"role={role} name={acc_name!r} (exact)"

        # 2b. form-control name / id attribute. Legacy table forms put the label
        # in a separate <td> with no <label for=...> / aria-label, so the input
        # has NO accessible name and step 2 can't see it - but it does have a
        # stable `name=` attribute, which is exactly what the model tends to pass
        # ({"role":"textbox","name":"address"} for <input name="address">). Try
        # that attribute (and id / placeholder) directly - exact first, then a
        # case-insensitive substring so a partial guess ("address" for
        # "mailing_address") still lands.
        for key in (name, text, label, placeholder):
            if not key or not isinstance(key, str):
                continue
            k = key.replace('"', "").replace("\\", "").strip()
            if not k:
                continue
            for sel, why in (
                (f'[name="{k}"], [id="{k}"]', f'name/id="{k}"'),
                (
                    f'input[name*="{k}" i], textarea[name*="{k}" i], select[name*="{k}" i], '
                    f'input[id*="{k}" i], [placeholder*="{k}" i]',
                    f'name~="{k}"',
                ),
            ):
                try:
                    loc = page.locator(sel)
                    n = await loc.count()
                except Exception:  # noqa: BLE001 - malformed selector from a weird key
                    continue
                if n:
                    return loc.first, why

        # 3. form label / placeholder
        if label:
            loc = page.get_by_label(label, exact=False)
            if await loc.count():
                return loc.first, f"label={label!r}"
        if placeholder:
            loc = page.get_by_placeholder(placeholder, exact=False)
            if await loc.count():
                return loc.first, f"placeholder={placeholder!r}"

        # 3b. legacy table form: the label sits in a plain <td>/<th> with no
        # <label for=...>, so get_by_label above found nothing. Find the text
        # node and take the first form control that follows it in DOM order
        # (same row, or the next cell). This is THE legacy-form pattern.
        landmark = near or label or (text if text and " " in str(text) else None)
        if landmark and not near:
            lm = str(landmark).rstrip(":").strip()
            anchor = page.get_by_text(lm, exact=False)
            if await anchor.count():
                a0 = anchor.first
                for xp, why in (
                    ("xpath=ancestor::tr[1]//*[self::select or self::input or self::textarea]",
                     f"label-cell={lm!r}:row-control"),
                    ("xpath=(following::select | following::input | following::textarea)[1]",
                     f"label-cell={lm!r}:next-control"),
                ):
                    try:
                        cand = a0.locator(xp)
                        if await cand.count():
                            return cand.first, why
                    except Exception:  # noqa: BLE001
                        continue

        # 4. "the control (or value cell) in the row labelled 'Savings'"
        if near:
            anchor = page.get_by_text(near, exact=True)
            if not await anchor.count():
                anchor = page.get_by_text(near, exact=False)
            if await anchor.count():
                row = anchor.first.locator("xpath=ancestor::tr[1]")
                if await row.count():
                    ctrl = row.locator("input, select, textarea, button, a")
                    if await ctrl.count():
                        return ctrl.first, f"near={near!r}:control"
                    cells = row.locator("td")
                    if await cells.count():
                        return cells.last, f"near={near!r}:value-cell"
                return anchor.first, f"near={near!r}:anchor"

        # 5. visible text — prefer an actionable element with that name
        if text:
            loc = page.get_by_text(text, exact=True)
            if await loc.count() == 1:
                return loc.first, f"text={text!r} (exact)"
            for r in ("button", "link"):
                loc = page.get_by_role(r, name=text, exact=False)
                if await loc.count():
                    return loc.first, f"{r}={text!r}"
            loc = page.get_by_text(text, exact=False)
            if await loc.count():
                return loc.first, f"text={text!r}"

        # 6. last resort: the only element of this role on the page
        if role:
            loc = page.get_by_role(role)
            if await loc.count() == 1:
                return loc.first, f"role={role} (sole)"

        raise SurfaceError(f"could not resolve target: {desc!r}")

    async def probe(self, session_handle: str, target: dict[str, Any]) -> bool:
        sess = self._sess(session_handle)
        try:
            loc, _ = await self._resolve(sess.page, target)
            return await loc.count() > 0 and await loc.first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    async def try_strategy(
        self, session_handle: str, kind: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        """Resolve one LocatorStrategy against the live page for the Locator
        Resolution Engine. Strict — no lenient fall-through between strategies
        (that is the engine's job, by rank). Returns {matched, count, visible,
        describe, reason}."""
        sess = self._sess(session_handle)
        page = sess.page
        target = _strategy_to_desc(kind, params)
        try:
            loc, describe = _strict_locate(page, kind, params, target)
        except Exception as exc:  # noqa: BLE001
            return {"matched": False, "count": 0, "visible": False, "describe": None, "reason": str(exc).splitlines()[0]}
        if loc is None:
            return {"matched": False, "count": 0, "visible": False, "describe": describe, "reason": "strategy not applicable to params"}
        try:
            count = await loc.count()
        except Exception as exc:  # noqa: BLE001
            return {"matched": False, "count": 0, "visible": False, "describe": describe, "reason": str(exc).splitlines()[0]}
        # `text` matched as a substring also hits the <td>/<tr> wrapping the
        # link. Narrow to an actionable element with that exact text first —
        # the same order discovery's resolver uses — before giving up.
        if kind == "text" and count != 1 and isinstance(params.get("text"), str):
            txt = params["text"]
            for cand, desc in (
                (page.get_by_role("link", name=txt, exact=True), f"link={txt!r} (exact)"),
                (page.get_by_role("button", name=txt, exact=True), f"button={txt!r} (exact)"),
                (page.get_by_text(txt, exact=True), f"text={txt!r} (exact)"),
            ):
                try:
                    c = await cand.count()
                except Exception:  # noqa: BLE001
                    continue
                if c >= 1:
                    loc, describe, count = cand, desc, c
                    break
        if count == 0:
            return {"matched": False, "count": 0, "visible": False, "describe": describe, "reason": "no element matched"}
        idx = 0
        if count > 1:
            # Legacy consoles repeat the same nav link in a top bar and a menu
            # body. N matches that are all anchors to the SAME href are
            # interchangeable — take the first visible one rather than failing.
            # Anything else genuinely ambiguous still fails closed.
            equiv_idx = await self._equivalent_link_index(loc, count)
            if equiv_idx is None:
                return {"matched": False, "count": count, "visible": False, "describe": describe, "reason": f"ambiguous: {count} matches"}
            idx = equiv_idx
            describe = f"{describe} (1 of {count} equivalent links)"
        visible = False
        try:
            visible = await loc.nth(idx).is_visible()
        except Exception:  # noqa: BLE001
            pass
        return {
            "matched": True, "count": 1, "visible": visible, "describe": describe,
            "reason": None, "target": target,
        }

    async def _shaped_cell_in_row(self, loc: Locator, shape: str) -> tuple[str] | None:
        """`loc` is a cell whose text didn't match `shape`. If it sits in a
        table row, return the (text,) of the first sibling <td> that does — the
        Balance column in a Share ID | Type | Balance | Status grid."""
        pat = re.compile(shape_pattern(shape))
        try:
            row = loc.locator("xpath=ancestor-or-self::tr[1]")
            if not await row.count():
                return None
            texts = await row.locator("td, th").evaluate_all(
                "els => els.map(e => (e.textContent || '').trim())"
            )
            for txt in texts:
                if txt and pat.search(txt):
                    return (txt,)
        except Exception:  # noqa: BLE001
            pass
        return None

    async def _equivalent_link_index(self, loc: Locator, count: int) -> int | None:
        """If every match is an <a> with the same href, return the index of the
        first visible one; else None (truly ambiguous)."""
        try:
            hrefs: list[str] = []
            for i in range(min(count, 8)):
                el = loc.nth(i)
                tag = (await el.evaluate("e => e.tagName")).lower()
                if tag != "a":
                    return None
                hrefs.append(await el.evaluate("e => e.getAttribute('href') || ''"))
            if len(set(hrefs)) != 1 or not hrefs[0]:
                return None
            for i in range(min(count, 8)):
                if await loc.nth(i).is_visible():
                    return i
            return 0
        except Exception:  # noqa: BLE001
            return None

    # -- ST-007: primitive actions ---------------------------------------
    async def execute(self, session_handle: str, action: Action) -> ActionResult:
        sess = self._sess(session_handle)
        page = sess.page
        t = action.type
        timeout = action.timeout_ms or self._default_timeout
        res = ActionResult(ok=False, action_type=t, target_description=str(action.target_description or ""))

        try:
            if t == ActionType.NAVIGATE:
                url = action.value or ""
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                res.ok = True

            elif t == ActionType.WAIT_FOR:
                ok = await self._wait_for(page, action.condition or {}, timeout)
                res.ok = ok
                res.timed_out = not ok

            elif t == ActionType.CLICK:
                loc, strat = await self._locator(sess, action)
                await loc.click(timeout=timeout)
                await self._settle(page)
                res.ok, res.matched_strategy = True, strat

            elif t == ActionType.TYPE:
                loc, strat = await self._locator(sess, action)
                await loc.fill(action.value or "", timeout=timeout)
                res.ok, res.matched_strategy = True, strat

            elif t == ActionType.SELECT:
                loc, strat = await self._locator(sess, action)
                res.matched_strategy = strat
                want = (action.value or "").strip()
                # the resolver may have landed on a wrapper; find the <select>
                sel = loc
                if (await loc.evaluate("e => e.tagName")).lower() != "select":
                    inner = loc.locator("select")
                    if await inner.count():
                        sel = inner.first
                    else:
                        anc = loc.locator("xpath=ancestor-or-self::*[.//select][1]//select")
                        if await anc.count():
                            sel = anc.first
                opts = await sel.locator("option").evaluate_all(
                    "els => els.map(e => ({value: e.value, label: (e.label||e.textContent||'').trim()}))"
                )
                loc = sel
                pick = _match_option(want, opts) if opts else None
                if pick is None:
                    res.ok = False
                    res.error = (
                        f"no <option> matches {want!r} on a <select>. available: "
                        + (", ".join(f"{o['label']!r}" for o in opts[:20]) or "(no <select> at the resolved target)")
                    )
                else:
                    # the option definitely exists — use explicit kwargs (a
                    # positional dict is coerced to a string and never matches)
                    try:
                        if pick.get("value"):
                            await loc.select_option(value=pick["value"], timeout=3000)
                        else:
                            await loc.select_option(label=pick["label"], timeout=3000)
                        res.ok = True
                    except Exception as exc:  # noqa: BLE001
                        res.ok = False
                        res.error = (
                            f"select_option({pick}) failed: {str(exc).splitlines()[0]}. "
                            f"options: " + ", ".join(f"{o['label']!r}" for o in opts[:20])
                        )

            elif t == ActionType.EXTRACT:
                loc, strat = await self._locator(sess, action)
                shape = action.expected_shape or "string"
                raw = (await loc.inner_text(timeout=timeout)).strip()
                value, err = _coerce_shape(raw, shape)
                if err and shape in _SHAPED:
                    # The row-relative resolver returns the LAST <td>, which is
                    # right for a 2-col label/value table but grabs "Status" in a
                    # Share ID | Type | Balance | Status grid. If the target cell
                    # is in a table row, pick the cell that matches the wanted
                    # shape instead of failing.
                    alt = await self._shaped_cell_in_row(loc, shape)
                    if alt is not None:
                        raw, (value, err), strat = alt[0], _coerce_shape(alt[0], shape), f"{strat} -> shape-matched cell"
                if err:
                    res.ok = False
                    res.error = err
                else:
                    res.ok = True
                    res.extracted = value
                res.matched_strategy = strat

            elif t == ActionType.ASSERT_STATE:
                ok = await self._wait_for(page, action.condition or {}, min(timeout, 3000))
                res.ok = ok
                if not ok:
                    res.error = f"assertion failed: {action.condition}"

            elif t == ActionType.SCROLL:
                tgt = action.target_description
                text = tgt.get("text") if isinstance(tgt, dict) else None
                if text:
                    await page.get_by_text(text, exact=False).first.scroll_into_view_if_needed(timeout=timeout)
                else:
                    d = (action.value or "down").lower()
                    if d == "top":
                        await page.evaluate("window.scrollTo(0, 0)")
                    elif d == "bottom":
                        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    elif d == "up":
                        await page.mouse.wheel(0, -700)
                    else:
                        await page.mouse.wheel(0, 700)
                await self._settle(page)
                res.ok = True

            elif t == ActionType.PRESS_KEY:
                await page.keyboard.press(action.value or "Enter")
                await self._settle(page)
                res.ok = True

            else:
                raise SurfaceError(f"unsupported action type: {t}")

        except SurfaceError as exc:
            res.ok, res.error = False, str(exc)
        except Exception as exc:  # noqa: BLE001 — normalize driver errors into a result
            msg = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
            res.ok = False
            res.error = msg
            res.timed_out = "Timeout" in msg or "timeout" in msg

        res.url_after = page.url
        res.state_hash_after = await self._state_hash(page)
        return res

    async def _locator(self, sess: _Session, action: Action) -> tuple[Locator, str]:
        if action.locator:
            eng = action.locator.get("engine", "css")
            val = action.locator["value"]
            sel = val if eng == "css" else f"{eng}={val}"
            return sess.page.locator(sel).first, f"{eng}={val}"
        return await self._resolve(sess.page, action.target_description)

    async def _settle(self, page: Page) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:  # noqa: BLE001
            pass

    async def _wait_for(self, page: Page, condition: dict[str, Any], timeout_ms: int) -> bool:
        """Bounded poll — never an unbounded sleep (§3.3)."""
        deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
        kind = condition.get("kind", "text_present")
        params = condition.get("params", condition)
        while asyncio.get_event_loop().time() < deadline:
            try:
                if kind == "url_matches":
                    if re.search(params.get("pattern", ".*"), page.url):
                        return True
                elif kind in {"text_present", "text_absent"}:
                    body = await page.evaluate("() => document.body ? document.body.innerText : ''")
                    # collapse whitespace runs: legacy tables render
                    # <td>Label:</td><td>Value</td> as "Label:\tValue", so an
                    # assertion of "Label: Value" would never match otherwise.
                    body = _WS_RE.sub(" ", body).lower()
                    needles = params.get("any") or [params.get("text", "")]
                    present = any(n and _WS_RE.sub(" ", n).strip().lower() in body for n in needles)
                    if (kind == "text_present" and present) or (kind == "text_absent" and not present):
                        return True
                elif kind in {"element_present", "element_absent"}:
                    loc = page.locator(params.get("selector", "body"))
                    cnt = await loc.count()
                    if (kind == "element_present" and cnt) or (kind == "element_absent" and not cnt):
                        return True
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.15)
        return False

    # -- ST-008 raw material ----------------------------------------
    async def snapshot(self, session_handle: str) -> RawSnapshot:
        sess = self._sess(session_handle)
        page = sess.page
        try:
            ax = await page.accessibility.snapshot(interesting_only=True)
        except Exception:  # noqa: BLE001
            ax = None
        html = ""
        try:
            html = await page.content()
        except Exception:  # noqa: BLE001
            pass
        png: bytes | None = None
        try:
            png = await page.screenshot(full_page=False)
        except Exception:  # noqa: BLE001
            pass
        visible_text = ""
        try:
            visible_text = await page.evaluate(
                "() => (document.body ? document.body.innerText : '').replace(/\\n{3,}/g, '\\n\\n').trim().slice(0, 4000)"
            )
        except Exception:  # noqa: BLE001
            pass
        form_values: list[dict[str, Any]] = []
        try:
            form_values = await page.evaluate(
                """() => [...document.querySelectorAll('input, select, textarea')]
                    .filter(e => e.type !== 'hidden' && e.offsetParent !== null)
                    .map(e => ({
                        tag: e.tagName.toLowerCase(),
                        type: e.type || null,
                        name: e.name || null,
                        id: e.id || null,
                        label: (e.labels && e.labels[0] && e.labels[0].innerText.trim()) || null,
                        value: (e.value || '').slice(0, 120),
                    }))"""
            )
        except Exception:  # noqa: BLE001
            pass
        return RawSnapshot(
            url=page.url,
            title=await page.title(),
            ax_tree=[ax] if ax else [],
            html=html,
            screenshot_png=png,
            visible_text=visible_text,
            form_values=form_values,
        )

    async def cdp_endpoint(self, session_handle: str) -> str | None:
        # A real browser CDP handoff endpoint is wired in Phase 8; the mechanism
        # (operator connects to the SAME context) is the point, not the transport.
        return None


def _strict_locate(page: Page, kind: str, params: dict[str, Any], target: dict[str, Any]) -> tuple[Locator | None, str]:
    """One strategy, matched strictly. Returns (locator | None, describe)."""
    p = {k: v for k, v in params.items() if not k.startswith("_")}
    if kind == "role_name":
        role, name = p.get("role"), p.get("name")
        if role and name:
            return page.get_by_role(role, name=name, exact=False), f"role={role} name={name!r}"
        if role:
            return page.get_by_role(role), f"role={role}"
        return None, "role_name: no role"
    if kind == "label":
        if p.get("label"):
            return page.get_by_label(p["label"], exact=False), f"label={p['label']!r}"
        if p.get("placeholder"):
            return page.get_by_placeholder(p["placeholder"], exact=False), f"placeholder={p['placeholder']!r}"
        return None, "label: no label/placeholder"
    if kind == "text":
        if p.get("text"):
            return page.get_by_text(p["text"], exact=False), f"text={p['text']!r}"
        return None, "text: no text"
    if kind == "relative_to_landmark":
        near = p.get("near")
        if not near:
            return None, "relative_to_landmark: no anchor"
        anchor = page.get_by_text(near, exact=True)
        row = anchor.first.locator("xpath=ancestor::tr[1]")
        ctrl = row.locator("input, select, textarea, button, a")
        return ctrl.first if _sync_hint(ctrl) else row.locator("td").last, f"near={near!r}"
    if kind in {"dom_anchor", "test_id"}:
        if p.get("css"):
            return page.locator(p["css"]), f"css={p['css']}"
        if p.get("test_id"):
            return page.locator(f'[data-testid="{p["test_id"]}"]'), f"testid={p['test_id']}"
        if p.get("xpath"):
            return page.locator(f"xpath={p['xpath']}"), f"xpath={p['xpath']}"
        return None, "dom_anchor: no css/xpath"
    return None, f"unknown kind {kind}"


def _sync_hint(_loc: Locator) -> bool:
    # We can't await here; prefer the value-cell fallback deterministically by
    # returning False. The row's last <td> is the robust choice for read steps,
    # and control steps carry a role_name/label strategy ahead of this one.
    return False


def _strategy_to_desc(kind: str, params: dict[str, Any]) -> dict[str, Any]:
    """Map a recorded LocatorStrategy to the resolver's target-description dict."""
    p = {k: v for k, v in params.items() if not k.startswith("_")}
    if kind == "role_name":
        return {"role": p.get("role"), "name": p.get("name")}
    if kind == "label":
        return {"label": p.get("label"), "placeholder": p.get("placeholder")}
    if kind == "relative_to_landmark":
        return {"near": p.get("near")}
    if kind == "text":
        return {"text": p.get("text")}
    if kind in {"dom_anchor", "test_id"}:
        if p.get("css"):
            return {"css": p["css"]}
        if p.get("test_id"):
            return {"css": f'[data-testid="{p["test_id"]}"]'}
        return dict(p)
    return dict(p)


def _coerce_shape(raw: str, shape: str) -> tuple[Any, str | None]:
    s = raw.strip()
    if shape in {"string", "date"}:
        return s, None
    if shape == "boolean":
        return s.lower() in {"true", "yes", "1", "on"}, None
    if shape in {"number", "currency", "integer"}:
        cleaned = re.sub(r"[^\d.\-]", "", s)
        if not cleaned or cleaned in {"-", ".", "-."}:
            return None, f"expected {shape}, observed {s!r}"
        try:
            num = float(cleaned)
        except ValueError:
            return None, f"expected {shape}, observed {s!r}"
        if shape == "integer":
            return int(num), None
        if shape == "currency":
            return {"raw": s, "amount": round(num, 2)}, None
        return num, None
    return s, None
