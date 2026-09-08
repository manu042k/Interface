"use client";

import { Crosshair, Flag, RotateCcw, ShieldAlert, Target } from "lucide-react";

type Condition = {
  kind?: string;
  params?: Record<string, unknown>;
  description?: string;
};

type Locator = {
  kind: string;
  rank: number;
  rationale: string;
  params?: Record<string, unknown>;
};

type Step = {
  step_index: number;
  action_type: string;
  description: string;
  idempotent: boolean;
  locator_spec: Locator[];
  value_binding?: { literal?: string | null; param?: string | null } | null;
  output_binding?: { field: string; shape: string } | null;
  step_checkpoint?: Condition | null;
};

type SchemaProp = {
  type?: string;
  example?: unknown;
  "x-shape"?: string;
  "x-sensitive"?: boolean;
};

type Artifact = {
  goal_description?: string;
  input_schema?: { properties?: Record<string, SchemaProp> };
  output_schema?: { properties?: Record<string, SchemaProp> };
  checkpoint?: Condition | null;
  risk_class?: string;
  steps: Step[];
  known_outcomes: { code: string; when?: Condition; message?: string }[];
  recoverable_rules: {
    name: string;
    action: string;
    when?: Condition;
  }[];
};

/* ---- humanisers ---------------------------------------------------------- */

function phraseCondition(c?: Condition | null): string {
  if (!c?.kind) return "—";
  const p = c.params ?? {};
  const s = (k: string) => (p[k] === undefined ? "…" : String(p[k]));
  switch (c.kind) {
    case "url_matches":
      return `URL matches ${s("pattern") !== "…" ? s("pattern") : s("url")}`;
    case "text_present":
      return `“${s("text")}” is visible on the page`;
    case "text_absent":
      return `“${s("text")}” is no longer on the page`;
    case "element_present":
      return `${s("selector")} is present`;
    case "element_absent":
      return `${s("selector")} is absent`;
    case "extract_equals":
      return `extracted ${s("as")} equals “${s("value")}”`;
    case "extract_matches":
      return `extracted ${s("as")} matches /${s("pattern")}/`;
    default:
      return `${c.kind} ${JSON.stringify(p)}`;
  }
}

function describeLocator(params?: Record<string, unknown>): string {
  if (!params || Object.keys(params).length === 0) return "";
  return Object.entries(params)
    .filter(([k]) => !k.startsWith("_"))
    .map(([k, v]) => {
      const val = typeof v === "string" ? v : JSON.stringify(v);
      return `${k}=${/\s/.test(String(val)) ? `"${val}"` : val}`;
    })
    .join(" · ");
}

const ACTION_TONE: Record<string, string> = {
  extract: "bg-primary/10 text-primary",
  assert_state: "bg-muted text-muted-foreground",
  wait_for: "bg-muted text-muted-foreground",
};

/* ---- component --------------------------------------------------------- */

