"use client";

import { useState } from "react";
import { ChevronDown, Flag, RotateCcw, ShieldAlert } from "lucide-react";

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
  recoverable_rules: { name: string; action: string; when?: Condition }[];
};

/* ---- humanisers ---------------------------------------------------------- */

function phraseCondition(c?: Condition | null): string {
  if (!c?.kind) return "-";
  const p = c.params ?? {};
  const s = (k: string) => (p[k] === undefined ? "…" : String(p[k]));
  switch (c.kind) {
    case "url_matches":
      return `URL matches ${s("pattern") !== "…" ? s("pattern") : s("url")}`;
    case "text_present":
      return `“${s("text")}” is visible`;
    case "text_absent":
      return `“${s("text")}” is gone`;
    case "element_present":
      return `${s("selector")} is present`;
    case "element_absent":
      return `${s("selector")} is absent`;
    case "extract_equals":
      return `${s("as")} equals “${s("value")}”`;
    case "extract_matches":
      return `${s("as")} matches /${s("pattern")}/`;
    default:
      return `${c.kind} ${JSON.stringify(p)}`;
  }
}

function locatorParams(params?: Record<string, unknown>): string {
  if (!params) return "";
  return Object.entries(params)
    .filter(([k]) => !k.startsWith("_"))
    .map(([k, v]) => {
      const val = typeof v === "string" ? v : JSON.stringify(v);
      return `${k}=${/\s/.test(String(val)) ? `"${val}"` : val}`;
    })
    .join(" ");
}

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
            This capability performs an <strong>irreversible action</strong>.
            Approving lets agents invoke it unattended - check every step marked{" "}
            <em>mutates state</em>.
          </span>
        </div>
      )}

      {/* Contract ------------------------------------------------------- */}
      <section className="border-border/70 divide-border/60 divide-y rounded-lg border text-sm">
        <ContractRow label="Takes">
          {inputs.length === 0 ? (
            <Muted>nothing</Muted>
          ) : (
            <ChipRow>
              {inputs.map(([name, v]) => (
                <Chip key={name}>
                  {name}
                  <span className="text-muted-foreground">
                    :{v.type ?? "string"}
                  </span>
                  {v["x-sensitive"] && (
                    <span className="text-warning"> ·sensitive</span>
                  )}
                </Chip>
              ))}
            </ChipRow>
          )}
        </ContractRow>
        <ContractRow label="Returns">
          {outputs.length === 0 ? (
            <Muted>nothing</Muted>
          ) : (
            <ChipRow>
              {outputs.map(([name, v]) => (
                <Chip key={name}>
                  {name}
                  <span className="text-muted-foreground">
                    :{v["x-shape"] ?? v.type ?? "string"}
                  </span>
                </Chip>
              ))}
            </ChipRow>
          )}
        </ContractRow>
        <ContractRow label="Done when">
          <span className="text-foreground">
            {phraseCondition(artifact.checkpoint)}
          </span>
        </ContractRow>
      </section>

      {/* Steps -------------------------------------------------------- */}
      <section>
        <SectionTitle count={artifact.steps.length}>Steps</SectionTitle>
        <ol className="border-border/70 divide-border/60 mt-2 divide-y rounded-lg border">
          {artifact.steps.map((s) => (
            <StepRow key={s.step_index} step={s} />
          ))}
        </ol>
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
          <ul className="mt-2 space-y-1.5 text-sm">
            {artifact.known_outcomes.map((o) => (
              <li key={o.code} className="flex items-start gap-2">
                <Flag className="text-warning mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  <code className="text-warning">{o.code}</code>
                  {o.message && (
                    <span className="text-muted-foreground"> - {o.message}</span>
                  )}
                  {o.when?.kind && (
                    <span className="text-muted-foreground">
                      {" "}
                      · when {phraseCondition(o.when)}
                    </span>
                  )}
                </span>
              </li>
            ))}
            {artifact.recoverable_rules.map((r) => (
              <li key={r.name} className="flex items-start gap-2">
                <RotateCcw className="text-muted-foreground mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  <code>{r.name}</code>
                  <span className="text-muted-foreground">
                    {" "}
                    · {r.action} then retry
                    {r.when?.kind && <> · when {phraseCondition(r.when)}</>}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

/* ---- step row ------------------------------------------------------------ */

function StepRow({ step: s }: { step: Step }) {
  const [open, setOpen] = useState(false);
  const primary = s.locator_spec[0];
  const rest = s.locator_spec.slice(1);

  return (
    <li className="px-3 py-2.5">
      <div className="flex items-baseline gap-2.5">
        <span className="text-muted-foreground/70 w-4 shrink-0 text-right text-xs tabular-nums">
          {s.step_index}
        </span>
        <span className="bg-muted text-foreground/70 mt-px shrink-0 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider">
          {s.action_type}
        </span>
        <span className="flex-1 text-sm">{s.description}</span>
        {!s.idempotent && (
          <span className="bg-warning/12 text-warning shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium">
            mutates state
          </span>
        )}
      </div>

      <div className="text-muted-foreground mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 pl-[26px] text-xs">
        {s.value_binding?.param && (
          <span>
            input <code className="text-foreground">← {s.value_binding.param}</code>
          </span>
        )}
        {s.value_binding?.literal != null && (
          <span>input ← “{s.value_binding.literal}”</span>
        )}
        {s.output_binding && (
          <span>
            output{" "}
            <code className="text-foreground">
              → {s.output_binding.field} ({s.output_binding.shape})
            </code>
          </span>
        )}
        {s.step_checkpoint?.kind && (
          <span>verify {phraseCondition(s.step_checkpoint)}</span>
        )}
        {primary && (
          <span>
            via <span className="text-foreground">{primary.kind}</span>
            {locatorParams(primary.params) && (
              <code className="ml-1">{locatorParams(primary.params)}</code>
            )}
          </span>
        )}
        {rest.length > 0 && (
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            className="hover:text-foreground inline-flex items-center gap-0.5"
          >
            {open ? "hide" : `+${rest.length} fallback${rest.length > 1 ? "s" : ""}`}
            <ChevronDown
              className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`}
            />
          </button>
        )}
      </div>

      {open && (
        <ol className="mt-1.5 space-y-1 pl-[26px] text-xs">
          {s.locator_spec.map((l, i) => (
            <li key={i} className="text-muted-foreground">
              <span className="tabular-nums">{l.rank}.</span>{" "}
              <span className={i === 0 ? "text-foreground font-medium" : ""}>
                {l.kind}
              </span>
              {locatorParams(l.params) && (
                <code className="ml-1">{locatorParams(l.params)}</code>
              )}
              {l.rationale && (
                <span className="text-muted-foreground/80 block pl-4">
                  {l.rationale}
                </span>
              )}
            </li>
          ))}
        </ol>
      )}
    </li>
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
    <h3 className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
      {children}
      {count !== undefined && (
        <span className="text-muted-foreground/70"> · {count}</span>
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
    <div className="flex gap-3 px-3 py-2.5">
      <span className="text-muted-foreground w-24 shrink-0 pt-0.5 text-xs font-medium uppercase">
        {label}
      </span>
      <div className="min-w-0 flex-1">{children}</div>
    </div>
  );
}

function ChipRow({ children }: { children: React.ReactNode }) {
  return <div className="flex flex-wrap gap-1.5">{children}</div>;
}

function Chip({ children }: { children: React.ReactNode }) {
  return (
    <code className="bg-muted rounded px-1.5 py-0.5 text-xs">{children}</code>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <span className="text-muted-foreground text-xs">{children}</span>;
}
