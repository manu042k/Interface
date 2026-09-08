"use client";

import { Monitor, ExternalLink, CheckCircle2 } from "lucide-react";

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

  const src = `${novncUrl.replace(/\/$/, "")}/vnc_lite.html?path=websockify&autoconnect=1&reconnect=1&resize=scale`;
  return (
    <div className="bg-card flex h-full min-h-0 flex-col overflow-hidden rounded-lg border">
      <div className="border-border/60 flex items-center justify-between border-b px-3 py-1.5 text-xs">
        <span className="text-muted-foreground flex items-center gap-1.5">
          <span
            className={`h-1.5 w-1.5 rounded-full ${interactive ? "bg-success" : "bg-primary animate-pulse"}`}
          />
          live sandbox{" "}
          {interactive ? "· you are in control" : "· automation driving"}
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
      <iframe title="live session" src={src} className="min-h-0 flex-1 w-full" />
    </div>
  );
}
