const BASE = "/api";

async function j<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(BASE + path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const text = await res.text();
  const body = text ? JSON.parse(text) : null;
  if (!res.ok) throw new Error(typeof body?.detail === "string" ? body.detail : JSON.stringify(body));
  return body as T;
}

export type Capability = {
  name: string;
  artifact_id: string;
  version: number;
  goal: string;
  vendor_app_id: string;
  input_schema: any;
  output_schema: any;
  risk_class: string;
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
};

export type Intervention = {
  intervention_id: string;
  tenant: string;
  capability: string | null;
  goal: string | null;
  step_index: number;
  reason: string;
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
  failure_detail?: { step_index: number; expected: string; observed: string } | null;
  evidence_refs?: string[];
  steps_executed?: number;
};

export const api = {
  capabilities: () => j<Capability[]>("/capabilities"),
  artifacts: (status?: string) => j<ArtifactSummary[]>(`/artifacts${status ? `?status=${status}` : ""}`),
  artifact: (id: string, v: number) => j<any>(`/artifacts/${id}/versions/${v}`),
  promote: (id: string, v: number, decision: "approve" | "reject", reviewer: string, notes?: string) =>
    j(`/artifacts/${id}/versions/${v}/promote`, { method: "POST", body: JSON.stringify({ decision, reviewer, notes }) }),
  invoke: (id: string, version: number, target: string, params: Record<string, unknown>, tenant = "default") =>
    j<ReplayResult>(`/replays/${id}/invoke`, {
      method: "POST",
      body: JSON.stringify({ version, target, params, tenant, wait_seconds: 60 }),
    }),
  interventions: (status = "open") => j<Intervention[]>(`/interventions?status=${status}`),
  interventionContext: (id: string) => j<any>(`/interventions/${id}`),
  claim: (id: string, operator: string) =>
    j(`/interventions/${id}/claim`, { method: "POST", body: JSON.stringify({ operator }) }),
  takeControl: (id: string, operator: string) =>
    j<any>(`/interventions/${id}/take-control`, { method: "POST", body: JSON.stringify({ operator }) }),
  operatorAction: (id: string, operator: string, action: Record<string, unknown>) =>
    j<any>(`/interventions/${id}/actions`, { method: "POST", body: JSON.stringify({ operator, action }) }),
  release: (id: string, operator: string, goal_checkpoint?: Record<string, unknown>) =>
    j<any>(`/interventions/${id}/release`, { method: "POST", body: JSON.stringify({ operator, goal_checkpoint }) }),
  startRun: (goal: string, target: string, params: Record<string, unknown>, capability_name?: string) =>
    j<{ run_id: string; status: string }>("/runs", {
      method: "POST",
      body: JSON.stringify({ goal, target, params, capability_name }),
    }),
  run: (id: string) => j<any>(`/runs/${id}`),
};
