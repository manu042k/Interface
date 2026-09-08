"use client";

import { useEffect, useRef } from "react";
import { wsUrl } from "@/lib/api";

export function SandboxTerminal({ runId }: { runId: string }) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let disposed = false;
    let cleanup = () => {};

    (async () => {
      const { Terminal } = await import("@xterm/xterm");
      const { FitAddon } = await import("@xterm/addon-fit");
      if (disposed || !hostRef.current) return;

      const term = new Terminal({
        fontSize: 12,
        fontFamily:
          "ui-monospace, SFMono-Regular, Menlo, monospace",
        theme: { background: "#201515", foreground: "#f8f4f0", cursor: "#ff4f00" },
        cursorBlink: true,
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.open(hostRef.current);
      fit.fit();

      const ws = new WebSocket(wsUrl(`/ws/runs/${runId}/terminal`));
      ws.binaryType = "arraybuffer";
      ws.onmessage = (m) => {
        if (typeof m.data === "string") term.write(m.data);
        else term.write(new Uint8Array(m.data));
      };
      ws.onopen = () => term.writeln("\x1b[90m[connected to sandbox]\x1b[0m");
      term.onData((d) => ws.readyState === 1 && ws.send(d));

      const onResize = () => fit.fit();
      window.addEventListener("resize", onResize);
      cleanup = () => {
        window.removeEventListener("resize", onResize);
        ws.close();
        term.dispose();
      };
    })();

    return () => {
      disposed = true;
      cleanup();
    };
  }, [runId]);

  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="border-border/60 border-b bg-[#201515] px-3 py-1.5 text-xs font-medium text-[#f8f4f0]">
        sandbox terminal — <code>docker exec bash</code>
      </div>
      <div ref={hostRef} className="h-56 bg-[#201515] p-2" />
    </div>
  );
}
