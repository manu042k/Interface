"use client";

type Step = {
  step_index: number;
  action_type: string;
  description: string;
  idempotent: boolean;
  locator_spec: { kind: string; rank: number; rationale: string }[];
  value_binding?: { literal?: string | null; param?: string | null } | null;
  output_binding?: { field: string; shape: string } | null;
};

type JsonSchema = {
  properties?: Record<
    string,
    { type?: string; example?: unknown; "x-shape"?: string; "x-sensitive"?: boolean }
  >;
  required?: string[];
};

type Artifact = {
  input_schema: JsonSchema;
  output_schema: JsonSchema;
  checkpoint: { kind?: string; params?: Record<string, unknown> } | null;
  risk_class: string;
  steps: Step[];
  known_outcomes: { code: string; when: unknown }[];
  recoverable_rules: { name: string; action: string }[];
};

export function ArtifactView({ artifact }: { artifact: Artifact }) {
  const inputs = Object.entries(artifact.input_schema?.properties ?? {});
  const outputs = Object.entries(artifact.output_schema?.properties ?? {});
  const cp = artifact.checkpoint;
  return (
    <div className="space-y-4 text-sm">
      <div className="grid gap-3">
        <div>
          <span className="text-muted-foreground text-xs uppercase">takes</span>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {inputs.length === 0 && (
              <span className="text-muted-foreground text-xs">no inputs</span>
            )}
            {inputs.map(([name, v]) => (
              <code key={name} className="bg-muted rounded px-1.5 py-0.5 text-xs">
                {name}
                <span className="text-muted-foreground">
                  {" : "}
                  {v.type ?? "string"}
                </span>
                {v["x-sensitive"] && (
                  <span className="text-warning"> · sensitive</span>
                )}
              </code>
            ))}
          </div>
        </div>
        <div>
          <span className="text-muted-foreground text-xs uppercase">
            returns
          </span>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {outputs.length === 0 && (
              <span className="text-muted-foreground text-xs">no outputs</span>
            )}
            {outputs.map(([name, v]) => (
              <code key={name} className="bg-muted rounded px-1.5 py-0.5 text-xs">
                {name}
                <span className="text-muted-foreground">
                  {" : "}
                  {v["x-shape"] ?? v.type ?? "string"}
                </span>
              </code>
            ))}
          </div>
        </div>
        <div>
          <span className="text-muted-foreground text-xs uppercase">
            checkpoint
          </span>
          <p className="mt-1 text-xs">
            {cp?.kind ? (
              <>
                <code className="bg-muted rounded px-1.5 py-0.5">{cp.kind}</code>
                {cp.params && Object.keys(cp.params).length > 0 && (
                  <span className="text-muted-foreground">
                    {" "}
                    {Object.entries(cp.params)
                      .map(([k, val]) => `${k}=${String(val)}`)
                      .join(", ")}
                  </span>
                )}
              </>
            ) : (
              <span className="text-muted-foreground">none</span>
            )}
          </p>
        </div>
        <div>
          <span className="text-muted-foreground text-xs uppercase">
            known outcomes
          </span>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {artifact.known_outcomes.map((o) => (
              <code
                key={o.code}
                className="bg-warning/10 text-warning rounded px-1.5 py-0.5 text-xs"
              >
                {o.code}
              </code>
            ))}
          </div>
        </div>
        <div>
          <span className="text-muted-foreground text-xs uppercase">
            recoverable rules
          </span>
          <div className="mt-1 flex flex-wrap gap-1.5">
            {artifact.recoverable_rules.map((r) => (
              <code
                key={r.name}
                className="bg-muted rounded px-1.5 py-0.5 text-xs"
              >
                {r.name} · {r.action}
              </code>
            ))}
          </div>
        </div>
      </div>

      <div>
        <span className="text-muted-foreground text-xs uppercase">steps</span>
        <ol className="mt-2 space-y-3">
          {artifact.steps.map((s) => (
            <li key={s.step_index} className="rounded-lg border p-3">
              <div className="flex flex-wrap items-baseline gap-2">
                <span className="text-muted-foreground text-xs">
                  {s.step_index}
                </span>
                <span className="font-medium">{s.action_type}</span>
                <span className="text-muted-foreground">{s.description}</span>
                <span className="text-muted-foreground text-xs">
                  {s.idempotent ? "idempotent" : "NON-idempotent"}
                </span>
                {s.value_binding?.param && (
                  <code className="text-xs">←param:{s.value_binding.param}</code>
                )}
                {s.output_binding && (
                  <code className="text-xs">
                    →{s.output_binding.field} ({s.output_binding.shape})
                  </code>
                )}
              </div>
              {s.locator_spec.length > 0 && (
                <ul className="mt-2 space-y-1">
                  {s.locator_spec.map((ls, i) => (
                    <li key={i} className="text-muted-foreground text-xs">
                      <span className="text-foreground">
                        rank {ls.rank} · {ls.kind}
                      </span>{" "}
                      — {ls.rationale}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
