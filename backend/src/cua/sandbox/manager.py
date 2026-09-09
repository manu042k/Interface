"""ST-041 (now real): SandboxManager — one Docker container per run.

Each `spawn()`:
  docker run -d --rm --add-host host.docker.internal:host-gateway \
    -p 0:6080 -p 0:9223 cua-sandbox:latest
  (0 = let Docker pick a free host port; we read it back with `docker port`)

Inside the container (supervisord):
  Xvfb :99  ->  xfce4  ->  headed Chromium  --remote-debugging-port=9222
  socat 9222 -> 9223            (Chrome binds CDP to loopback only)
  x11vnc :5900  ->  websockify :6080  (serves noVNC + the RFB websocket)

The worker attaches Playwright to `cdp_url` (ws://<host>:<port>) and drives the
*same* browser the user watches in noVNC. Teardown is `docker rm -f`.

All docker calls run in a thread / subprocess so the event loop never blocks.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from dataclasses import dataclass


class SandboxUnavailable(RuntimeError):
    """Docker missing, image absent, or a container failed to come up."""


@dataclass(frozen=True)
class SandboxHandle:
    container: str
    novnc_url: str          # http://<host>:<port>/  -> redirects to vnc_lite.html
    novnc_ws: str           # ws://<host>:<port>/websockify
    cdp_url: str            # http://<host>:<port>  (Playwright connect_over_cdp auto-discovers the ws)
    novnc_port: int
    cdp_port: int


class SandboxManager:
    def __init__(
        self,
        *,
        image: str = "cua-sandbox:latest",
        host: str = "localhost",
        screen: tuple[int, int] = (1366, 768),
        ready_timeout_s: float = 90.0,
    ) -> None:
        self._image = image
        self._host = host
        self._w, self._h = screen
        self._ready_timeout = ready_timeout_s

    # -- lifecycle ---------------------------------------------------
    async def spawn(self, url: str = "about:blank", *, browser: str = "chromium") -> SandboxHandle:
        if shutil.which("docker") is None:
            raise SandboxUnavailable("docker not found on PATH")
        if not await self._image_present():
            raise SandboxUnavailable(
                f"image {self._image!r} not built — run backend/sandbox_image/build.sh"
            )

        name = f"cua-sandbox-{uuid.uuid4().hex[:10]}"
        args = [
            "docker", "run", "-d", "--rm", "--name", name,
            "--shm-size", "1g",
            "--add-host", "host.docker.internal:host-gateway",
            "-p", "0:6080", "-p", "0:9223",
            "-e", f"CHROME_URL={url}",
            "-e", f"BROWSER={browser}",
            "-e", f"SCREEN_WIDTH={self._w}",
            "-e", f"SCREEN_HEIGHT={self._h}",
            self._image,
        ]
        rc, out, err = await self._run(args)
        if rc != 0:
            raise SandboxUnavailable(f"docker run failed: {err.strip() or out.strip()}")

        try:
            novnc_port = await self._published_port(name, 6080)
            cdp_port = await self._published_port(name, 9223)
            handle = SandboxHandle(
                container=name,
                novnc_url=f"http://{self._host}:{novnc_port}/",
                novnc_ws=f"ws://{self._host}:{novnc_port}/websockify",
                cdp_url=f"http://{self._host}:{cdp_port}",
                novnc_port=novnc_port,
                cdp_port=cdp_port,
            )
            await self._await_cdp(handle)
            return handle
        except Exception:
            await self.stop_by_name(name)
            raise

    async def stop(self, handle: SandboxHandle) -> None:
        await self.stop_by_name(handle.container)

    async def stop_by_name(self, name: str) -> None:
        await self._run(["docker", "rm", "-f", name])

    async def stop_all(self) -> None:
        """Kill any leftover cua-sandbox-* containers (best effort, on shutdown)."""
        rc, out, _ = await self._run(
            ["docker", "ps", "-q", "--filter", "name=cua-sandbox-"]
        )
        ids = [i for i in out.split() if i]
        if ids:
            await self._run(["docker", "rm", "-f", *ids])

    async def is_running(self, container: str) -> bool:
        rc, out, _ = await self._run(["docker", "inspect", "-f", "{{.State.Running}}", container])
        return rc == 0 and out.strip() == "true"

    # -- internals ------------------------------------------------
    async def _image_present(self) -> bool:
        rc, out, _ = await self._run(["docker", "images", "-q", self._image])
        return rc == 0 and bool(out.strip())

    async def _published_port(self, name: str, container_port: int) -> int:
        rc, out, err = await self._run(["docker", "port", name, str(container_port)])
        if rc != 0 or not out.strip():
            raise SandboxUnavailable(f"could not read published port {container_port}: {err.strip()}")
        # e.g. "0.0.0.0:54137\n[::]:54137"
        first = out.strip().splitlines()[0]
        return int(first.rsplit(":", 1)[1])

    async def _await_cdp(self, handle: SandboxHandle) -> None:
        """Poll the CDP HTTP endpoint until the browser answers or we time out."""
        http = f"http://{self._host}:{handle.cdp_port}/json/version"
        deadline = asyncio.get_event_loop().time() + self._ready_timeout
        last = ""
        while asyncio.get_event_loop().time() < deadline:
            rc, out, err = await self._run(["curl", "-sf", "--max-time", "2", http])
            if rc == 0 and out.strip():
                try:
                    json.loads(out)
                    return
                except ValueError:
                    pass
            last = err or out
            await asyncio.sleep(1.0)
        raise SandboxUnavailable(f"sandbox {handle.container} CDP not ready in {self._ready_timeout:.0f}s ({last.strip()})")

    @staticmethod
    async def _run(argv: list[str]) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")
