# CUA Operator Console (frontend)

Minimal Vite + React + TypeScript UI over the gateway API (`cua serve`, :8080).

Three panels:

| Panel | Backs onto |
|---|---|
| **Capabilities** | `GET /capabilities` + `POST /replays/{id}/invoke` — the agent-facing catalog: pick an approved capability, fill its typed params, run a deterministic replay, see the structured outcome (`success` / `business_outcome` / `recoverable_then_success` / `hard_failure`). |
| **Review** | `GET /artifacts?status=draft` + `POST …/promote` — inspect a draft's ranked locator strategies, risk class, and known-outcome / recoverable rules, then approve or reject. |
| **Interventions** | `GET /interventions` + claim / take-control / actions / release — claim a stuck run, take the control lock on the **same live session**, drive it (actions are recorded), hand control back. |

## Run

```bash
# terminal 1 — backend
cd ../backend && source .venv/bin/activate
cua serve-mock &          # :8799
cua serve                 # :8080

# terminal 2 — frontend
npm install
npm run dev               # http://localhost:5173  (proxies /api -> :8080)
```

The operator console UI is deliberately thin (brief §3.6 scopes a full
co-browsing console out); the **handoff mechanism** it drives is real.