export function ArtifactView({ artifact }: { artifact: Artifact }) {
  const inputs = Object.entries(artifact.input_schema?.properties ?? {});
  const outputs = Object.entries(artifact.output_schema?.properties ?? {});
  const risky = artifact.risk_class === "risky_irreversible";

  return (
    <div className="space-y-6">
      {risky && (
        <div className="border-warning/40 bg-warning/10 text-warning flex items-start gap-2 rounded-lg border p-3 text-xs">
          <ShieldAlert className="mt-px h-4 w-4 shrink-0" />
          <span>
            This capability performs an{" "}
            <strong>irreversible action</strong>. Approving lets agents invoke it
            unattended — check every non-idempotent step below.
          </span>
        </div>
      )}

      {/* Contract ------------------------------------------------------- */}
      <section className="border-border/70 divide-border/60 divide-y rounded-lg border">
        <ContractRow label="Takes">
          {inputs.length === 0 ? (
            <Muted>no inputs</Muted>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {inputs.map(([name, v]) => (
                <Chip key={name}>
                  {name}
                  <span className="text-muted-foreground">
                    &nbsp;: {v.type ?? "string"}
                  </span>
                  {v["x-sensitive"] && (
                    <span className="text-warning">&nbsp;· sensitive</span>
                  )}
                </Chip>
              ))}
            </div>
          )}
        </ContractRow>
        <ContractRow label="Returns">
          {outputs.length === 0 ? (
            <Muted>no outputs</Muted>
          ) : (
            <div className="flex flex-wrap gap-1.5">
              {outputs.map(([name, v]) => (
                <Chip key={name}>
                  {name}
                  <span className="text-muted-foreground">
                    &nbsp;: {v["x-shape"] ?? v.type ?? "string"}
                  </span>
                </Chip>
              ))}
            </div>
          )}
        </ContractRow>
        <ContractRow label="Done when">
          <span className="flex items-start gap-1.5 text-sm">
            <Target className="text-primary mt-0.5 h-3.5 w-3.5 shrink-0" />
            {phraseCondition(artifact.checkpoint)}
          </span>
        </ContractRow>
      </section>

      {/* Steps -------------------------------------------------------- */}
      <section>
        <SectionTitle count={artifact.steps.length}>Steps</SectionTitle>
        <ol className="mt-3 space-y-2.5">
          {artifact.steps.map((s) => (
            <li
              key={s.step_index}
              className="border-border/70 rounded-lg border p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="bg-muted text-muted-foreground grid h-5 w-5 shrink-0 place-items-center rounded text-[11px] font-medium">
                  {s.step_index}
                </span>
                <code
                  className={`rounded px-1.5 py-0.5 text-xs font-semibold ${
                    ACTION_TONE[s.action_type] ?? "bg-secondary"
                  }`}
                >
                  {s.action_type}
                </code>
                <span className="text-sm">{s.description}</span>
                <span className="ml-auto shrink-0">
                  {s.idempotent ? (
                    <span className="text-muted-foreground text-[11px]">
                      repeatable
                    </span>
                  ) : (
                    <span className="bg-warning/12 text-warning rounded px-1.5 py-0.5 text-[11px] font-medium">
                      mutates state
                    </span>
                  )}
                </span>
              </div>

              {(s.value_binding || s.output_binding || s.step_checkpoint) && (
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 pl-7 text-xs">
                  {s.value_binding?.param && (
                    <span>
                      <span className="text-muted-foreground">input </span>
                      <code>← {s.value_binding.param}</code>
                    </span>
                  )}
                  {s.value_binding?.literal != null && (
                    <span className="text-muted-foreground">
                      input ← “{s.value_binding.literal}”
                    </span>
                  )}
                  {s.output_binding && (
                    <span>
                      <span className="text-muted-foreground">output </span>
                      <code>
                        → {s.output_binding.field} ({s.output_binding.shape})
                      </code>
                    </span>
                  )}
                  {s.step_checkpoint?.kind && (
                    <span className="text-muted-foreground">
                      then verify {phraseCondition(s.step_checkpoint)}
                    </span>
                  )}
                </div>
              )}

              {s.locator_spec.length > 0 && (
                <div className="mt-2.5 pl-7">
                  <div className="text-muted-foreground mb-1 flex items-center gap-1 text-[11px] font-medium uppercase">
                    <Crosshair className="h-3 w-3" /> finds the element by
                  </div>
                  <ol className="space-y-1">
                    {s.locator_spec.map((l, i) => (
                      <li
                        key={i}
                        className={`text-xs ${
                          i === 0 ? "" : "text-muted-foreground"
                        }`}
                      >
                        <span className="text-muted-foreground tabular-nums">
                          {l.rank}.
                        </span>{" "}
                        <span className={i === 0 ? "font-medium" : ""}>
                          {l.kind}
                        </span>
                        {describeLocator(l.params) && (
                          <code className="text-muted-foreground ml-1.5">
                            {describeLocator(l.params)}
                          </code>
                        )}
                        {l.rationale && (
                          <span className="text-muted-foreground/80 block pl-4">
                            ↳ {l.rationale}
                          </span>
                        )}
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </li>
          ))}
        </ol>
        <p className="text-muted-foreground/80 mt-2 text-xs">
          Replay tries the strategies top-down and takes the first that resolves
          to one visible element — no model. A match below rank 0 is logged as
          drift.
        </p>
      </section>

      {/* Handles ---------------------------------------------------------- */}
      {(artifact.known_outcomes.length > 0 ||
        artifact.recoverable_rules.length > 0) && (
        <section>
          <SectionTitle
            count={
              artifact.known_outcomes.length + artifact.recoverable_rules.length
            }
          >
            Handles
          </SectionTitle>
          <div className="mt-3 space-y-2">
            {artifact.known_outcomes.map((o) => (
              <div
                key={o.code}
                className="border-border/70 flex items-start gap-2 rounded-lg border p-2.5 text-xs"
              >
                <Flag className="text-warning mt-px h-3.5 w-3.5 shrink-0" />
                <div>
                  <code className="text-warning font-medium">{o.code}</code>
                  {o.message && <span> — {o.message}</span>}
                  {o.when?.kind && (
                    <span className="text-muted-foreground block">
                      stops &amp; reports when {phraseCondition(o.when)}
                    </span>
                  )}
                </div>
              </div>
            ))}
            {artifact.recoverable_rules.map((r) => (
              <div
                key={r.name}
                className="border-border/70 flex items-start gap-2 rounded-lg border p-2.5 text-xs"
              >
                <RotateCcw className="text-muted-foreground mt-px h-3.5 w-3.5 shrink-0" />
                <div>
                  <code className="font-medium">{r.name}</code>
                  <span className="text-muted-foreground">
                    {" "}
                    · {r.action} then retry
                  </span>
                  {r.when?.kind && (
                    <span className="text-muted-foreground block">
                      triggers when {phraseCondition(r.when)}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

/* ---- small pieces ---------------------------------------------------- */

function SectionTitle({
  children,
  count,
}: {
  children: React.ReactNode;
  count?: number;
}) {
  return (
    <h3 className="text-muted-foreground flex items-center gap-1.5 text-xs font-medium uppercase tracking-wide">
      {children}
      {count !== undefined && (
        <span className="text-muted-foreground/70">· {count}</span>
      )}
    </h3>
  );
}

function ContractRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-3 p-3">
      <span className="text-muted-foreground w-20 shrink-0 pt-0.5 text-xs font-medium uppercase">
        {label}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <code className="bg-muted rounded px-1.5 py-0.5 text-xs">{children}</code>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <span className="text-muted-foreground text-xs">{children}</span>;
}
