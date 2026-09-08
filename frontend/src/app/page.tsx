"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Plus, Trash2, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

const EXAMPLES = [
  "look up member 12345 and read their current savings balance",
  "open a new Holiday Club sub-account for member 12345 and reach the confirmation screen",
];

export default function NewRunPage() {
  const router = useRouter();
  const [goal, setGoal] = useState(EXAMPLES[0]);
  const [target, setTarget] = useState(
    "http://host.docker.internal:8799/search",
  );
  const [name, setName] = useState("read_savings_balance");
  const [params, setParams] = useState<{ k: string; v: string }[]>([
    { k: "member_id", v: "12345" },
  ]);
  const [confirmRisky, setConfirmRisky] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit() {
    setBusy(true);
    try {
      const p: Record<string, string> = {};
      for (const { k, v } of params) if (k.trim()) p[k.trim()] = v;
      const { run_id } = await api.startRun({
        goal,
        target,
        params: p,
        capability_name: name || undefined,
        confirm_risky: confirmRisky,
      });
      toast.success("Discovery run started");
      router.push(`/runs/${run_id}`);
    } catch (e) {
      toast.error(String((e as Error).message));
      setBusy(false);
    }
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">
          Start a discovery run
        </h1>
        <p className="text-muted-foreground mt-1">
          Give the agent a goal and an entry point. It drives the real UI, and
          you watch it live.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle>Goal</CardTitle>
          <CardDescription>
            Plain language. The run is recorded as a replayable capability.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="space-y-2">
            <Label htmlFor="goal">Goal</Label>
            <Textarea
              id="goal"
              rows={2}
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
            />
            <div className="flex flex-wrap gap-2 pt-1">
              {EXAMPLES.map((ex) => (
                <button
                  key={ex}
                  onClick={() => setGoal(ex)}
                  className="text-muted-foreground hover:text-foreground border-border rounded-full border px-2.5 py-1 text-xs"
                >
                  {ex.length > 54 ? ex.slice(0, 54) + "…" : ex}
                </button>
              ))}
            </div>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="target">Target entry point</Label>
              <Input
                id="target"
                value={target}
                onChange={(e) => setTarget(e.target.value)}
              />
              <p className="text-muted-foreground text-xs">
                The sandbox reaches your host via{" "}
                <code>host.docker.internal</code>.
              </p>
            </div>
            <div className="space-y-2">
              <Label htmlFor="name">Capability name</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
          </div>

          <div className="space-y-2">
            <Label>Typed parameters</Label>
            <div className="space-y-2">
              {params.map((row, i) => (
                <div key={i} className="flex gap-2">
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
                    onClick={() =>
                      setParams((p) => p.filter((_, j) => j !== i))
                    }
                  >
                    <Trash2 className="h-4 w-4" />
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
              className="accent-primary h-4 w-4"
            />
            Pre-authorize risky / irreversible steps for this goal
          </label>

          <Button onClick={submit} disabled={busy} size="lg">
            {busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Run discovery
          </Button>
        </CardContent>
      </Card>
    </div>
  );
}
