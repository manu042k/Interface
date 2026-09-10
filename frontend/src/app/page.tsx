"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Plus, X, Loader2, CheckCircle2, AlertTriangle } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/page-header";

/* ---- client-side validation ------------------------------------------------ */

function goalError(v: string): string | null {
  const g = v.trim();
  if (!g) return null; // empty -> just keep the button disabled, no scolding
  if (g.length < 12) return "Too short — say what the agent should do (~12+ characters).";
  const words = g.split(/\s+/).filter((w) => w.length >= 2);
  if (words.length < 3) return "Write a sentence — at least 3 words.";
  if (!/[A-Za-z]/.test(g) || /^(.)\1*$/.test(g.replace(/\s+/g, "")))
    return "That looks like placeholder text.";
  return null;
}

function targetError(v: string): string | null {
  const t = v.trim();
  if (!t) return null;
  let u: URL;
  try {
    u = new URL(t);
  } catch {
    return "Not a valid URL — include https://";
  }
  if (u.protocol !== "http:" && u.protocol !== "https:")
    return "Must be an http(s) URL.";
  if (!u.hostname.includes(".") && u.hostname !== "localhost")
    return `“${u.hostname}” is not a valid hostname.`;
  return null;
}

type Reach =
  | { state: "idle" | "checking" }
  | { state: "ok"; status: number; redirected: boolean }
  | { state: "warn"; detail: string };

