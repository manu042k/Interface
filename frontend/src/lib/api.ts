export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8080";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(API_BASE + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const d = body?.detail;
    const msg =
      typeof d === "string"
        ? d
        : Array.isArray(d) && d[0]?.msg
          ? // FastAPI validation error: "<field>: <message>"
            d
              .map(
                (e: { loc?: unknown[]; msg?: string }) =>
                  `${(e.loc ?? []).slice(1).join(".") || "request"}: ${(e.msg ?? "").replace(/^Value error, /, "")}`,
              )
              .join("; ")
          : JSON.stringify(d ?? body);
    throw new Error(msg || `${res.status} ${res.statusText}`);
  }
  return body as T;
}

export type RunView = {
  run_id: string;
  mode: string;
  status: string;
  tenant_id: string;
  app_target: string;
  goal: string | null;
  name: string | null;
  params: Record<string, string>;
  detail: string | null;
  step_count: number;
  artifact_id: string | null;
  artifact_version: number | null;
  record_outcome: "new" | "new_version" | "reused" | "updated_draft" | null;
  novnc_url: string | null;
  sandbox_container: string | null;
  browser: string;
  started_at: number;
  ended_at: number | null;
  llm_calls: number;
  tokens_in: number;
  tokens_out: number;
};

export type RunRow = {
  run_id: string;
  mode: string;
  status: string;
  goal: string | null;
  name: string | null;
  started_at: number;
  ended_at: number | null;
  step_count: number;
  artifact_id: string | null;
  has_sandbox: boolean;
};

export type Capability = {
  name: string;
  artifact_id: string;
  version: number;
  older_versions: number;
  confirmations: number;
  supersedes: number | null;
  entry_url: string;
  vendor_app_id: string;
  app_version: string;
  risk_class: string;
  goal: string;
  summary: string;
  summary_source: "model" | "goal";
  inputs: {
    name: string;
    type: string;
    example: unknown;
    sensitive: boolean;
  }[];
  outputs: { field: string; shape: string }[];
  steps: {
    i: number;
    action: string;
    description: string;
    target: string | null;
    idempotent: boolean;
    binding: { param?: string; literal?: string | null } | null;
    output: string | null;
    checkpoint: { kind: string; params: Record<string, unknown> } | null;
    locators: {
      kind: string;
      rank: number;
      params: Record<string, unknown>;
      rationale: string;
    }[];
  }[];
  handles: {
    business_outcomes: { code: string; message: string }[];
    recoverable: string[];
  };
  provenance: {
    created_from_run_id: string | null;
    reviewed_by: string | null;
    reviewed_at: number | null;
  };
  input_schema: { properties?: Record<string, { "x-sensitive"?: boolean }> };
  output_schema: { properties?: Record<string, unknown> };
  invoke: string;
};

export type ArtifactSummary = {
  artifact_id: string;
  version: number;
  name: string;
  status: string;
  goal: string;
  vendor_app_id: string;
  risk_class: string;
  steps: number;
  known_outcomes: string[];
  record_outcome:
    | "new"
    | "new_version"
    | "reused"
    | "updated_draft"
    | "duplicate"
    | "drift_patch"
    | null;
  supersedes: number | null;
  review_notes: string | null;
};

export type Intervention = {
  intervention_id: string;
  tenant: string;
  capability: string | null;
  goal: string | null;
  run_id: string;
  step_index: number;
  reason: string;
  attempting: string | null;
  opened_at: number;
  status: string;
};

export type ReplayResult = {
  invocation_id?: string;
  status?: string;
  outcome?: string;
  outputs?: Record<string, unknown> | null;
  business_outcome_code?: string | null;
  recovered_conditions?: string[];
  failure_detail?: {
    step_index: number;
    expected: string;
    observed: string;
  } | null;
  evidence_refs?: string[];
  steps_executed?: number;
  duration_seconds?: number;
};

