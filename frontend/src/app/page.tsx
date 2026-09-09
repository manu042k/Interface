"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Plus, X, Loader2, Radio } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

export default function NewRunPage() {
  const router = useRouter();
  const [goalName, setGoalName] = useState("");
  const [description, setDescription] = useState("");
  const [target, setTarget] = useState("http://localhost:8799/search");
  const [params, setParams] = useState<{ k: string; v: string }[]>([
    { k: "member_id", v: "12345" },
  ]);
  const [confirmRisky, setConfirmRisky] = useState(false);
  const [busy, setBusy] = useState(false);

  const { data: active } = useQuery({
    queryKey: ["active-run"],
    queryFn: api.activeRun,
    refetchInterval: 3000,
  });

  async function submit() {
    setBusy(true);
    try {
      const p: Record<string, string> = {};
      for (const { k, v } of params) if (k.trim()) p[k.trim()] = v;
      const { run_id } = await api.startRun({
        goal: description.trim(),
        capability_name: goalName.trim() || undefined,
        target,
        params: p,
        confirm_risky: confirmRisky,
      });
      toast.success("Discovery run started — watch it live");
      router.push(`/runs/${run_id}`);
    } catch (e) {
      toast.error(String((e as Error).message));
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">
            Start a discovery run
          </h1>
          <p className="text-muted-foreground mt-1">
            Give the agent a goal and an entry point. It drives the real UI and
            you watch it live.
          </p>
        </div>
        {active && (
          <Button asChild variant="outline">
            <Link href={`/runs/${active.run_id}`}>
              <Radio className="text-primary mr-1.5 h-4 w-4 animate-pulse" />
              View current run
            </Link>
          </Button>
        )}
      </header>

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
              className="resize-none"
              placeholder="What should the agent do? Include member numbers, credentials, and the exact steps in plain language."
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="target">Target entry point</Label>
            <Input
              id="target"
              value={target}
              onChange={(e) => setTarget(e.target.value)}
            />
            <p className="text-muted-foreground text-xs">
              The URL the agent starts from.
            </p>
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

          <div className="flex items-center gap-3 pt-1">
            <Button
              onClick={submit}
              disabled={busy || !description.trim() || !target.trim()}
              size="lg"
            >
              {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Run discovery
            </Button>
            <Link
              href="/runs"
              className="text-muted-foreground hover:text-foreground text-sm"
            >
              past runs
            </Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
