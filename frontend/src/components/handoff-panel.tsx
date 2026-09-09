"use client";

import { useEffect, useRef, useState } from "react";
import { HandMetal, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";

const OPERATOR = "op_" + Math.random().toString(36).slice(2, 6);

type Iv = {
  intervention_id: string;
  status: string;
  claimed_by: string | null;
  step_index: number;
  reason: string;
  attempting: string | null;
};

export function HandoffPanel({
  runId,
  onResolved,
}: {
  runId: string;
  onResolved: () => void;
}) {
  const [iv, setIv] = useState<Iv | null>(null);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const releasing = useRef(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const cur = await api.runIntervention(runId);
        if (alive && !releasing.current) setIv(cur);
      } catch {
        /* ignore */
      }
    };
    tick();
    const t = setInterval(tick, 2500);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [runId]);

  if (!iv) return null;
  const step = (m: string) => setLog((l) => [...l, m]);
  const inControl = iv.status === "claimed";

  async function claimAndTake() {
    if (!iv) return;
    setBusy(true);
    try {
      await api.claim(iv.intervention_id, OPERATOR);
      const h = await api.takeControl(iv.intervention_id, OPERATOR);
      step(`control acquired - ${h.live_handle} (${h.remote_display})`);
      setIv({ ...iv, status: "claimed", claimed_by: OPERATOR });
      toast.success("You are in control of the live session");
    } catch (e) {
      toast.error(String((e as Error).message));
    }
    setBusy(false);
  }

  async function handBack() {
    if (!iv) return;
    setBusy(true);
    releasing.current = true;
    try {
      // release without a goal checkpoint: control returns to automation and it
      // continues from where it is (the previous hard-coded MockBank checkpoint
      // could never hold on any other site, leaving the run wedged).
      const out = await api.release(iv.intervention_id, iv.claimed_by ?? OPERATOR);
      step(`handed back - control returned to automation`);
      toast.success(out.detail || "Control returned to automation");
      setIv(null);
      onResolved();
    } catch (e) {
      releasing.current = false;
      toast.error(String((e as Error).message));
    }
    setBusy(false);
  }

  // Once the operator is driving, collapse to a slim bar so the live page
  // underneath stays usable.
  if (inControl) {
    return (
      <div className="border-warning/50 bg-card/95 flex items-center gap-3 rounded-lg border px-3 py-2 shadow-lg backdrop-blur">
        <HandMetal className="text-warning h-4 w-4 shrink-0" />
        <span className="min-w-0 flex-1 truncate text-sm">
          <span className="text-muted-foreground">{iv.claimed_by} in control</span>{" "}
          - drive the page below
        </span>
        <Button size="sm" onClick={handBack} disabled={busy}>
          {busy && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}
          Hand control back
        </Button>
      </div>
    );
  }

  return (
    <div className="border-warning/50 bg-card/95 mx-auto max-w-2xl rounded-lg border p-4 shadow-xl backdrop-blur">
      <div className="flex items-start gap-3">
        <HandMetal className="text-warning mt-0.5 h-5 w-5 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            The run is stuck at step {iv.step_index} - a human is needed.
          </p>
          {iv.attempting && (
            <p className="mt-1 text-sm">
              <span className="text-muted-foreground">The agent was trying to: </span>
              {iv.attempting}
            </p>
          )}
          <p className="text-muted-foreground mt-1 text-xs">
            Why it stopped: {iv.reason}
          </p>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button size="sm" onClick={claimAndTake} disabled={busy}>
              {busy && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}
              Claim &amp; take control
            </Button>
          </div>

          {log.length > 0 && (
            <pre className="text-muted-foreground mt-3 rounded bg-black/5 p-2 text-xs">
              {log.join("\n")}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
}
