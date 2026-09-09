from __future__ import annotations

import os
import time

from playwright.sync_api import sync_playwright
from playwright.sync_api import Error as PlaywrightError


def _get_env(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v else default


def main() -> int:
    os.environ["DISPLAY"] = _get_env("DISPLAY", ":99")

    url = _get_env("CHROME_URL", "about:blank")
    browser_name = _get_env("BROWSER", "chromium").lower()

    width = int(_get_env("SCREEN_WIDTH", "1366"))
    height = int(_get_env("SCREEN_HEIGHT", "768"))
    chromium_cdp_port = int(_get_env("CHROME_CDP_PORT", "9222"))

    with sync_playwright() as p:
        if browser_name in ("chromium", "chrome", "msedge", "edge"):
            browser_type = p.chromium
            channel = None
            if browser_name in ("msedge", "edge"):
                channel = "msedge"
            elif browser_name == "chrome":
                channel = "chrome"
            launch_args = [
                # fill the whole Xvfb display so the noVNC view isn't mostly
                # empty desktop — the page, not the window chrome, is what
                # matters here.
                f"--window-size={width},{height}",
                "--window-position=0,0",
                "--start-maximized",
                "--force-device-scale-factor=1",
                "--remote-debugging-address=0.0.0.0",
                f"--remote-debugging-port={chromium_cdp_port}",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-breakpad",
                "--disable-gpu",
                "--no-restore-last-session",
                "--restore-last-session=false",
                "--remote-allow-origins=*",
            ]
        elif browser_name == "firefox":
            browser_type = p.firefox
            launch_args = []
            channel = None
        elif browser_name == "webkit":
            browser_type = p.webkit
            launch_args = []
            channel = None
        else:
            raise SystemExit(
                f"Unsupported BROWSER={browser_name!r} "
                f"(expected chromium|chrome|msedge|firefox|webkit)"
            )

        try:
            context = browser_type.launch_persistent_context(
                user_data_dir="/home/pwuser/browser-profile",
                headless=False,
                args=launch_args,
                channel=channel,
                no_viewport=True,
            )
        except PlaywrightError as e:
            # On Linux Arm64, Playwright does not support chrome/msedge channels.
            # Fall back to bundled Chromium when a channel binary is missing.
            if channel and "is not found" in str(e).lower():
                context = browser_type.launch_persistent_context(
                    user_data_dir="/home/pwuser/browser-profile",
                    headless=False,
                    args=launch_args,
                    channel=None,
                    no_viewport=True,
                )
            else:
                raise
        # Reuse the page launch_persistent_context already opens; never create a second tab.
        page = context.pages[0] if context.pages else context.new_page()

        # Fill the whole Xvfb display so the noVNC view is the page, not an
        # empty desktop. --start-maximized is unreliable under xfwm4; drive it
        # over CDP instead.
        try:
            cdp = context.new_cdp_session(page)
            win = cdp.send("Browser.getWindowForTarget")
            cdp.send(
                "Browser.setWindowBounds",
                {"windowId": win["windowId"], "bounds": {"windowState": "fullscreen"}},
            )
        except Exception:  # noqa: BLE001 — best effort
            pass

        # Best-effort warm-up navigation. A target that is down/slow at this
        # instant must NOT take the whole browser down - the worker attaches
        # over CDP and does its own page.goto anyway. Land on about:blank so
        # the noVNC view isn't blank-white and Chrome stays alive.
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=10_000)
        except Exception as e:  # noqa: BLE001
            print(f"[launch_browser] warm-up goto {url!r} failed: {e}", flush=True)
            try:
                page.goto("about:blank")
            except Exception:  # noqa: BLE001
                pass

        while True:
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())