export type RunReport = {
  run: RunView;
  generated_at: number;
  timeline: Array<Record<string, unknown>>;
  artifact: Record<string, unknown> | null;
  replays: Array<Record<string, unknown> & { outcome?: string }>;
  evidence: string[];
};

export const api = {
  startRun: (body: {
    goal: string;
    target: string;
    params?: Record<string, string>;
    capability_name?: string;
    confirm_risky?: boolean;
    tenant?: string;
  }) =>
    j<{ run_id: string; status: string }>("/runs", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  probeTarget: (url: string) =>
    j<{
      ok: boolean;
      reason: string;
      status?: number;
      final_url?: string;
      detail?: string;
    }>(`/targets/probe?url=${encodeURIComponent(url)}`),
  run: (id: string) => j<RunView>(`/runs/${id}`),
  cancelRun: (id: string) =>
    j<RunView>(`/runs/${id}/cancel`, { method: "POST" }),
  runs: () => j<RunRow[]>("/runs"),
  activeRun: () =>
    j<{ run_id: string; status: string; goal: string | null; mode: string } | null>(
      "/runs/active",
    ),
  replay: (id: string) => j<ReplayResult>(`/replays/${id}`),
  report: (id: string) => j<RunReport>(`/runs/${id}/report`),

  capabilities: () => j<Capability[]>("/capabilities"),
  artifacts: (status?: string) =>
    j<ArtifactSummary[]>(`/artifacts${status ? `?status=${status}` : ""}`),
  artifact: (id: string, v: number) =>
    j<Record<string, unknown>>(`/artifacts/${id}/versions/${v}`),
  promote: (
    id: string,
    v: number,
    decision: "approve" | "reject",
    reviewer: string,
    notes?: string,
  ) =>
    j(`/artifacts/${id}/versions/${v}/promote`, {
      method: "POST",
      body: JSON.stringify({ decision, reviewer, notes }),
    }),
  invoke: (
    id: string,
    version: number,
    // null/"" -> the gateway uses the artifact's recorded entry_url
    target: string | null,
    params: Record<string, unknown>,
    tenant = "default",
    // 0 = return the invocation id straight away so the caller can navigate to
    // the run view and watch the replay live; >0 = block for the result.
    waitSeconds = 90,
  ) =>
    j<ReplayResult>(`/replays/${id}/invoke`, {
      method: "POST",
      body: JSON.stringify({
        version,
        target: target || undefined,
        params,
        tenant,
        wait_seconds: waitSeconds,
      }),
    }),

  interventions: (status = "open") =>
    j<Intervention[]>(`/interventions?status=${status}`),
  runIntervention: (runId: string) =>
    j<{
      intervention_id: string;
      status: string;
      claimed_by: string | null;
      step_index: number;
      reason: string;
      attempting: string | null;
    } | null>(`/runs/${runId}/intervention`),
  interventionContext: (id: string) =>
    j<Record<string, unknown>>(`/interventions/${id}`),
  claim: (id: string, operator: string) =>
    j(`/interventions/${id}/claim`, {
      method: "POST",
      body: JSON.stringify({ operator }),
    }),
  takeControl: (id: string, operator: string) =>
    j<{ session_id: string; live_handle: string; remote_display: string }>(
      `/interventions/${id}/take-control`,
      { method: "POST", body: JSON.stringify({ operator }) },
    ),
  operatorAction: (
    id: string,
    operator: string,
    action: Record<string, unknown>,
  ) =>
    j<Record<string, unknown>>(`/interventions/${id}/actions`, {
      method: "POST",
      body: JSON.stringify({ operator, action }),
    }),
  release: (
    id: string,
    operator: string,
    goal_checkpoint?: Record<string, unknown>,
  ) =>
    j<{
      resumed: boolean;
      checkpoint_already_holds: boolean;
      detail: string;
    }>(`/interventions/${id}/release`, {
      method: "POST",
      body: JSON.stringify({ operator, goal_checkpoint }),
    }),
};

export function wsUrl(path: string): string {
  const base = API_BASE.replace(/^http/, "ws");
  return base + path;
}