export default function NewRunPage() {
  const router = useRouter();
  const [goalName, setGoalName] = useState("");
  const [description, setDescription] = useState("");
  const [target, setTarget] = useState("");
  const [params, setParams] = useState<{ k: string; v: string }[]>([
    { k: "", v: "" },
  ]);
  const [confirmRisky, setConfirmRisky] = useState(false);
  const [busy, setBusy] = useState(false);
  const [touched, setTouched] = useState<{ goal?: boolean; target?: boolean }>({});
  const [reach, setReach] = useState<Reach>({ state: "idle" });

  const descErr = goalError(description);
  const targetErr = targetError(target);
  const canSubmit =
    !busy && !!description.trim() && !!target.trim() && !descErr && !targetErr;

  // debounced reachability probe once the URL is well-formed. All state writes
  // happen inside async callbacks (not the effect body) so a bad URL just stops
  // scheduling — the render below ignores `reach` whenever `targetErr` is set.
  useEffect(() => {
    const url = target.trim();
    if (targetErr || !url) return;
    let live = true;
    const startChecking = setTimeout(() => live && setReach({ state: "checking" }), 0);
    const handle = setTimeout(async () => {
      let next: Reach;
      try {
        const r = await api.probeTarget(url);
        next = r.ok
          ? {
              state: "ok",
              status: r.status ?? 200,
              redirected: !!r.final_url && r.final_url !== url,
            }
          : {
              state: "warn",
              detail:
                r.reason === "unreachable"
                  ? "Couldn't reach this URL from the server."
                  : r.reason === "http_error"
                    ? `Server answered ${r.status}.`
                    : (r.detail ?? "Couldn't verify this URL."),
            };
      } catch {
        next = { state: "warn", detail: "Couldn't verify this URL." };
      }
      if (live) setReach(next);
    }, 500);
    return () => {
      live = false;
      clearTimeout(startChecking);
      clearTimeout(handle);
    };
  }, [target, targetErr]);

  const paramWarnings = useMemo(
    () =>
      params
        .filter((r) => r.v.trim() && !r.k.trim())
        .map((r) => `Value “${r.v}” has no key and will be dropped.`),
    [params],
  );

  async function submit() {
    setBusy(true);
    try {
      const p: Record<string, string> = {};
      for (const { k, v } of params) if (k.trim()) p[k.trim()] = v;
      const { run_id } = await api.startRun({
        goal: description.trim(),
        capability_name: goalName.trim() || undefined,
        target: target.trim(),
        params: p,
        confirm_risky: confirmRisky,
      });
      toast.success("Discovery run started - watch it live");
      router.push(`/runs/${run_id}`);
    } catch (e) {
      toast.error(String((e as Error).message));
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <PageHeader
        title="Start a discovery run"
        description="Give the agent a goal and an entry point. It drives the real UI and you watch it live."
      />

      <Card>
        <CardContent className="space-y-5 pt-6">
          <div className="space-y-2">
            <Label htmlFor="goal-name">Goal name</Label>
            <Input
              id="goal-name"
              value={goalName}
              onChange={(e) => setGoalName(e.target.value)}
              placeholder="e.g. Read member savings balance"
            />
            <p className="text-muted-foreground text-xs">
              A few words. Becomes the recorded capability&rsquo;s name.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="description">Description</Label>
            <Textarea
              id="description"
              rows={3}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              onBlur={() => setTouched((t) => ({ ...t, goal: true }))}
              aria-invalid={!!(touched.goal && descErr)}
              className="resize-none"
              placeholder="What should the agent do? Include member numbers, credentials, and the exact steps in plain language."
            />
            {touched.goal && descErr && (
              <p className="text-destructive text-xs">{descErr}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="target">Target entry point</Label>
            <Input
              id="target"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
              onBlur={() => setTouched((t) => ({ ...t, target: true }))}
              aria-invalid={!!(touched.target && targetErr)}
              placeholder="https://…"
            />
            {touched.target && targetErr ? (
              <p className="text-destructive text-xs">{targetErr}</p>
            ) : targetErr || !target.trim() ? (
              <p className="text-muted-foreground text-xs">
                The URL the agent starts from.
              </p>
            ) : reach.state === "checking" ? (
              <p className="text-muted-foreground flex items-center gap-1 text-xs">
                <Loader2 className="h-3 w-3 animate-spin" /> Checking the link…
              </p>
            ) : reach.state === "ok" ? (
              <p className="flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                <CheckCircle2 className="h-3 w-3" /> Reachable (HTTP {reach.status}
                {reach.redirected ? ", redirects" : ""}).
              </p>
            ) : reach.state === "warn" ? (
              <p className="text-warning flex items-center gap-1 text-xs">
                <AlertTriangle className="h-3 w-3" /> {reach.detail} You can still
                run it.
              </p>
            ) : (
              <p className="text-muted-foreground text-xs">
                The URL the agent starts from.
              </p>
            )}
          </div>

          <div className="space-y-2">
            <Label>Typed parameters</Label>
            <div className="space-y-2">
              {params.map((row, i) => (
                <div key={i} className="flex items-center gap-2">
                  <Input
                    placeholder="key"
                    value={row.k}
                    onChange={(e) =>
                      setParams((p) =>
                        p.map((r, j) =>
                          j === i ? { ...r, k: e.target.value } : r,
                        ),
                      )
                    }
                  />
                  <Input
                    placeholder="value"
                    value={row.v}
                    onChange={(e) =>
                      setParams((p) =>
                        p.map((r, j) =>
                          j === i ? { ...r, v: e.target.value } : r,
                        ),
                      )
                    }
                  />
                  <Button
                    variant="ghost"
                    size="icon"
                    className="text-muted-foreground shrink-0"
                    onClick={() =>
                      setParams((p) => p.filter((_, j) => j !== i))
                    }
                  >
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ))}
              <Button
                variant="outline"
                size="sm"
                onClick={() => setParams((p) => [...p, { k: "", v: "" }])}
              >
                <Plus className="mr-1 h-4 w-4" /> Add parameter
              </Button>
              {paramWarnings.map((w) => (
                <p key={w} className="text-warning text-xs">
                  {w}
                </p>
              ))}
            </div>
          </div>

          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={confirmRisky}
              onChange={(e) => setConfirmRisky(e.target.checked)}
              className="accent-primary h-4 w-4 rounded"
            />
            Pre-authorize risky / irreversible steps for this goal
          </label>

          <div className="flex justify-end pt-1">
            <Button onClick={submit} disabled={!canSubmit} size="lg">
              {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Run discovery
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
