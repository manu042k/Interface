import { useCallback, useEffect, useState } from "react";
import { api, type ArtifactSummary, type Capability, type Intervention, type ReplayResult } from "./api";

type Tab = "capabilities" | "review" | "interventions";

export function App() {
  const [tab, setTab] = useState<Tab>("capabilities");
  return (
    <>
      <header>
        <h1>CUA · Operator Console</h1>
        <nav className="row">
          {(["capabilities", "review", "interventions"] as Tab[]).map((t) => (
            <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
              {t[0].toUpperCase() + t.slice(1)}
            </button>
          ))}
        </nav>
      </header>
      <main>
        {tab === "capabilities" && <Capabilities />}
        {tab === "review" && <Review />}
        {tab === "interventions" && <Interventions />}
      </main>
    </>
  );
}

function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): [T | null, string | null, () => void] {
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const run = useCallback(() => {
    fn().then(setData).catch((e) => setErr(String(e.message || e)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(run, [run]);
  return [data, err, run];
}

/* ---------- Capabilities catalog + invoke ---------- */
function Capabilities() {
  const [caps, err, reload] = useAsync(() => api.capabilities(), []);
  const [sel, setSel] = useState<Capability | null>(null);
  return (
    <>
      <div className="panel">
        <h2>Approved capabilities — agent-invocable</h2>
        {err && <div className="err">{err}</div>}
        <table>
          <thead>
            <tr><th>name</th><th>goal</th><th>app</th><th>risk</th><th>version</th><th></th></tr>
          </thead>
          <tbody>
            {(caps || []).map((c) => (
              <tr key={c.artifact_id}>
                <td><code>{c.name}</code></td>
                <td className="muted">{c.goal}</td>
                <td>{c.vendor_app_id}</td>
                <td><span className={`pill ${c.risk_class}`}>{c.risk_class}</span></td>
                <td>v{c.version}</td>
                <td><button className="act ghost" onClick={() => setSel(c)}>invoke</button></td>
              </tr>
            ))}
            {caps && caps.length === 0 && <tr><td colSpan={6} className="muted">no approved capabilities yet — run a discovery + approve it</td></tr>}
          </tbody>
        </table>
        <button className="act ghost" onClick={reload}>refresh</button>
      </div>
      {sel && <InvokePanel cap={sel} onClose={() => setSel(null)} />}
    </>
  );
}

function InvokePanel({ cap, onClose }: { cap: Capability; onClose: () => void }) {
  const props: string[] = Object.keys(cap.input_schema?.properties || {});
  const [params, setParams] = useState<Record<string, string>>(Object.fromEntries(props.map((p) => [p, ""])));
  const [target, setTarget] = useState("http://127.0.0.1:8799/search");
  const [result, setResult] = useState<ReplayResult | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function go() {
    setBusy(true); setErr(null); setResult(null);
    try {
      setResult(await api.invoke(cap.artifact_id, cap.version, target, params));
    } catch (e: any) { setErr(String(e.message || e)); }
    setBusy(false);
  }

  return (
    <div className="panel">
      <h2>Invoke — {cap.name} <button className="act ghost" style={{ float: "right" }} onClick={onClose}>close</button></h2>
      <div className="row">
        <label>target&nbsp;<input value={target} onChange={(e) => setTarget(e.target.value)} size={40} /></label>
      </div>
      {props.map((p) => (
        <div className="row" key={p} style={{ marginTop: 8 }}>
          <label style={{ minWidth: 120 }}>{p}</label>
          <input value={params[p]} onChange={(e) => setParams({ ...params, [p]: e.target.value })} />
          <span className="muted">{cap.input_schema.properties[p]?.["x-sensitive"] ? "(sensitive — supplied per call)" : ""}</span>
        </div>
      ))}
      <div style={{ marginTop: 12 }}>
        <button className="act" disabled={busy} onClick={go}>{busy ? "running…" : "run deterministic replay"}</button>
      </div>
      {err && <div className="err" style={{ marginTop: 10 }}>{err}</div>}
      {result && (
        <div style={{ marginTop: 12 }}>
          <span className={`pill ${result.outcome}`}>{result.outcome}</span>
          {result.business_outcome_code && <span className="muted"> code: <code>{result.business_outcome_code}</code></span>}
          {result.recovered_conditions?.length ? <span className="muted"> · recovered: {result.recovered_conditions.join(", ")}</span> : null}
          <pre>{JSON.stringify(result.outputs ?? result.failure_detail ?? {}, null, 2)}</pre>
          <div className="muted">output_schema: <code>{JSON.stringify(cap.output_schema.properties)}</code></div>
        </div>
      )}
    </div>
  );
}

/* ---------- Artifact review ---------- */
function Review() {
  const [drafts, err, reload] = useAsync(() => api.artifacts("draft"), []);
  const [open, setOpen] = useState<ArtifactSummary | null>(null);
  const [full, setFull] = useState<any>(null);
  const [reviewer, setReviewer] = useState("operator");

  useEffect(() => {
    if (open) api.artifact(open.artifact_id, open.version).then(setFull);
    else setFull(null);
  }, [open]);

  async function decide(d: "approve" | "reject") {
    if (!open) return;
    await api.promote(open.artifact_id, open.version, d, reviewer);
    setOpen(null); reload();
  }

  return (
    <>
      <div className="panel">
        <h2>Draft artifacts — pending review</h2>
        {err && <div className="err">{err}</div>}
        <table>
          <thead><tr><th>name</th><th>goal</th><th>risk</th><th>steps</th><th>known outcomes</th><th></th></tr></thead>
          <tbody>
            {(drafts || []).map((a) => (
              <tr key={a.artifact_id + a.version}>
                <td><code>{a.name}</code> v{a.version}</td>
                <td className="muted">{a.goal}</td>
                <td><span className={`pill ${a.risk_class}`}>{a.risk_class}</span></td>
                <td>{a.steps}</td>
                <td className="muted">{a.known_outcomes.join(", ") || "—"}</td>
                <td><button className="act ghost" onClick={() => setOpen(a)}>review</button></td>
              </tr>
            ))}
            {drafts && drafts.length === 0 && <tr><td colSpan={6} className="muted">no drafts</td></tr>}
          </tbody>
        </table>
      </div>
      {open && (
        <div className="panel">
          <h2>{open.name} v{open.version}</h2>
          <div className="row">
            reviewer <input value={reviewer} onChange={(e) => setReviewer(e.target.value)} />
            <button className="act" onClick={() => decide("approve")}>approve</button>
            <button className="act ghost" onClick={() => decide("reject")}>reject</button>
            <button className="act ghost" onClick={() => setOpen(null)}>close</button>
          </div>
          <p className="muted">Check the ranked locator strategies + rationale, the risk class, and that known-outcome / recoverable rules cover the exceptional states.</p>
          <pre style={{ maxHeight: 460 }}>{full ? JSON.stringify(full, null, 2) : "loading…"}</pre>
        </div>
      )}
    </>
  );
}

/* ---------- Interventions / handoff ---------- */
function Interventions() {
  const [list, err, reload] = useAsync(() => api.interventions("open"), []);
  const [active, setActive] = useState<Intervention | null>(null);
  const [ctx, setCtx] = useState<any>(null);
  const [handle, setHandle] = useState<any>(null);
  const [log, setLog] = useState<string[]>([]);
  const [operator] = useState("op_" + Math.random().toString(36).slice(2, 6));
  const [navUrl, setNavUrl] = useState("http://127.0.0.1:8799/member/12345?ack=1");

  useEffect(() => {
    if (active) api.interventionContext(active.intervention_id).then(setCtx);
    else { setCtx(null); setHandle(null); setLog([]); }
  }, [active]);

  async function claimAndTake() {
    if (!active) return;
    await api.claim(active.intervention_id, operator);
    setHandle(await api.takeControl(active.intervention_id, operator));
    setLog((l) => [...l, `control acquired on ${handle?.session_id ?? "session"}`]);
  }
  async function doNav() {
    if (!active) return;
    const r = await api.operatorAction(active.intervention_id, operator, { type: "navigate", value: navUrl });
    setLog((l) => [...l, `navigate ${navUrl} -> ok=${r.ok}`]);
  }
  async function handBack() {
    if (!active) return;
    const r = await api.release(active.intervention_id, operator, { kind: "text_present", params: { text: "Savings" } });
    setLog((l) => [...l, `released; resumed=${r.resumed}; checkpoint_holds=${r.checkpoint_already_holds}`]);
    setActive(null); reload();
  }

  return (
    <>
      <div className="panel">
        <h2>Open interventions <span className="muted">— you are {operator}</span></h2>
        {err && <div className="err">{err}</div>}
        <table>
          <thead><tr><th>goal</th><th>step</th><th>reason</th><th>tenant</th><th></th></tr></thead>
          <tbody>
            {(list || []).map((i) => (
              <tr key={i.intervention_id}>
                <td className="muted">{i.goal}</td>
                <td>{i.step_index}</td>
                <td>{i.reason}</td>
                <td>{i.tenant}</td>
                <td><button className="act ghost" onClick={() => setActive(i)}>open</button></td>
              </tr>
            ))}
            {list && list.length === 0 && <tr><td colSpan={5} className="muted">none — trigger one with a stuck discovery run</td></tr>}
          </tbody>
        </table>
        <button className="act ghost" onClick={reload}>refresh</button>
      </div>

      {active && (
        <div className="panel">
          <h2>Handoff — {active.goal}</h2>
          <div className="muted">reason: {active.reason} · step {active.step_index}</div>
          {ctx && <div className="muted">current_url: <code>{ctx.current_url}</code></div>}
          {ctx?.transcript_tail && <pre>{(ctx.transcript_tail as string[]).join("\n")}</pre>}
          <div className="row" style={{ marginTop: 10 }}>
            <button className="act" disabled={!!handle} onClick={claimAndTake}>claim + take control of the live session</button>
          </div>
          {handle && (
            <div style={{ marginTop: 10 }}>
              <div className="muted">live handle: <code>{handle.live_handle}</code> · remote display: <code>{handle.remote_display}</code></div>
              <div className="row" style={{ marginTop: 8 }}>
                <input value={navUrl} onChange={(e) => setNavUrl(e.target.value)} size={44} />
                <button className="act ghost" onClick={doNav}>navigate (recorded)</button>
                <button className="act" onClick={handBack}>hand control back</button>
              </div>
            </div>
          )}
          {log.length > 0 && <pre style={{ marginTop: 10 }}>{log.join("\n")}</pre>}
        </div>
      )}
    </>
  );
}
