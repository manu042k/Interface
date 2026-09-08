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
                f"--window-size={width},{height}",
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
                viewport={"width": width, "height": height},
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
                    viewport={"width": width, "height": height},
                )
            else:
                raise
        # Reuse the page launch_persistent_context already opens; never create a second tab.
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url, wait_until="domcontentloaded")

        while True:
            time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())

