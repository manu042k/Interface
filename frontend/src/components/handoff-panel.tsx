"use client";

import { useEffect, useState } from "react";
import { HandMetal, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api, type Intervention } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

const OPERATOR = "op_" + Math.random().toString(36).slice(2, 6);

export function HandoffPanel({
  runId,
  onResolved,
}: {
  runId: string;
  onResolved: () => void;
}) {
  const [iv, setIv] = useState<Intervention | null>(null);
  const [inControl, setInControl] = useState(false);
  const [busy, setBusy] = useState(false);
  const [log, setLog] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const open = await api.interventions("open");
        const mine = open.find((i) => i.run_id === runId) ?? null;
        if (alive) setIv(mine);
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

  const step = (msg: string) => setLog((l) => [...l, msg]);

  async function claimAndTake() {
    if (!iv) return;
    setBusy(true);
    try {
      await api.claim(iv.intervention_id, OPERATOR);
      const h = await api.takeControl(iv.intervention_id, OPERATOR);
      setInControl(true);
      step(`control acquired — ${h.live_handle} (${h.remote_display})`);
      toast.success("You are in control of the live session");
    } catch (e) {
      toast.error(String((e as Error).message));
    }
    setBusy(false);
  }

  async function handBack() {
    if (!iv) return;
    setBusy(true);
    try {
      const out = await api.release(iv.intervention_id, OPERATOR, {
        kind: "text_present",
        params: { text: "Savings" },
      });
      step(
        `handed back — resumed=${out.resumed}, checkpoint_holds=${out.checkpoint_already_holds}`,
      );
      toast.success(out.detail);
      onResolved();
    } catch (e) {
      toast.error(String((e as Error).message));
    }
    setBusy(false);
  }

  return (
    <div className="border-warning/40 bg-warning/8 rounded-lg border p-4">
      <div className="flex items-start gap-3">
        <HandMetal className="text-warning mt-0.5 h-5 w-5 shrink-0" />
        <div className="min-w-0 flex-1">
          <p className="font-medium">
            The run is stuck at step {iv.step_index} — a human is needed.
          </p>
          <p className="text-muted-foreground text-sm">{iv.reason}</p>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            {!inControl ? (
              <Button size="sm" onClick={claimAndTake} disabled={busy}>
                {busy && <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />}
                Claim &amp; take control
              </Button>
            ) : (
              <>
                <span className="text-muted-foreground text-sm">
                  Drive the page in the live view above, then:
                </span>
                <Button size="sm" onClick={handBack} disabled={busy}>
                  {busy && (
                    <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                  )}
                  Hand control back
                </Button>
              </>
            )}
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
