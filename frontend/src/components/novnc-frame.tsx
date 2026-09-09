"use client";

import { useEffect, useRef, useState } from "react";
import {
  Monitor,
  ExternalLink,
  CheckCircle2,
  Lock,
  Hand,
  Loader2,
  Maximize2,
} from "lucide-react";

// Native framebuffer size of the sandbox display (see backend/sandbox_image).
const FB_W = 1280;
const FB_H = 720;

function feedSrc(novncUrl: string): string {
  const base = novncUrl.replace(/\/$/, "");
  // Constant URL for the whole run - it must not change when the operator claims
  // the handoff, or the iframe reloads and the noVNC session drops.
  return `${base}/vnc_lite.html?path=websockify&autoconnect=1&reconnect=1&resize=scale`;
}

/**
 * The scaled noVNC iframe. Its height is derived purely from its own measured
 * WIDTH (fixed 16:9) - so whatever appears above it (the handoff panel, a
 * banner) only pushes it down, it never resizes or reconnects. The iframe is
 * pinned at the framebuffer size and CSS-scaled from its top-left corner.
 */
export function LiveFeed({
  novncUrl,
  interactive,
}: {
  novncUrl: string;
  interactive: boolean;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(0);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const obs = new ResizeObserver(([entry]) => {
      setScale(Math.max(0, entry.contentRect.width / FB_W));
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, []);

  return (
    <div
      ref={wrapRef}
      className="bg-muted/40 relative w-full overflow-hidden"
      style={{ aspectRatio: `${FB_W} / ${FB_H}` }}
    >
      <iframe
        title="live session"
        src={feedSrc(novncUrl)}
        style={{
          width: FB_W,
          height: FB_H,
          border: "none",
          position: "absolute",
          top: 0,
          left: 0,
          transform: `scale(${scale})`,
          transformOrigin: "top left",
          pointerEvents: interactive ? "auto" : "none",
        }}
      />
      {!interactive && (
        <div
          className="absolute inset-0"
          aria-hidden
          title="View only - the model is driving this run"
        />
      )}
    </div>
  );
}

export function NoVncFrame({
  novncUrl,
  interactive,
  ended,
  starting,
  onExpand,
}: {
  novncUrl: string | null;
  interactive: boolean;
  ended?: boolean;
  /** run is live and a sandbox is still spinning up - not "headless, no feed" */
  starting?: boolean;
  /** show a maximize button that opens the feed in a modal */
  onExpand?: () => void;
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
          {starting ? (
            <>
              <Loader2 className="mx-auto mb-2 h-6 w-6 animate-spin" />
              <p className="text-sm">
                Starting the live sandbox…
                <br />
                the feed appears here once the container is up.
              </p>
            </>
          ) : (
            <>
              <Monitor className="mx-auto mb-2 h-6 w-6" />
              <p className="text-sm">
                No live sandbox for this run.
                <br />
                Start the gateway with <code>CUA_USE_SANDBOX=1</code>.
              </p>
            </>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="bg-card flex flex-col overflow-hidden rounded-lg border">
      <div className="border-border/60 flex items-center justify-between gap-2 border-b px-3 py-1.5 text-xs">
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
        <span className="flex items-center gap-3">
          {onExpand && (
            <button
              type="button"
              onClick={onExpand}
              className="text-muted-foreground hover:text-foreground flex items-center gap-1"
              title="Expand"
            >
              <Maximize2 className="h-3 w-3" /> expand
            </button>
          )}
          <a
            href={feedSrc(novncUrl)}
            target="_blank"
            rel="noreferrer"
            className="text-muted-foreground hover:text-foreground flex items-center gap-1"
          >
            open <ExternalLink className="h-3 w-3" />
          </a>
        </span>
      </div>
      <LiveFeed novncUrl={novncUrl} interactive={interactive} />
    </div>
  );
}
