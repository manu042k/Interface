"use client";

import { Monitor, ExternalLink, CheckCircle2, Lock, Hand } from "lucide-react";

export function NoVncFrame({
  novncUrl,
  interactive,
  ended,
}: {
  novncUrl: string | null;
  interactive: boolean;
  ended?: boolean;
}) {
  if (ended) {
    return (
      <div className="bg-card text-muted-foreground flex h-full min-h-0 flex-col items-center justify-center rounded-lg border p-6 text-center">
        <CheckCircle2 className="text-success mb-2 h-6 w-6" />
        <p className="text-sm">
          The run has finished and its sandbox was torn down.
          <br />
          The full step history is on the right; screenshots are in the report.
        </p>
      </div>
    );
  }

  if (!novncUrl) {
    return (
      <div className="bg-card text-muted-foreground grid h-full min-h-0 place-items-center rounded-lg border">
        <div className="text-center">
          <Monitor className="mx-auto mb-2 h-6 w-6" />
          <p className="text-sm">
            No live sandbox for this run.
            <br />
            Start the gateway with <code>CUA_USE_SANDBOX=1</code>.
          </p>
        </div>
      </div>
    );
  }

  const base = novncUrl.replace(/\/$/, "");
  // view_only=1 tells noVNC to send no pointer/keyboard events; pointer-events
  // none on the iframe is the belt-and-braces so the parent never forwards a
  // click either. Both lift only when an operator has taken the handoff.
  const src =
    `${base}/vnc_lite.html?path=websockify&autoconnect=1&reconnect=1&resize=scale` +
    (interactive ? "" : "&view_only=1");

  return (
    <div className="bg-card flex h-full min-h-0 flex-col overflow-hidden rounded-lg border">
      <div className="border-border/60 flex items-center justify-between border-b px-3 py-1.5 text-xs">
        <span className="text-muted-foreground flex items-center gap-1.5">
          <span
            className={`h-1.5 w-1.5 rounded-full ${interactive ? "bg-success" : "bg-primary animate-pulse"}`}
          />
          live sandbox
          {interactive ? (
            <span className="text-success inline-flex items-center gap-1 font-medium">
              <Hand className="h-3 w-3" /> you are in control
            </span>
          ) : (
            <span className="inline-flex items-center gap-1">
              <Lock className="h-3 w-3" /> view only · automation driving
            </span>
          )}
        </span>
        <a
          href={src}
          target="_blank"
          rel="noreferrer"
          className="text-muted-foreground hover:text-foreground flex items-center gap-1"
        >
          open <ExternalLink className="h-3 w-3" />
        </a>
      </div>
      <div className="relative min-h-0 flex-1">
        <iframe
          title="live session"
          src={src}
          className="h-full w-full"
          style={{ pointerEvents: interactive ? "auto" : "none" }}
        />
        {!interactive && (
          <div className="absolute inset-0" aria-hidden title="View only — the model is driving this run" />
        )}
      </div>
    </div>
  );
}
